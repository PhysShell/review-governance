"""Replay of the live A2a delivery log through the same reducer that ran.

The receiver's in-process state was the authority during the experiment;
replaying its verified deliveries through `ControlPlane` reproduces that
state deterministically, so the assertions below are about what actually
happened on GitHub, not about a model of it.
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

import control_plane  # noqa: E402

FIXTURES = BASE / "fixtures"
LOG = FIXTURES / "delivery_log.json"
PROBE_REPO = "PhysShell/evm-from-scratch"
PROBE_PR = 17
EPOCH_1 = "e4e07459ba9a796f640a45c46f48ba829e6241de"
EPOCH_2 = "0f8223447cdc8f849047eebb526dac641fe50bd5"
REDELIVERED_GUID = "944ad5c0-9de5-11f1-91e9-2ef4aa12fd77"


def log():
    return json.loads(LOG.read_text())


def test_every_recorded_delivery_passed_signature_verification():
    for entry in log()["deliveries"]:
        assert entry["signature_verified"] is True, entry["delivery_id"]
        assert entry["signature_present"] is True, entry["delivery_id"]


def test_github_redelivery_reuses_the_guid_and_is_ignored_once_seen():
    entries = [e for e in log()["deliveries"]
               if e["delivery_id"] == REDELIVERED_GUID]
    assert len(entries) == 2, "expected an original and a redelivery"
    first, second = entries
    assert first["duplicate"] is False
    assert first["effect"].startswith("EPOCH_OPENED")
    assert second["duplicate"] is True
    assert second["effect"] == "DUPLICATE_IGNORED"
    assert second["received_at"] > first["received_at"]


def test_synchronize_marked_the_previous_epoch_stale_live():
    effects = [e["effect"] for e in log()["deliveries"]
               if e.get("pr") == PROBE_PR and e["action"] == "synchronize"]
    assert any(ef.startswith("EPOCH_OPENED head=e4e07459ba") for ef in effects)
    assert any("head=0f8223447c stale_marked=1" in ef for ef in effects)


def test_revocation_event_produced_auth_lost_live():
    revocations = [e for e in log()["deliveries"]
                   if e["event"] == "github_app_authorization"]
    assert len(revocations) == 1
    event = revocations[0]
    assert event["action"] == "revoked"
    assert event["sender"] == {"login": "PhysShell", "id": 45852143,
                               "type": "User"}
    assert event["effect"] == "AUTH_LOST"
    assert event["signature_verified"] is True


def test_unrelated_traffic_changed_no_gate_state():
    for entry in log()["deliveries"]:
        if entry["event"] in ("check_suite", "installation"):
            assert entry["effect"] == "EVENT_IGNORED"
        if entry["event"] == "pull_request" and entry["action"] == "edited":
            assert entry["effect"].startswith("PR_ACTION_IGNORED")


def replay():
    """Rebuild the receiver's end state from the verified deliveries."""
    state = control_plane.ControlPlane()
    for entry in log()["deliveries"]:
        payload = {"action": entry["action"], "sender": entry["sender"]}
        if entry.get("repo"):
            payload["repository"] = {"full_name": entry["repo"]}
        if entry.get("pr"):
            payload["pull_request"] = {"number": entry["pr"],
                                       "head": {"sha": entry["head_sha"]}}
        state.apply(entry["delivery_id"], entry["event"], payload)
    return state


def test_replayed_state_matches_the_observed_effects():
    state = replay()
    epochs = {e.head_sha: e.state for e in state.epochs_for(PROBE_REPO, PROBE_PR)}
    assert epochs[EPOCH_1] == control_plane.STALE
    assert epochs[EPOCH_2] == control_plane.CURRENT
    assert state.auth_state == control_plane.AUTH_LOST


def test_triggers_are_forbidden_in_the_end_state():
    state = replay()
    assert state.may_trigger_providers(PROBE_REPO, PROBE_PR, EPOCH_2) is False
    assert state.may_trigger_providers(PROBE_REPO, PROBE_PR, EPOCH_1) is False


def test_no_gate_state_was_ever_established_let_alone_clean():
    state = replay()
    for sha in (EPOCH_1, EPOCH_2):
        assert state.gate_state(PROBE_REPO, PROBE_PR, sha) == \
            control_plane.NOT_ESTABLISHED
    assert state.gate_states == {}


def test_no_signature_or_secret_material_in_the_fixture():
    text = LOG.read_text()
    assert "sha256=" not in text
    assert "x-hub-signature" not in text.lower()
    secret_path = Path.home() / ".config" / "review-governor" / "webhook-secret"
    if secret_path.exists():
        assert secret_path.read_text().strip() not in text
