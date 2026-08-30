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

#: How a carrier is bound to the head it answers for.
ATTESTED = "ATTESTED"              # the provider's text names the head
REQUEST_DERIVED = "REQUEST_DERIVED"  # GitHub attached it to our request


def _parse(value):
    return datetime.datetime.strptime(
        value.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")


def _rewritten_after_request(carrier, request_row):
    """A carrier that existed before the request but was mutated after it.

    This is the normal CodeRabbit shape and the reason the flat
    `created_at` veto was wrong: the sticky on `#8` was created on 20
    August and rewritten on the 29th with a new run id. A rule that only
    looked at creation refused the one carrier the provider actually uses.

    Three facts, all of which must hold; any one alone is not causality:

        the carrier was in the pre-request baseline, and its stored digest
        differs from the one observed now
        it now carries a provider-labelled run id absent from that baseline
        its `updated_at` is later than the recorded intent
    """
    if not carrier.get("carrier_was_rewritten"):
        return False, "carrier was not in the pre-request baseline as a rewrite"
    before = carrier.get("baseline_digest_for_carrier")
    now = carrier.get("observed_digest")
    if not before or not now or before == now:
        return False, ("no digest movement between the captured baseline and "
                       "the observed carrier")
    if not carrier.get("new_run_ids"):
        return False, "no provider-labelled run id absent from the baseline"
    try:
        if _parse(carrier["updated_at"]) <= _parse(
                request_row["intent_recorded_at"]):
            return False, (f"carrier last updated {carrier.get('updated_at')} "
                           f"is not newer than the intent "
                           f"{request_row['intent_recorded_at']}")
    except (KeyError, ValueError, TypeError):
        return False, "carrier or intent timestamp unusable"
    return True, "rewritten after the request with a new run id"


def admissibility(carrier, request_row, *, head_sha, generation):
    """Why this carrier may or may not answer this request."""
    reasons = []
    provider = request_row["provider"]
    expected = triggers.PROVIDER_IDENTITY.get(provider, {})

    # Identity, from whichever field the surface actually carries. An
    # issue comment has `performed_via_github_app`; a reaction has only its
    # user. Requiring the App id of a reaction refused every clean Codex
    # answer, and requiring only the login would let any account with that
    # display name qualify.
    app_id = carrier.get("author_app_id")
    user_id = carrier.get("author_user_id")
    login = carrier.get("author_login")
    if app_id is not None:
        if app_id != expected.get("app_id"):
            reasons.append({
                "code": WRONG_PROVIDER,
                "detail": f"carrier authored via app {app_id}, expected "
                          f"{expected.get('app_id')} for {provider}"})
    elif user_id is not None:
        if user_id != expected.get("bot_user_id"):
            reasons.append({
                "code": WRONG_PROVIDER,
                "detail": f"carrier authored by user {user_id}, expected the "
                          f"{provider} bot {expected.get('bot_user_id')}"})
    else:
        reasons.append({
            "code": WRONG_PROVIDER,
            "detail": "carrier carries no author identity at all; a parsed "
                      "observation that dropped it cannot be attributed"})
    if login is not None and login != expected.get("login"):
        reasons.append({
            "code": WRONG_PROVIDER,
            "detail": f"carrier login {login!r}, expected "
                      f"{expected.get('login')!r}"})

    # Causal ordering, by carrier shape rather than by one timestamp.
    rewritten, why = _rewritten_after_request(carrier, request_row)
    if not rewritten:
        try:
            created = _parse(carrier["created_at"])
            intent = _parse(request_row["intent_recorded_at"])
            if created <= intent:
                reasons.append({
                    "code": PREEXISTING,
                    "detail": f"carrier created {carrier['created_at']} is not "
                              f"newer than the recorded intent "
                              f"{request_row['intent_recorded_at']}, and it is "
                              f"not a post-request rewrite ({why})"})
        except (KeyError, ValueError, TypeError):
            reasons.append({"code": PREEXISTING,
                            "detail": "carrier or intent timestamp unusable; "
                                      "ordering could not be established"})

    if request_row.get("request_carrier_id") is None:
        reasons.append({
            "code": UNASSOCIATED,
            "detail": "the request has no carrier id, so nothing can be "
                      "shown to be its answer"})
    else:
        # Association must be established positively. The previous version
        # only checked `in_reply_to_id` when it was not None — and issue
        # comments have no such field at all, confirmed against the live
        # API, so that branch never executed. Right bot, later timestamp
        # and right SHA was enough to be admitted.
        reply_to = carrier.get("in_reply_to_id")
        new_runs = carrier.get("new_run_ids")
        reaction_on = carrier.get("reaction_on_request_carrier")
        if reply_to is not None:
            if reply_to != request_row["request_carrier_id"]:
                reasons.append({"code": UNASSOCIATED,
                                "detail": "carrier replies to another request"})
        elif reaction_on is not None:
            if reaction_on != request_row["request_carrier_id"]:
                reasons.append({"code": UNASSOCIATED,
                                "detail": "reaction is on another request"})
        elif new_runs or carrier.get("absent_from_baseline"):
            # Two shapes of positive association, one per provider surface.
            # CodeRabbit rewrites a sticky and the proof is a run id absent
            # from the pre-request capture; Codex posts a fresh comment and
            # the proof is the carrier's own absence from it. Requiring a
            # run id of both refused every Codex answer, because Codex
            # carriers do not have one.
            if not rewritten and \
                    carrier.get("created_at", "") <= request_row["intent_recorded_at"]:
                reasons.append({
                    "code": UNASSOCIATED,
                    "detail": "a carrier older than the intent that was not "
                              "rewritten cannot be this request's answer"})
        else:
            reasons.append({
                "code": UNASSOCIATED,
                "detail": "no association evidence: no reply id, no reaction "
                          "on our request, and no provider run identifier "
                          "absent from the pre-request baseline"})

    observed_generation = carrier.get("generation")
    if observed_generation is None:
        # Defaulting to the expected value turned missing evidence into a
        # match. Absence is UNRESOLVED, never agreement.
        reasons.append({"code": WRONG_GENERATION,
                        "detail": "carrier carries no generation binding; "
                                  "absence is not a match"})
    elif int(observed_generation) != int(generation):
        reasons.append({"code": WRONG_GENERATION,
                        "detail": "carrier belongs to another generation"})

    # Head binding. Two admissible shapes, and neither is "the text mentions
    # a SHA somewhere".
    binding = carrier.get("head_binding")
    if binding == REQUEST_DERIVED:
        # A reaction carries no text and therefore attests nothing. What it
        # does carry is stronger: GitHub, not the provider, decides which
        # comment it is attached to, and that comment is our request for
        # exactly one head. Requiring an attestation here refused every
        # clean Codex answer by construction.
        on = carrier.get("reaction_on_request_carrier")
        if on is None or on != request_row.get("request_carrier_id"):
            reasons.append({
                "code": WRONG_HEAD,
                "detail": f"head binding claims to derive from our request "
                          f"carrier, but the reaction is on {on!r}"})
        elif request_row["requested_for_head"] != head_sha:
            reasons.append({
                "code": WRONG_HEAD,
                "detail": f"the request this binding derives from was for "
                          f"{request_row['requested_for_head']}, not {head_sha}"})
    elif binding == ATTESTED:
        if carrier.get("head_claim") != head_sha:
            reasons.append({
                "code": WRONG_HEAD,
                "detail": f"carrier attests {carrier.get('head_claim')!r}, "
                          f"request was for {head_sha}"})
    else:
        reasons.append({
            "code": WRONG_HEAD,
            "detail": f"carrier declares no head binding ({binding!r}); "
                      "absence of a binding is not a match"})

    return {
        "admissible": not reasons,
        "state": ADMISSIBLE if not reasons else reasons[0]["code"],
        "refusals": reasons,
        "carrier_id": carrier.get("id"),
        "provider": provider,
        "head_binding": binding,
        "causality": "POST_REQUEST_REWRITE" if rewritten else "NEWER_CARRIER",
        "checked_at": datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "an ATTESTED head is the provider's word; a REQUEST_DERIVED "
                "head is GitHub's, and only the second is a binding",
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
