"""Tests for the edge host: receiver, watchdog, heartbeat and cold start.

The load-bearing assertions are the ones about what the edge *cannot* do.
A watchdog that could publish a success, or a cold-started primary that
could adopt one from GitHub, would quietly undo the entire program.
"""
import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

import cold_start  # noqa: E402
import edge_service  # noqa: E402
import edge_store  # noqa: E402
import edge_watchdog  # noqa: E402
import heartbeat_client  # noqa: E402

WEBHOOK_SECRET = b"edge-webhook-secret-not-real"
HEARTBEAT_SECRET = b"edge-heartbeat-secret-not-real"
REPO = "PhysShell/evm-from-scratch"


@pytest.fixture()
def service(tmp_path):
    store = edge_store.EdgeStore(tmp_path / "edge.sqlite3")
    yield edge_service.EdgeService(store, WEBHOOK_SECRET, HEARTBEAT_SECRET)
    store.close()


def signed(secret, body: bytes, header="x-hub-signature-256"):
    return {header: "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()}


def webhook(payload, guid="d-1", event="pull_request", secret=WEBHOOK_SECRET):
    raw = json.dumps(payload).encode()
    headers = {"X-GitHub-Event": event, "X-GitHub-Delivery": guid,
               **signed(secret, raw)}
    return headers, raw


# --- webhook receiver -------------------------------------------------------

def test_delivery_is_durable_before_the_ack(service):
    headers, raw = webhook({"action": "synchronize",
                            "repository": {"full_name": REPO}})
    status, body = service.handle_webhook(headers, raw)
    assert status == 202 and body["accepted"] is True
    stored = service.store.delivery("d-1")
    assert stored is not None                      # written, then acknowledged
    assert stored["processing_state"] == edge_store.RECEIVED
    assert stored["event"] == "pull_request"
    assert stored["body_hash"] == hashlib.sha256(raw).hexdigest()


def test_forged_signature_is_rejected_and_consumes_no_guid(service):
    headers, raw = webhook({"action": "synchronize"}, guid="d-2",
                           secret=b"attacker")
    status, _ = service.handle_webhook(headers, raw)
    assert status == 401
    assert service.store.delivery("d-2") is None

    headers, raw = webhook({"action": "synchronize"}, guid="d-2")
    assert service.handle_webhook(headers, raw)[0] == 202
    assert service.store.delivery("d-2") is not None


def test_redelivery_with_the_same_guid_is_recorded_once(service):
    headers, raw = webhook({"action": "synchronize"}, guid="d-3")
    first = service.handle_webhook(headers, raw)[1]
    second = service.handle_webhook(headers, raw)[1]
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert len(service.store.deliveries()) == 1


def test_the_webhook_is_only_a_signal(service):
    headers, raw = webhook({"action": "synchronize"}, guid="d-4")
    body = service.handle_webhook(headers, raw)[1]
    assert "signal only" in body["note"]
    source = (BASE / "harness" / "edge_service.py").read_text()
    assert "check-runs" not in source          # the receiver publishes nothing


# --- heartbeat --------------------------------------------------------------

def test_signed_heartbeat_is_recorded(service):
    body = json.dumps({"primary_instance_id": "primary@host",
                       "at": "2026-08-22T10:00:00Z"}).encode()
    status, _ = service.handle_heartbeat(
        signed(HEARTBEAT_SECRET, body, "x-governor-signature"), body)
    assert status == 202
    beat = service.store.latest_heartbeat()
    assert beat["primary_instance_id"] == "primary@host"


def test_unsigned_or_forged_heartbeat_cannot_keep_the_watchdog_asleep(service):
    body = json.dumps({"primary_instance_id": "impostor"}).encode()
    assert service.handle_heartbeat({}, body)[0] == 401
    assert service.handle_heartbeat(
        signed(b"wrong", body, "x-governor-signature"), body)[0] == 401
    assert service.store.latest_heartbeat() is None


def test_heartbeat_payload_carries_no_policy_content(monkeypatch):
    """Assert what is actually sent, not what the file happens to say."""
    captured = {}

    class FakeResponse:
        status = 202

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        return FakeResponse()

    monkeypatch.setattr(heartbeat_client.urllib.request, "urlopen", fake_urlopen)
    heartbeat_client.beat_once("https://edge.invalid", HEARTBEAT_SECRET)

    assert set(captured["body"]) == {"primary_instance_id", "at", "kind"}
    assert captured["body"]["kind"] == "liveness"
    for policy_field in ("verdict", "success", "bundle", "conclusion",
                         "head_sha", "epoch_id"):
        assert policy_field not in captured["body"]
    assert captured["headers"]["x-governor-signature"].startswith("sha256=")


def test_a_failed_heartbeat_is_reported_not_swallowed(monkeypatch):
    monkeypatch.setattr(heartbeat_client.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    result = heartbeat_client.beat_once("https://edge.invalid", b"s")
    assert result["status"] is None
    assert "OSError" in result["error"]


# --- watchdog capability ----------------------------------------------------

def test_watchdog_cannot_publish_any_passing_conclusion():
    for passing in ("success", "neutral", "skipped"):
        with pytest.raises(edge_watchdog.WatchdogCapability,
                           match="can only revoke"):
            edge_watchdog.guarded("PATCH", "/repos/x/y/check-runs/1", "t",
                                  {"conclusion": passing})


def test_watchdog_cannot_create_runs_merge_or_touch_rulesets():
    for method, path in (("POST", "/repos/x/y/check-runs"),
                         ("PUT", "/repos/x/y/pulls/1/merge"),
                         ("POST", "/repos/x/y/statuses/abc"),
                         ("DELETE", "/repos/x/y/rulesets/1")):
        with pytest.raises(edge_watchdog.WatchdogCapability):
            edge_watchdog.guarded(method, path, "t", {"conclusion": "failure"})


def test_watchdog_holds_no_user_credentials():
    source = (BASE / "harness" / "edge_watchdog.py").read_text()
    for forbidden in ("user-credentials", "user_token", "refresh_token",
                      "device_flow"):
        assert forbidden not in source


def test_passing_set_includes_neutral_and_skipped():
    """GitHub counts them as passing for required checks, so the watchdog
    must be willing to extinguish them too."""
    assert edge_watchdog.PASSING == frozenset({"success", "neutral", "skipped"})
    assert not (edge_watchdog.PASSING & edge_watchdog.NON_PASSING)


def test_github_is_a_cleanup_surface_and_the_asymmetry_is_documented():
    source = (BASE / "harness" / "edge_watchdog.py").read_text()
    assert "FORBIDDEN" in source and "PERMITTED" in source
    assert "monotone in the safe direction" in source


# --- cold start -------------------------------------------------------------

def test_cold_start_never_reconstructs_success_from_github():
    observed = [{"pr_number": 8, "head_sha": "a" * 40, "conclusion": "success"},
                {"pr_number": 12, "head_sha": "b" * 40, "conclusion": "success"}]
    plan = cold_start.plan_cold_start(observed, durable_state_available=False)
    assert plan["cold_start"] is True
    assert plan["successes_reconstructed"] == 0
    assert cold_start.adopted_verdicts(plan) == {"NOT_ESTABLISHED"}
    for item in plan["plan"]:
        assert item["adopted_from_github"] is False
        assert item["requires_fresh_qualification"] is True


def test_cold_start_is_not_triggered_when_state_survives():
    plan = cold_start.plan_cold_start([], durable_state_available=True)
    assert plan["cold_start"] is False


# --- edge store cannot hold a verdict ---------------------------------------

def test_edge_schema_has_no_place_to_store_a_success(tmp_path):
    store = edge_store.EdgeStore(tmp_path / "e.sqlite3")
    tables = [row[0] for row in store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'").fetchall()]
    assert set(tables) == {"webhook_deliveries", "primary_heartbeat",
                          "watchdog_incidents"}
    schema = " ".join(row[0] or "" for row in store.conn.execute(
        "SELECT sql FROM sqlite_master").fetchall())
    assert "verdict" not in schema.lower()
    assert "bundle" not in schema.lower()
    store.close()


def test_dropped_delivery_is_visible_for_reconciliation(tmp_path):
    """A5a-c1 step 10 injects this state deliberately; it must be findable."""
    store = edge_store.EdgeStore(tmp_path / "e.sqlite3")
    store.record_delivery(guid="g1", event="pull_request", action="synchronize",
                          repository=REPO, received_at="t", body_hash="h")
    store.set_processing_state("g1", edge_store.DROPPED)
    dropped = store.deliveries(state=edge_store.DROPPED)
    assert [row["delivery_guid"] for row in dropped] == ["g1"]
    store.close()
