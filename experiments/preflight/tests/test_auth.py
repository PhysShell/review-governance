"""Tests for the A1c auth-state producer and the safety transition it feeds.

The gap this closes was not a missing dashboard tile. By A1c semantics
`AUTH_LOST` and `REFRESH_OUTCOME_UNKNOWN` forbid provider triggers,
invalidate standing successes and demand fresh qualification — so a state
nobody produces is a safety transition nobody can enter.

Accordingly the assertions are about refusals, asymmetry, and what must
*not* happen on recovery.
"""
import json
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent.parent / "operational-readiness"
sys.path.insert(0, str(BASE / "harness"))

import alerting  # noqa: E402
import auth_gate  # noqa: E402
import auth_producer  # noqa: E402
import auth_state  # noqa: E402

REPO = "PhysShell/evm-from-scratch"


@pytest.fixture()
def store(tmp_path):
    s = auth_state.AuthStore(tmp_path / "auth.sqlite3")
    yield s
    s.close()


def observe(store, state, generation=3, source="fixture", at="2026-08-26T00:00:00Z"):
    return store.record(state=state, auth_generation=generation,
                        observed_at=at, source=source)


# --- fail closed --------------------------------------------------------------

def test_never_observed_is_not_permission(store):
    """A Governor that has never established authorization has not
    established it. That is the same operational fact as having lost it."""
    assert store.current() is None
    assert store.permits_triggers() is False
    with pytest.raises(auth_state.AuthorizationRefused):
        auth_state.require_triggers_permitted(store)


@pytest.mark.parametrize("state", [auth_state.AUTH_LOST,
                                   auth_state.REFRESH_OUTCOME_UNKNOWN])
def test_bad_states_forbid_triggers_and_demand_invalidation(store, state):
    observe(store, state)
    assert store.permits_triggers() is False
    assert store.demands_invalidation() is True
    with pytest.raises(auth_state.AuthorizationRefused) as exc:
        auth_state.require_triggers_permitted(store)
    assert state in str(exc.value)


def test_authorized_permits_and_demands_nothing(store):
    observe(store, auth_state.AUTHORIZED)
    assert store.permits_triggers() is True
    assert store.demands_invalidation() is False


def test_unknown_state_is_refused_not_stored(store):
    with pytest.raises(auth_state.UnknownAuthState):
        store.record(state="PROBABLY_FINE", auth_generation=1,
                     observed_at="t", source="fixture")
    with pytest.raises(auth_state.UnknownAuthState):
        store.record(state=auth_state.AUTHORIZED, auth_generation=1,
                     observed_at="t", source="vibes")
    assert store.history() == []


def test_permits_triggers_is_an_allowlist():
    """A state nobody anticipated must fail closed rather than fall through
    a negative check."""
    source = (BASE / "harness" / "auth_state.py").read_text()
    assert "PERMITS_TRIGGERS = frozenset({AUTHORIZED})" in source
    assert auth_state.PERMITS_TRIGGERS == {auth_state.AUTHORIZED}


# --- append-only --------------------------------------------------------------

def test_authorization_history_is_append_only(store):
    observe(store, auth_state.AUTHORIZED)
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("UPDATE auth_observations SET state='AUTH_LOST'")
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("DELETE FROM auth_observations")


def test_history_keeps_the_transition_not_just_the_latest(store):
    observe(store, auth_state.AUTHORIZED, at="t1")
    observe(store, auth_state.AUTH_LOST, at="t2")
    observe(store, auth_state.AUTHORIZED, generation=4, at="t3")
    states = [(r["previous_state"], r["state"]) for r in store.history()]
    assert states == [(None, "AUTHORIZED"), ("AUTHORIZED", "AUTH_LOST"),
                      ("AUTH_LOST", "AUTHORIZED")]


# --- the mirror is not the authority -----------------------------------------

def test_projection_is_written_but_never_read_back(store, tmp_path):
    observe(store, auth_state.AUTH_LOST, generation=3)
    path = tmp_path / "auth-state.json"
    mirror = store.project(path)
    assert mirror["state"] == auth_state.AUTH_LOST
    assert json.loads(path.read_text())["auth_generation"] == 3

    # tamper with the mirror; authority must be unmoved
    path.write_text(json.dumps({"state": "AUTHORIZED", "auth_generation": 99}))
    assert store.current()["state"] == auth_state.AUTH_LOST
    assert store.permits_triggers() is False


