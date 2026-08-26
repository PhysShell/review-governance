"""Tests for A5b-preflight: alert payloads, transitions, and key proof.

The assertions that matter most are the negative ones. An alerting system
is judged by what it refuses to send and by what it refuses to stay quiet
about, not by whether the happy path formats nicely.
"""
import json
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent.parent / "operational-readiness"
sys.path.insert(0, str(BASE / "harness"))

import alerting  # noqa: E402
import key_verify  # noqa: E402

REPO = "PhysShell/evm-from-scratch"


@pytest.fixture()
def notifier(tmp_path):
    transport = alerting.NullTransport()
    n = alerting.Notifier(tmp_path / "alerts.sqlite3", transport,
                          origin="test", renotify=10_000)
    yield n
    n.close()


# --- payload is an allowlist, not a filter -----------------------------------

def test_payload_rejects_anything_not_on_the_allowlist():
    with pytest.raises(alerting.PayloadRefused):
        alerting.build_payload(alerting.CRITICAL, "watchdog_incident",
                               webhook_body='{"secret":"..."}')
    with pytest.raises(alerting.PayloadRefused):
        alerting.build_payload(alerting.CRITICAL, "auth_lost",
                               refresh_token="ghr_xxx")
    with pytest.raises(alerting.PayloadRefused):
        alerting.build_payload(alerting.CRITICAL, "watchdog_incident",
                               summary="the provider comment said ...")


def test_payload_rejects_an_unregistered_cause():
    """A typo must not silently become an alert class with no runbook."""
    with pytest.raises(alerting.PayloadRefused):
        alerting.build_payload(alerting.CRITICAL, "watchdog_incidnet")


def test_rendered_message_contains_only_allowlisted_values(notifier):
    notifier.raise_(alerting.CRITICAL, "watchdog_incident", repo=REPO,
                    pr_number=29, check_run_id=98034999395, incident_id=2,
                    detected_at="2026-08-26T02:04:16Z", state="1 revoked")
    text = notifier.transport.sent[0]
    for expected in ("CRITICAL", "watchdog_incident", REPO, "29",
                     "98034999395", "2026-08-26T02:04:16Z"):
        assert expected in text
    # nothing else got in
    payload = json.loads(notifier.log()[0]["payload"])
    assert set(payload) <= set(alerting.ALLOWED_FIELDS)


def test_every_registered_cause_can_build_a_payload():
    for cause in alerting.CAUSES:
        payload = alerting.build_payload(alerting.CRITICAL, cause, repo=REPO)
        assert payload["cause"] == cause


# --- transitions, not log lines ----------------------------------------------

def test_repeated_raise_does_not_repage(notifier):
    for _ in range(5):
        notifier.raise_(alerting.CRITICAL, "heartbeat_age_critical", repo=REPO)
    assert len(notifier.transport.sent) == 1
    assert notifier.open_causes() == ["heartbeat_age_critical"]


def test_undelivered_alert_is_retried_every_poll(tmp_path):
    """A channel outage must not become a swallowed incident."""
    class Failing(alerting.NullTransport):
        name = "failing"

        def send(self, text):
            self.sent.append(text)
            return False, "channel down"

    n = alerting.Notifier(tmp_path / "a.sqlite3", Failing(), origin="test")
    for _ in range(3):
        n.raise_(alerting.CRITICAL, "watchdog_incident", repo=REPO)
    assert len(n.transport.sent) == 3, "a failed send must be retried"
    assert n.open_causes() == ["watchdog_incident"]
    assert [row["delivered"] for row in n.log()] == [0, 0, 0]
    n.close()


def test_clear_sends_a_recovery_and_closes_the_condition(notifier):
    notifier.raise_(alerting.CRITICAL, "heartbeat_age_critical", repo=REPO)
    result = notifier.clear("heartbeat_age_critical", repo=REPO)
    assert result["sent"] is True
    assert notifier.open_causes() == []
    assert alerting.RECOVERY in notifier.transport.sent[-1]


