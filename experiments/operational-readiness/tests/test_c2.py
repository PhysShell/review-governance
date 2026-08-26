"""Tests for the three A5a-c2 corrections.

Each one exists because the review found a defect, so each test is written
against the defect rather than against the happy path:

  c2-1  a watchdog that stops watching after one incident
  c2-2  a signal feed nobody consumed, and a guess dressed as an observation
  c2-3  a hash that cannot match across the enable flip by construction
"""
import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

import cutover  # noqa: E402
import edge_service  # noqa: E402
import edge_store  # noqa: E402
import observations  # noqa: E402
import signal_client  # noqa: E402

HEARTBEAT_SECRET = b"edge-heartbeat-secret-not-real"
WEBHOOK_SECRET = b"edge-webhook-secret-not-real"
REPO = "PhysShell/evm-from-scratch"


@pytest.fixture()
def service(tmp_path):
    store = edge_store.EdgeStore(tmp_path / "edge.sqlite3")
    yield edge_service.EdgeService(store, WEBHOOK_SECRET, HEARTBEAT_SECRET)
    store.close()


def signals_sig(after, secret=HEARTBEAT_SECRET):
    return {"X-Governor-Signature": "sha256=" + hmac.new(
        secret, f"signals:{after}".encode(), hashlib.sha256).hexdigest()}


def seed(store, n=3):
    for i in range(1, n + 1):
        store.record_delivery(guid=f"g{i}", event="pull_request",
                              action="synchronize", repository=REPO,
                              received_at=f"2026-08-26T00:00:0{i}Z",
                              body_hash=f"h{i}")


# --- c2-2: the signal feed ---------------------------------------------------

def test_signals_requires_a_signature(service):
    seed(service.store)
    status, body = service.handle_signals({}, 0)
    assert status == 401
    assert "signals" not in body


def test_signals_signature_is_bound_to_the_cursor(service):
    """A signature for one position must not authorise another, or a replayed
    request could silently re-read from the beginning."""
    seed(service.store)
    status, _ = service.handle_signals(signals_sig(0), 2)
    assert status == 401
    status, body = service.handle_signals(signals_sig(2), 2)
    assert status == 200
    assert [s["seq"] for s in body["signals"]] == [3]


def test_signals_carry_metadata_only(service):
    """The edge must not be able to tell the primary what happened — only
    that something did. A payload here would make the edge a second source
    of truth, which is the thing the whole topology refuses."""
    service.handle_webhook(
        {"x-hub-signature-256": "sha256=" + hmac.new(
            WEBHOOK_SECRET, b'{"action":"opened","repository":'
            b'{"full_name":"' + REPO.encode() + b'"},"pull_request":'
            b'{"number":7,"head":{"sha":"deadbeef"}}}', hashlib.sha256).hexdigest(),
         "x-github-delivery": "gx", "x-github-event": "pull_request"},
        b'{"action":"opened","repository":{"full_name":"' + REPO.encode() +
        b'"},"pull_request":{"number":7,"head":{"sha":"deadbeef"}}}')
    status, body = service.handle_signals(signals_sig(0), 0)
    assert status == 200
    signal = body["signals"][0]
    assert set(signal) == {"seq", "delivery_guid", "event", "action",
                           "repository", "received_at", "body_hash"}
    flat = json.dumps(body)
    assert "deadbeef" not in flat        # no head sha
    assert "pull_request\":{" not in flat  # no payload object
    assert "\"number\": 7" not in flat   # no PR number


def test_cursor_advances_only_after_the_observation_is_durable(tmp_path):
    store = observations.ObservationStore(tmp_path / "obs.sqlite3")
    assert store.cursor() == 0
    store.record(seq=5, delivery_guid="g5", event="pull_request",
                 action="synchronize", repository=REPO,
                 received_at="2026-08-26T00:00:00Z",
                 observed_at="2026-08-26T00:00:02Z", latency_seconds=2.0,
                 pr_snapshot=[{"number": 1, "head": "aaa"}])
    assert store.cursor() == 0, "recording must not move the cursor by itself"
    store.advance(5, "2026-08-26T00:00:02Z")
    assert store.cursor() == 5
    store.close()