def test_gate_reads_the_store_not_the_json():
    """Structural: if policy ever reads the mirror, a file anything on the
    host can edit decides whether the gate opens."""
    source = (BASE / "harness" / "auth_gate.py").read_text()
    assert "auth-state.json" not in source.replace(
        "never `auth-state.json`", "").replace(
        "if policy read the mirror", "")
    assert "AuthStore" in source


# --- the producer never spends a credential ----------------------------------

def test_refresh_is_reachable_only_by_explicit_command():
    """Refresh tokens are single-use with rotation, so spending one must be
    something an operator asked for — never something a poll loop decided.

    The earlier version of this test forbade refresh outright. That was
    correct until the adapter had to be able to produce AUTHORIZED after the
    eight-hour access token expires; forbidding it then would have left the
    producer permanently unable to observe a healthy system.
    """
    source = (BASE / "harness" / "auth_producer.py").read_text()
    assert source.count("oauth/access_token") == 1, \
        "exactly one place may spend the refresh token"
    refresh_body = source.split("def cmd_refresh(")[1].split("\ndef ")[0]
    assert "oauth/access_token" in refresh_body
    # exactly one dispatch site, and it is the CLI command table
    callers = [line.strip() for line in source.splitlines()
               if "cmd_refresh" in line and "def cmd_refresh" not in line]
    assert len(callers) == 1, callers
    assert '"refresh": cmd_refresh' in callers[0]


def test_expired_access_token_is_not_a_revocation(store, monkeypatch, tmp_path):
    """Eight-hour expiry is the normal condition. Treating it as AUTH_LOST
    would revoke every standing green check twice a day."""
    monkeypatch.setattr(auth_producer, "current_credential",
                        lambda: {"access_token": "ghu_x", "generation": 3})
    monkeypatch.setattr(auth_producer, "probe_access_token",
                        lambda t: (401, None))
    result = auth_producer.cmd_probe(object(), store)
    assert result["recorded"] is False
    assert result["result"] == "needs_refresh"
    assert store.history() == []


def test_successful_probe_records_authorized(store, monkeypatch):
    monkeypatch.setattr(auth_producer, "current_credential",
                        lambda: {"access_token": "ghu_x", "generation": 7})
    monkeypatch.setattr(auth_producer, "probe_access_token",
                        lambda t: (200, {"login": "PhysShell"}))
    result = auth_producer.cmd_probe(object(), store)
    assert result["state"] == auth_state.AUTHORIZED
    assert store.current()["auth_generation"] == 7
    assert store.permits_triggers() is True


def test_indeterminate_probe_leaves_state_alone(store, monkeypatch):
    """Unreadable is not revoked."""
    observe(store, auth_state.AUTHORIZED)
    monkeypatch.setattr(auth_producer, "current_credential",
                        lambda: {"access_token": "ghu_x", "generation": 3})
    monkeypatch.setattr(auth_producer, "probe_access_token",
                        lambda t: (503, None))
    result = auth_producer.cmd_probe(object(), store)
    assert result["result"] == "INDETERMINATE"
    assert len(store.history()) == 1
    assert store.permits_triggers() is True


def test_missing_credential_is_not_evidence_of_loss(store, monkeypatch):
    monkeypatch.setattr(auth_producer, "current_credential", lambda: None)
    result = auth_producer.cmd_probe(object(), store)
    assert result["recorded"] is False
    assert "not evidence of loss" in result["note"]


# --- the gate can only ever revoke -------------------------------------------

def test_gate_cannot_publish_anything_passing():
    for method, path, body in [
            ("POST", "/repos/x/check-runs", {"conclusion": "success"}),
            ("PATCH", "/repos/x/check-runs/1", {"conclusion": "success"}),
            ("PATCH", "/repos/x/check-runs/1", {"conclusion": "neutral"}),
            ("PATCH", "/repos/x/check-runs/1", {"conclusion": "skipped"}),
            ("DELETE", "/repos/x/check-runs/1", None)]:
        with pytest.raises(auth_gate.GateCapability):
            auth_gate.guarded_write(method, path, "token", body)


def test_gate_permits_only_a_failing_patch(monkeypatch):
    seen = {}
    monkeypatch.setattr(auth_gate.governor, "request",
                        lambda m, p, t, b=None: seen.update(
                            {"m": m, "p": p, "b": b}) or (200, {}))
    auth_gate.guarded_write("PATCH", "/repos/x/check-runs/1", "t",
                            {"conclusion": "failure"})
    assert seen["m"] == "PATCH"


