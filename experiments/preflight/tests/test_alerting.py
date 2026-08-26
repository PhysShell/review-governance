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
