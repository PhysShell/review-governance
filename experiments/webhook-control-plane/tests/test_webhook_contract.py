"""Adversarial tests for the A2a webhook control-plane contract.

Every invariant from the protocol is asserted here against the same
`Receiver` object that runs in production of this experiment — the HTTP
layer is a thin shell around `Receiver.handle`, so these tests exercise the
real path, not a parallel implementation.
"""
import json
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

import control_plane  # noqa: E402
import verify  # noqa: E402
from receiver import Receiver  # noqa: E402

SECRET = b"a1b2c3-test-secret-not-a-real-one"
REPO = "PhysShell/evm-from-scratch"
SHA_A = "a" * 40
SHA_B = "b" * 40


@pytest.fixture()
def receiver(tmp_path):
    return Receiver(SECRET, tmp_path / "deliveries.jsonl")


def pr_payload(action, sha, number=99, before=None):
    return {
        "action": action,
        "repository": {"full_name": REPO},
        "pull_request": {"number": number, "draft": True, "state": "open",
                         "head": {"sha": sha, "ref": "probe"},
                         "base": {"sha": "0" * 40}},
        "before": before, "after": sha if action == "synchronize" else None,
        "sender": {"login": "PhysShell", "id": 45852143, "type": "User"},
        "installation": {"id": 155393018},
    }


def revoked_payload():
    return {"action": "revoked",
            "sender": {"login": "PhysShell", "id": 45852143, "type": "User"}}


def deliver(receiver, event, payload, delivery_id, *, secret=SECRET,
            signature=None, raw=None):
    body = raw if raw is not None else json.dumps(payload).encode()
    sig = signature if signature is not None else \
        verify.compute_signature(secret, body)
    headers = {"X-GitHub-Event": event, "X-GitHub-Delivery": delivery_id,
               "X-Hub-Signature-256": sig,
               "Content-Type": "application/json"}
    return receiver.handle(headers, body)


# --- signature verification -------------------------------------------------

def test_valid_signature_is_accepted(receiver):
    status, body = deliver(receiver, "pull_request",
                           pr_payload("synchronize", SHA_A), "d-1")
    assert status == 202 and body["accepted"] is True


def test_wrong_secret_is_rejected(receiver):
    status, _ = deliver(receiver, "pull_request",
                        pr_payload("synchronize", SHA_A), "d-2",
                        secret=b"attacker-secret")
    assert status == 401


def test_missing_signature_header_is_rejected(receiver):
    status, _ = deliver(receiver, "pull_request",
                        pr_payload("synchronize", SHA_A), "d-3", signature="")
    assert status == 401


def test_signature_covers_the_body_not_just_its_shape(receiver):
    payload = pr_payload("synchronize", SHA_A)
    body = json.dumps(payload).encode()
    good = verify.compute_signature(SECRET, body)
    tampered = json.dumps(pr_payload("synchronize", SHA_B)).encode()
    status, _ = deliver(receiver, "pull_request", None, "d-4",
                        signature=good, raw=tampered)
    assert status == 401


def test_invalid_signature_does_not_consume_the_delivery_id(receiver):
    """A forged request must not burn a delivery id: the genuine
    redelivery with the same id must still take effect."""
    forged = pr_payload("synchronize", SHA_B)
    status, _ = deliver(receiver, "pull_request", forged, "d-5",
                        secret=b"attacker-secret")
    assert status == 401
    assert "d-5" not in receiver.state.seen_deliveries
    assert receiver.state.epochs == []

    status, body = deliver(receiver, "pull_request",
                           pr_payload("synchronize", SHA_A), "d-5")
    assert status == 202
    assert body["effect"].startswith("EPOCH_OPENED")
    assert receiver.state.current_epoch(REPO, 99).head_sha == SHA_A


# --- idempotency ------------------------------------------------------------

def test_duplicate_delivery_has_exactly_once_effect(receiver):
    deliver(receiver, "pull_request", pr_payload("synchronize", SHA_A), "d-6")
    epochs_after_first = [(e.head_sha, e.state) for e in receiver.state.epochs]

    status, body = deliver(receiver, "pull_request",
                           pr_payload("synchronize", SHA_A), "d-6")
    assert status == 202
    assert body["effect"] == "DUPLICATE_IGNORED"
    assert [(e.head_sha, e.state) for e in receiver.state.epochs] == \
        epochs_after_first


def test_redelivery_of_revocation_does_not_double_apply(receiver):
    deliver(receiver, "github_app_authorization", revoked_payload(), "d-7")
    assert receiver.state.auth_state == control_plane.AUTH_LOST
    status, body = deliver(receiver, "github_app_authorization",
                           revoked_payload(), "d-7")
    assert body["effect"] == "DUPLICATE_IGNORED"
    assert receiver.state.auth_state == control_plane.AUTH_LOST


def test_distinct_delivery_ids_are_not_conflated(receiver):
    deliver(receiver, "pull_request", pr_payload("synchronize", SHA_A), "d-8")
    deliver(receiver, "pull_request", pr_payload("synchronize", SHA_B), "d-9")
    assert len(receiver.state.seen_deliveries) == 2


# --- epoch staleness --------------------------------------------------------

