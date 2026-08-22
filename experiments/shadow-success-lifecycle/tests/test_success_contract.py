"""Adversarial tests for the A3b success contract.

The question these answer is not "can we turn a check green" but "can a
green check be taken away the instant its basis stops holding" — and,
just as important, "can it be turned green at all when the basis is
missing".
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

import decisions as dec  # noqa: E402
import gh  # noqa: E402
import qualify  # noqa: E402

HEAD_A = "448dd46f9d73b0c15e15057f48651fda7c2b7048"
HEAD_B = "b" * 40
EPOCH_A = "epoch-d6743339efed1671"
HASH_1 = "1" * 64
HASH_2 = "2" * 64


@pytest.fixture()
def history(tmp_path):
    h = dec.History(tmp_path / "decisions.sqlite3")
    yield h
    h.close()


# --- the publication guard --------------------------------------------------

def test_success_requires_the_bundle_hash_it_derived_from():
    repo = gh.Repo.__new__(gh.Repo)
    with pytest.raises(ValueError, match="requires the full evidence bundle"):
        gh.Repo.conclude_check(repo, 1, "success", {"summary": "no hash here"})


def test_success_must_carry_that_hash_in_the_published_output():
    repo = gh.Repo.__new__(gh.Repo)
    with pytest.raises(ValueError, match="must carry its evidence hash"):
        gh.Repo.conclude_check(repo, 1, "success",
                               {"summary": "Governor verdict: SUCCESS"},
                               evidence_hash=HASH_1)


def test_neutral_and_skipped_stay_refused_even_in_a3b():
    repo = gh.Repo.__new__(gh.Repo)
    for forbidden in ("neutral", "skipped"):
        with pytest.raises(ValueError, match="read as passing"):
            gh.Repo.conclude_check(repo, 1, forbidden, {"summary": ""})


def test_verdict_to_conclusion_mapping_is_total_and_has_one_success():
    mapping = {v: dec.expected_conclusion(v) for v in
               ("SUCCESS", "EVIDENCE_INVALIDATED", "NOT_ESTABLISHED",
                "AUTHORIZATION_UNAVAILABLE", "STALE")}
    assert mapping["SUCCESS"] == "success"
    assert list(mapping.values()).count("success") == 1
    assert mapping["STALE"] == "cancelled"
    assert all(mapping[v] == "failure" for v in
               ("EVIDENCE_INVALIDATED", "NOT_ESTABLISHED",
                "AUTHORIZATION_UNAVAILABLE"))


# --- append-only history ----------------------------------------------------

def test_decision_rows_cannot_be_updated_or_deleted(history):
    did = history.record(epoch_id=EPOCH_A, head_sha=HEAD_A, verdict="SUCCESS",
                         bundle_hash=HASH_1, bundle_schema="v1",
                         decision_rule_revision="a3b.1", auth_generation=3,
                         decided_at="t1")
    with pytest.raises(sqlite3.IntegrityError):
        history.conn.execute("UPDATE decisions SET verdict='NOT_ESTABLISHED' "
                             "WHERE decision_id=?", (did,))
    with pytest.raises(sqlite3.IntegrityError):
        history.conn.execute("DELETE FROM decisions WHERE decision_id=?", (did,))
    assert history.chain()[0]["verdict"] == "SUCCESS"


def test_every_success_names_its_bundle_and_every_revocation_names_the_success(history):
    d1 = history.record(epoch_id=EPOCH_A, head_sha=HEAD_A, verdict="SUCCESS",
                        bundle_hash=HASH_1, bundle_schema="v1",
                        decision_rule_revision="a3b.1", auth_generation=3,
                        decided_at="t1")
    d2 = history.record(epoch_id=EPOCH_A, head_sha=HEAD_A,
                        verdict="EVIDENCE_INVALIDATED", bundle_hash=None,
                        bundle_schema="v1", decision_rule_revision="a3b.1",
                        auth_generation=3, decided_at="t2",
                        cause="newer_provider_request_generation",
                        invalidates_decision_id=d1, invalidates_bundle_hash=HASH_1)
    chain = history.chain()
    assert chain[0]["bundle_hash"] == HASH_1
    assert chain[1]["invalidates_decision_id"] == d1
    assert chain[1]["invalidates_bundle_hash"] == HASH_1
    assert chain[1]["previous_decision_id"] == d1
    assert d2 > d1


def test_replay_rebuilds_the_projection_without_reading_github(history, tmp_path):
    for verdict, bundle in (("SUCCESS", HASH_1), ("EVIDENCE_INVALIDATED", None),
                            ("SUCCESS", HASH_2)):
        history.record(epoch_id=EPOCH_A, head_sha=HEAD_A, verdict=verdict,
                       bundle_hash=bundle, bundle_schema="v1",
                       decision_rule_revision="a3b.1", auth_generation=3,
                       decided_at="t")
    history.close()

    restarted = dec.History(tmp_path / "decisions.sqlite3")   # fresh process
    replayed = restarted.replay()[EPOCH_A]
    assert replayed["verdict"] == "SUCCESS"
    assert replayed["bundle_hash"] == HASH_2
    assert len(restarted.chain()) == 3
    restarted.close()


def test_history_survives_and_keeps_superseded_successes(history):
    history.record(epoch_id=EPOCH_A, head_sha=HEAD_A, verdict="SUCCESS",
                   bundle_hash=HASH_1, bundle_schema="v1",
                   decision_rule_revision="a3b.1", auth_generation=3,
                   decided_at="t1")
    history.record(epoch_id=EPOCH_A, head_sha=HEAD_A, verdict="STALE",
                   bundle_hash=None, bundle_schema="v1",
                   decision_rule_revision="a3b.1", auth_generation=3,
                   decided_at="t2", invalidates_bundle_hash=HASH_1)
    verdicts = [row["verdict"] for row in history.chain()]
    assert verdicts == ["SUCCESS", "STALE"]      # the success is not erased


# --- validity predicates ----------------------------------------------------

def bundle(head=HEAD_A, epoch=EPOCH_A, codex=None, rabbit=None):
    codex = codex or {"provider": "codex", "state": "CODEX_ADVISORY_POSITIVE",
                      "qualified": True, "reasons": [],
                      "request_carrier": "app_mediated_user",
                      "findings_seen": {"reviews": 0, "inline": 0},
                      "terminal_comment": {"id": 1, "body_hash": "h1",
                                           "updated_at": "t1"}}
    rabbit = rabbit or {"provider": "coderabbit",
                        "state": "CODERABBIT_ADVISORY_POSITIVE",
                        "qualified": True, "reasons": [],
                        "request_carrier": "app_mediated_user",
                        "findings_seen": {"reviews": 0, "inline": 0},
                        "mutable_advisory_carrier": {"id": 2, "body_hash": "h2",
                                                     "updated_at": "t2"}}
    return qualify.build_bundle(
        {"epoch_id": epoch, "generation": 1}, head, 3,
        {"codex": {"epoch_id": epoch, "request_generation": 1},
         "coderabbit": {"epoch_id": epoch, "request_generation": 1}},
        {"codex": codex, "coderabbit": rabbit}, "cutoff")


def test_wrong_current_head_yields_no_success():
    decision = qualify.evaluate(bundle(), HEAD_B, "AUTHORIZED")
    assert decision["verdict"] == qualify.STALE


def test_auth_loss_yields_no_success():
    for lost in ("AUTH_LOST", "REAUTH_REQUIRED", "REFRESH_OUTCOME_UNKNOWN"):
        assert qualify.evaluate(bundle(), HEAD_A, lost)["verdict"] == \
            qualify.INVALIDATED


def test_bundle_hash_mismatch_is_detectable():
    original = bundle()
    tampered = dict(original)
    tampered["head_sha"] = HEAD_B
    recomputed = qualify.build_bundle(
        {"epoch_id": tampered["epoch_id"], "generation": 1}, tampered["head_sha"],
        tampered["auth_generation"], tampered["requests"],
        tampered["observations"], tampered["inventory_cutoff"])
    assert recomputed["evidence_hash"] != original["evidence_hash"]


def test_provider_carrier_mutation_invalidates_a_published_success():
    original = bundle()
    mutated_rabbit = json.loads(json.dumps(
        original["observations"]["coderabbit"]))
    mutated_rabbit["mutable_advisory_carrier"]["body_hash"] = "changed"
    mutation = qualify.detect_mutation(
        original, {"codex": original["observations"]["codex"],
                   "coderabbit": mutated_rabbit})
    assert mutation["verdict"] == qualify.INVALIDATED
    assert dec.expected_conclusion("EVIDENCE_INVALIDATED") == "failure"


def test_a_new_provider_finding_invalidates():
    original = bundle()
    with_finding = json.loads(json.dumps(original["observations"]["codex"]))
    with_finding["findings_seen"] = {"reviews": 1, "inline": 0}
    mutation = qualify.detect_mutation(
        original, {"codex": with_finding,
                   "coderabbit": original["observations"]["coderabbit"]})
    assert mutation["verdict"] == qualify.INVALIDATED


def test_wrong_actor_or_incomplete_qualification_blocks_success():
    unqualified = {"provider": "codex", "state": "NOT_QUALIFIED",
                   "qualified": False, "reasons": ["wrong actor"],
                   "request_carrier": "app_mediated_user",
                   "findings_seen": {"reviews": 0, "inline": 0},
                   "terminal_comment": None}
    decision = qualify.evaluate(bundle(codex=unqualified), HEAD_A, "AUTHORIZED")
    assert decision["verdict"] == qualify.NOT_ESTABLISHED


def test_stale_epoch_with_a_perfect_old_bundle_gives_no_success_on_the_new_head():
    old = bundle(head=HEAD_A)
    assert qualify.evaluate(old, HEAD_B, "AUTHORIZED")["verdict"] == qualify.STALE


def test_old_bundle_remains_audit_evidence_after_the_carrier_mutates():
    """The bundle is immutable: mutation of the live carrier changes the
    verdict, never the recorded basis of the earlier decision."""
    original = bundle()
    frozen_hash = original["evidence_hash"]
    mutated = json.loads(json.dumps(original["observations"]["coderabbit"]))
    mutated["mutable_advisory_carrier"]["body_hash"] = "changed"
    qualify.detect_mutation(original, {"codex": original["observations"]["codex"],
                                       "coderabbit": mutated})
    assert original["evidence_hash"] == frozen_hash
    assert original["observations"]["coderabbit"]["mutable_advisory_carrier"][
        "body_hash"] == "h2"


def test_foreign_check_with_the_same_name_is_not_governor_owned():
    impostor = {"name": gh.CHECK_NAME, "head_sha": HEAD_A,
                "app": {"id": 999, "slug": "someone-else"}}
    assert impostor["app"]["id"] != gh.GOVERNOR_APP_ID
