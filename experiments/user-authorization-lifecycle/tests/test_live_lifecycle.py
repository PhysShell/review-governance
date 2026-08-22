"""Replay tests over the live A1c captures.

Everything asserted here was observed against GitHub: rotation, old-pair
invalidation, carrier stability across three credential generations,
revocation behaviour, and installation independence. The fixtures are also
asserted to be free of credential material.
"""
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

FIXTURES = BASE / "fixtures"
EXPECTED_USER = {"login": "PhysShell", "id": 45852143, "type": "User"}
GOVERNOR = {"id": 4669438, "slug": "physshell-review-governor"}
# Credential *shapes*, not the word "authorization" — which legitimately
# appears in prose like `github_app_device_flow_reauthorization`. A prefix
# class on its own (`"ghu_"`) is evidence; a prefix followed by token body
# is a leak.
SECRET_SHAPES = re.compile(
    r"gh[uprs]_[A-Za-z0-9]{16,}"            # real token bodies
    r"|\"authorization\"\s*:"               # captured HTTP header
    r"|Authorization:\s*\S"                 # raw header line
    r"|Bearer\s+[A-Za-z0-9._-]{16,}"        # bearer with a token after it
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",   # JWT
    re.IGNORECASE)


def load(name):
    return json.loads((FIXTURES / name).read_text())


# --- refresh rotation -------------------------------------------------------

def test_single_refresh_rotated_the_pair():
    refresh = load("refresh_G0_to_G1.json")
    assert refresh["from_generation"] == 0
    assert refresh["outcome"] == "ROTATED"
    assert refresh["response"]["granted"] is True
    assert refresh["response"]["expires_in"] == 28800
    assert refresh["response"]["refresh_token_expires_in"] == 15897600
    assert refresh["committed_generation"]["generation"] == 1


def test_new_access_works_and_old_access_is_dead():
    assert load("probe_G1_access_after_refresh.json")["result"]["ok"] is True
    old = load("probe_G0_access_after_refresh.json")["result"]
    assert old["ok"] is False
    assert old["status"] == 401
    assert old["error"]["message"] == "Bad credentials"


def test_generations_are_distinct_by_fingerprint():
    generations = load("credential_generations.json")["generations"]
    access = [g["access_fingerprint"] for g in generations]
    refresh = [g["refresh_fingerprint"] for g in generations]
    assert len(set(access)) == len(access) == 3
    assert len(set(refresh)) == len(refresh) == 3
    assert all(g["access_prefix_class"] == "ghu_" for g in generations)
    assert all(g["refresh_prefix_class"] == "ghr_" for g in generations)


# --- the rejection surface (A1c-c1) ----------------------------------------

def test_consumed_refresh_is_rejected_with_http_200_and_a_misleading_error():
    reuse = load("probe_G0_refresh_reuse.json")["result"]
    assert reuse["granted"] is False
    assert reuse["http_status"] == 200          # failure arrives as 200
    assert reuse["error"] == "incorrect_client_credentials"
    assert reuse["error"] != "bad_refresh_token"


def test_never_issued_refresh_token_produces_the_identical_error():
    control = load("probe_bogus_refresh_control.json")["result"]
    reuse = load("probe_G0_refresh_reuse.json")["result"]
    assert control["error"] == reuse["error"]
    assert control["error_description"] == reuse["error_description"]
    assert control["granted"] is False


# --- carrier stability ------------------------------------------------------

def test_carrier_identical_across_all_three_generations():
    carriers = [load(f"carrier_{label}.json") for label in ("C0", "C1", "C2")]
    for carrier in carriers:
        assert carrier["comment"]["user"] == EXPECTED_USER
        assert carrier["comment"]["performed_via_github_app"] == GOVERNOR
        assert carrier["matches_expected_user"] is True
        assert carrier["matches_governor_mediation"] is True
    assert [c["credential_generation"] for c in carriers] == [0, 1, 2]
    assert len({c["comment"]["id"] for c in carriers}) == 3


# --- revocation and installation independence ------------------------------

def test_revocation_detected_by_401_on_use():
    detection = load("auth_loss_detection.json")
    assert detection["status"] == 401
    assert detection["error"]["message"] == "Bad credentials"
    assert detection["detection_method"].startswith("access token 401")


def test_revoked_refresh_token_is_also_rejected():
    result = load("probe_G1_refresh_after_revocation.json")["result"]
    assert result["granted"] is False


def test_installation_identity_survives_every_phase():
    for name in ("installation_before_refresh", "installation_before_revocation",
                 "installation_after_revocation", "installation_after_reauth"):
        probe = load(f"{name}.json")
        assert probe["usable"] is True, name
        assert probe["pr_probe_status"] == 200, name


def test_reauthorization_restored_a_working_generation():
    assert load("probe_G2_access_after_reauth.json")["result"]["ok"] is True
    assert load("credential_generations.json")["current_generation"] == 2


def test_webhook_detection_was_not_available_in_this_environment():
    feasibility = load("webhook_feasibility.json")
    assert feasibility["webhook_config_status"] == 404
    assert feasibility["app_events_subscribed"] == []


# --- no credential material anywhere ---------------------------------------

def test_secret_scanner_catches_real_credential_shapes():
    """Positive control: the tightened pattern must still catch leaks."""
    leaks = [
        'ghu_' + 'A1b2C3d4E5f6G7h8I9j0' * 2,
        'ghr_' + 'Z9y8X7w6V5u4T3s2R1q0' * 2,
        '{"authorization": "Bearer ghs_something"}',
        'Authorization: Bearer ' + 'k' * 40,
        'eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJpYXQiOjE3MDAwMDAwMDB9',
    ]
    for leak in leaks:
        assert SECRET_SHAPES.search(leak), leak[:24]
    for benign in ['"access_prefix_class": "ghu_"',
                   '"obtained_via": "github_app_device_flow_reauthorization"',
                   '"access_fingerprint": "3ab6f5265858095a"',
                   'github_app_authorization']:
        assert not SECRET_SHAPES.search(benign), benign


def test_no_fixture_contains_credential_material():
    for path in sorted(FIXTURES.glob("*.json")):
        text = path.read_text()
        assert not SECRET_SHAPES.search(text), f"possible secret in {path.name}"