def test_synchronize_marks_the_previous_epoch_stale(receiver):
    deliver(receiver, "pull_request", pr_payload("opened", SHA_A), "e-1")
    deliver(receiver, "pull_request", pr_payload("synchronize", SHA_B,
                                                 before=SHA_A), "e-2")
    epochs = {e.head_sha: e.state for e in receiver.state.epochs_for(REPO, 99)}
    assert epochs[SHA_A] == control_plane.STALE
    assert epochs[SHA_B] == control_plane.CURRENT
    assert receiver.state.current_epoch(REPO, 99).head_sha == SHA_B


def test_stale_head_cannot_carry_a_gate_state_forward(receiver):
    deliver(receiver, "pull_request", pr_payload("opened", SHA_A), "e-3")
    receiver.state.note_reconciliation_gap(REPO, 99, SHA_A, "missed delivery")
    assert receiver.state.gate_state(REPO, 99, SHA_A) == control_plane.UNCERTAIN

    deliver(receiver, "pull_request", pr_payload("synchronize", SHA_B), "e-4")
    assert receiver.state.gate_state(REPO, 99, SHA_A) == \
        control_plane.NOT_ESTABLISHED
    assert receiver.state.gate_state(REPO, 99, SHA_B) == \
        control_plane.NOT_ESTABLISHED


def test_malformed_pull_request_payload_changes_nothing(receiver):
    status, body = deliver(receiver, "pull_request",
                           {"action": "synchronize"}, "e-5")
    assert status == 202
    assert body["effect"] == "MALFORMED_IGNORED"
    assert receiver.state.epochs == []


def test_malformed_json_is_rejected_without_state_change(receiver):
    status, _ = deliver(receiver, "pull_request", None, "e-6",
                        raw=b"{not json")
    assert status == 400
    assert receiver.state.epochs == []
    assert "e-6" not in receiver.state.seen_deliveries


# --- authorization and trigger gating --------------------------------------

def test_revocation_event_causes_immediate_auth_loss(receiver):
    deliver(receiver, "github_app_authorization", revoked_payload(), "a-1")
    assert receiver.state.auth_state == control_plane.AUTH_LOST
    assert "revoked" in receiver.state.auth_reason


def test_triggers_forbidden_after_auth_loss(receiver):
    deliver(receiver, "pull_request", pr_payload("synchronize", SHA_A), "a-2")
    assert receiver.state.may_trigger_providers(REPO, 99, SHA_A) is True
    deliver(receiver, "github_app_authorization", revoked_payload(), "a-3")
    assert receiver.state.may_trigger_providers(REPO, 99, SHA_A) is False


def test_triggers_forbidden_when_refresh_outcome_unknown(receiver):
    deliver(receiver, "pull_request", pr_payload("synchronize", SHA_A), "a-4")
    receiver.state.note_auth_state(control_plane.REFRESH_OUTCOME_UNKNOWN,
                                   "response lost before durable commit")
    assert receiver.state.may_trigger_providers(REPO, 99, SHA_A) is False


def test_triggers_forbidden_for_a_stale_head(receiver):
    deliver(receiver, "pull_request", pr_payload("opened", SHA_A), "a-5")
    deliver(receiver, "pull_request", pr_payload("synchronize", SHA_B), "a-6")
    assert receiver.state.may_trigger_providers(REPO, 99, SHA_A) is False
    assert receiver.state.may_trigger_providers(REPO, 99, SHA_B) is True


# --- the invariant the program exists for ----------------------------------

def test_no_sequence_of_inputs_manufactures_clean(receiver):
    sequences = [
        ("pull_request", pr_payload("opened", SHA_A), "n-1"),
        ("pull_request", pr_payload("synchronize", SHA_B), "n-2"),
        ("pull_request", pr_payload("closed", SHA_B), "n-3"),
        ("github_app_authorization", revoked_payload(), "n-4"),
        ("pull_request", pr_payload("reopened", SHA_B), "n-5"),
    ]
    for event, payload, delivery in sequences:
        deliver(receiver, event, payload, delivery)
    receiver.state.note_reconciliation_gap(REPO, 99, SHA_B, "missed delivery")

    for sha in (SHA_A, SHA_B):
        assert receiver.state.gate_state(REPO, 99, sha) in (
            control_plane.NOT_ESTABLISHED, control_plane.UNCERTAIN)
    rendered = json.dumps({str(k): v
                           for k, v in receiver.state.gate_states.items()})
    assert "CLEAN" not in rendered


def test_reducer_source_contains_no_clean_state():
    source = (BASE / "harness" / "control_plane.py").read_text()
    assert '"CLEAN"' not in source and "'CLEAN'" not in source


def test_unknown_event_types_are_ignored_not_guessed(receiver):
    status, body = deliver(receiver, "check_suite", {"action": "requested"},
                           "u-1")
    assert status == 202
    assert body["effect"] == "EVENT_IGNORED"
    assert receiver.state.epochs == []


# --- capture hygiene --------------------------------------------------------

def test_captures_record_signature_presence_not_the_signature(receiver,
                                                              tmp_path):
    deliver(receiver, "pull_request", pr_payload("synchronize", SHA_A), "c-1")
    lines = receiver.capture_path.read_text().strip().splitlines()
    envelope = json.loads(lines[-1])
    assert envelope["signature_present"] is True
    assert envelope["signature_verified"] is True
    text = json.dumps(envelope)
    assert "sha256=" not in text
    assert SECRET.decode() not in text
