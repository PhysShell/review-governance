"""Raw GitHub -> provider observation. The step that was missing entirely.

`predicates.py` expected an object with `review_ran`, `findings`,
`head_claim` and `generation`. GitHub returns none of those. The only place
such objects existed was the test fixtures, which invented them — so
`CODEX_POSITIVE_PREDICATE PASS_OFFLINE` meant "the classifier classifies
our imagination correctly", and said nothing about production being able
to build the thing being classified.

Two provider surfaces, observed rather than assumed:

**CodeRabbit answers by rewriting a sticky comment.** On `#8` the sticky
was created on 20 August and last updated on the 29th — nine days later,
carrying a fresh run id. So causality cannot be `created_at > request`: a
genuine later review mutates an older carrier. The baseline must be frozen
before the trigger, and the proof of a new answer is a run id that was not
in it.

**Issue comments have no `in_reply_to_id` at all.** Confirmed against the
live API: the field is absent, not null. Any association check written
against it was dead code that never ran, which is worse than a check that
sometimes fails.

**Codex may answer a clean review with a reaction rather than a comment.**
An empty comment set is therefore not evidence of anything, and the
absence of findings must be established positively rather than inferred
from silence.
"""
import datetime
import hashlib
import re


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def require_baseline(base):
    """A baseline must be an *observation*, and a failed read is not one.

    An observed empty surface is perfectly valid — a brand-new PR really
    has no previous runs. What is refused is an unobserved one: without a
    successful read, every existing run id looks new and a carrier that
    predates the request parses as its answer.

    So `read_ok` is the field that matters, not emptiness, and the payload
    must come from a durable capture rather than from a caller's dict.
    """
    if not isinstance(base, dict) or not base.get("captured_at"):
        raise ParseRefused(
            "no baseline capture: an unobserved surface cannot be "
            "distinguished from an empty one")
    if base.get("read_ok") is not True:
        raise ParseRefused(
            "baseline read did not succeed; an unreadable provider surface "
            "is not an empty one")
    if not base.get("baseline_id"):
        raise ParseRefused(
            "baseline is not durable: a caller-supplied dict shaped like a "
            "baseline is not a captured one")
    return base

#: A run id is only a run id where the provider labels it as one.
#:
#: A bare UUID pattern over the whole body picked up seven "run ids" from
#: the real #8 sticky, two of which are the RFC 4122 example UUIDs quoted
#: inside the reviewed diff. Content under review could therefore
#: manufacture the identifiers used to prove that a review happened, which
#: is the same defect one layer further out: text that looks like evidence
#: being read as evidence.
RUN_ID = re.compile(
    r"\*\*Run ID\*\*:\s*`([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})`", re.I)
BARE_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
SHA40 = re.compile(r"\b[0-9a-f]{40}\b")

SKIP_MARKERS = ("skip review", "does not receive automatic reviews",
                "review skipped")
RATE_MARKERS = ("rate limit", "rate limited")

#: Read from the live surface, not assumed. `CODEX_APP` used to hold
#: 199175422, which is the bot *user* id; the Codex App is 1144995. The
#: collector checked `performed_via_github_app.id` against it, so every
#: real Codex carrier would have been refused as WRONG_PROVIDER_IDENTITY
#: while the fixtures — which put the user id in the app field — passed.
CODERABBIT_APP = 347564
CODERABBIT_BOT_USER = 136622811
CODEX_APP = 1144995
CODEX_BOT_USER = 199175422


def author_identity(raw):
    """The three identifiers the carrier actually carries.

    Preserved into the parsed observation because the collector needs them
    and the parser used to drop them: `parse_coderabbit` returned a fresh
    dict with no `performed_via_github_app` and no `app`, so
    `collector.admissibility` read `None` and refused every genuinely
    parsed carrier. Both modules passed their own tests; the composition
    could not admit anything.
    """
    return {
        "author_app_id": ((raw.get("performed_via_github_app") or {}).get("id")
                          or (raw.get("app") or {}).get("id")),
        "author_user_id": (raw.get("user") or {}).get("id"),
        "author_login": (raw.get("user") or {}).get("login"),
    }

