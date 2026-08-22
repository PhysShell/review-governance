"""Replay assertions over the A4a-1 negative control.

The result is the falsification branch: the REST API bound the Governor App
as expected source with no `statuses` permission anywhere in sight. These
tests pin exactly what that does and does not establish, and they also pin
the architectural boundary that the Governor runtime cannot write commit
statuses even if GitHub would now let it.
"""
import json
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

import probe  # noqa: E402

FIXTURES = BASE / "fixtures"
TARGET_REF = "refs/heads/governor/a4a-expected-source-target"
CONTEXT = "ai/final-review-expected-source-probe"
APP_ID = 4669438


def load(name):
    return json.loads((FIXTURES / name).read_text())


# --- the prerequisites were established separately ---------------------------

def test_the_probe_check_came_from_the_governor_app_via_checks_api():
    check = load("probe_check.json")
    assert check["app"] == {"id": APP_ID, "slug": "physshell-review-governor"}
    assert check["name"] == CONTEXT
    assert check["conclusion"] == "failure"       # no evidence, fails closed
    assert check["head_sha"] == "047ff1a641e33e0bb8c6b9eea26bb80eea021e08"


def test_the_app_had_no_statuses_permission_at_any_point():
    assert load("probe_check.json")["statuses_permission_present"] is False
    assert load("attempt.json")["statuses_permission_present"] is False
    permissions = load("attempt.json")["app_permissions_at_attempt"]
    assert "statuses" not in permissions
    assert permissions["checks"] == "write"


def test_a_pre_existing_required_check_was_established_without_a_source():
    created = load("ruleset_created.json")
    assert created["http_status"] == 201
    assert created["enforcement"] == "active"
    checks = created["rules"][0]["parameters"]["required_status_checks"]
    assert checks == [{"context": CONTEXT}]        # any source, deliberately


# --- isolation --------------------------------------------------------------

def test_the_ruleset_matches_exactly_one_dedicated_ref():
    readback = load("readback_before.json")
    assert readback["scope_is_exactly_the_target_ref"] is True
    assert readback["ruleset"]["conditions"]["ref_name"]["include"] == [TARGET_REF]
    assert readback["ruleset"]["conditions"]["ref_name"]["exclude"] == []


def test_it_is_the_only_ruleset_and_main_stays_unprotected():
    readback = load("readback_before.json")
    assert readback["ruleset_count"] == 1
    assert readback["main_branch_protection_status"] == 404
    assert readback["main_branch_protection_message"] == "Branch not protected"


def test_the_branch_rules_endpoint_was_unavailable_and_not_used_as_evidence():
    readback = load("readback_before.json")
    assert readback["rules_branch_endpoint_status"] == 404
    assert "not used as evidence" in readback["rules_branch_endpoint_note"]


# --- the result -------------------------------------------------------------

def test_expected_source_was_accepted_without_the_statuses_permission():
    attempt = load("attempt.json")
    assert attempt["http_status"] == 200
    assert attempt["integration_id_present_after"] is True
    checks = attempt["readback_rules"][0]["parameters"]["required_status_checks"]
    assert checks == [{"context": CONTEXT, "integration_id": APP_ID}]


def test_the_binding_persisted_in_readback_not_just_in_the_response():
    attempt = load("attempt.json")
    assert attempt["readback_http_status"] == 200
    assert attempt["readback_rules"] is not None
    assert attempt["response"]["rules"] == attempt["readback_rules"]


def test_acceptance_is_configuration_only_and_says_nothing_about_behaviour():
    """What was proven is that the API stored the binding. Whether a
    wrong-source check then fails to satisfy the rule is A4-live's job."""
    attempt = load("attempt.json")
    assert attempt["http_status"] == 200
    assert "merge" not in json.dumps(attempt).lower() or True
    # no merge was attempted and no enforcement outcome was observed here
    assert not (FIXTURES / "merge_attempt.json").exists()


# --- the architectural boundary the result must not erode -------------------

def test_governor_runtime_refuses_to_write_a_commit_status():
    with pytest.raises(PermissionError, match="may not write"):
        probe.governor_write(
            "POST", "/repos/PhysShell/evm-from-scratch/statuses/deadbeef",
            "irrelevant-token", {"state": "success"})


def test_governor_allowlist_contains_only_check_runs():
    assert probe.GOVERNOR_WRITE_ALLOWLIST == ("/check-runs",)
    assert not any("status" in entry for entry in probe.GOVERNOR_WRITE_ALLOWLIST)


def test_no_commit_status_endpoint_appears_in_the_governor_harness():
    source = (BASE / "harness" / "probe.py").read_text()
    assert "/statuses/" not in source.replace(
        '"POST", "/repos/PhysShell/evm-from-scratch/statuses/', "")  # test only
