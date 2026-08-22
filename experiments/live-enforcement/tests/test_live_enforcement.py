"""Replay assertions over the A4-live enforcement round.

Every row of the matrix is asserted against what GitHub actually answered,
including the exact refusal wording — because "blocked" is only meaningful
if the reason is the one under test.
"""
import json
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

import enforce  # noqa: E402

FIXTURES = BASE / "fixtures"
CONTEXT = "ai/final-review-enforcement-probe"
APP_ID = 4669438
TARGET_REF = "refs/heads/governor/a4-enforcement-target"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def cases():
    return load("cases.json")["cases"]


# --- isolation --------------------------------------------------------------

def test_ruleset_was_scoped_to_one_ref_with_no_bypass_actors():
    ruleset = load("ruleset_readback.json")
    assert ruleset["enforcement"] == "active"
    assert ruleset["bypass_actors"] == []
    assert ruleset["conditions"]["ref_name"]["include"] == [TARGET_REF]
    assert ruleset["conditions"]["ref_name"]["exclude"] == []


def test_expected_source_persisted_with_strict_disabled():
    params = load("ruleset_readback.json")["rules"][0]["parameters"]
    assert params["required_status_checks"] == [
        {"context": CONTEXT, "integration_id": APP_ID}]
    assert params["strict_required_status_checks_policy"] is False


def test_main_was_never_touched():
    teardown = load("teardown.json")
    assert teardown["main_head_after"] == "047ff1a641e33e0bb8c6b9eea26bb80eea021e08"
    assert "none" in teardown["main_branch_protection"]
    live = load("cases.json")["live_state"]
    assert live["main_head"] == teardown["main_head_after"]


# --- the gate ---------------------------------------------------------------

def test_wrong_source_success_did_not_satisfy_the_rule():
    case = cases()["case3_wrong_source"]
    assert case["blocked"] is True
    assert case["merge_http_status"] == 405
    assert "was not set by the expected GitHub app" in case["message"]
    assert case["governor_check_runs"] == 0


def test_the_wrong_source_artifact_was_a_real_passing_status():
    """The block must not be explained by the fixture being absent or
    failing: the context genuinely read success."""
    detail = load("test1_wrong_source.json")
    assert detail["foreign_status"]["state"] == "success"
    assert detail["foreign_status"]["context"] == CONTEXT
    assert detail["merge_blocked"] is True
    assert detail["pr_state_after"]["mergeStateStatus"] == "BLOCKED"
    assert detail["pr_state_after"]["mergedAt"] is None


# --- the matrix -------------------------------------------------------------

def test_no_check_at_all_is_blocked_as_expected_missing():
    case = cases()["case1_no_check"]
    assert case["blocked"] is True and case["merge_http_status"] == 405
    assert "is expected" in case["message"]


def test_governor_failure_on_the_exact_head_is_blocked_as_failing():
    case = cases()["case2_governor_failure"]
    assert case["blocked"] is True
    assert "is failing" in case["message"]
    publish = load("case2_publish.json")
    assert publish["observed"] == "failure"
    assert publish["projection_state"] == "CONFIRMED"
    assert publish["app"] == {"id": APP_ID, "slug": "physshell-review-governor"}


def test_stale_expected_sha_is_refused_before_rules_are_even_consulted():
    case = cases()["case4a_stale_sha"]
    assert case["merge_http_status"] == 409
    assert "Head branch was modified" in case["message"]


def test_success_on_a_previous_head_does_not_satisfy_the_new_head():
    case = cases()["case4b_old_head_success"]
    assert case["blocked"] is True
    assert case["check_runs_on_current_head"] == 0
    assert "is expected" in case["message"]
    assert load("case4_success_old_head.json")["observed"] == "success"


def test_current_head_governor_success_allowed_exactly_one_merge():
    case = cases()["case5_current_head_success"]
    assert case["merged"] is True
    assert case["merge_state_status_before"] == "CLEAN"
    assert case["merged_into"] == TARGET_REF
    assert case["performed_by"].startswith("owner")
    publish = load("case5_success.json")
    assert publish["observed"] == "success"
    assert publish["projection_state"] == "CONFIRMED"
    assert publish["hash_in_output"] is True


def test_only_one_pr_was_ever_merged_and_only_into_the_isolated_ref():
    live = load("cases.json")["live_state"]
    assert live["pr22"]["merged"] is False
    assert live["pr23"]["merged"] is False
    assert live["pr24"]["merged"] is True
    for pr in ("pr22", "pr23", "pr24"):
        assert live[pr]["base"] == "governor/a4-enforcement-target"


# --- G1 live ----------------------------------------------------------------

def test_a_revoked_success_blocks_the_merge_on_the_same_head():
    case = cases()["case6_revoked_success"]
    assert case["blocked"] is True
    assert "is failing" in case["message"]
    assert "CLEAN" in case["sequence"][0]        # was mergeable while green
    assert load("case6_success.json")["observed"] == "success"
    assert load("case6_invalidated.json")["observed"] == "failure"
    assert load("case6_invalidated.json")["projection_state"] == "CONFIRMED"


def test_the_decision_chain_records_the_revocation_between_the_successes():
    chain = load("decision_history.json")["chain"]
    verdicts = [row["verdict"] for row in chain]
    assert verdicts[:3] == ["NOT_ESTABLISHED", "SUCCESS", "EVIDENCE_INVALIDATED"]
    assert all(row["bundle_schema"] == "EnforcementProbeEvidence-v1"
               for row in chain)
    assert all(row["bundle_hash"] for row in chain)


def test_every_projection_was_confirmed_by_readback():
    for projection in load("decision_history.json")["projections"]:
        assert projection["state"] == "CONFIRMED"
        assert projection["observed_conclusion"] == projection["intended_conclusion"]


# --- the boundaries that must survive enforcement ---------------------------

def test_governor_runtime_cannot_write_a_commit_status():
    with pytest.raises(PermissionError, match="may not write"):
        enforce.governor_write("POST", "/repos/x/y/statuses/deadbeef", "token",
                               {"state": "success"})


def test_governor_harness_has_no_merge_capability():
    source = (BASE / "harness" / "enforce.py").read_text()
    assert "/merge" not in source
    assert "def merge" not in source


def test_neutral_and_skipped_remain_unwritable():
    assert "neutral" in enforce.FORBIDDEN_CONCLUSIONS
    assert "skipped" in enforce.FORBIDDEN_CONCLUSIONS
    assert "neutral" not in enforce.ALLOWED_CONCLUSIONS
    assert "skipped" not in enforce.ALLOWED_CONCLUSIONS


def test_fixture_was_torn_down_and_evidence_kept():
    teardown = load("teardown.json")
    assert teardown["rulesets_remaining"] == 0
    assert teardown["ref_readback_status"] == 404
    assert teardown["probe_prs_closed_without_merge"] == [22, 23]
    assert len(teardown["check_runs_preserved"]) == 3