#: Fields a caller may never supply: they are conclusions, and this module
#: exists precisely to derive them from raw material.
DERIVED_ONLY = ("review_ran", "findings", "head_claim", "generation")


class ParseRefused(Exception):
    """Raised where semantics are handed in instead of being derived."""


def reject_synthetic(raw):
    """A raw carrier carrying a conclusion is refused.

    Without this, the same fixture shape that made the predicates look
    qualified can be passed straight through production and nobody notices
    that nothing derived anything.
    """
    smuggled = [k for k in DERIVED_ONLY if k in raw]
    if smuggled:
        raise ParseRefused(
            f"raw carrier carries derived fields {smuggled}: these are "
            "conclusions and must come from parsing, not from the caller")
    return raw


def body_digest(body):
    return hashlib.sha256((body or "").encode()).hexdigest()


def baseline(comments, *, provider_app):
    """Frozen before the trigger: what this provider's surface already said.

    Run ids are the load-bearing part. A sticky that is rewritten keeps its
    id and its creation time, so the only durable signal that a new review
    happened is a run identifier absent from this baseline.
    """
    theirs = [c for c in comments
              if ((c.get("performed_via_github_app") or {}).get("id")
                  or (c.get("user") or {}).get("id")) == provider_app
              or (c.get("user") or {}).get("login", "").startswith(
                  "coderabbitai" if provider_app == CODERABBIT_APP else "chatgpt-codex")]
    runs = set()
    for c in theirs:
        runs.update(RUN_ID.findall(c.get("body") or ""))
    triggering = {c["id"]: triggering_comment_ids(c.get("body") or "")
                  for c in theirs}
    return {
        "captured_at": _utcnow(),
        "provider_app": provider_app,
        "carrier_ids": sorted(c["id"] for c in theirs),
        "run_ids": sorted(runs),
        # A6g-c2: the triggering-comment handles already on the surface.
        # Without these, "the provider names our request" is a statement
        # about one reading; with them it is a differential — the provider
        # *began* naming our request, which is the same shape as the run-id
        # rule and is what makes it evidence of causality.
        "triggering_ids": {str(k): v for k, v in triggering.items()},
        "all_triggering_ids": sorted({i for v in triggering.values() for i in v}),
        "digests": {c["id"]: body_digest(c.get("body")) for c in theirs},
        "updated_at": {c["id"]: c.get("updated_at") for c in theirs},
        "note": "frozen before the request; a later review may rewrite an "
                "existing carrier rather than add one",
    }


SKIP_BLOCK = re.compile(
    r"<!--\s*This is an auto-generated comment: skip review.*?"
    r"<!--\s*end of auto-generated comment: skip review[^>]*-->",
    re.S | re.I)
RANGE = re.compile(r"between\s+([0-9a-f]{40})\s+and\s+([0-9a-f]{40})", re.I)

#: The id of the comment that triggered a review, written into CodeRabbit's
#: own sticky markup.
#:
#: Derived from a corpus rather than from documentation, in A6g-c1: on `#32`
#: the sticky carries `…-5469066573`, which is the exact request the
#: Governor posted, four times under two prefixes; on `#8` it carries
#: `…-5461445560`, the human `@coderabbitai review` that triggered that
#: run. The Codex request posted eleven seconds later is **not** there, so
#: the handle is selective rather than "some recent comment".
#:
#: Treated as association evidence and never as sufficient on its own. It
#: is an observed regularity in provider-generated markup, not a contract,
#: and A6g-c1 records it as such.
RADIO_GROUP = re.compile(r'"radioGroupId"\s*:\s*"[a-z0-9-]*?-(\d{6,})"')

#: The command-response shape, which is a different protocol from the
#: sticky. Observed on `#32` as carrier 5469070667.
AUTO_REPLY = re.compile(
    r"<!--\s*This is an auto-generated reply by CodeRabbit\s*-->", re.I)
COMMAND_INVOCATION = re.compile(
    r"CodeRabbit review command invocation:\s*(v\d+):([0-9a-f]{64})", re.I)
REVIEWED_COMMIT_ONLY = re.compile(
    r"I reviewed commit\s*`([0-9a-f]{40})`\s*only", re.I)