def test_clear_without_an_open_condition_is_silent(notifier):
    """Recovery for an incident nobody was told about is noise, and noise is
    how alerting dies."""
    result = notifier.clear("heartbeat_age_critical", repo=REPO)
    assert result["sent"] is False
    assert notifier.transport.sent == []


def test_failed_recovery_keeps_the_condition_open(tmp_path):
    class Flaky(alerting.NullTransport):
        name = "flaky"

        def __init__(self):
            super().__init__()
            self.fail_next = False

        def send(self, text):
            self.sent.append(text)
            return (not self.fail_next), ("down" if self.fail_next else None)

    transport = Flaky()
    n = alerting.Notifier(tmp_path / "a.sqlite3", transport, origin="test")
    n.raise_(alerting.CRITICAL, "watchdog_incident", repo=REPO)
    transport.fail_next = True
    n.clear("watchdog_incident", repo=REPO)
    assert n.open_causes() == ["watchdog_incident"], \
        "an undelivered recovery must not close the incident"
    n.close()


def test_renotify_only_after_the_interval(tmp_path):
    n = alerting.Notifier(tmp_path / "a.sqlite3", alerting.NullTransport(),
                          origin="test", renotify=0)
    n.raise_(alerting.CRITICAL, "reconciliation_stale", repo=REPO)
    n.raise_(alerting.CRITICAL, "reconciliation_stale", repo=REPO)
    assert len(n.transport.sent) == 2, "renotify=0 means every poll re-sends"
    n.close()


# --- heartbeat severity is mutually exclusive --------------------------------

def test_heartbeat_critical_clears_the_warning(notifier):
    """Both open at once means one of the recoveries never arrives and the
    operator is left holding a stale red."""
    import edge_watchdog

    class Args:
        repo = REPO
        stale_after = 45
        warn_after = 30

    edge_watchdog.alert_on_heartbeat(
        notifier, Args(), {"checked_at": "t", "heartbeat_age_seconds": 35}, 35)
    assert notifier.open_causes() == ["heartbeat_age_warning"]

    edge_watchdog.alert_on_heartbeat(
        notifier, Args(), {"checked_at": "t", "heartbeat_age_seconds": 50}, 50)
    assert notifier.open_causes() == ["heartbeat_age_critical"]

    edge_watchdog.alert_on_heartbeat(
        notifier, Args(), {"checked_at": "t", "heartbeat_age_seconds": 5}, 5)
    assert notifier.open_causes() == []


def test_missing_heartbeat_is_critical_not_silent(notifier):
    import edge_watchdog

    class Args:
        repo = REPO
        stale_after = 45
        warn_after = 30

    edge_watchdog.alert_on_heartbeat(
        notifier, Args(), {"checked_at": "t", "heartbeat_age_seconds": None},
        None)
    assert notifier.open_causes() == ["heartbeat_age_critical"]


# --- sentinel refuses to invent health ---------------------------------------

def test_sentinel_reports_not_reported_rather_than_healthy(tmp_path):
    import sentinel

    class Args:
        repo = REPO
        health_file = str(tmp_path / "absent.json")
        auth_state_file = str(tmp_path / "absent-auth.json")
        reconciliation_max_age = 60

    assert sentinel.check_reconciliation(Args(), None)["state"] == "NOT_REPORTED"
    auth = sentinel.check_auth_state(Args(), None)
    assert auth["state"] == "NOT_REPORTED"
    assert "not evidence" in auth["note"]


def test_sentinel_never_touches_the_refresh_token():
    """Refresh tokens are single-use with rotation, so probing one is a
    write that can strand the credential."""
    source = (BASE / "harness" / "sentinel.py").read_text()
    assert "refresh_token" not in source.replace(
        "refresh_outcome_unknown", "").replace("REFRESH_OUTCOME_UNKNOWN", "")
    assert "oauth/access_token" not in source


