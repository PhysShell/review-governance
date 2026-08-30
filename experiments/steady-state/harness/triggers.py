"""Provider trigger adapters. The railway under the train timetable.

A6a recorded lineage for requests that nothing could send. `lineage.py`
said so plainly — "the actual posting does not exist on this branch" —
which is a candid thing for a module to say in the middle of a positive
path with 319 green tests behind it.

These adapters can send. In A6f they are exercised only against an
injected transport; nothing here has run against a real provider.

Two properties are structural rather than intended.

**Intent is durable before the network.** The caller records it; this
function refuses to post without a request row already written, because a
post whose response is lost is only recoverable if we wrote down that we
were about to try.

**Exactly one attempt.** A3b-c3 established that an indeterminate provider
POST is `REQUEST_OUTCOME_UNKNOWN` and must not be retried: a second
comment is a second request, and the provider would answer both.

The carrier is the app-mediated user identity established in A1b. The
installation-bot carrier was refused by both providers, so the trigger
must be made with a user access token that carries
`performed_via_github_app`.
"""
import datetime
import json

import auth_policy
import rounds

CODEX = "codex"
CODERABBIT = "coderabbit"

#: Preregistered invocations. A provider command is a contract with the
#: provider, not a string to be tuned until something answers.
INVOCATION = {
    CODEX: "@codex review",
    CODERABBIT: "@coderabbitai review",
}

#: Identities a terminal answer must come from, read from the live surface
#: rather than assumed.
#:
#: The previous table called one field `PROVIDER_APP_ID` and put two
#: different kinds of identifier in it. CodeRabbit's 347564 is the App id;
#: Codex's 199175422 is the **bot user** id, and the Codex App is 1144995.
#: So `performed_via_github_app.id == 199175422` was false on every real
#: Codex carrier — the check only ever passed because the fixtures inserted
#: that number into a field GitHub fills with the other one.
#:
#: Verified over all nine bot carriers on `#8` and against `GET /apps/{slug}`:
#:
#:     coderabbitai[bot]             user 136622811   app  347564
#:     chatgpt-codex-connector[bot]  user 199175422   app 1144995
#:
#: Three fields because no single one covers both surfaces: an issue
#: comment carries `performed_via_github_app`, and a reaction carries only
#: its user.
PROVIDER_IDENTITY = {
    CODEX: {"app_id": 1144995, "bot_user_id": 199175422,
            "login": "chatgpt-codex-connector[bot]"},
    CODERABBIT: {"app_id": 347564, "bot_user_id": 136622811,
                 "login": "coderabbitai[bot]"},
}
PROVIDER_LOGIN = {p: i["login"] for p, i in PROVIDER_IDENTITY.items()}
PROVIDER_APP = {p: i["app_id"] for p, i in PROVIDER_IDENTITY.items()}
PROVIDER_BOT_USER = {p: i["bot_user_id"] for p, i in PROVIDER_IDENTITY.items()}


def identity_of(carrier):
    """The three identifiers a carrier actually offers, whichever it has."""
    return {
        "app_id": ((carrier.get("performed_via_github_app") or {}).get("id")
                   or (carrier.get("app") or {}).get("id")),
        "user_id": (carrier.get("user") or {}).get("id"),
        "login": (carrier.get("user") or {}).get("login"),
    }


def _unused_provider_app_id(*args, **kwargs):
    """`PROVIDER_APP_ID` retired: it conflated an App id with a user id.

    Left as a raising trap rather than deleted, because a name that used to
    resolve is the easiest thing in the world to type back in.
    """
    raise TriggerRefused(
        "PROVIDER_APP_ID conflated App ids with bot user ids; use "
        "PROVIDER_IDENTITY, which names which is which")


class TriggerRefused(Exception):
    """Raised where a provider would be contacted without a durable intent."""


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def body_for(provider, head_sha):
    """The request text, naming the commit it is about.

    The head is stated in the request so the answer can be checked against
    it later — but a provider echoing a SHA is an attestation, never a
    binding, and the collector treats it that way.
    """
    return (f"{INVOCATION[provider]}\n\n"
            f"Requested by the Governor for head `{head_sha}`.\n"
            f"This request is bound to that exact commit; an answer about "
            f"any other commit is not evidence for it.")


def send(post, store, *, request_row, permission, head_sha):
    """One POST, at most, for a request whose intent is already durable.

    `post(path, body) -> (status, parsed)` is injected, so this is testable
    without a network and cannot reach a provider by accident in a test.
    """
    permission = auth_policy.require(permission)
    if not permission.permits_action:
        raise TriggerRefused(
            f"provider request refused before the network: authorization "
            f"permission is {permission.state}")
    if not request_row or request_row.get("request_outcome") != rounds.INTENT_RECORDED:
        raise TriggerRefused(
            "a provider is contacted only for a request whose intent is "
            "already recorded; without that row a lost response is "
            "unrecoverable")
    if request_row["requested_for_head"] != head_sha:
        raise TriggerRefused("request row is for another head")
    # The durable record says which observation authorised this request.
    # Posting under a different one would make the provenance a sentence
    # about an authorization that did not license the mutation.
    if permission.observation_id != request_row.get("auth_observation_id") or \
            permission.auth_generation != request_row.get("auth_generation"):
        raise TriggerRefused(
            f"permission does not match the recorded intent: intent was "
            f"authorised by observation {request_row.get('auth_observation_id')} "
            f"generation {request_row.get('auth_generation')}, this call "
            f"carries {permission.observation_id}/{permission.auth_generation}")

    provider = request_row["provider"]
    path = (f"/repos/{request_row['repo']}/issues/"
            f"{request_row['pr_number']}/comments")
    attempts = {"count": 0}
    try:
        attempts["count"] += 1
        status, created = post(path, {"body": body_for(provider, head_sha)})
    except Exception as exc:
        settled = store.settle_request(
            request_row["request_id"], outcome=rounds.OUTCOME_UNKNOWN)
        return {"state": rounds.OUTCOME_UNKNOWN, "attempts": attempts["count"],
                "retry_performed": False, "request": settled,
                "cause": f"transport failure: {type(exc).__name__}",
                "recovery": "never a second post; a repeat is a second "
                            "request and the provider would answer both"}

    carrier_id = (created or {}).get("id")
    if status not in (200, 201) or not carrier_id:
        settled = store.settle_request(
            request_row["request_id"], outcome=rounds.OUTCOME_UNKNOWN)
        return {"state": rounds.OUTCOME_UNKNOWN, "attempts": attempts["count"],
                "retry_performed": False, "request": settled,
                "http_status": status,
                "cause": "no usable carrier id after one post"}

    settled = store.settle_request(request_row["request_id"],
                                   outcome=rounds.SENT, carrier_id=carrier_id)
    return {"state": rounds.SENT, "attempts": attempts["count"],
            "retry_performed": False, "request": settled,
            "request_carrier_id": carrier_id, "http_status": status}
