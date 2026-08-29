"""Provider request lineage. Implemented and tested; never fired in A6a.

The point of lineage is that a provider's answer is only evidence about the
commit it was asked about. A1b/A3a established that both providers answer
through mutable carriers — a comment that can be rewritten, and was
observed rewriting itself into something that read clean for a head it had
never reviewed. So the request must be recorded with the head it was made
for, and the terminal carrier must be attested against that same head.

Nothing here posts anything. `request()` builds the record a trigger path
would write; the actual posting does not exist on this branch, and a test
asserts it.
"""
import datetime
import hashlib
import json

SCHEMA_NAME = "ProviderRequestLineage-v1"
PROVIDERS = ("codex", "coderabbit")

REQUESTED = "REQUESTED"
OUTCOME_UNKNOWN = "REQUEST_OUTCOME_UNKNOWN"
ANSWERED = "ANSWERED"
STALE = "STALE"


class LineageError(Exception):
    """Raised where a record would be built without the head it is about."""


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def request(*, repo, pr_number, provider, requested_for_head, generation,
            accepted_at, request_carrier_id=None, requested_at=None):
    """The record a trigger path must write *before* it posts anything.

    Written first on purpose. A request whose carrier id is unknown because
    the POST response was lost is `REQUEST_OUTCOME_UNKNOWN`, and A3b-c3
    already established that such a state must not be resolved by posting
    again.
    """
    if provider not in PROVIDERS:
        raise LineageError(f"unknown provider {provider!r}")
    if len(requested_for_head or "") != 40:
        raise LineageError("a request must name the full head it is about")
    return {
        "schema": SCHEMA_NAME,
        "repo": repo, "pr_number": pr_number, "provider": provider,
        "generation": int(generation),
        "requested_for_head": requested_for_head,
        "accepted_at": accepted_at,
        "requested_at": requested_at or utcnow(),
        "request_carrier_id": request_carrier_id,
        "state": REQUESTED if request_carrier_id else OUTCOME_UNKNOWN,
        "terminal_carriers": [],
        "attestation": None,
        "qualification": None,
    }


def attest(record, *, carrier_id, carrier_head_claim, carrier_updated_at,
           current_head):
    """Bind a terminal carrier to a head, and say plainly what that proves.

    `TERMINAL_HEAD_ATTESTATION_MATCH` is a text claim by the provider.
    `AUTHORITATIVE_HEAD_BINDING` would be the carrier being bound to the
    commit by GitHub itself. A1b-c3 fixed these as separate results and
    they are kept separate here: a comment that mentions a SHA has not been
    bound to it.
    """
    matches_request = carrier_head_claim == record["requested_for_head"]
    current = record["requested_for_head"] == current_head
    updated = {**record}
    updated["terminal_carriers"] = record["terminal_carriers"] + [{
        "carrier_id": carrier_id,
        "carrier_head_claim": carrier_head_claim,
        "carrier_updated_at": carrier_updated_at,
    }]
    updated["attestation"] = {
        "TERMINAL_HEAD_ATTESTATION_MATCH": matches_request,
        "AUTHORITATIVE_HEAD_BINDING": False,
        "note": "providers answer through mutable comments; a text match is "
                "an attestation, never a binding",
    }
    updated["state"] = (ANSWERED if matches_request and current
                        else STALE if not current else record["state"])
    return updated


def qualify(record, *, current_head):
    """Positive only when the answer is about the commit still in play."""
    reasons = []
    if record["state"] != ANSWERED:
        reasons.append(f"state is {record['state']}")
    if record["requested_for_head"] != current_head:
        reasons.append("requested for a head that is no longer current")
    if not record["terminal_carriers"]:
        reasons.append("no terminal carrier")
    att = record.get("attestation") or {}
    if not att.get("TERMINAL_HEAD_ATTESTATION_MATCH"):
        reasons.append("terminal carrier does not attest the requested head")
    qualified = not reasons
    return {**record, "qualification": {
        "qualified": qualified, "reasons": reasons,
        "provider_state": (f"{record['provider'].upper()}_ADVISORY_POSITIVE"
                           if qualified else "NOT_POSITIVE"),
        "note": "advisory-positive is never CLEAN; the Governor publishes "
                "its own verdict derived from this, and never promotes the "
                "carrier into provenance",
    }}


def lineage_hash(records):
    """Stable identity for a set of provider answers about one head."""
    payload = [{k: r[k] for k in ("provider", "generation",
                                  "requested_for_head", "state")}
               for r in sorted(records, key=lambda r: (r["provider"],
                                                       r["generation"]))]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()).hexdigest()
