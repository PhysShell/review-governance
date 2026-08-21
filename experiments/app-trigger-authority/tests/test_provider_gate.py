"""Regression tests for provider-gate normalization.

Pins the observed A1 fact: an App-authored `@codex review` elicited the
"To use Codex here…" no-start response from the Codex connector actor.
That must normalize to COMMAND_HANDLED + REVIEW_UNAVAILABLE_FOR_REQUESTOR,
gate UNAVAILABLE — and no input whatsoever may normalize to CLEAN.
"""
import copy
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

from provider_gate import normalize_codex_response  # noqa: E402

FIXTURES = BASE / "fixtures"
REAL_RESPONSE = json.loads(
    (FIXTURES / "codex_response_onboarding_refusal.json").read_text())["response"]


def test_observed_no_start_response_normalizes_to_unavailable():
    result = normalize_codex_response(REAL_RESPONSE)
    assert result["command_handled"] is True
    assert result["review_state"] == "REVIEW_UNAVAILABLE_FOR_REQUESTOR"
    assert result["gate"] == "UNAVAILABLE"


def test_no_start_response_is_never_clean():
    assert normalize_codex_response(REAL_RESPONSE)["gate"] != "CLEAN"


def test_same_body_from_wrong_actor_is_not_a_codex_response():
    spoofed = copy.deepcopy(REAL_RESPONSE)
    spoofed["user"] = {"login": "physshell-review-governor[bot]",
                       "id": 319376779, "type": "Bot"}
    result = normalize_codex_response(spoofed)
    assert result["gate"] == "UNRECOGNIZED"
    assert result["command_handled"] is None


def test_unknown_codex_actor_body_is_unrecognized_not_clean():
    unknown = copy.deepcopy(REAL_RESPONSE)
    unknown["body"] = "Something entirely different."
    result = normalize_codex_response(unknown)
    assert result["gate"] == "UNRECOGNIZED"
    assert result["gate"] != "CLEAN"