FINDING_HEADING = re.compile(r"\*\*Finding\b[^*]*\*\*", re.I)
REVIEW_FINISHED = re.compile(r"\bReview finished\b", re.I)


def triggering_comment_ids(body):
    """Which comment CodeRabbit says it was answering."""
    return sorted({int(x) for x in RADIO_GROUP.findall(body or "")})


def split_run_blocks(body):
    """Separate the sticky into the blocks it actually contains.

    Observed on the real #8 sticky: a skip block for run a3d2af24 sits
    above a completed review for run a765cb7e, in one comment. Evaluating
    the whole body meant the older skip marker decided `review_ran` for a
    review that had run — fail-closed, but wrong about what happened, and
    a clean review would never have qualified.
    """
    skipped_runs = []
    remainder = body
    for match in SKIP_BLOCK.finditer(body):
        skipped_runs.extend(RUN_ID.findall(match.group(0)))
    remainder = SKIP_BLOCK.sub("", body)
    return {"skipped_run_ids": sorted(set(skipped_runs)),
            "review_text": remainder,
            "review_run_ids": sorted(set(RUN_ID.findall(remainder)))}


def _findings_from_review_block(text):
    """Actionable comments, from the structure rather than from a phrase.

    "Review completed" and "status: success" describe the run. Two shapes
    carry the result, and the absence of both is not zero.
    """
    if re.search(r"no actionable comments", text, re.I):
        return []
    match = re.search(r"actionable comments?\D{0,20}?(\d+)", text, re.I)
    if not match:
        return None
    return [{"kind": "actionable"} for _ in range(int(match.group(1)))]


def parse_coderabbit(raw_comments, *, base, requested_head, generation):
    """Derive one observation for this generation, or none."""
    base = require_baseline(base)
    for c in raw_comments:
        reject_synthetic(c)
    mine = [c for c in raw_comments
            if (c.get("user") or {}).get("login") == "coderabbitai[bot]"]
    known = set(base["payload"]["run_ids"] if "payload" in base
                else base["run_ids"])
    known_triggers = set(base.get("all_triggering_ids") or [])
    for c in sorted(mine, key=lambda c: c.get("updated_at") or ""):
        body = c.get("body") or ""
        blocks = split_run_blocks(body)
        # A skip block belongs to its own run and cannot speak for another.
        new_review_runs = sorted(set(blocks["review_run_ids"]) - known)
        named = triggering_comment_ids(body)
        new_triggers = sorted(set(named) - known_triggers)
        # Either kind of new marker makes this carrier worth parsing.
        # Association and content are decided separately and downstream:
        # a carrier that names our request but says nothing reviewable is
        # associated and NOT_POSITIVE, which is a different answer from
        # "not our answer".
        if not new_review_runs and not new_triggers:
            continue
        digests = base["payload"]["digests"] if "payload" in base else base["digests"]
        carriers = base["payload"]["carrier_ids"] if "payload" in base else base["carrier_ids"]
        rewritten = (str(c["id"]) in {str(k) for k in digests}
                     and body_digest(body) != digests.get(
                         c["id"], digests.get(str(c["id"]))))
        is_new_carrier = c["id"] not in carriers
        if not (rewritten or is_new_carrier):
            continue
        if len(new_review_runs) > 1:
            return {"ambiguous": True, "id": c["id"], "provider": "coderabbit",
                    "new_run_ids": new_review_runs, "generation": generation,
                    "cause": "several new review runs in one carrier; the "
                             "applicable one is not determined"}
        text = blocks["review_text"]
        low = text.lower()
        rng = RANGE.search(text)
        return {
            "id": c["id"], "provider": "coderabbit",
            **author_identity(c),
            "created_at": c.get("created_at"),
            "updated_at": c.get("updated_at"),
            # The digest the baseline held for this carrier, so the
            # collector can see the mutation rather than take the flag.
            "baseline_digest_for_carrier": digests.get(
                c["id"], digests.get(str(c["id"]))),
            "observed_digest": body_digest(body),
            "head_binding": "ATTESTED",
            "shape": "STICKY",
            # CodeRabbit writes the id of the comment that triggered the
            # review into its own markup. Provider-generated, selective,
            # and therefore association evidence — never sufficient alone.
            "triggering_comment_ids": named,
            "new_triggering_ids": new_triggers,
            "body": text,
            "reviewed_range": {"from": rng.group(1), "to": rng.group(2)} if rng else None,
            # The head is attested by the *end of the reviewed range*, not
            # by a SHA appearing anywhere in the comment.
            "head_claim": (rng.group(2) if rng and rng.group(2) == requested_head
                           else None),
            "generation": generation,
            "new_run_ids": new_review_runs,
            "skipped_run_ids": blocks["skipped_run_ids"],
            "carrier_was_rewritten": rewritten,
            "review_ran": not any(m in low for m in RATE_MARKERS),
            "findings": _findings_from_review_block(text),
        }
    return None


