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
CODERABBIT_APP = 347564
CODEX_APP = 199175422

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
    return {
        "captured_at": _utcnow(),
        "provider_app": provider_app,
        "carrier_ids": sorted(c["id"] for c in theirs),
        "run_ids": sorted(runs),
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
    for c in sorted(mine, key=lambda c: c.get("updated_at") or ""):
        body = c.get("body") or ""
        blocks = split_run_blocks(body)
        # A skip block belongs to its own run and cannot speak for another.
        new_review_runs = sorted(set(blocks["review_run_ids"]) - known)
        if not new_review_runs:
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
            "created_at": c.get("created_at"),
            "updated_at": c.get("updated_at"),
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


def parse_codex(raw_comments, raw_reactions, *, base, requested_head,
                generation, request_carrier_id):
    """Codex answers with a comment when it has findings, and may answer a
    clean review with a reaction on the request itself."""
    base = require_baseline(base)
    for c in raw_comments:
        reject_synthetic(c)
    mine = [c for c in raw_comments
            if (c.get("user") or {}).get("login") == "chatgpt-codex-connector[bot]"
            and c["id"] not in base["carrier_ids"]]
    if mine:
        c = sorted(mine, key=lambda c: c.get("created_at") or "")[-1]
        body = c.get("body") or ""
        heads = SHA40.findall(body)
        low = body.lower()
        findings = None
        m = re.search(r"(\d+)\s+(?:issue|finding|problem)s?", low)
        if m:
            findings = [{"kind": "issue"} for _ in range(int(m.group(1)))]
        elif "no issues" in low or "no findings" in low:
            findings = []
        return {
            "id": c["id"], "provider": "codex",
            "created_at": c.get("created_at"),
            "updated_at": c.get("updated_at"), "body": body,
            "head_claim": requested_head if requested_head in heads else None,
            "generation": generation,
            "new_run_ids": sorted(set(RUN_ID.findall(body))
                                  - set(base["run_ids"])),
            "carrier_was_rewritten": False,
            "review_ran": not any(m in low for m in RATE_MARKERS),
            "findings": findings,
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
        # A reaction carries no text, so it attests no head. The head
        # binding must come from the request it is attached to, and that is
        # the caller's association proof, not a claim in the carrier.
        "head_claim": None,
        "generation": generation, "new_run_ids": [],
        "carrier_was_rewritten": False,
        "reaction_on_request_carrier": request_carrier_id,
        "review_ran": True,
        "findings": [],
    }