def test_no_transition_when_authorized(store, tmp_path, monkeypatch):
    import decisions as dec
    observe(store, auth_state.AUTHORIZED)
    history = dec.History(tmp_path / "d.sqlite3")

    def explode(*a, **k):
        raise AssertionError("must not mint a token when nothing is demanded")

    monkeypatch.setattr(auth_gate.governor, "installation_token", explode)

    class Args:
        repo = REPO
        context = "ai/final-review-readiness-probe"

    result = auth_gate.apply_transition(Args(), store, history)
    assert result["invalidations"] == []
    assert result["permits_triggers"] is True
    history.close()


def test_transition_invalidates_and_confirms_by_readback(store, tmp_path,
                                                        monkeypatch):
    import decisions as dec
    observe(store, auth_state.REFRESH_OUTCOME_UNKNOWN, generation=3)
    history = dec.History(tmp_path / "d.sqlite3")
    monkeypatch.setattr(auth_gate.governor, "installation_token",
                        lambda: "tok")

    calls = []

    def fake_request(method, path, token, body=None):
        calls.append((method, path))
        if path.startswith("/repos/") and "pulls?" in path:
            return 200, [{"number": 29, "head": {"sha": "a" * 40}}]
        if "check-runs?per_page" in path:
            return 200, {"check_runs": [
                {"id": 5, "name": "ai/final-review-readiness-probe",
                 "conclusion": "success", "app": {"id": 4669438}}]}
        if method == "PATCH":
            return 200, {"conclusion": "failure"}
        if method == "GET" and "/check-runs/5" in path:
            return 200, {"conclusion": "failure"}
        return 404, None

    monkeypatch.setattr(auth_gate.governor, "request", fake_request)

    class Args:
        repo = REPO
        context = "ai/final-review-readiness-probe"

    result = auth_gate.apply_transition(Args(), store, history)
    assert len(result["invalidations"]) == 1
    inv = result["invalidations"][0]
    assert inv["observed"] == "failure"
    assert inv["state"] == "CONFIRMED"
    assert result["restores_automatically"] is False
    history.close()


def test_recovery_has_no_code_path_that_restores():
    """Reauthorization restores the ability to review, never the evidence."""
    source = (BASE / "harness" / "auth_gate.py").read_text()
    assert '"conclusion": "success"' not in source
    assert "restores_automatically" in source
    assert auth_gate.NON_PASSING.isdisjoint(auth_gate.PASSING)


def test_returning_to_authorized_does_not_reopen_the_old_success(store, tmp_path,
                                                                 monkeypatch):
    import decisions as dec
    observe(store, auth_state.REFRESH_OUTCOME_UNKNOWN)
    observe(store, auth_state.AUTHORIZED, generation=4)
    history = dec.History(tmp_path / "d.sqlite3")
    monkeypatch.setattr(auth_gate.governor, "installation_token",
                        lambda: (_ for _ in ()).throw(
                            AssertionError("no writes on recovery")))

    class Args:
        repo = REPO
        context = "ai/final-review-readiness-probe"

    result = auth_gate.apply_transition(Args(), store, history)
    assert result["invalidations"] == []
    assert result["permits_triggers"] is True
    assert "recovery never restores" in result["note"]
    history.close()


# --- the production publisher refuses a success without authorization --------

def test_publish_refuses_a_success_while_unauthorized(tmp_path):
    import governor
    db = tmp_path / "auth.sqlite3"
    s = auth_state.AuthStore(db)
    s.record(state=auth_state.AUTH_LOST, auth_generation=3,
             observed_at="t", source="fixture")
    s.close()
    with pytest.raises(auth_state.AuthorizationRefused):
        governor.authorization_row(str(db), "success")


def test_publish_still_permits_revocation_while_unauthorized(tmp_path):
    """Refusing to revoke while unauthorized would strand green checks
    precisely when nobody is watching them."""
    import governor
    db = tmp_path / "auth.sqlite3"
    s = auth_state.AuthStore(db)
    s.record(state=auth_state.AUTH_LOST, auth_generation=3,
             observed_at="t", source="fixture")
    s.close()
    row = governor.authorization_row(str(db), "failure")
    assert row["state"] == auth_state.AUTH_LOST


