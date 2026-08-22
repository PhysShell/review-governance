"""Adversarial tests for the A1c lifecycle reducer.

These encode the hazards that make an unattended Governor dangerous rather
than merely inconvenient: a single-use refresh token means two workers can
race, and a lost response can destroy a credential chain that GitHub will
never hand back. The reducer's job is to fail closed and to never mistake
a lost race for a lost authorization.
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

from lifecycle import (AUTH_LOST, AUTHORIZED,  # noqa: E402
                       AUTHORIZED_NEW_GENERATION, REAUTH_REQUIRED,
                       REFRESH_DUE, REFRESH_OUTCOME_UNKNOWN, REFRESHING,
                       State, may_trigger, reduce)


# --- the two-worker race ----------------------------------------------------

def test_race_loser_adopts_the_winners_generation_instead_of_panicking():
    """W1 rotated 7 -> 8 and committed. W2, still on 7, gets a rejection.
    The durable store says 8, so W2 must adopt it, not declare AUTH_LOST."""
    w2 = State(REFRESHING, 7)
    after = reduce(w2, {"kind": "refresh_rejected",
                        "error": "incorrect_client_credentials"},
                   durable_generation=8)
    assert after.name == AUTHORIZED_NEW_GENERATION
    assert after.generation == 8
    assert "another worker rotated" in after.reason


def test_refresh_rejection_without_newer_generation_requires_human():
    w = State(REFRESHING, 7)
    after = reduce(w, {"kind": "refresh_rejected",
                       "error": "incorrect_client_credentials"},
                   durable_generation=7)
    assert after.name == REAUTH_REQUIRED
    assert after.may_trigger_providers is False


def test_rejection_alone_never_yields_auth_lost():
    """AUTH_LOST is reserved for an unambiguous revocation signal."""
    for durable in (7, 8, 99):
        after = reduce(State(REFRESHING, 7),
                       {"kind": "refresh_rejected", "error": "whatever"},
                       durable_generation=durable)
        assert after.name != AUTH_LOST


def test_misleading_error_string_is_not_a_control_flow_key():
    """The observed rejection says incorrect_client_credentials (A1c-c1).
    Every rejection, whatever its wording, takes the same branch."""
    outcomes = {
        reduce(State(REFRESHING, 3), {"kind": "refresh_rejected", "error": err},
               durable_generation=4).name
        for err in ("incorrect_client_credentials", "bad_refresh_token",
                    "unexpected_new_error", None)}
    assert outcomes == {AUTHORIZED_NEW_GENERATION}


# --- the ambiguous outcome --------------------------------------------------

def test_lost_refresh_response_is_fail_closed_not_retried():
    after = reduce(State(REFRESHING, 5), {"kind": "refresh_outcome_unknown"},
                   durable_generation=5)
    assert after.name == REFRESH_OUTCOME_UNKNOWN
    assert after.may_trigger_providers is False


def test_unknown_outcome_resolves_if_the_store_already_advanced():
    """Crash after a successful durable commit but before the process saw
    it: the store is the arbiter, so the state resolves without a human."""
    after = reduce(State(REFRESHING, 5), {"kind": "refresh_outcome_unknown"},
                   durable_generation=6)
    assert after.name == AUTHORIZED_NEW_GENERATION
    assert after.generation == 6


# --- revocation and 401 -----------------------------------------------------

def test_webhook_revocation_is_immediate_auth_loss():
    after = reduce(State(AUTHORIZED, 2),
                   {"kind": "webhook_authorization_revoked"},
                   durable_generation=2)
    assert after.name == AUTH_LOST
    assert after.may_trigger_providers is False


def test_401_alone_is_not_revocation_but_schedules_a_controlled_refresh():
    after = reduce(State(AUTHORIZED, 2),
                   {"kind": "access_token_401",
                    "refresh_generation_available": True},
                   durable_generation=2)
    assert after.name == REFRESH_DUE
    assert after.name != AUTH_LOST


def test_401_without_any_refresh_generation_requires_human():
    after = reduce(State(AUTHORIZED, 2),
                   {"kind": "access_token_401",
                    "refresh_generation_available": False},
                   durable_generation=2)
    assert after.name == REAUTH_REQUIRED


def test_human_reauthorization_starts_a_new_generation():
    after = reduce(State(REAUTH_REQUIRED, 2),
                   {"kind": "reauthorized", "new_generation": 3},
                   durable_generation=3)
    assert after.name == AUTHORIZED
    assert after.generation == 3


# --- the trigger gate -------------------------------------------------------

def test_only_known_current_authorized_states_may_trigger_providers():
    durable = 9
    allowed = [State(AUTHORIZED, 9), State(AUTHORIZED_NEW_GENERATION, 9)]
    for state in allowed:
        assert may_trigger(state, durable) is True
    forbidden = [State(REFRESH_DUE, 9), State(REFRESHING, 9),
                 State(AUTH_LOST, 9), State(REAUTH_REQUIRED, 9),
                 State(REFRESH_OUTCOME_UNKNOWN, 9)]
    for state in forbidden:
        assert may_trigger(state, durable) is False


def test_stale_generation_may_not_trigger_even_when_authorized():
    """A worker holding generation 8 while the store is at 9 is not
    known-current: its credentials were rotated out from under it."""
    assert may_trigger(State(AUTHORIZED, 8), durable_generation=9) is False


def test_full_race_sequence_ends_with_both_workers_usable():
    durable = 7
    w1 = State(AUTHORIZED, 7)
    w2 = State(AUTHORIZED, 7)

    w1 = reduce(w1, {"kind": "refresh_started"}, durable)
    w2 = reduce(w2, {"kind": "refresh_started"}, durable)
    w1 = reduce(w1, {"kind": "refresh_succeeded", "new_generation": 8}, durable)
    durable = 8                                   # CAS commit landed
    w2 = reduce(w2, {"kind": "refresh_rejected",
                     "error": "incorrect_client_credentials"}, durable)

    assert (w1.name, w1.generation) == (AUTHORIZED_NEW_GENERATION, 8)
    assert (w2.name, w2.generation) == (AUTHORIZED_NEW_GENERATION, 8)
    assert may_trigger(w1, durable) and may_trigger(w2, durable)
