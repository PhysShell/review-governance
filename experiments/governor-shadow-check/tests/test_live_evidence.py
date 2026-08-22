"""Replay assertions over the live A2b captures.

These are about what GitHub actually did: which SHA each check bound to,
which App owned it, what conclusions were written, and that no success ever
appeared on either head.
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

import checks  # noqa: E402
import store  # noqa: E402

FIXTURES = BASE / "fixtures"
HEAD_A = "c9416bd778b0ec375c8b7e40470192d48f645894"
HEAD_B = "11b0b5d143b3c787a543cb5d7c014a4ab629fd75"
EPOCH_A = "epoch-ccab3cc4c15085e2"
EPOCH_B = "epoch-a22f7efbe6ecfe9d"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def test_check_was_created_on_the_exact_full_head_sha():
    opened = load("epoch_A_open.json")
    assert opened["created"] is True
    assert opened["check"]["head_sha"] == HEAD_A
    assert len(opened["check"]["head_sha"]) == 40
    assert opened["epoch"]["head_sha"] == HEAD_A


def test_governor_app_provenance_on_every_live_run():
    live = load("checks_after_reconciliation.json")
    for side in ("HEAD_A", "HEAD_B"):
        assert live[side]["all_governor_owned"] is True
        for run in live[side]["runs"]:
            assert run["app"] == {"id": checks.GOVERNOR_APP_ID,
                                  "slug": checks.GOVERNOR_APP_SLUG}
            assert run["name"] == checks.CHECK_NAME


def test_first_terminal_verdict_was_fail_closed():
    concluded = load("epoch_A_conclude.json")
    assert concluded["verdict"] == "NOT_ESTABLISHED"
    assert concluded["check"]["conclusion"] == "failure"
    assert concluded["http_status"] == 200


def test_the_synchronize_was_genuinely_missed():
    evidence = load("missed_delivery_evidence.json")
    assert evidence["receiver_running"] is False
    assert evidence["tunnel_alive"] is False
    assert evidence["deliveries_after_head_B_push"] == []


def test_reconciliation_superseded_the_old_epoch_and_built_a_new_one():
    result = load("reconcile_1_missed_webhook.json")
    assert result["stored_head"] == HEAD_A
    assert result["github_head"] == HEAD_B
    assert result["changed"] is True
    actions = result["actions"]
    assert actions[0]["epoch_stale"] == EPOCH_A
    assert actions[1]["check_cancelled"] == 96985023054
    assert actions[1]["conclusion"] == "cancelled"
    assert actions[2]["epoch_opened"] == EPOCH_B
    assert actions[2]["head"] == HEAD_B
    assert actions[3]["conclusion"] == "failure"


def test_old_check_stayed_bound_to_the_old_head():
    live = load("checks_after_reconciliation.json")
    old = live["HEAD_A"]["runs"][0]
    assert old["head_sha"] == HEAD_A
    assert old["conclusion"] == "cancelled"
    assert old["external_id"] == EPOCH_A
    new = live["HEAD_B"]["runs"][0]
    assert new["head_sha"] == HEAD_B
    assert new["external_id"] == EPOCH_B
    assert old["id"] != new["id"]


def test_exactly_one_governor_run_per_head():
    live = load("checks_after_reconciliation.json")
    assert live["HEAD_A"]["count"] == 1
    assert live["HEAD_B"]["count"] == 1


def test_reconciliation_second_pass_changed_nothing():
    second = load("reconcile_2_idempotent.json")
    assert second["changed"] is False
    assert second["stored_head"] == second["github_head"] == HEAD_B
    assert second["actions"] == [{"noop": "stored head already current"}]


def test_lost_mapping_was_recovered_without_creating_a_duplicate():
    third = load("reconcile_3_mapping_recovery.json")
    recovered = [a for a in third["actions"] if "check_mapping_recovered" in a]
    assert recovered and recovered[0]["check_mapping_recovered"] == 96985225301
    assert not any("check_recreated" in a for a in third["actions"])


def test_durable_state_matches_what_github_shows():
    state = load("durable_state.json")
    epochs = {e["head_sha"]: e for e in state["epochs"]}
    assert epochs[HEAD_A]["state"] == store.STALE
    assert epochs[HEAD_A]["generation"] == 1
    assert epochs[HEAD_B]["state"] == store.CURRENT
    assert epochs[HEAD_B]["generation"] == 2
    assert {c["check_run_id"] for c in state["checks"]} == {96985023054, 96985225301}
    assert len(state["reconciliations"]) == 3


def test_no_success_and_no_provider_evidence_anywhere_in_the_live_run():
    state = load("durable_state.json")
    for decision in state["decisions"]:
        assert decision["conclusion"] in ("failure", "cancelled")
        providers = json.loads(decision["provider_state"])
        assert set(providers.values()) <= {"ABSENT"}, providers
    live = load("checks_after_reconciliation.json")
    conclusions = [r["conclusion"] for side in ("HEAD_A", "HEAD_B")
                   for r in live[side]["runs"]]
    assert set(conclusions) == {"cancelled", "failure"}
    assert "success" not in conclusions and "neutral" not in conclusions