def test_publish_refuses_a_success_when_nothing_was_ever_observed(tmp_path):
    import governor
    with pytest.raises(auth_state.AuthorizationRefused):
        governor.authorization_row(str(tmp_path / "empty.sqlite3"), "success")


# --- the sentinel reads the projection the producer actually writes ----------

def test_sentinel_reads_the_projection_field_names(tmp_path):
    """A mirror whose field names nobody checked renders as None forever,
    which looks calm and says nothing."""
    import sentinel
    s = auth_state.AuthStore(tmp_path / "auth.sqlite3")
    s.record(state=auth_state.AUTH_LOST, auth_generation=5,
             observed_at="2026-08-26T00:00:00Z", source="authorization_webhook")
    projection = tmp_path / "auth-state.json"
    s.project(projection)
    s.close()

    class Args:
        repo = REPO
        auth_state_file = str(projection)

    reported = sentinel.check_auth_state(Args(), None)
    assert reported["state"] == auth_state.AUTH_LOST
    assert reported["auth_generation"] == 5
    assert reported["source"] == "authorization_webhook"
    assert reported["observed_at"] == "2026-08-26T00:00:00Z"


def test_sentinel_forwards_both_bad_states(tmp_path):
    import sentinel
    for bad, cause in [(auth_state.AUTH_LOST, "auth_lost"),
                       (auth_state.REFRESH_OUTCOME_UNKNOWN,
                        "refresh_outcome_unknown")]:
        n = alerting.Notifier(tmp_path / f"a-{cause}.sqlite3",
                              alerting.NullTransport(), origin="test")
        s = auth_state.AuthStore(tmp_path / f"s-{cause}.sqlite3")
        s.record(state=bad, auth_generation=1, observed_at="2026-08-26T00:00:00Z",
                 source="refresh")
        proj = tmp_path / f"p-{cause}.json"
        s.project(proj)
        s.close()

        class Args:
            repo = REPO
            auth_state_file = str(proj)

        sentinel.check_auth_state(Args(), n)
        assert n.open_causes() == [cause]
        n.close()


# --- refresh: exactly one attempt, strict classification ---------------------

FULL = {"access_token": "ghu_new", "refresh_token": "ghr_new",
        "expires_in": 28800, "refresh_token_expires_in": 15897600}