def test_observation_stores_the_whole_snapshot_not_a_guess(tmp_path, monkeypatch):
    """The first draft recorded `pulls[0]` as *the* head for a signal that
    names no PR. That is a guess with the shape of evidence."""
    store = observations.ObservationStore(tmp_path / "obs.sqlite3")
    edge = edge_store.EdgeStore(tmp_path / "edge.sqlite3")
    seed(edge, 1)
    svc = edge_service.EdgeService(edge, WEBHOOK_SECRET, HEARTBEAT_SECRET)

    monkeypatch.setattr(signal_client, "fetch_signals",
                        lambda ep, sec, after: svc.handle_signals(
                            signals_sig(after, sec), after))
    monkeypatch.setattr(signal_client, "open_pr_snapshot",
                        lambda repo, token=None: [{"number": 1, "head": "aaa"},
                                                  {"number": 2, "head": "bbb"}])
    result = signal_client.drain("http://edge", HEARTBEAT_SECRET, store, REPO)
    assert result["processed"][0]["pr_count"] == 2
    stored = store.observations()[0]
    assert stored["pr_snapshot"] == [{"head": "aaa", "number": 1},
                                     {"head": "bbb", "number": 2}]
    store.close(); edge.close()


def test_failed_github_read_does_not_advance_the_cursor(tmp_path, monkeypatch):
    """A signal the primary could not derive anything from must be retried,
    not skipped. Advancing here would turn a transient outage into a
    permanently unobserved change."""
    store = observations.ObservationStore(tmp_path / "obs.sqlite3")
    edge = edge_store.EdgeStore(tmp_path / "edge.sqlite3")
    seed(edge, 1)
    svc = edge_service.EdgeService(edge, WEBHOOK_SECRET, HEARTBEAT_SECRET)
    monkeypatch.setattr(signal_client, "fetch_signals",
                        lambda ep, sec, after: svc.handle_signals(
                            signals_sig(after, sec), after))
    monkeypatch.setattr(signal_client, "open_pr_snapshot",
                        lambda repo, token=None: None)
    result = signal_client.drain("http://edge", HEARTBEAT_SECRET, store, REPO)
    assert result["stopped_at_seq"] == 1
    assert store.cursor() == 0
    assert store.observations() == []
    store.close(); edge.close()


def test_signal_client_supports_running_until_stopped():
    """The runbook deploys it with `--window 0`; a detector that expires is
    the same defect as a watchdog that expires."""
    source = (BASE / "harness" / "signal_client.py").read_text()
    assert "args.window <= 0" in source
    assert "deadline is None or time.time() < deadline" in source


def test_reconciliation_does_not_import_the_cursor():
    """Structural, not behavioural: if reconciliation ever learns about the
    cursor, a missed delivery becomes invisible to the mechanism meant to
    catch it."""
    source = (BASE / "harness" / "reconcile.py").read_text()
    assert "observations" not in source
    assert "signal_client" not in source
    assert "cursor" not in source.lower().split("independent of the edge spool")[1]


# --- c2-1: the watchdog must keep watching -----------------------------------

def test_watch_loop_defaults_to_running_until_stopped():
    """`--window 0` is what the deployed unit uses; stopping after an
    incident must be something a fixture asks for explicitly."""
    import edge_watchdog
    ap_source = (BASE / "harness" / "edge_watchdog.py").read_text()
    assert "--stop-after-incident" in ap_source
    assert "args.window <= 0" in ap_source
    fn = edge_watchdog.cmd_watch.__doc__
    assert "until stopped" in fn


class LoopEscape(Exception):
    """Only way out of the unbounded loop the deployed unit actually runs."""


