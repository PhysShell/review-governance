"""A5a tests: watchdog capability boundaries, the no-restore rule, the
cutover plan, and replay over the live probes.

The offline half matters as much as the live half here — a watchdog is only
trustworthy if the things it *cannot* do are enforced rather than promised.
"""
import json
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

import cutover  # noqa: E402
import decisions as dec  # noqa: E402
import governor  # noqa: E402
import watchdog  # noqa: E402

FIXTURES = BASE / "fixtures"
HEAD_A = "92e68d2d1301117b1261a6f8c74bbc0e0e3a84ba"
HEAD_B = "e113e8a1dd75943002d42764cb213296dbb1206f"


def load(name):
    return json.loads((FIXTURES / name).read_text())


# --- watchdog capability boundary -------------------------------------------

def test_watchdog_cannot_create_a_check_run():
    with pytest.raises(watchdog.WatchdogCapability, match="may only patch"):
        watchdog.watchdog_write("POST", "/repos/x/y/check-runs", "t",
                                {"conclusion": "failure"})


def test_watchdog_cannot_publish_a_passing_conclusion():
    for passing in ("success", "neutral", "skipped"):
        with pytest.raises(watchdog.WatchdogCapability, match="can only revoke"):
            watchdog.watchdog_write("PATCH", "/repos/x/y/check-runs/1", "t",
                                    {"conclusion": passing})


def test_watchdog_cannot_write_a_commit_status_or_merge():
    for path in ("/repos/x/y/statuses/abc", "/repos/x/y/pulls/1/merge",
                 "/repos/x/y/rulesets/1"):
        with pytest.raises(watchdog.WatchdogCapability, match="may not write"):
            watchdog.watchdog_write("PATCH", path, "t",
                                    {"conclusion": "failure"})


def test_watchdog_may_revoke_to_any_non_passing_conclusion():
    assert watchdog.NON_PASSING == frozenset(
        {"failure", "cancelled", "action_required", "timed_out"})
    assert "success" not in watchdog.NON_PASSING


def test_watchdog_never_reads_the_user_credential_store():
    """Its only token source is the App installation, so a revoked or
    expired user authorization cannot disarm the watchdog."""
    for module in ("watchdog.py", "governor.py"):
        source = (BASE / "harness" / module).read_text()
        assert "user-credentials" not in source
        assert "user_token" not in source
        assert "as_user" not in source
    watchdog_source = (BASE / "harness" / "watchdog.py").read_text()
    assert "primary.installation_token()" in watchdog_source
    assert watchdog_source.count("installation_token") == 1


# --- the no-restore rule ----------------------------------------------------

def test_a_revoked_success_is_not_standing_again(tmp_path):
    history = dec.History(tmp_path / "d.sqlite3")
    epoch, head, run = "e1", HEAD_B, 42
    success = history.record(epoch_id=epoch, head_sha=head, verdict="SUCCESS",
                             bundle_hash="h" * 64, bundle_schema="v1",
                             decision_rule_revision="a5a.1", auth_generation=3,
                             decided_at="t1")
    history.project_pending(epoch, head, run, "success", success, "t1")
    history.settle_projection(epoch, state="CONFIRMED",
                              observed_conclusion="success", at="t2")
    assert len(watchdog.standing_successes(history)) == 1

    revocation = history.record(epoch_id=epoch, head_sha=head,
                                verdict="EVIDENCE_INVALIDATED", bundle_hash=None,
                                bundle_schema="WatchdogIncident-v1",
                                decision_rule_revision="a5a.1",
                                auth_generation=0, decided_at="t3",
                                cause=watchdog.CAUSE,
                                invalidates_decision_id=success)
    history.project_pending(epoch, head, run, "failure", revocation, "t3")
    history.settle_projection(epoch, state="CONFIRMED",
                              observed_conclusion="failure", at="t4")
    assert watchdog.standing_successes(history) == []
    history.close()


def test_watchdog_source_states_the_no_restore_rule():
    source = (BASE / "harness" / "watchdog.py").read_text()
    assert "does NOT restore" in source
    assert "fresh qualification" in source.lower()


# --- cutover plan -----------------------------------------------------------

