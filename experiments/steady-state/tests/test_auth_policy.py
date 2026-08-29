"""Freshness closure: a permission must carry its own provenance.

The defect these guard against was not that the token expired. It was that
`AUTHORIZED` — a fact about a past moment — was read as permission for a
present action, and travelled onward as a bare boolean that could no longer
say when it had been observed.
"""
import datetime
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "harness"))
sys.path.insert(0, str(HERE.parents[0] / "operational-readiness" / "harness"))

import accept  # noqa: E402
import auth_policy as ap  # noqa: E402
import auth_state  # noqa: E402
import lineage  # noqa: E402
import publish  # noqa: E402

H = "2" * 40
NOW = datetime.datetime(2026, 8, 29, 12, 0, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture()
def store(tmp_path):
    s = auth_state.AuthStore(tmp_path / "auth.sqlite3")
    yield s
    s.close()


def observe(store, state, at="2026-08-29T11:59:30Z", generation=4,
            source="device_flow"):
    return store.record(state=state, auth_generation=generation,
                        observed_at=at, source=source)


# --- stored facts are untouched -------------------------------------------------

def test_the_vocabulary_of_stored_facts_is_unchanged():
    """STALE is a derived conclusion. Writing it into the store would
    record an inference as an observation — the same confusion in the
    other direction."""
    assert set(auth_state.STATES) == {"AUTHORIZED", "AUTH_LOST",
                                      "REFRESH_OUTCOME_UNKNOWN"}
    assert ap.STALE not in auth_state.STATES
    assert ap.FRESH_AUTHORIZED not in auth_state.STATES


def test_evaluation_writes_nothing(store):
    observe(store, "AUTHORIZED")
    before = len(store.history())
    ap.evaluate(store, now=NOW)
    ap.evaluate(store, now=NOW + datetime.timedelta(days=3))
    assert len(store.history()) == before


# --- the derivation table -------------------------------------------------------

def test_fresh_authorized_within_the_bound(store):
    observe(store, "AUTHORIZED", at="2026-08-29T11:59:30Z")
    p = ap.evaluate(store, now=NOW)
    assert p.state == ap.FRESH_AUTHORIZED and p.age_seconds == 30
    assert p.permits_action is True


def test_authorized_beyond_the_bound_is_stale(store):
    observe(store, "AUTHORIZED", at="2026-08-29T11:58:00Z")
    p = ap.evaluate(store, now=NOW)
    assert p.state == ap.STALE and p.age_seconds == 120
    assert p.permits_action is False


def test_the_boundary_is_sixty_seconds(store):
    observe(store, "AUTHORIZED", at="2026-08-29T11:59:00Z")
    assert ap.evaluate(store, now=NOW).state == ap.FRESH_AUTHORIZED
    observe(store, "AUTHORIZED", at="2026-08-29T11:58:59Z")
    assert ap.evaluate(store, now=NOW).state == ap.STALE
    assert ap.AUTH_PERMISSION_MAX_AGE_SECONDS == 60


@pytest.mark.parametrize("state", ["AUTH_LOST", "REFRESH_OUTCOME_UNKNOWN"])
def test_evidential_bad_states_are_forbidden(store, state):
    observe(store, state)
    p = ap.evaluate(store, now=NOW)
    assert p.state == ap.FORBIDDEN and p.asserts_loss is True


def test_no_observation_fails_closed(store):
    p = ap.evaluate(store, now=NOW)
    assert p.state == ap.UNOBSERVED and p.permits_action is False


def test_malformed_time_fails_closed(store):
    observe(store, "AUTHORIZED", at="whenever")
    p = ap.evaluate(store, now=NOW)
    assert p.state == ap.UNOBSERVED
    assert "cannot be established" in p.cause


def test_future_time_fails_closed(store):
    """A clock disagreement is not a fresh permission."""
    observe(store, "AUTHORIZED", at="2027-01-01T00:00:00Z")
    p = ap.evaluate(store, now=NOW)
    assert p.state == ap.UNOBSERVED
    assert "in the future" in p.cause


# --- STALE is not AUTH_LOST -----------------------------------------------------

def test_stale_does_not_assert_loss(store):
    """Otherwise every quiet night becomes revoke-refresh-revoke, and the
    alerts get trained out of meaning."""
    observe(store, "AUTHORIZED", at="2026-08-29T10:00:00Z")
    p = ap.evaluate(store, now=NOW)
    assert p.state == ap.STALE
    assert p.asserts_loss is False
    assert ap.STALE not in ap.ASSERTS_LOSS


def test_only_evidential_states_assert_loss(store):
    observe(store, "AUTH_LOST")
    assert ap.evaluate(store, now=NOW).asserts_loss is True


def test_staleness_is_not_a_stored_invalidation_trigger():
    source = (HERE / "harness" / "auth_policy.py").read_text()
    assert "does not assert revocation" in source
    assert ap.ASSERTS_LOSS == frozenset({ap.FORBIDDEN})


# --- the raw boolean is refused at every critical interface --------------------

def fresh(store):
    observe(store, "AUTHORIZED", at="2026-08-29T11:59:50Z")
    return ap.evaluate(store, now=NOW)


def ok_checks(permission, **over):
    base = {"draft": False, "base_current": True, "ruleset_verified": True,
            "carrier": {"state": "CONFIRMED", "head_sha": H},
            "permission": permission, "open_generations": []}
    base.update(over)
    return base


@pytest.mark.parametrize("bogus", [True, 1, "AUTHORIZED", {"state": "ok"}])
def test_accept_refuses_a_bare_boolean(bogus):
    with pytest.raises(ap.PermissionRequired):
        accept.accept(repo="r", pr_number=8, head_sha=H,
                      **ok_checks(bogus))


@pytest.mark.parametrize("bogus", [True, 1, {"state": "ok"}])
def test_success_guard_refuses_a_bare_boolean(bogus):
    with pytest.raises(ap.PermissionRequired):
        publish.guard(reduction={"verdict": "SUCCESS", "head_sha": H},
                      bundle={"head_sha": H}, current_head_sha=H,
                      permission=bogus)


@pytest.mark.parametrize("bogus", [True, 1, None])
def test_provider_request_refuses_a_bare_boolean(bogus):
    with pytest.raises(ap.PermissionRequired):
        lineage.request(repo="r", pr_number=8, provider="codex",
                        requested_for_head=H, generation=1, accepted_at="t",
                        permission=bogus)


def test_a_permission_carries_where_it_came_from(store):
    p = fresh(store)
    d = p.as_dict()
    for key in ("state", "auth_generation", "observed_at", "age_seconds",
                "source", "evaluated_at"):
        assert key in d, key
    assert d["auth_generation"] == 4 and d["source"] == "device_flow"


# --- stale blocks every new dangerous action ------------------------------------

def stale(store):
    observe(store, "AUTHORIZED", at="2026-08-29T10:00:00Z")
    return ap.evaluate(store, now=NOW)


def test_stale_refuses_acceptance(store):
    r = accept.accept(repo="r", pr_number=8, head_sha=H,
                      **ok_checks(stale(store)))
    assert r["state"] == accept.REFUSED
    assert any("STALE" in f for f in r["failures"])


def test_stale_refuses_a_provider_request_before_the_network(store):
    with pytest.raises(lineage.LineageError) as exc:
        lineage.request(repo="r", pr_number=8, provider="codex",
                        requested_for_head=H, generation=1, accepted_at="t",
                        permission=stale(store))
    assert "before the network" in str(exc.value)


def test_stale_refuses_a_success_publication(store):
    checked = publish.guard(reduction={"verdict": "SUCCESS", "head_sha": H},
                            bundle={"head_sha": H}, current_head_sha=H,
                            permission=stale(store))
    assert checked["may_publish_success"] is False
    assert any("STALE" in r for r in checked["refusals"])


def test_fresh_permits_the_success_guard(store):
    checked = publish.guard(reduction={"verdict": "SUCCESS", "head_sha": H},
                            bundle={"head_sha": H}, current_head_sha=H,
                            permission=fresh(store))
    assert checked["may_publish_success"] is True


def test_acceptance_records_the_permission_it_relied_on(store):
    r = accept.accept(repo="r", pr_number=8, head_sha=H,
                      **ok_checks(fresh(store)))
    assert r["state"] == accept.ACCEPTED
    assert r["authorization"]["state"] == ap.FRESH_AUTHORIZED
