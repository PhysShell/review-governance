"""Replay tests for A1b-R.

The load-bearing claim is that `plain_user` and `app_mediated_user` are
**different carriers**: same human identity, same command text,
distinguished only by `performed_via_github_app`. A1 measured CodeRabbit on
the first and on the installation bot; A1b-R measures the third.
"""
import copy
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

from attribution import classify, extract  # noqa: E402
from coderabbit_result import (evaluate_review_object,  # noqa: E402
                               normalize_comment)

FIXTURES = BASE / "fixtures"
GOVERNOR_SLUG = "physshell-review-governor"
EXPECTED_USER = {
    "login": "PhysShell",
    "id": 45852143,
    "governor_bot_login": "physshell-review-governor[bot]",
    "governor_bot_id": 319376779,
}
FROZEN_HEAD = "44ed22c487ae59528e0840e03ff983c6fea3bfcb"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def classify_fixture(fixture):
    return classify(extract(fixture), EXPECTED_USER, GOVERNOR_SLUG)


# --- the three carriers are distinct ---------------------------------------

def test_primary_request_is_app_mediated_user_trigger():
    result = classify_fixture(load("coderabbit_user_attributed_request.json"))
    assert result["authorship_class"] == "app_mediated_user"
    assert result["trigger_for_coderabbit"] is True
    assert result["user_identity_match"] is True
    assert result["app_mediation_matches_governor"] is True
    assert result["is_user_attributed_app_mediated_trigger"] is True


def test_plain_user_carrier_is_not_the_app_mediated_carrier():
    plain = classify_fixture(load("reference_a1_plain_user_request.json"))
    assert plain["authorship_class"] == "plain_user"
    assert plain["trigger_for_coderabbit"] is True
    assert plain["user_identity_match"] is True          # same human…
    assert plain["app_mediation_observed"] is False      # …different carrier
    assert plain["is_user_attributed_app_mediated_trigger"] is False


def test_same_identity_and_body_differ_only_by_mediation_field():
    mediated = extract(load("coderabbit_user_attributed_request.json"))
    plain = extract(load("reference_a1_plain_user_request.json"))
    assert mediated["user"]["id"] == plain["user"]["id"] == 45852143
    assert mediated["body"].strip().startswith("@coderabbitai full review")
    assert plain["body"].strip().startswith("@coderabbitai full review")
    assert mediated["performed_via_github_app"]["slug"] == GOVERNOR_SLUG
    assert plain["performed_via_github_app"] is None


def test_installation_carrier_is_neither():
    result = classify_fixture(load("reference_a1_installation_request.json"))
    assert result["authorship_class"] == "app_installation_bot"
    assert result["user_identity_match"] is False
    assert result["is_user_attributed_app_mediated_trigger"] is False


def test_benign_probe_is_mediated_but_not_a_trigger():
    result = classify_fixture(load("benign_user_attributed_comment.json"))
    assert result["authorship_class"] == "app_mediated_user"
    assert result["trigger_for_coderabbit"] is False
    assert result["is_user_attributed_app_mediated_trigger"] is False


# --- fail-closed guards -----------------------------------------------------

def test_wrong_user_id_with_governor_mediation_fails_closed():
    fixture = copy.deepcopy(load("coderabbit_user_attributed_request.json"))
    fixture["request_comment"]["user"]["id"] += 1
    result = classify_fixture(fixture)
    assert result["authorship_class"] == "other"
    assert result["is_user_attributed_app_mediated_trigger"] is False


def test_foreign_app_mediation_fails_closed():
    fixture = copy.deepcopy(load("coderabbit_user_attributed_request.json"))
    fixture["request_comment"]["performed_via_github_app"] = {"slug": "other-app"}
    result = classify_fixture(fixture)
    assert result["app_mediation_matches_governor"] is False
    assert result["authorship_class"] == "other"


# --- provider handling ------------------------------------------------------

def test_rate_limit_response_counts_as_handling_never_clean():
    response = load("coderabbit_response_rate_limited.json")["response"]
    result = normalize_comment(response)
    assert result["command_handled"] is True
    assert result["handling_kind"] == "RATE_LIMITED"
    assert result["gate"] == "ADVISORY_ONLY"
    assert result["gate"] != "CLEAN"


def test_plain_user_acknowledgement_also_counts_as_handling():
    response = load("reference_a1_plain_user_ack.json")["response"]
    result = normalize_comment(response)
    assert result["command_handled"] is True
    assert result["handling_kind"] == "ACKNOWLEDGED"
    assert result["gate"] != "CLEAN"


def test_non_provider_comment_is_not_handling_evidence():
    request = extract(load("coderabbit_user_attributed_request.json"))
    result = normalize_comment(request)
    assert result["command_handled"] is None
    assert result["handling_kind"] == "NOT_PROVIDER_AUTHORED"


def test_a1_installation_carrier_produced_no_handling_evidence():
    silence = load("reference_a1_installation_carrier_silence.json")
    assert silence["classification_per_protocol"] == "NO_OBSERVED_START"
    assert silence["signals_observed"]["issue_comments"] == 0
    assert silence["signals_observed"]["reactions_on_request"] == 0


def test_review_object_binding_rules():
    review = {"user": {"login": "coderabbitai[bot]", "id": 136622811,
                       "type": "Bot"},
              "id": 1, "commit_id": FROZEN_HEAD}
    assert evaluate_review_object(review, FROZEN_HEAD)["gate"] == "REVIEW_OBSERVED"
    assert evaluate_review_object(review, "f" * 40)["gate"] == "STALE"


def test_no_review_object_was_emitted_in_a1br():
    inventory = load("final_inventory.json")
    assert inventory["reviews_present"] == 0
    assert inventory["frozen_head"] == FROZEN_HEAD
    assert inventory["draft"] is True
