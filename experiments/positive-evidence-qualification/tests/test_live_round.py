"""Replay assertions over the live A3a round.

What GitHub actually did: two providers triggered on one unchanged head
through the App-mediated user carrier, both qualified as advisory-positive,
a frozen bundle evaluated to an internal SUCCESS_CANDIDATE — and a shadow
check published as `failure` anyway, because A3a publishes no green light.
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

import qualify  # noqa: E402

FIXTURES = BASE / "fixtures"
HEAD_H = "a3274d7e7222c3ee9a63c70379a0a06ac5208ba6"
HEAD_H2 = "1d1de2522a602f80f2c696f97d2b2eea931297f2"
EPOCH = "epoch-26cd2742db0dab2c"


def load(name):
    return json.loads((FIXTURES / name).read_text())


# --- freeze and lineage -----------------------------------------------------

def test_round_was_frozen_before_any_trigger():
    freeze = load("freeze.json")
    assert freeze["head_sha"] == HEAD_H and len(freeze["head_sha"]) == 40
    assert freeze["epoch"]["epoch_id"] == EPOCH
    assert freeze["auth"]["state"] == "AUTHORIZED"
    assert freeze["auth"]["carrier"] == "app_mediated_user"
    assert freeze["decision_rule_revision"] == qualify.DECISION_RULE_REVISION


def test_both_requests_used_the_app_mediated_user_carrier():
    g1 = load("triggers_generation_1.json")
    g2 = load("triggers_generation_2.json")
    assert g1["codex"]["carrier"] == "app_mediated_user"
    assert g2["coderabbit"]["carrier"] == "app_mediated_user"


def test_rate_limit_produced_a_new_generation_not_a_blind_retry():
    g1 = load("triggers_generation_1.json")
    g2 = load("triggers_generation_2.json")
    assert g1["coderabbit"]["generation"] == 1
    assert g2["coderabbit"]["generation"] == 2
    assert g2["coderabbit"]["comment_id"] != g1["coderabbit"]["comment_id"]
    assert g2["coderabbit"]["created_at"] > g1["coderabbit"]["created_at"]


def test_bundle_lineage_is_single_epoch_and_single_head():
    bundle = load("evidence_bundle.json")
    assert bundle["bundle_version"] == "PositiveEvidenceBundle-v1"
    assert bundle["head_sha"] == HEAD_H
    assert bundle["epoch_id"] == EPOCH
    assert bundle["requests"]["codex"]["epoch_id"] == EPOCH
    assert bundle["requests"]["coderabbit"]["epoch_id"] == EPOCH
    assert bundle["auth_generation"] == 3
    assert len(bundle["evidence_hash"]) == 64


# --- what each provider actually gave --------------------------------------

def test_codex_qualified_with_a_uniquely_resolving_attestation():
    codex = load("evidence_bundle.json")["observations"]["codex"]
    assert codex["state"] == "CODEX_ADVISORY_POSITIVE"
    assert codex["qualified"] is True
    terminal = codex["terminal_comment"]
    assert terminal["actor_id"] == qualify.CODEX_ACTOR_ID
    assert HEAD_H.startswith(terminal["attested_prefix"])
    assert terminal["resolved_full_sha"] == HEAD_H
    assert terminal["carrier_kind"] == "mutable_advisory_carrier"
    assert codex["findings_seen"] == {"reviews": 0, "inline": 0}


def test_coderabbit_qualified_with_a_range_terminating_at_the_head():
    rabbit = load("evidence_bundle.json")["observations"]["coderabbit"]
    assert rabbit["state"] == "CODERABBIT_ADVISORY_POSITIVE"
    assert rabbit["qualified"] is True
    carrier = rabbit["mutable_advisory_carrier"]
    assert carrier["actor_id"] == qualify.CODERABBIT_ACTOR_ID
    assert HEAD_H.startswith(carrier["review_range"]["to"])
    assert rabbit["findings_seen"]["inline"] == 0
    assert "check-run status is never used" in rabbit["note"]


def test_no_provider_state_is_recorded_as_clean():
    text = (FIXTURES / "evidence_bundle.json").read_text()
    assert "CLEAN" not in text


# --- decision and settling --------------------------------------------------

def test_internal_verdict_was_success_candidate_and_unpublishable():
    decision = load("qualification_decision.json")["decision"]
    assert decision["verdict"] == qualify.SUCCESS_CANDIDATE
    assert decision["reasons"] == []
    assert decision["publishable"] is False


def test_settling_window_was_honoured_and_snapshot_stable():
    settling = load("settling_window.json")
    assert settling["waited_seconds"] == 120
    assert settling["snapshot_stable"] is True
    assert settling["changes"] == []
    assert settling["head_after_settling"] == HEAD_H
    assert settling["decision_after_settling"]["verdict"] == \
        qualify.SUCCESS_CANDIDATE


def test_shadow_check_was_published_as_failure_despite_the_candidate():
    published = load("shadow_check_published.json")
    assert published["conclusion"] == "failure"
    assert published["internal_verdict"] == qualify.SUCCESS_CANDIDATE
    assert published["published_conclusion_is_failure_by_design"] is True
    assert published["head_sha"] == HEAD_H
    assert published["app"] == {"id": 4669438, "slug": "physshell-review-governor"}
    assert published["external_id"] == EPOCH


# --- the live invalidation --------------------------------------------------

def test_head_change_made_the_frozen_bundle_stale():
    invalidation = load("post_round_invalidation.json")
    assert invalidation["bundle_head"] == HEAD_H
    assert invalidation["current_head"] == HEAD_H2
    assert invalidation["verdict_against_new_head"] == qualify.STALE


def test_the_mutable_carrier_really_mutated_under_the_frozen_bundle():
    mutation = load("post_round_invalidation.json")["mutation_check"]
    assert mutation["stable"] is False
    assert mutation["verdict"] == qualify.INVALIDATED
    assert any("carrier body changed" in c for c in mutation["changes"])
    assert any("updated_at changed" in c for c in mutation["changes"])


def test_auth_loss_would_have_invalidated_the_same_bundle():
    assert load("post_round_invalidation.json")["verdict_if_auth_lost"] == \
        qualify.INVALIDATED


def test_fresh_observations_alone_would_have_looked_fine():
    """The trap: re-observing without the frozen head/hash still 'qualifies',
    because the mutated carrier still displays a positive line. Only binding
    the decision to the frozen bundle catches the head change."""
    fresh = load("post_round_invalidation.json")["fresh_observations"]
    assert fresh["codex"]["qualified"] is True
    assert fresh["coderabbit"]["qualified"] is True
    assert load("post_round_invalidation.json")["verdict_against_new_head"] == \
        qualify.STALE
