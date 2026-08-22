"""The A1c lifecycle reducer: how a Governor worker may reason about its
own authorization state.

The hazards this encodes come from one observed fact — a refresh token is
single-use and rotates the whole pair — plus its two consequences:

  race:      two workers refresh the same generation; one wins, the other
             is told `bad_refresh_token` while authorization is perfectly
             healthy.
  ambiguity: GitHub accepted the refresh and destroyed the old pair, but
             the response never reached durable storage.

Invariants:
  * `bad_refresh_token` alone NEVER means revoked — the durable generation
    is re-read first.
  * an unknown refresh outcome is never retried blindly.
  * only a known-current AUTHORIZED state may issue provider triggers.
"""
from dataclasses import dataclass, replace

AUTHORIZED = "AUTHORIZED"
REFRESH_DUE = "REFRESH_DUE"
REFRESHING = "REFRESHING"
AUTHORIZED_NEW_GENERATION = "AUTHORIZED_NEW_GENERATION"
AUTH_LOST = "AUTH_LOST"
REAUTH_REQUIRED = "REAUTH_REQUIRED"
REFRESH_OUTCOME_UNKNOWN = "REFRESH_OUTCOME_UNKNOWN"

TRIGGER_ALLOWED_STATES = frozenset({AUTHORIZED, AUTHORIZED_NEW_GENERATION})


@dataclass(frozen=True)
class State:
    name: str
    generation: int
    reason: str = ""

    @property
    def may_trigger_providers(self) -> bool:
        return self.name in TRIGGER_ALLOWED_STATES


def may_trigger(state: State, durable_generation: int) -> bool:
    """Provider triggers require a known-current AUTHORIZED state: the
    worker's generation must still be the durable one."""
    return state.may_trigger_providers and state.generation == durable_generation


def reduce(state: State, event: dict, durable_generation: int) -> State:
    """`durable_generation` is re-read from the store for every event —
    that re-read is what distinguishes a lost race from a lost
    authorization."""
    kind = event["kind"]

    # Revocation is the only unambiguous loss signal.
    if kind == "webhook_authorization_revoked":
        return State(AUTH_LOST, state.generation,
                     "github_app_authorization: revoked")

    if kind == "access_token_expiring":
        return replace(state, name=REFRESH_DUE, reason="token near expiry")

    if kind == "access_token_401":
        # A 401 may be plain expiry, or revocation. It is never treated as
        # proof of revocation on its own: try a controlled refresh if a
        # refresh generation exists.
        if event.get("refresh_generation_available", True):
            return State(REFRESH_DUE, state.generation,
                         "401 on access token; controlled refresh due")
        return State(REAUTH_REQUIRED, state.generation,
                     "401 with no refresh generation available")

    if kind == "refresh_started":
        return State(REFRESHING, state.generation, "refresh in flight")

    if kind == "refresh_succeeded":
        # Durable commit happened before this event is emitted.
        return State(AUTHORIZED_NEW_GENERATION, event["new_generation"],
                     "refresh rotated the pair")

    if kind == "refresh_bad_refresh_token":
        # THE critical branch: re-read the durable generation first.
        if durable_generation > state.generation:
            return State(AUTHORIZED_NEW_GENERATION, durable_generation,
                         "another worker rotated; adopting newer generation")
        return State(REAUTH_REQUIRED, state.generation,
                     "refresh rejected and no newer durable generation exists")

    if kind == "refresh_outcome_unknown":
        # Response lost/crash between GitHub accepting the refresh and the
        # durable commit. Never retry blindly.
        if durable_generation > state.generation:
            return State(AUTHORIZED_NEW_GENERATION, durable_generation,
                         "durable store already advanced; outcome resolved")
        return State(REFRESH_OUTCOME_UNKNOWN, state.generation,
                     "refresh response lost before durable commit")

    if kind == "reauthorized":
        return State(AUTHORIZED, event["new_generation"],
                     "human re-authorization")

    return replace(state, reason=f"ignored event: {kind}")
