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
    """A baseline must have been captured, not merely be empty.

    Found live: passing `{"run_ids": []}` made every pre-existing run id
    look new, so the skip comment on #32 parsed as an answer to a request
    that had not been made. An unestablished baseline is not an empty one —
    the same substitution as every other absence in this programme, this
    time inside the module written to catch it.
    """
    if not isinstance(base, dict) or not base.get("captured_at"):
        raise ParseRefused(
            "no baseline capture: without a pre-request snapshot every "
            "existing run id looks new, and a carrier that predates the "
            "request parses as its answer")
    return base

RUN_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
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


def _findings_from_coderabbit(body):
    """Actionable comments, from the structure rather than from a phrase.

    'Review completed' and 'status: success' describe the run. The count
    lives in the actionable-comments line, and its absence is not zero.
    """
    match = re.search(r"actionable comments?\D{0,20}?(\d+)", body, re.I)
    if not match:
        return None
    count = int(match.group(1))
    return [{"kind": "actionable"} for _ in range(count)]


def parse_coderabbit(raw_comments, *, base, requested_head, generation):
    """Derive one observation for this generation, or none."""
    base = require_baseline(base)
    for c in raw_comments:
        reject_synthetic(c)
    mine = [c for c in raw_comments
            if (c.get("user") or {}).get("login") == "coderabbitai[bot]"]
    for c in sorted(mine, key=lambda c: c.get("updated_at") or ""):
        body = c.get("body") or ""
        runs = set(RUN_ID.findall(body))
        new_runs = sorted(runs - set(base["run_ids"]))
        rewritten = (c["id"] in base["digests"]
                     and body_digest(body) != base["digests"][c["id"]])
        is_new_carrier = c["id"] not in base["carrier_ids"]
        if not new_runs:
            continue                      # nothing here that we caused
        if not (rewritten or is_new_carrier):
            continue
        low = body.lower()
        heads = SHA40.findall(body)
        return {
            "id": c["id"], "provider": "coderabbit",
            "created_at": c.get("created_at"),
            "updated_at": c.get("updated_at"),
            "body": body,
            "head_claim": requested_head if requested_head in heads else None,
            "generation": generation,
            "new_run_ids": new_runs,
            "carrier_was_rewritten": rewritten,
            "review_ran": not any(m in low for m in SKIP_MARKERS)
                          and not any(m in low for m in RATE_MARKERS),
            "findings": _findings_from_coderabbit(body),
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
