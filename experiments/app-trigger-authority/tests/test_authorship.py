"""Replay tests: App authorship must be proven by identity, never by text.

The negative control is the point: an identical command body authored by an
ordinary user (real, sourced read-only from frozen pilot PR #11) must not
classify as an App-authored trigger.
"""
import copy
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

from authorship import classify, extract  # noqa: E402

IDENTITY = json.loads((BASE / "app-identity.json").read_text())
FIXTURES = BASE / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def classify_fixture(fixture):
    user, body = extract(fixture)
    return classify(user, body, IDENTITY)


def test_app_authored_codex_trigger_is_recognized():
    result = classify_fixture(load("app_request_codex.json"))
    assert result == {"trigger_for": "codex", "is_app_authored": True,
                      "is_app_authored_trigger": True}


def test_app_authored_coderabbit_trigger_is_recognized():
    result = classify_fixture(load("app_request_coderabbit.json"))
    assert result == {"trigger_for": "coderabbit", "is_app_authored": True,
                      "is_app_authored_trigger": True}


def test_identity_probe_is_app_authored_but_not_a_trigger():
    result = classify_fixture(load("app_request_identity-probe.json"))
    assert result["is_app_authored"] is True
    assert result["trigger_for"] is None
    assert result["is_app_authored_trigger"] is False


def test_user_authored_same_codex_text_is_not_app_trigger():
    result = classify_fixture(load("user_request_codex_negative.json"))
    assert result["trigger_for"] == "codex"          # text IS a trigger…
    assert result["is_app_authored"] is False        # …but not App-authored
    assert result["is_app_authored_trigger"] is False


def test_user_authored_same_coderabbit_text_is_not_app_trigger():
    result = classify_fixture(load("user_request_coderabbit_negative.json"))
    assert result["trigger_for"] == "coderabbit"
    assert result["is_app_authored"] is False
    assert result["is_app_authored_trigger"] is False


def test_provider_response_is_neither_trigger_nor_app_authored():
    fixture = load("codex_response_onboarding_refusal.json")["response"]
    result = classify_fixture(fixture)
    assert result == {"trigger_for": None, "is_app_authored": False,
                      "is_app_authored_trigger": False}


def test_human_control_same_text_is_not_app_trigger():
    result = classify_fixture(load("user_request_coderabbit_control.json"))
    assert result["trigger_for"] == "coderabbit"
    assert result["is_app_authored"] is False
    assert result["is_app_authored_trigger"] is False


def test_coderabbit_ack_is_neither_trigger_nor_app_authored():
    fixture = load("coderabbit_response_human_control_ack.json")["response"]
    result = classify_fixture(fixture)
    assert result == {"trigger_for": None, "is_app_authored": False,
                      "is_app_authored_trigger": False}


def test_wrong_numeric_id_fails_even_with_matching_login_and_type():
    fixture = copy.deepcopy(load("app_request_codex.json"))
    fixture["request_comment"]["user"]["id"] += 1
    result = classify_fixture(fixture)
    assert result["is_app_authored"] is False
    assert result["is_app_authored_trigger"] is False


def test_wrong_login_fails_even_with_matching_numeric_id():
    fixture = copy.deepcopy(load("app_request_codex.json"))
    fixture["request_comment"]["user"]["login"] = "impostor[bot]"
    result = classify_fixture(fixture)
    assert result["is_app_authored"] is False


def test_user_type_fails_even_with_matching_login_and_id():
    fixture = copy.deepcopy(load("app_request_codex.json"))
    fixture["request_comment"]["user"]["type"] = "User"
    result = classify_fixture(fixture)
    assert result["is_app_authored"] is False
