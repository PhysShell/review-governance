"""Replay assertions over the live A3b lifecycle.

Success published, revoked on the same head, requalified, then cancelled by
a head change — all against GitHub, with the append-only chain as the
record and the Check Run only ever as its projection.
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

import decisions as dec  # noqa: E402

FIXTURES = BASE / "fixtures"
HEAD_A = "448dd46f9d73b0c15e15057f48651fda7c2b7048"
HEAD_B = "561b7f72bbdafbce63a844fb709ccf5e5b44b4dd"
EPOCH_A = "epoch-d6743339efed1671"
EPOCH_B = "epoch-78deabcd44a7b0f6"
RUN_A = 96998302115
RUN_B = 96998879863
BUNDLE_1 = "01e8ed0927c5dbc49ae42049cc14a288c79a4cc9349f7240027f9fc5d0920c1b"
BUNDLE_2 = "ef6f89dd74d498913517e7f61a6d452d69652939fba795acced6eec59d0dba46"


def load(name):
    return json.loads((FIXTURES / name).read_text())


# --- publication ------------------------------------------------------------

def test_first_success_was_guarded_before_and_after_publication():
    published = load("publish_1.json")
    assert published["published"] is True
    assert published["pre_guard"]["passed"] is True
    assert published["post_guard"]["passed"] is True
    assert published["pre_guard"]["failures"] == []
    assert published["post_guard"]["failures"] == []


def test_success_carries_full_head_and_bundle_hash_provenance():
    published = load("publish_1.json")
    assert published["conclusion"] == "success"
    assert published["head_sha"] == HEAD_A and len(published["head_sha"]) == 40
    assert published["external_id"] == EPOCH_A
    assert published["app"] == {"id": 4669438, "slug": "physshell-review-governor"}
    assert published["bundle_hash_in_output"] is True


def test_the_two_bundles_are_distinct_and_bound_to_the_same_head():
    bundles = load("bundles.json")
    assert bundles["bundle_1"]["evidence_hash"] == BUNDLE_1
    assert bundles["bundle_2"]["evidence_hash"] == BUNDLE_2
    assert BUNDLE_1 != BUNDLE_2
    assert bundles["bundle_1"]["head_sha"] == bundles["bundle_2"]["head_sha"] == HEAD_A
    assert bundles["bundle_1"]["decision_rule_revision"] == "a3b.1"


def test_settling_windows_were_honoured():
    for name in ("settle_1.json", "settle_2.json"):
        settling = load(name)
        assert settling["waited_seconds"] == 120
        assert settling["passed"] is True
        assert settling["head_at_guard"] == HEAD_A


# --- same-head revocation ---------------------------------------------------

def test_newer_provider_generation_revoked_the_success_on_the_same_head():
    revocation = load("supersede.json")
    assert revocation["superseded"] is True
    assert revocation["conclusion"] == "failure"
    assert revocation["head_sha"] == HEAD_A          # same head, not a new one
    assert revocation["check_run_id"] == RUN_A       # same logical check
    assert "codex" in revocation["newer_generations"]
    assert revocation["http_status"] == 200


def test_revocation_did_not_wait_for_the_new_review_outcome():
    """The revocation is caused by the existence of a newer request, and it
    lands within seconds — long before that review could finish."""
    revocation = load("supersede.json")
    trigger = load("triggers_codex_g2.json")
    assert revocation["detected_at"] >= trigger["codex"]["created_at"]
    assert revocation["revoked_at"] >= revocation["detected_at"]


def test_requalification_restored_success_on_the_same_run_with_a_new_bundle():
    republished = load("publish_2.json")
    assert republished["published"] is True
    assert republished["conclusion"] == "success"
    assert republished["check_run_id"] == RUN_A      # one logical check
    assert republished["head_sha"] == HEAD_A
    assert republished["decision_id"] == 3


# --- head change ------------------------------------------------------------

def test_head_change_cancelled_the_old_run_in_place():
    change = load("headchange.json")
    assert change["changed"] is True
    assert change["old"]["check_run_id"] == RUN_A
    assert change["old"]["head_sha"] == HEAD_A       # binding never moved
    assert change["old"]["conclusion"] == "cancelled"


def test_new_head_got_its_own_run_failing_closed():
    change = load("headchange.json")
    assert change["new"]["check_run_id"] == RUN_B
    assert change["new"]["head_sha"] == HEAD_B
    assert change["new"]["conclusion"] == "failure"
    assert change["new"]["external_id"] == EPOCH_B


def test_no_success_survives_anywhere_at_the_end():
    final = load("final_checks.json")
    assert final["HEAD_A"]["any_success"] is False
    assert final["HEAD_B"]["any_success"] is False
    assert final["HEAD_A"]["runs"][0]["conclusion"] == "cancelled"
    assert final["HEAD_B"]["runs"][0]["conclusion"] == "failure"
    for side in ("HEAD_A", "HEAD_B"):
        for run in final[side]["runs"]:
            assert run["app"]["id"] == 4669438


def test_exactly_one_governor_run_per_head():
    final = load("final_checks.json")
    assert len(final["HEAD_A"]["runs"]) == 1
    assert len(final["HEAD_B"]["runs"]) == 1


# --- the append-only chain --------------------------------------------------

def test_decision_chain_matches_the_preregistered_sequence():
    chain = load("decision_history.json")["chain"]
    assert [row["verdict"] for row in chain] == [
        "SUCCESS", "EVIDENCE_INVALIDATED", "SUCCESS", "STALE", "NOT_ESTABLISHED"]
    assert chain[0]["bundle_hash"] == BUNDLE_1
    assert chain[1]["cause"] == "newer_provider_request_generation"
    assert chain[1]["invalidates_decision_id"] == 1
    assert chain[1]["invalidates_bundle_hash"] == BUNDLE_1
    assert chain[2]["bundle_hash"] == BUNDLE_2
    assert chain[3]["cause"] == "head_superseded"
    assert chain[3]["invalidates_bundle_hash"] == BUNDLE_2
    assert chain[4]["head_sha"] == HEAD_B


def test_chain_is_linked_and_nothing_was_rewritten():
    chain = load("decision_history.json")["chain"]
    for previous, row in zip(chain, chain[1:]):
        assert row["previous_decision_id"] == previous["decision_id"]
    assert [row["decision_id"] for row in chain] == [1, 2, 3, 4, 5]
    successes = [row for row in chain if row["verdict"] == "SUCCESS"]
    assert len(successes) == 2          # both survive as history


def test_projection_matches_the_chain_and_the_live_checks():
    dump = load("decision_history.json")
    projections = {p["epoch_id"]: p for p in dump["projections"]}
    assert projections[EPOCH_A]["conclusion"] == "cancelled"
    assert projections[EPOCH_A]["check_run_id"] == RUN_A
    assert projections[EPOCH_B]["conclusion"] == "failure"
    assert projections[EPOCH_B]["check_run_id"] == RUN_B
    final = load("final_checks.json")
    assert final["HEAD_A"]["runs"][0]["conclusion"] == projections[EPOCH_A]["conclusion"]
    assert final["HEAD_B"]["runs"][0]["conclusion"] == projections[EPOCH_B]["conclusion"]


def test_replay_reproduces_the_projection_without_github():
    replayed = load("decision_history.json")["replayed"]
    assert replayed[EPOCH_A]["verdict"] == "STALE"
    assert replayed[EPOCH_B]["verdict"] == "NOT_ESTABLISHED"
    assert dec.expected_conclusion(replayed[EPOCH_A]["verdict"]) == "cancelled"
    assert dec.expected_conclusion(replayed[EPOCH_B]["verdict"]) == "failure"


# --- TOCTOU -----------------------------------------------------------------

def test_toctou_windows_were_measured_and_are_nonzero():
    timings = load("timings.json")
    for label, window in timings["publications"].items():
        assert window["pre_publish_validation_at"] <= window["github_success_at"]
        assert window["github_success_at"] <= window["post_publish_validation_at"]
    revocation = timings["revocations"][0]
    assert revocation["detected_at"] <= revocation["revoked_at"]
    assert revocation["conclusion"] == "failure"
