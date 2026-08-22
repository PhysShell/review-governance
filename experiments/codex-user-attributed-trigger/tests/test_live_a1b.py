"""Replay tests over the live A1b captures.

These pin the observations the verdict rests on: the primary request was
user-attributed *and* App-mediated, the matched control was the App bot
itself, Codex executed a review for the former and refused the latter, and
the terminal artifact attests the frozen HEAD on a non-authoritative
carrier.
"""
import copy
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

from attribution import classify, extract  # noqa: E402
from codex_result import (evaluate_review_comment,  # noqa: E402
                          normalize_comment)

FIXTURES = BASE / "fixtures"
GOVERNOR_SLUG = "physshell-review-governor"
EXPECTED_USER = {
    "login": "PhysShell",
    "id": 45852143,
    "governor_bot_login": "physshell-review-governor[bot]",
    "governor_bot_id": 319376779,
}
FROZEN_HEAD = "a4e756b0324e1bebd76a2476a684dfa753abca54"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def classify_fixture(fixture):
    return classify(extract(fixture), EXPECTED_USER, GOVERNOR_SLUG)


# --- the primary request ----------------------------------------------------

def test_primary_request_is_app_mediated_user_attributed_trigger():
    result = classify_fixture(load("codex_user_attributed_request.json"))
    assert result["authorship_class"] == "app_mediated_user"
    assert result["user_identity_match"] is True
    assert result["app_mediation_observed"] is True
    assert result["app_mediation_matches_governor"] is True
    assert result["is_user_attributed_app_mediated_trigger"] is True


def test_primary_request_records_the_user_token_auth_model():
    envelope = load("codex_user_attributed_request.json")
    assert envelope["auth_model"] == "github_app_user_access_token"
    assert envelope["app_mediation_observability"] == "PASS"
    assert envelope["token_provenance"]["token_prefix"] == "ghu_"
    assert envelope["token_provenance"]["obtained_via"] == "github_app_device_flow"
    assert envelope["token_provenance"]["refresh_token_used_in_a1b"] is False
    assert envelope["pr_at_request"]["head_sha"] == FROZEN_HEAD


def test_benign_probe_is_app_mediated_but_not_a_trigger():
    result = classify_fixture(load("benign_user_attributed_comment.json"))
    assert result["authorship_class"] == "app_mediated_user"
    assert result["trigger_for_codex"] is False
    assert result["is_user_attributed_app_mediated_trigger"] is False


# --- the matched identity control ------------------------------------------

def test_matched_control_is_the_app_installation_bot():
    result = classify_fixture(load("matched_control_app_identity_request.json"))
    assert result["authorship_class"] == "app_installation_bot"
    assert result["user_identity_match"] is False
    assert result["is_user_attributed_app_mediated_trigger"] is False


def test_control_shares_pr_and_head_with_the_primary_request():
    primary = load("codex_user_attributed_request.json")
    control = load("matched_control_app_identity_request.json")
    assert control["pr_number"] == primary["pr_number"] == 14
    assert control["pr_at_request"]["head_sha"] == FROZEN_HEAD
    assert control["auth_model"] == "github_app_installation_token"
    assert control["request_comment"]["body"] == primary["request_comment"]["body"]
    assert control["request_comment"]["created_at"] > \
        primary["request_comment"]["created_at"]


def test_control_response_is_a_refusal_never_clean():
    response = load("matched_control_app_identity_response.json")["response"]
    result = normalize_comment(response)
    assert result["gate"] == "UNAVAILABLE"
    assert result["review_state"] == "REVIEW_UNAVAILABLE_FOR_REQUESTOR"
    assert result["gate"] != "CLEAN"


# --- the terminal artifact --------------------------------------------------

def test_terminal_artifact_attests_the_frozen_head_but_stays_advisory():
    response = load("codex_response.json")["response"]
    result = evaluate_review_comment(response, FROZEN_HEAD)
    assert result["is_codex_review_result"] is True
    assert result["review_state"] == "REVIEW_EXECUTED"
    assert result["attested_commit"] == "a4e756b032"
    assert result["attested_commit_is_prefix"] is True
    assert result["binds_frozen_head"] is True
    assert result["carrier_is_authoritative"] is False
    assert result["gate"] == "ADVISORY_ONLY"
    assert result["gate"] != "CLEAN"


def test_terminal_artifact_does_not_bind_a_different_head():
    response = load("codex_response.json")["response"]
    other = "b" * 40
    assert evaluate_review_comment(response, other)["binds_frozen_head"] is False


def test_tampered_attested_commit_fails_to_bind():
    response = copy.deepcopy(load("codex_response.json")["response"])
    response["body"] = response["body"].replace("a4e756b032", "deadbeef99")
    result = evaluate_review_comment(response, FROZEN_HEAD)
    assert result["attested_commit"] == "deadbeef99"
    assert result["binds_frozen_head"] is False


def test_refusal_text_is_not_a_review_result():
    refusal = load("matched_control_app_identity_response.json")["response"]
    assert evaluate_review_comment(refusal, FROZEN_HEAD)["gate"] == "UNRECOGNIZED"


def test_no_pull_request_review_object_was_emitted():
    inventory = load("final_inventory.json")
    assert inventory["reviews_present"] == 0
    assert inventory["inline_comments_present"] == 0
    assert inventory["frozen_head"] == FROZEN_HEAD
