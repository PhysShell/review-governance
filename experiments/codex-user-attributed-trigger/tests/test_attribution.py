"""Regression tests for A1b attribution and Codex outcome normalization.

Reference inputs are real, frozen A1/pilot evidence: the installation-token
App-bot request, an ordinary human/OAuth request, the Codex no-start
response, and a genuine Codex review object. Live A1b fixtures are added
once observed; these tests already pin the discriminations the experiment
depends on.
"""
import copy
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

from attribution import classify, extract  # noqa: E402
from codex_result import evaluate_terminal_review, normalize_comment  # noqa: E402

FIXTURES = BASE / "fixtures"
GOVERNOR_SLUG = "physshell-review-governor"
EXPECTED_USER = {
    "login": "PhysShell",
    "id": 45852143,
    "governor_bot_login": "physshell-review-governor[bot]",
    "governor_bot_id": 319376779,
}
A1B_FROZEN_HEAD = "a4e756b0324e1bebd76a2476a684dfa753abca54"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def classify_fixture(fixture):
    return classify(extract(fixture), EXPECTED_USER, GOVERNOR_SLUG)


# --- carrier discrimination -------------------------------------------------

def test_a1_installation_request_is_not_user_attributed():
    result = classify_fixture(load("reference_a1_installation_token_request.json"))
    assert result["authorship_class"] == "app_installation_bot"
    assert result["trigger_for_codex"] is True
    assert result["user_identity_match"] is False
    assert result["is_user_attributed_app_mediated_trigger"] is False


def test_ordinary_user_request_is_not_app_mediated():
    result = classify_fixture(load("reference_a1_ordinary_user_request.json"))
    assert result["authorship_class"] == "plain_user"
    assert result["user_identity_match"] is True
    assert result["app_mediation_observed"] is False
    assert result["is_user_attributed_app_mediated_trigger"] is False


def test_app_mediated_user_carrier_is_recognized_when_present():
    # Shape of the A1b hypothesis: same user identity, mediated by the App.
    fixture = copy.deepcopy(load("reference_a1_ordinary_user_request.json"))
    comment = fixture["request_comment"]
    comment["body"] = "@codex review"
    comment["performed_via_github_app"] = {"slug": GOVERNOR_SLUG}
    result = classify_fixture(fixture)
    assert result["authorship_class"] == "app_mediated_user"
    assert result["is_user_attributed_app_mediated_trigger"] is True


# --- fail-closed guards -----------------------------------------------------

def test_wrong_user_id_with_correct_app_fails_closed():
    fixture = copy.deepcopy(load("reference_a1_ordinary_user_request.json"))
    fixture["request_comment"]["body"] = "@codex review"
    fixture["request_comment"]["performed_via_github_app"] = {"slug": GOVERNOR_SLUG}
    fixture["request_comment"]["user"]["id"] += 1
    result = classify_fixture(fixture)
    assert result["authorship_class"] == "other"
    assert result["is_user_attributed_app_mediated_trigger"] is False


def test_foreign_app_mediation_is_not_governor_mediation():
    fixture = copy.deepcopy(load("reference_a1_ordinary_user_request.json"))
    fixture["request_comment"]["body"] = "@codex review"
    fixture["request_comment"]["performed_via_github_app"] = {"slug": "some-other-app"}
    result = classify_fixture(fixture)
    assert result["app_mediation_observed"] is True
    assert result["app_mediation_matches_governor"] is False
    assert result["authorship_class"] == "other"


def test_command_text_alone_never_qualifies():
    fixture = copy.deepcopy(load("reference_a1_installation_token_request.json"))
    fixture["request_comment"]["user"] = {"login": "stranger", "id": 1, "type": "User"}
    fixture["request_comment"]["performed_via_github_app"] = None
    result = classify_fixture(fixture)
    assert result["trigger_for_codex"] is True
    assert result["is_user_attributed_app_mediated_trigger"] is False


# --- Codex outcome normalization -------------------------------------------

def test_codex_no_start_is_unavailable_never_clean():
    response = load("reference_a1_codex_no_start.json")["response"]
    result = normalize_comment(response)
    assert result["gate"] == "UNAVAILABLE"
    assert result["review_state"] == "REVIEW_UNAVAILABLE_FOR_REQUESTOR"
    assert result["gate"] != "CLEAN"


def test_real_codex_review_binds_only_its_own_commit():
    review = load("reference_a1_pilot_codex_review.json")["review"]
    own = evaluate_terminal_review(review, review["commit_id"])
    assert own["is_codex_review"] is True
    assert own["binds_frozen_head"] is True
    assert own["gate"] == "REVIEW_OBSERVED"

    foreign = evaluate_terminal_review(review, A1B_FROZEN_HEAD)
    assert foreign["binds_frozen_head"] is False
    assert foreign["gate"] == "STALE"


def test_non_codex_review_is_not_terminal_evidence():
    review = copy.deepcopy(load("reference_a1_pilot_codex_review.json")["review"])
    review["user"] = {"login": "PhysShell", "id": 45852143, "type": "User"}
    result = evaluate_terminal_review(review, review["commit_id"])
    assert result["is_codex_review"] is False
    assert result["gate"] == "UNRECOGNIZED"