#: The head Codex says it reviewed, in the form it actually writes it.
#:
#: Observed on comment 5462308601: `**Reviewed commit:** ` followed by a
#: ten-character abbreviation. `SHA40` never matched it, so `head_claim`
#: was None on every real Codex comment and the collector refused all of
#: them for WRONG_HEAD — while the fixtures, which pasted a full SHA into
#: free text, passed.
REVIEWED_COMMIT = re.compile(
    r"reviewed\s+commit[^`\n]*`([0-9a-f]{7,40})`", re.I)

#: Codex's own words for a clean review, from the live carrier:
#: "Codex Review: Didn't find any major issues. Swish!"
CODEX_CLEAN = (
    r"did\s*n[o']?t\s+find\s+any\s+(?:major\s+)?issues",
    r"no\s+issues\s+found", r"\bno\s+issues\b", r"\bno\s+findings\b",
)


def _codex_head_claim(body, requested_head):
    """An abbreviation attests the head only if it is one of its prefixes.

    Prefix matching is deliberately narrow: at least seven hex characters,
    and compared against the head we asked about rather than searched for
    among candidates.
    """
    for abbrev in REVIEWED_COMMIT.findall(body):
        if len(abbrev) >= 7 and requested_head.lower().startswith(abbrev.lower()):
            return requested_head
    return requested_head if requested_head in SHA40.findall(body) else None


def _codex_findings(body):
    low = body.lower()
    m = re.search(r"(\d+)\s+(?:issue|finding|problem)s?", low)
    if m:
        return [{"kind": "issue"} for _ in range(int(m.group(1)))]
    if any(re.search(p, low) for p in CODEX_CLEAN):
        return []
    return None


def parse_codex(raw_comments, raw_reactions, *, base, requested_head,
                generation, request_carrier_id):
    """Codex answers with a comment, and sometimes with a reaction.

    Its own help text says it comments when it has suggestions and reacts
    with 👍 otherwise — but the live carrier on `#8` is a *clean* review
    delivered as a comment, so both shapes must parse.
    """
    base = require_baseline(base)
    for c in raw_comments:
        reject_synthetic(c)
    mine = [c for c in raw_comments
            if (c.get("user") or {}).get("login") == "chatgpt-codex-connector[bot]"
            and c["id"] not in base["carrier_ids"]]
    if mine:
        c = sorted(mine, key=lambda c: c.get("created_at") or "")[-1]
        body = c.get("body") or ""
        low = body.lower()
        return {
            "id": c["id"], "provider": "codex",
            **author_identity(c),
            "created_at": c.get("created_at"),
            "updated_at": c.get("updated_at"), "body": body,
            "head_claim": _codex_head_claim(body, requested_head),
            "head_binding": "ATTESTED",
            "generation": generation,
            "new_run_ids": sorted(set(RUN_ID.findall(body))
                                  - set(base["run_ids"])),
            # Codex carriers have no run id, so the association evidence a
            # new comment offers is its own absence from the pre-request
            # baseline. Recorded as a fact about the capture rather than
            # left for the collector to assume.
            "absent_from_baseline": True,
            "carrier_was_rewritten": False,
            "review_ran": not any(m in low for m in RATE_MARKERS),
            "findings": _codex_findings(body),
        }

    # No comment. A reaction on our own request is the clean-review carrier.
    ours = [r for r in (raw_reactions or [])
            if r.get("content") == "+1"
            and (r.get("user") or {}).get("login") == "chatgpt-codex-connector[bot]"]
    if not ours:
        return None
    r = ours[0]
    return {
        "id": f"reaction:{request_carrier_id}:{r.get('id')}",
        "provider": "codex", "created_at": r.get("created_at"),
        "updated_at": r.get("created_at"), "body": "",
        # A reaction has no `performed_via_github_app` — confirmed against
        # the live endpoint, which returns only `user`. So the identity a
        # reaction can offer is the bot user, and the collector checks that
        # rather than an App id the surface does not carry.
        "author_app_id": None,
        "author_user_id": (r.get("user") or {}).get("id"),
        "author_login": (r.get("user") or {}).get("login"),
        # A reaction carries no text, so it attests no head. The binding
        # comes from the request it is attached to — which is a stronger
        # fact than an attestation, since GitHub, not the provider, decides
        # which comment a reaction is on.
        "head_claim": None,
        "head_binding": "REQUEST_DERIVED",
        "generation": generation, "new_run_ids": [],
        "carrier_was_rewritten": False,
        "reaction_on_request_carrier": request_carrier_id,
        "review_ran": True,
        "findings": [],
    }


