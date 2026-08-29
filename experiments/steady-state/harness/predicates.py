"""What the answer actually says. Separate from whether it is our answer.

A6a's `qualify()` called an answer positive when it was ANSWERED, for the
current head, with a terminal carrier attesting the right SHA. Nothing
looked at the content. A provider could write "found a critical bug" on
exactly the right commit and be scored `ADVISORY_POSITIVE`.

So there are two questions and they live in two modules:

    collector.py   is this the answer to our request, for this head?
    predicates.py  what does that answer say?

A positive result requires the provider to have reviewed and to have
reported nothing actionable. Silence is not agreement: a skipped review, a
rate limit, an unparsed body and a review that never ran are all
NOT_POSITIVE, and each says so separately.

The provider-specific traps are the ones already paid for:

    CodeRabbit  "Review completed" and "status: success" describe the run,
                not the findings (A1b-R / A3a). Actionable comments are
                counted separately, and a sticky that rewrote itself into
                a clean-looking summary for an unreviewed head is exactly
                why the count must come from a parsed structure rather
                than a phrase.
    Codex       a terminal SHA in free text is an attestation, not a
                binding (A1b-c3), and "no issues" must be distinguished
                from "did not run".
"""
import hashlib
import json

SCHEMA_REVISION = "ProviderPositivePredicate-v1"

POSITIVE = "ADVISORY_POSITIVE"
NOT_POSITIVE = "NOT_POSITIVE"

SKIP_MARKERS = ("skip review", "does not receive automatic reviews",
                "review skipped", "trigger review")
RATE_LIMIT_MARKERS = ("rate limit", "rate limited", "quota")


def _digest(snapshot):
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def normalize(carrier):
    """A frozen, hashable view of what the provider said.

    The raw comment is mutable and is not carried into the bundle; this
    snapshot is, so `qualified` can be re-derived by a reader instead of
    trusted.
    """
    body = (carrier.get("body") or "")
    snapshot = {
        "carrier_id": carrier.get("id"),
        "provider": carrier.get("provider"),
        "created_at": carrier.get("created_at"),
        "updated_at": carrier.get("updated_at"),
        "head_claim": carrier.get("head_claim"),
        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "body_length": len(body),
        "findings": carrier.get("findings"),
        "review_ran": carrier.get("review_ran"),
    }
    snapshot["snapshot_digest"] = _digest(snapshot)
    return snapshot


def _lower(carrier):
    return (carrier.get("body") or "").lower()


def codex_predicate(carrier):
    reasons = []
    body = _lower(carrier)
    if carrier.get("review_ran") is not True:
        reasons.append("the review is not established as having run")
    findings = carrier.get("findings")
    if findings is None:
        reasons.append("no parsed findings structure; a body that cannot be "
                       "parsed is not a clean review")
    elif findings:
        reasons.append(f"{len(findings)} finding(s) reported")
    if any(m in body for m in RATE_LIMIT_MARKERS):
        reasons.append("rate limited; handling is not a verdict")
    return reasons


def coderabbit_predicate(carrier):
    reasons = []
    body = _lower(carrier)
    if any(m in body for m in SKIP_MARKERS):
        reasons.append("the carrier says the review was skipped")
    if carrier.get("review_ran") is not True:
        reasons.append("the review is not established as having run")
    findings = carrier.get("findings")
    if findings is None:
        reasons.append("no parsed actionable-comment count; 'Review "
                       "completed' describes the run, not the findings")
    elif findings:
        reasons.append(f"{len(findings)} actionable comment(s)")
    if any(m in body for m in RATE_LIMIT_MARKERS):
        reasons.append("rate limited; handling is not a verdict")
    return reasons


PREDICATES = {"codex": codex_predicate, "coderabbit": coderabbit_predicate}


def evaluate(provider, carrier):
    """Positive only when the provider reviewed and reported nothing."""
    predicate = PREDICATES.get(provider)
    if predicate is None:
        return {"provider": provider, "state": NOT_POSITIVE,
                "reasons": [f"no predicate for provider {provider!r}"],
                "schema_revision": SCHEMA_REVISION}
    reasons = predicate(carrier)
    snapshot = normalize({**carrier, "provider": provider})
    return {
        "provider": provider,
        "state": POSITIVE if not reasons else NOT_POSITIVE,
        "reasons": reasons,
        "findings_count": (len(carrier["findings"])
                           if isinstance(carrier.get("findings"), list)
                           else None),
        "snapshot": snapshot,
        "snapshot_digest": snapshot["snapshot_digest"],
        "schema_revision": SCHEMA_REVISION,
        "note": "advisory-positive is never CLEAN; the Governor derives its "
                "own verdict and does not promote this carrier into "
                "provenance",
    }