def _prep(tmp_path, monkeypatch, response=None, raises=None, counter=None):
    creds = tmp_path / "user-credentials.json"
    creds.write_text(json.dumps({
        "current": {"access_token": "ghu_old", "refresh_token": "ghr_old",
                    "generation": 3, "label": "G3"},
        "history": [{"access_token": "ghu_ancient",
                     "refresh_token": "ghr_ancient", "generation": 2}]}))
    monkeypatch.setattr(auth_producer, "CREDENTIALS", creds)
    monkeypatch.setattr(auth_producer, "CONFIG_DIR", tmp_path)
    (tmp_path / "app-credentials.json").write_text(json.dumps(
        {"client_id": "Iv1", "client_secret": "s"}))

    class Resp:
        status = 200

        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(response).encode()

    def fake_urlopen(req, timeout=None):
        if counter is not None:
            counter.append(1)
        if raises:
            raise raises
        return Resp()

    monkeypatch.setattr(auth_producer.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(auth_producer, "probe_access_token",
                        lambda t: (200, {"login": "PhysShell"}))
    return creds


def test_refresh_makes_exactly_one_request(store, tmp_path, monkeypatch):
    """If TCP dies at the worst millisecond, a second request either fails
    against a spent token or succeeds and discards a rotation nobody
    recorded. That uncertainty is not a thing to retry away."""
    calls = []
    _prep(tmp_path, monkeypatch, raises=TimeoutError("boom"), counter=calls)
    result = auth_producer.cmd_refresh(object(), store)
    assert len(calls) == 1
    assert result["state"] == auth_state.REFRESH_OUTCOME_UNKNOWN
    assert result["retry_performed"] is False
    assert result["recovery"] == "fresh Device Flow only"


def test_refresh_success_requires_readback_and_a_real_call(store, tmp_path,
                                                           monkeypatch):
    creds = _prep(tmp_path, monkeypatch, response=FULL)
    result = auth_producer.cmd_refresh(object(), store)
    assert result["state"] == auth_state.AUTHORIZED
    assert result["old_auth_generation"] == 3
    assert result["new_auth_generation"] == 4
    assert result["readback"]["generation_matches"] is True
    assert result["readback"]["access_token_matches"] is True
    assert result["authenticated_as"] == "PhysShell"
    assert json.loads(creds.read_text())["current"]["validated"] is True


def test_history_keeps_no_secrets_after_rotation(store, tmp_path, monkeypatch):
    """Filesystems support archaeology. A superseded refresh token is a
    candidate an attacker can try, and it buys an audit nothing."""
    creds = _prep(tmp_path, monkeypatch, response=FULL)
    auth_producer.cmd_refresh(object(), store)
    blob = json.loads(creds.read_text())
    flat = json.dumps(blob["history"])
    assert "ghr_old" not in flat and "ghu_old" not in flat
    assert "ghr_ancient" not in flat and "ghu_ancient" not in flat
    assert all(h["secrets_removed"] for h in blob["history"])
    assert all("refresh_token_fingerprint" in h for h in blob["history"])
    # exactly one live credential on disk
    assert blob["current"]["refresh_token"] == "ghr_new"
    assert list(blob) == ["current", "history"]


def test_incomplete_response_is_ambiguous_but_keeps_the_material(store, tmp_path,
                                                                 monkeypatch):
    """AUTHORIZED needs full structural validity; discarding a live refresh
    token because an expiry field is missing would guarantee a Device Flow
    for no safety gain."""
    partial = {"access_token": "ghu_new", "refresh_token": "ghr_new"}
    creds = _prep(tmp_path, monkeypatch, response=partial)
    result = auth_producer.cmd_refresh(object(), store)
    assert result["state"] == auth_state.REFRESH_OUTCOME_UNKNOWN
    assert result["credential_persisted"] is True
    stored = json.loads(creds.read_text())["current"]
    assert stored["refresh_token"] == "ghr_new"
    assert stored["validated"] is False
    assert store.permits_triggers() is False, "the gate closes via the store"


def test_definitive_error_is_auth_lost(store, tmp_path, monkeypatch):
    """A1c: this failure arrives as HTTP 200 with an error field."""
    _prep(tmp_path, monkeypatch, response={"error": "incorrect_client_credentials"})
    result = auth_producer.cmd_refresh(object(), store)
    assert result["state"] == auth_state.AUTH_LOST
    assert store.demands_invalidation() is True


def test_unrecognised_error_is_ambiguous_not_lost(store, tmp_path, monkeypatch):
    _prep(tmp_path, monkeypatch, response={"error": "something_new_in_2027"})
    result = auth_producer.cmd_refresh(object(), store)
    assert result["state"] == auth_state.REFRESH_OUTCOME_UNKNOWN


def test_new_token_that_fails_a_real_call_is_ambiguous(store, tmp_path,
                                                       monkeypatch):
    """A credential we cannot prove works is not an authorization."""
    _prep(tmp_path, monkeypatch, response=FULL)
    monkeypatch.setattr(auth_producer, "probe_access_token",
                        lambda t: (401, None))
    result = auth_producer.cmd_refresh(object(), store)
    assert result["state"] == auth_state.REFRESH_OUTCOME_UNKNOWN
    assert result["credential_persisted"] is True


def test_preflight_failure_does_not_spend_the_token(store, tmp_path, monkeypatch):
    calls = []
    _prep(tmp_path, monkeypatch, response=FULL, counter=calls)
    (tmp_path / "app-credentials.json").write_text(json.dumps({"client_id": ""}))
    result = auth_producer.cmd_refresh(object(), store)
    assert result["attempted"] is False
    assert calls == []
    assert store.history() == []


def test_refresh_is_reachable_only_by_explicit_command():
    """Spending a single-use token must be something an operator asked for,
    never something a poll loop decided."""
    source = (BASE / "harness" / "auth_producer.py").read_text()
    assert source.count("oauth/access_token") == 1
    callers = [line.strip() for line in source.splitlines()
               if "cmd_refresh" in line and "def cmd_refresh" not in line]
    assert len(callers) == 1, callers
    assert '"refresh": cmd_refresh' in callers[0]


def test_probe_never_escalates_to_refresh():
    source = (BASE / "harness" / "auth_producer.py").read_text()
    probe_body = source.split("def cmd_probe(")[1].split("\ndef ")[0]
    assert "cmd_refresh" not in probe_body
    assert "oauth/access_token" not in probe_body