def test_stale_reconciliation_pages(tmp_path, notifier):
    import sentinel

    health = tmp_path / "health.json"
    health.write_text(json.dumps(
        {"last_complete_sweep_at": "2020-01-01T00:00:00Z", "pr_count": 2}))

    class Args:
        repo = REPO
        health_file = str(health)
        auth_state_file = str(tmp_path / "none.json")
        reconciliation_max_age = 60

    state = sentinel.check_reconciliation(Args(), notifier)
    assert state["state"] == "STALE"
    assert notifier.open_causes() == ["reconciliation_stale"]


# --- key verification --------------------------------------------------------

def test_key_verify_never_emits_pem_material(tmp_path, monkeypatch):
    monkeypatch.setattr(key_verify, "fingerprint", lambda p: "abcd1234abcd1234")
    monkeypatch.setattr(key_verify, "app_jwt", lambda p, a=0: "jwt")
    monkeypatch.setattr(key_verify, "request",
                        lambda m, p, t, bearer=True: (401, {"message": "bad"}))
    result = key_verify.verify(tmp_path / "k.pem", expect_success=False)
    flat = json.dumps(result)
    assert "BEGIN" not in flat and "PRIVATE KEY" not in flat
    assert result["fingerprint"] == "abcd1234abcd1234"
    assert result["verdict"] == "REJECTED"
    assert result["matches_expectation"] is True


def test_key_verify_requires_every_step_not_just_the_first(tmp_path, monkeypatch):
    """A minted token does not prove the repository is reachable."""
    monkeypatch.setattr(key_verify, "fingerprint", lambda p: "ffff0000ffff0000")
    monkeypatch.setattr(key_verify, "app_jwt", lambda p, a=0: "jwt")

    def fake_request(method, path, token, bearer=True):
        if path == "/app":
            return 200, {"id": key_verify.EXPECTED_APP_ID, "slug": "s"}
        if path == "/app/installations":
            return 200, [{"id": key_verify.EXPECTED_INSTALLATION_ID}]
        if path.endswith("/access_tokens"):
            return 201, {"token": "t"}
        return 404, {"message": "Not Found"}

    monkeypatch.setattr(key_verify, "request", fake_request)
    result = key_verify.verify(tmp_path / "k.pem")
    assert result["steps"]["installation_token_minted"] == "PASS"
    assert result["steps"]["expected_repository_accessible"] == "FAIL"
    assert result["verdict"] == "FAIL"


def test_key_verify_rejects_an_unexpected_installation(tmp_path, monkeypatch):
    monkeypatch.setattr(key_verify, "fingerprint", lambda p: "1111222233334444")
    monkeypatch.setattr(key_verify, "app_jwt", lambda p, a=0: "jwt")

    def fake_request(method, path, token, bearer=True):
        if path == "/app":
            return 200, {"id": key_verify.EXPECTED_APP_ID, "slug": "s"}
        if path == "/app/installations":
            return 200, [{"id": 999}]
        return 404, {"message": "Not Found"}

    monkeypatch.setattr(key_verify, "request", fake_request)
    result = key_verify.verify(tmp_path / "k.pem")
    assert result["steps"]["expected_installation_present"] == "FAIL"
    assert result["verdict"] == "FAIL"


# --- installation pinning ----------------------------------------------------

def test_runtime_pins_the_installation_rather_than_taking_the_first():
    """`installs[0]` is the same shape of guess as the `pulls[0]` defect."""
    for module in ("governor.py", "edge_watchdog.py"):
        source = (BASE / "harness" / module).read_text()
        # the call form, not the prose: both modules explain the defect in
        # a docstring that necessarily names it
        assert "installs[0]['id']" not in source, f"{module} still guesses"
        assert "GOVERNOR_INSTALLATION_ID = 155393018" in source
        assert "InstallationMismatch" in source


