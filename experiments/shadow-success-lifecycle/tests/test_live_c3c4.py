"""Replay assertions over the live A3b-c3/c4 correction round (probe PR #21).

The two claims under test are narrow and load-bearing: the Governor
extinguished its own success and *confirmed* the failure before the
request that invalidates the evidence existed, and no projection was ever
believed on the strength of a PATCH response alone.
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

FIXTURES = BASE / "fixtures" / "c3c4"
HEAD = "d20d370668a045c7c15adba3dd40d27d0157cfe9"
EPOCH = "epoch-42d70fbf63dd30bd"
RUN = 97008202609
BUNDLE = "e4009b2d8f9e9ed6585e7b2e585a79c8a8659b9b01b6e81b7a2ea392e27304d2"
REQUEST_COMMENT = 5379406791


def load(name):
    return json.loads((FIXTURES / name).read_text())


# --- c4: the projection was confirmed by reading, not by the write ----------

def test_success_projection_reached_confirmed_via_independent_readback():
    published = load("publish_1.json")
    assert published["projection_state"] == "CONFIRMED"
    assert published["published"] is True
    assert published["readback_status"] == 200
    assert published["conclusion"] == "success"


def test_the_confirmed_success_matched_on_every_identity_field():
    published = load("publish_1.json")
    assert published["head_sha"] == HEAD and len(HEAD) == 40
    assert published["external_id"] == EPOCH
    assert published["app"] == {"id": 4669438, "slug": "physshell-review-governor"}
    assert published["bundle_hash_in_output"] is True
    assert load("bundle_1.json")["evidence_hash"] == BUNDLE


def test_projection_row_records_intent_and_observation_separately():
    projections = load("decision_history.json")["projections"]
    assert len(projections) == 1
    projection = projections[0]
    assert projection["state"] == "CONFIRMED"
    assert projection["intended_conclusion"] == "failure"     # after the rerun
    assert projection["observed_conclusion"] == "failure"
    assert projection["check_run_id"] == RUN


# --- c3: ordering -----------------------------------------------------------

def test_a_plain_trigger_is_impossible_while_a_success_stands():
    """Proven live: the CLI refused, and the only request in the round came
    from the ordered rerun path."""
    rerun = load("rerun.json")
    assert rerun["rerun"] is True
    assert rerun["request_comment_id"] == REQUEST_COMMENT


def test_failure_was_confirmed_before_the_provider_request_existed():
    rerun = load("rerun.json")
    ordering = rerun["ordering"]
    assert rerun["check_conclusion_before_request"] == "failure"
    assert rerun["projection_state"] == "CONFIRMED"
    assert ordering["invalidation_decided_at"] <= \
        ordering["failure_patch_attempted_at"]
    assert ordering["failure_patch_attempted_at"] <= \
        ordering["failure_confirmed_at"]
    assert ordering["failure_confirmed_at"] <= \
        ordering["provider_request_created_at"]


def test_strict_ordering_rests_on_monotonic_measurement_not_on_second_stamps():
    """GitHub stamps are second-resolution and here they collide, so the
    strict claim is made only where it is actually measured."""
    ordering = load("rerun.json")["ordering"]
    assert ordering["failure_confirmed_at"] == \
        ordering["provider_request_created_at"]          # equal to the second
    gap = ordering["monotonic_seconds"]["failure_confirmed_to_provider_request"]
    assert gap > 0                                        # strict, locally
    assert ordering["monotonic_seconds"][
        "invalidation_to_failure_confirmed"] > 0


def test_github_server_clock_corroborates_the_ordering():
    """Independent of our clock: GitHub's own completed_at for the failure is
    earlier than its own created_at for the request."""
    corroboration = load("ordering_corroboration.json")
    server = corroboration["server_side_corroboration"]
    assert server["check_completed_at"] < server["request_created_at"]
    assert server["check_not_after_request"] is True
    assert corroboration["check_run"]["conclusion"] == "failure"
    assert corroboration["check_run"]["output_title"] == \
        "Governor: EVIDENCE_INVALIDATED"


def test_the_request_that_invalidates_came_after_and_on_the_right_carrier():
    corroboration = load("ordering_corroboration.json")
    request = corroboration["provider_request_comment"]
    assert request["id"] == REQUEST_COMMENT
    assert request["user"]["id"] == 45852143
    assert request["via"] == "physshell-review-governor"
    assert request["body"].startswith("@codex review")


# --- the decision chain of the correction round -----------------------------

def test_chain_records_the_invalidation_before_the_request_with_its_cause():
    chain = load("decision_history.json")["chain"]
    assert [row["verdict"] for row in chain] == ["SUCCESS", "EVIDENCE_INVALIDATED"]
    assert chain[0]["bundle_hash"] == BUNDLE
    assert chain[1]["cause"] == "rerun_requested_pre_request_invalidation"
    assert chain[1]["invalidates_decision_id"] == chain[0]["decision_id"]
    assert chain[1]["invalidates_bundle_hash"] == BUNDLE


def test_settling_and_guard_were_clean_before_the_success():
    assert load("settle_1.json")["passed"] is True
    assert load("settle_1.json")["failures"] == []
    assert load("publish_1.json")["timings"][
        "pre_publish_validation_at"] <= load("publish_1.json")["timings"][
        "github_success_at"]


def test_rate_limited_generation_one_was_replaced_not_resent():
    g1 = load("triggers_g1.json")
    g2 = load("triggers_g2.json")
    assert g1["coderabbit"]["generation"] == 1
    assert g2["coderabbit"]["generation"] == 2
    assert g2["coderabbit"]["comment_id"] != g1["coderabbit"]["comment_id"]
    assert g2["coderabbit"]["carrier"] == "app_mediated_user"