def test_watch_keeps_going_after_an_incident(monkeypatch):
    """`--window 0` is the deployed configuration, so the incident path has
    to be exercised inside the loop that never ends on its own."""
    import edge_watchdog
    calls = {"n": 0}

    def fake_check(args, notifier=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"revocations": [{"check_run_id": 1, "state": "CONFIRMED"}],
                    "incident_id": 7, "checked_at": "t1"}
        if calls["n"] >= 4:
            raise LoopEscape
        return {"revocations": [], "checked_at": f"t{calls['n']}",
                "primary_stale": True}

    monkeypatch.setattr(edge_watchdog, "cmd_check", fake_check)
    monkeypatch.setattr(edge_watchdog.time, "sleep", lambda s: None)

    class Args:
        window = 0                 # what the unit runs with
        interval = 0
        stop_after_incident = False
        no_alerts = True           # a fixture must never page a human

    with pytest.raises(LoopEscape):
        edge_watchdog.cmd_watch(Args())
    assert calls["n"] == 4, "the loop stopped after the first incident"


def test_window_elapsed_reports_the_last_observed_state(monkeypatch):
    import edge_watchdog
    ticks = iter([0, 1, 2, 3, 4, 5, 6, 7, 8])
    monkeypatch.setattr(edge_watchdog.time, "time", lambda: next(ticks))
    monkeypatch.setattr(edge_watchdog.time, "sleep", lambda s: None)
    monkeypatch.setattr(edge_watchdog, "cmd_check",
                        lambda args, notifier=None: {
        "revocations": [], "primary_stale": True, "checked_at": "t"})

    class Args:
        window = 3
        interval = 0
        stop_after_incident = False
        no_alerts = True

    result = edge_watchdog.cmd_watch(Args())
    assert result["primary_stale"] is True, "last observed state must survive"
    assert result["revocations"] == []
    assert result["incidents_this_run"] == []


def test_stop_after_incident_is_still_available_for_fixtures(monkeypatch):
    import edge_watchdog
    monkeypatch.setattr(edge_watchdog, "cmd_check",
                        lambda args, notifier=None: {
        "revocations": [{"check_run_id": 1}], "incident_id": 9})
    monkeypatch.setattr(edge_watchdog.time, "sleep", lambda s: None)

    class Args:
        window = 600
        interval = 0
        stop_after_incident = True
        no_alerts = True

    result = edge_watchdog.cmd_watch(Args())
    assert result["polls"] == 1


# --- c2-3: the three hashes --------------------------------------------------

def test_policy_hash_survives_the_enforcement_flip():
    """The defect: hashing the whole object means the disabled readback and
    the active readback can never match, so 'the hash changed' stops
    distinguishing a state transition from a policy edit."""
    digests = cutover.hashes()
    disabled = cutover.ruleset_with("disabled")
    active = cutover.ruleset_with("active")
    assert cutover.policy_hash(disabled) == cutover.policy_hash(active)
    assert digests["POLICY_HASH"] == cutover.policy_hash(disabled)
    assert digests["DISABLED_RULESET_HASH"] != digests["ACTIVE_RULESET_HASH"]


def test_policy_hash_still_detects_a_real_policy_change():
    """It must ignore `enforcement` and nothing else."""
    tampered = cutover.ruleset_with("active")
    tampered["rules"][0]["parameters"]["required_status_checks"][0]["context"] = \
        "ai/something-else"
    assert cutover.policy_hash(tampered) != cutover.hashes()["POLICY_HASH"]

    bypassed = cutover.ruleset_with("active")
    bypassed["bypass_actors"] = [{"actor_id": 1, "actor_type": "OrganizationAdmin"}]
    assert cutover.policy_hash(bypassed) != cutover.hashes()["POLICY_HASH"]


def test_active_ruleset_hash_is_unchanged_by_the_split():
    """A5b verifies the active readback against a value reviewed earlier; the
    split must not silently move it."""
    assert cutover.hashes()["ACTIVE_RULESET_HASH"] == \
        cutover.canonical_hash(cutover.canonical_ruleset())
