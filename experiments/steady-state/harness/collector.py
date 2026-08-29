"""Admissibility: did this answer arise from *our* request, for *this* head.

The lineage in A6a proved nothing of the sort. `attest()` was handed a
carrier id, a head claim and a timestamp, and declared the record ANSWERED
whenever the text matched. Any comment mentioning the right SHA would have
qualified — including one written before the request existed.

`#32` supplied the live counter-example within six seconds of opening:
CodeRabbit posted comment 5462558501 saying *skip review*, unprompted,
before the Governor had asked anything. If that comment can become terminal
evidence for generation 1, the entire request lineage is decorative.

So admissibility is proven from five separate facts, and any one of them
missing is a refusal:

    provider identity      the author is that provider's bot, by app id
    causal ordering        the carrier is newer than the recorded intent
    request association    it belongs to the request we actually sent
    generation exactness   it is this generation, not a neighbouring one
    head binding           it is about the head the request named

Ordering is the one people skip, and it is the one that catches a
pre-existing carrier: a comment cannot answer a question asked after it.
"""
import datetime

import triggers

PREEXISTING = "PREEXISTING_NONREQUEST_CARRIER"
WRONG_PROVIDER = "WRONG_PROVIDER_IDENTITY"
WRONG_GENERATION = "WRONG_GENERATION"
WRONG_HEAD = "WRONG_HEAD"
UNASSOCIATED = "NOT_ASSOCIATED_WITH_REQUEST"
ADMISSIBLE = "ADMISSIBLE"


def _parse(value):
    return datetime.datetime.strptime(
        value.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")


def admissibility(carrier, request_row, *, head_sha, generation):
    """Why this carrier may or may not answer this request."""
    reasons = []
    provider = request_row["provider"]

    author_app = ((carrier.get("performed_via_github_app") or {}).get("id")
                  or (carrier.get("app") or {}).get("id"))
    if author_app != triggers.PROVIDER_APP_ID.get(provider):
        reasons.append({
            "code": WRONG_PROVIDER,
            "detail": f"carrier authored by app {author_app}, expected "
                      f"{triggers.PROVIDER_APP_ID.get(provider)} for "
                      f"{provider}"})

    try:
        created = _parse(carrier["created_at"])
        intent = _parse(request_row["intent_recorded_at"])
        if created <= intent:
            reasons.append({
                "code": PREEXISTING,
                "detail": f"carrier created {carrier['created_at']} is not "
                          f"newer than the recorded intent "
                          f"{request_row['intent_recorded_at']}; a comment "
                          "cannot answer a question asked after it"})
    except (KeyError, ValueError, TypeError):
        reasons.append({"code": PREEXISTING,
                        "detail": "carrier or intent timestamp unusable; "
                                  "ordering could not be established"})

    if request_row.get("request_carrier_id") is None:
        reasons.append({
            "code": UNASSOCIATED,
            "detail": "the request has no carrier id, so nothing can be "
                      "shown to be its answer"})
    elif carrier.get("in_reply_to_id") is not None and \
            carrier["in_reply_to_id"] != request_row["request_carrier_id"]:
        reasons.append({"code": UNASSOCIATED,
                        "detail": "carrier replies to a different request"})

    if int(carrier.get("generation", generation)) != int(generation):
        reasons.append({"code": WRONG_GENERATION,
                        "detail": "carrier belongs to another generation"})

    if carrier.get("head_claim") != head_sha:
        reasons.append({
            "code": WRONG_HEAD,
            "detail": f"carrier attests {carrier.get('head_claim')!r}, "
                      f"request was for {head_sha}"})

    return {
        "admissible": not reasons,
        "state": ADMISSIBLE if not reasons else reasons[0]["code"],
        "refusals": reasons,
        "carrier_id": carrier.get("id"),
        "provider": provider,
        "checked_at": datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "TERMINAL_HEAD_ATTESTATION_MATCH only; a carrier that names "
                "a SHA has not been bound to it by GitHub",
    }


def collect(carriers, request_row, *, head_sha, generation):
    """Every candidate judged separately, and exactly one may survive.

    Several admissible carriers is not a preference problem to be solved by
    taking the newest — it is an ambiguity, and the applicable answer is
    not determined.
    """
    judged = [admissibility(c, request_row, head_sha=head_sha,
                            generation=generation) for c in carriers]
    admitted = [j for j in judged if j["admissible"]]
    if len(admitted) == 1:
        return {"state": "COLLECTED", "terminal": admitted[0],
                "judged": judged}
    if not admitted:
        return {"state": "NO_ADMISSIBLE_CARRIER", "judged": judged,
                "cause": "no carrier could be shown to answer this request"}
    return {"state": "AMBIGUOUS", "judged": judged,
            "admitted": [j["carrier_id"] for j in admitted],
            "cause": f"{len(admitted)} admissible carriers; the applicable "
                     "answer is not determined"}