def test_fingerprint_refuses_to_hash_nothing(tmp_path):
    """Observed live: `openssl` absent from PATH produced zero bytes, and
    sha256("") is stable enough that three distinct keys reported the same
    fingerprint. A rotation cannot be verified by a constant."""
    empty = tmp_path / "not-a-key.pem"
    empty.write_text("this is not a PEM\n")
    with pytest.raises(key_verify.FingerprintUnavailable):
        key_verify.fingerprint(empty)


def test_unfingerprintable_key_fails_the_whole_verdict(tmp_path):
    result = key_verify.verify(tmp_path / "missing.pem")
    assert result["steps"]["readable_private_key"] == "FAIL"
    assert result["verdict"] == "FAIL"
    assert result["fingerprint"] is None


# --- the delivery ack: an alert that can actually clear ----------------------

def test_ack_marks_received_deliveries_processed(tmp_path):
    """`delivery_stuck` was unclearable: nothing ever advanced
    processing_state, so the warning would light once and stay lit."""
    import edge_service
    import edge_store
    import hashlib as _h
    import hmac as _hm

    store = edge_store.EdgeStore(tmp_path / "e.sqlite3")
    for i in range(1, 4):
        store.record_delivery(guid=f"g{i}", event="pull_request",
                              action="synchronize", repository=REPO,
                              received_at=f"2026-08-26T00:00:0{i}Z",
                              body_hash=f"h{i}")
    svc = edge_service.EdgeService(store, b"w", b"hb")
    raw = json.dumps({"through": 2}).encode()
    sig = {"X-Governor-Signature": "sha256=" + _hm.new(
        b"hb", raw, _h.sha256).hexdigest()}

    status, body = svc.handle_ack(sig, raw)
    assert status == 202 and body["marked"] == 2
    states = {r["delivery_guid"]: r["processing_state"]
              for r in store.deliveries()}
    assert states == {"g1": "PROCESSED", "g2": "PROCESSED", "g3": "RECEIVED"}
    store.close()


def test_ack_requires_a_signature(tmp_path):
    import edge_service
    import edge_store
    store = edge_store.EdgeStore(tmp_path / "e.sqlite3")
    svc = edge_service.EdgeService(store, b"w", b"hb")
    status, _ = svc.handle_ack({}, json.dumps({"through": 5}).encode())
    assert status == 401
    store.close()


def test_ack_never_touches_a_dropped_delivery(tmp_path):
    """DROPPED is a deliberate injection for the reconciliation fixture and
    must stay visible."""
    import edge_service
    import edge_store
    import hashlib as _h
    import hmac as _hm

    store = edge_store.EdgeStore(tmp_path / "e.sqlite3")
    store.record_delivery(guid="g1", event="pull_request", action="a",
                          repository=REPO, received_at="t", body_hash="h")
    store.set_processing_state("g1", edge_store.DROPPED)
    svc = edge_service.EdgeService(store, b"w", b"hb")
    raw = json.dumps({"through": 99}).encode()
    sig = {"X-Governor-Signature": "sha256=" + _hm.new(
        b"hb", raw, _h.sha256).hexdigest()}
    svc.handle_ack(sig, raw)
    assert store.deliveries()[0]["processing_state"] == "DROPPED"
    store.close()


def test_ack_failure_does_not_disturb_the_primary_cursor(tmp_path, monkeypatch):
    """Best effort by design: the cursor is the only record that matters."""
    import observations
    import signal_client

    store = observations.ObservationStore(tmp_path / "obs.sqlite3")
    store.advance(7, "2026-08-26T00:00:00Z")
    monkeypatch.setattr(signal_client, "fetch_signals",
                        lambda ep, sec, after: (200, {"signals": []}))
    monkeypatch.setattr(signal_client, "ack",
                        lambda ep, sec, through: None)   # ack fails
    result = signal_client.drain("http://edge", b"hb", store, REPO)
    assert result["cursor"] == 7
    assert store.cursor() == 7
    store.close()


# --- watchdog liveness: turning, not merely running --------------------------