def parse_coderabbit_command_response(raw_comments, *, base, requested_head,
                                      generation, request_carrier_id):
    """The reply CodeRabbit posts to an explicit review command.

    A different protocol from the sticky, and A6g met it live: carrier
    5469070667 carries an App identity, an invocation marker, the exact
    full target SHA, an explicit finding and `Review finished` — and no
    `**Run ID**` label, no reviewed range, no actionable count. The sticky
    parser correctly declined it, which is why A6g ended INCONCLUSIVE.

    Content and association are derived separately here, because they are
    separately knowable. The `v2:<hash>` marker is emitted as an opaque
    invocation id and is **not** treated as correlating with anything: A6g-c1
    tested it against every preimage the request offers — comment id, node
    id, body, timestamps, repo-scoped combinations — over three
    command/response pairs on two PRs, and none matched. An unexplained
    64-hex value that happens to be unique per invocation is not a proof of
    anything, however much it looks like one.
    """
    base = require_baseline(base)
    for c in raw_comments:
        reject_synthetic(c)
    known = set(base["run_ids"])
    for c in sorted(raw_comments, key=lambda c: c.get("created_at") or "",
                    reverse=True):
        if (c.get("user") or {}).get("login") != "coderabbitai[bot]":
            continue
        body = c.get("body") or ""
        if not AUTO_REPLY.search(body):
            continue
        invocation = COMMAND_INVOCATION.search(body)
        target = REVIEWED_COMMIT_ONLY.search(body)
        findings = [{"kind": "finding", "heading": m}
                    for m in FINDING_HEADING.findall(body)]
        low = body.lower()
        return {
            "id": c["id"], "provider": "coderabbit",
            "shape": "COMMAND_RESPONSE",
            **author_identity(c),
            "created_at": c.get("created_at"),
            "updated_at": c.get("updated_at"),
            "observed_digest": body_digest(body),
            "baseline_digest_for_carrier": base["digests"].get(
                c["id"], base["digests"].get(str(c["id"]))),
            "body": body,
            # The provider names the commit it reviewed, in full.
            "head_claim": (target.group(1)
                           if target and target.group(1) == requested_head
                           else None),
            "head_binding": "ATTESTED",
            "generation": generation,
            # No run-id protocol in this shape at all.
            "new_run_ids": sorted(set(RUN_ID.findall(body)) - known),
            "invocation_id": (f"{invocation.group(1)}:{invocation.group(2)}"
                              if invocation else None),
            "invocation_correlation": "NOT_DERIVED",
            # Association evidence this carrier itself offers. Empty is the
            # honest answer for this shape, and the collector refuses on it.
            "triggering_comment_ids": triggering_comment_ids(body),
            "new_triggering_ids": sorted(
                set(triggering_comment_ids(body))
                - set(base.get("all_triggering_ids") or [])),
            "carrier_was_rewritten": False,
            "absent_from_baseline": c["id"] not in base["carrier_ids"],
            "review_ran": bool(REVIEW_FINISHED.search(body))
                          and not any(m in low for m in RATE_MARKERS),
            "findings": findings,
        }
    return None