def test_canonical_production_ruleset_is_strict_with_no_bypass():
    ruleset = cutover.canonical_ruleset()
    params = ruleset["rules"][0]["parameters"]
    assert ruleset["conditions"]["ref_name"]["include"] == ["refs/heads/main"]
    assert ruleset["bypass_actors"] == []
    assert params["strict_required_status_checks_policy"] is True
    assert params["required_status_checks"] == [
        {"context": "ai/final-review", "integration_id": 4669438}]


def test_canonical_hash_is_stable_and_change_sensitive():
    ruleset = cutover.canonical_ruleset()
    first = cutover.canonical_hash(ruleset)
    assert first == cutover.canonical_hash(cutover.canonical_ruleset())
    loosened = cutover.canonical_ruleset()
    loosened["rules"][0]["parameters"]["strict_required_status_checks_policy"] = False
    assert cutover.canonical_hash(loosened) != first


def test_forbidden_conclusions_are_still_forbidden_in_production():
    assert set(cutover.FORBIDDEN_CONCLUSIONS) == {"neutral", "skipped"}
    assert "success" in cutover.ALLOWED_CONCLUSIONS
    for forbidden in cutover.FORBIDDEN_CONCLUSIONS:
        assert forbidden not in cutover.ALLOWED_CONCLUSIONS


def test_bootstrap_plan_is_a_dry_run_that_starts_no_provider_round():
    plan = load("bootstrap_plan.json")
    assert plan["applied"] is False
    assert plan["production_context_used"] is False
    assert {item["pr_number"] for item in plan["frozen_inventory"]} == {8, 12}
    for item in plan["planned_bootstrap"]:
        assert item["check"]["conclusion"] == "failure"
        assert item["check"]["verdict"] == "NOT_ESTABLISHED"
        assert item["provider_round"] == "NOT started"


def test_draft_pr_is_planned_to_stay_failing_without_provider_quota():
    plan = load("bootstrap_plan.json")
    draft = next(i for i in plan["frozen_inventory"] if i["draft"])
    planned = next(p for p in plan["planned_bootstrap"]
                   if p["pr_number"] == draft["pr_number"])
    assert draft["pr_number"] == 12          # the frozen pilot baseline
    assert "no provider quota" in planned["reason"]


# --- live replay ------------------------------------------------------------

def test_strict_base_drift_blocked_the_merge():
    drift = load("p1_strict.json")
    assert drift["base_moved"] is True
    assert drift["head_unchanged"] is True
    assert drift["success_on_head_confirmed"] is True
    assert drift["merge_blocked"] is True
    assert drift["merge_http_status"] == 405


def test_updating_the_branch_produced_a_head_with_no_checks():
    drift = load("p1_strict.json")
    assert drift["new_head"] == HEAD_B
    assert drift["check_runs_on_new_head"] == 0
    assert drift["merge_on_new_head_blocked"] is True


def test_watchdog_revoked_every_standing_success_during_the_outage():
    trip = load("p2_watchdog_trip.json")
    assert trip["primary_stale"] is True
    assert trip["heartbeat_age_seconds"] > 45
    assert trip["incident"]["cause"] == "GOVERNOR_UNAVAILABLE"
    assert trip["incident"]["restores_automatically"] is False
    assert len(trip["revocations"]) == 3
    for revocation in trip["revocations"]:
        assert revocation["observed"] == "failure"
        assert revocation["projection_state"] == "CONFIRMED"


def test_the_returning_primary_restored_nothing():
    after = load("p2_after_return.json")
    assert after["primary_stale"] is False
    assert after["standing_successes"] == []
    assert after["revocations"] == []


def test_break_glass_drill_restored_the_exact_ruleset():
    drill = load("p3_break_glass.json")
    assert drill["enforcement_before"] == "active"
    assert drill["enforcement_during"] == "disabled"
    assert drill["enforcement_after"] == "active"
    assert drill["hashes_identical"] is True
    steps = [s["step"] for s in drill["steps"]]
    assert steps[0] == "incident_recorded"
    assert steps[-1] == "closure_recorded"
    exceptional = next(s for s in drill["steps"]
                       if s["step"] == "exceptional_operation")
    assert exceptional["performed"] is False


def test_production_context_was_never_used():
    usage = load("production_context_check.json")
    assert usage["ai_final_review_check_runs_found"] == 0
    assert usage["rulesets_on_main"] == 0