def test_watchdog_poll_is_recorded_and_counted(tmp_path):
    import edge_store
    store = edge_store.EdgeStore(tmp_path / "e.sqlite3")
    assert store.watchdog_liveness() is None
    store.record_watchdog_poll("2026-08-26T10:00:00Z")
    store.record_watchdog_poll("2026-08-26T10:00:10Z")
    live = store.watchdog_liveness()
    assert live == {"last_poll_at": "2026-08-26T10:00:10Z", "polls": 2}
    store.close()


def test_sentinel_pages_when_the_watchdog_stops_turning(notifier):
    """`Restart=always` covers a crash but not a hang, and is-active is true
    for a loop that stopped looping. Nothing else covers this."""
    import sentinel

    class Args:
        repo = REPO
        watchdog_max_age = 60

    stale = {"last_watchdog_poll": "2020-01-01T00:00:00Z", "watchdog_polls": 7}
    state = sentinel.check_watchdog(Args(), notifier, stale)
    assert state["state"] == "NOT_POLLING"
    assert notifier.open_causes() == ["watchdog_not_polling"]

    fresh = {"last_watchdog_poll": alerting.utcnow(), "watchdog_polls": 8}
    state = sentinel.check_watchdog(Args(), notifier, fresh)
    assert state["state"] == "POLLING"
    assert notifier.open_causes() == []


def test_unreachable_edge_does_not_silently_pass_the_watchdog_check(notifier):
    """An unreachable receiver must not read as 'watchdog fine'."""
    import sentinel

    class Args:
        repo = REPO
        watchdog_max_age = 60
        startup_grace = 0          # steady state, not the startup window

    state = sentinel.check_watchdog(Args(), notifier, None)
    assert state["state"] == "NOT_REPORTED"
    assert notifier.open_causes() == ["watchdog_not_polling"]


# --- startup grace: only for absence of data ---------------------------------

def test_startup_grace_holds_absence_of_data(notifier, tmp_path):
    """Observed live: the sentinel paged reconciliation_stale simply because
    it started before the first sweep landed. Every stack restart would wake
    the operator for nothing."""
    import sentinel

    class Args:
        repo = REPO
        health_file = str(tmp_path / "absent.json")
        auth_state_file = str(tmp_path / "absent-auth.json")
        reconciliation_max_age = 60
        startup_grace = 90

    state = sentinel.check_reconciliation(Args(), notifier)
    assert state["state"] == "NOT_REPORTED"
    assert state["alert"] == "HELD_STARTUP_GRACE"
    assert notifier.open_causes() == []


def test_startup_grace_expires(notifier, tmp_path, monkeypatch):
    """Absence of data is still an incident once there has been time for it
    to appear."""
    import sentinel
    monkeypatch.setattr(sentinel, "STARTED_AT",
                        sentinel.time.monotonic() - 1000)

    class Args:
        repo = REPO
        health_file = str(tmp_path / "absent.json")
        auth_state_file = str(tmp_path / "absent-auth.json")
        reconciliation_max_age = 60
        startup_grace = 90

    state = sentinel.check_reconciliation(Args(), notifier)
    assert state["alert"] == "RAISED"
    assert notifier.open_causes() == ["reconciliation_stale"]


def test_startup_grace_never_suppresses_real_staleness(notifier, tmp_path):
    """A timestamp that exists and is old is a real condition and pages
    immediately, at startup like any other time."""
    import sentinel

    health = tmp_path / "health.json"
    health.write_text(json.dumps(
        {"last_complete_sweep_at": "2020-01-01T00:00:00Z", "pr_count": 2}))

    class Args:
        repo = REPO
        health_file = str(health)
        auth_state_file = str(tmp_path / "none.json")
        reconciliation_max_age = 60
        startup_grace = 90

    state = sentinel.check_reconciliation(Args(), notifier)
    assert state["state"] == "STALE"
    assert state["alert"] == "RAISED"
    assert notifier.open_causes() == ["reconciliation_stale"]
