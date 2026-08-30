"""A6f: the positive path, closed offline.

Seven defects sat between ACCEPT and a green check, and six of them were
in code that already had passing tests — because the tests asserted the
shape of a record rather than what the record was allowed to claim.
"""
import datetime
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "harness"))
sys.path.insert(0, str(HERE.parents[0] / "operational-readiness" / "harness"))

import auth_policy as ap  # noqa: E402
import auth_state  # noqa: E402
import collector  # noqa: E402
import evidence  # noqa: E402
import gate as gate_mod  # noqa: E402
import predicates  # noqa: E402
import publish  # noqa: E402
import rounds  # noqa: E402
import snapshots  # noqa: E402
import triggers  # noqa: E402

REPO = "PhysShell/evm-from-scratch"
A = "7ad19f5e72a13a8fbd10ba9f6a2b0ea4bf430f52"
B = "b" * 40
EPOCH = "pe-5a41d21a944c13836e1cc6ff"


# `store`, `snaps` and `fresh` now come from conftest, and `accept` goes
# through the observation the writer gates over.
from conftest import accept, captured_baseline  # noqa: E402


# --- 1. durable acceptance -----------------------------------------------------

def test_acceptance_survives_the_process(store, fresh, tmp_path):
    acc = accept(store, fresh)
    store.close()
    reopened = rounds.RoundStore(tmp_path / "rounds.sqlite3")
    assert reopened.acceptance(acc["acceptance_id"])["head_sha"] == A
    assert reopened.acceptance(acc["acceptance_id"])["auth_generation"] == 5
    reopened.close()


def test_acceptance_records_the_authorization_observation(store, fresh):
    acc = accept(store, fresh)
    assert acc["auth_observation_id"] is not None
    assert acc["state"] == rounds.ACCEPTED


def test_a_stale_permission_cannot_produce_an_acceptance(store, tmp_path):
    a = auth_state.AuthStore(tmp_path / "old.sqlite3")
    a.record(state="AUTHORIZED", auth_generation=5,
             observed_at="2020-01-01T00:00:00Z", source="refresh")
    stale = ap.evaluate(a)
    a.close()
    with pytest.raises(rounds.RoundError):
        accept(store, stale)
    assert store.acceptances_for(REPO, 32) == []


def test_an_acceptance_cannot_be_repointed(store, fresh):
    acc = accept(store, fresh)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("UPDATE acceptances SET head_sha=? WHERE acceptance_id=?",
                           (B, acc["acceptance_id"]))
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("DELETE FROM acceptances")


def test_head_move_invalidates_without_repointing(store, fresh):
    acc = accept(store, fresh)
    moved = store.invalidate_for_head_move(REPO, 32, B)
    assert moved[0]["was_for_head"] == A and moved[0]["current_head"] == B
    assert store.acceptance(acc["acceptance_id"])["head_sha"] == A
    assert store.current_acceptance(REPO, 32, B) is None


def test_only_an_acceptance_about_this_commit_counts(store, fresh):
    accept(store, fresh, head=A)
    assert store.current_acceptance(REPO, 32, A) is not None
    assert store.current_acceptance(REPO, 32, B) is None


# --- 2. intent before the network ----------------------------------------------

def test_intent_is_recorded_before_any_post(store, snaps, fresh):
    acc = accept(store, fresh)
    row = _intent(store, snaps, fresh)
    assert row["request_outcome"] == rounds.INTENT_RECORDED
    assert row["request_carrier_id"] is None


def test_a_provider_is_never_contacted_without_a_recorded_intent(store, snaps, fresh):
    posted = {"n": 0}

    def post(path, body):
        posted["n"] += 1
        return 201, {"id": 1}

    with pytest.raises(triggers.TriggerRefused):
        triggers.send(post, store, request_row=None, permission=fresh,
                      head_sha=A)
    assert posted["n"] == 0


def test_a_request_for_another_head_is_refused(store, snaps, fresh):
    acc = accept(store, fresh)
    with pytest.raises(rounds.RoundError):
        store.record_intent(
            acceptance_id=acc["acceptance_id"], repo=REPO, pr_number=32,
            provider="codex", generation=1, requested_for_head=B,
            permission=fresh, baseline=captured_baseline(snaps,
                                                         provider="codex"))


def test_recorded_intent_cannot_be_rewritten(store, snaps, fresh):
    row = _intent(store, snaps, fresh)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "UPDATE provider_requests SET requested_for_head=? WHERE request_id=?",
            (B, row["request_id"]))


# --- 3. trigger adapters, injected transport only ------------------------------

def _intent(store, snaps, fresh, provider="codex"):
    acc = accept(store, fresh)
    return store.record_intent(
        acceptance_id=acc["acceptance_id"], repo=REPO, pr_number=32,
        provider=provider, generation=1, requested_for_head=A,
        permission=fresh, baseline=captured_baseline(snaps, provider=provider))


@pytest.mark.parametrize("provider", ["codex", "coderabbit"])
def test_adapter_sends_exactly_one_post(store, snaps, fresh, provider):
    calls = []

    def post(path, body):
        calls.append((path, body))
        return 201, {"id": 777}

    row = _intent(store, snaps, fresh, provider)
    r = triggers.send(post, store, request_row=row, permission=fresh,
                      head_sha=A)
    assert len(calls) == 1
    assert triggers.INVOCATION[provider] in calls[0][1]["body"]
    assert A in calls[0][1]["body"]
    assert r["state"] == rounds.SENT and r["request_carrier_id"] == 777
    assert store.request(row["request_id"])["request_carrier_id"] == 777


def test_transport_failure_is_unknown_and_never_retried(store, snaps, fresh):
    calls = {"n": 0}

    def post(path, body):
        calls["n"] += 1
        raise TimeoutError("boom")

    row = _intent(store, snaps, fresh)
    r = triggers.send(post, store, request_row=row, permission=fresh,
                      head_sha=A)
    assert calls["n"] == 1
    assert r["state"] == rounds.OUTCOME_UNKNOWN
    assert r["retry_performed"] is False
    assert store.request(row["request_id"])["request_outcome"] == rounds.OUTCOME_UNKNOWN


def test_a_response_without_a_carrier_id_is_unknown(store, snaps, fresh):
    row = _intent(store, snaps, fresh)
    r = triggers.send(lambda p, b: (201, {}), store, request_row=row,
                      permission=fresh, head_sha=A)
    assert r["state"] == rounds.OUTCOME_UNKNOWN


def test_a_stale_permission_stops_the_trigger_before_the_network(store, snaps,
                                                                 fresh, tmp_path):
    a = auth_state.AuthStore(tmp_path / "old2.sqlite3")
    a.record(state="AUTHORIZED", auth_generation=5,
             observed_at="2020-01-01T00:00:00Z", source="refresh")
    stale = ap.evaluate(a)
    a.close()
    row = _intent(store, snaps, fresh)
    calls = {"n": 0}
    with pytest.raises(triggers.TriggerRefused):
        triggers.send(lambda p, b: calls.update(n=calls["n"] + 1) or (201, {"id": 1}),
                      store, request_row=row, permission=stale, head_sha=A)
    assert calls["n"] == 0


# --- 4/5. admissibility, and the live pre-existing carrier ---------------------

def carrier(**over):
    """A *parsed* observation, in the shape `parsers` now produces.

    The old fixture put `performed_via_github_app` on it, which the parsers
    never emitted — so the collector was tested against a shape production
    could not hand it. Identity is the three fields the parser preserves.
    """
    base = {"id": 900, "created_at": "2026-08-29T14:00:00Z",
            "author_app_id": 347564, "author_user_id": 136622811,
            "author_login": "coderabbitai[bot]",
            "head_claim": A, "head_binding": collector.ATTESTED,
            "body": "Review completed", "generation": 1,
            # association evidence is now required rather than assumed:
            # a run id absent from the pre-request baseline
            "new_run_ids": ["a3d2af24-8685-49a2-9e6e-728a59d8dcd4"],
            "carrier_was_rewritten": True,
            "baseline_digest_for_carrier": "old", "observed_digest": "new",
            "updated_at": "2026-08-29T14:00:00Z"}
    base.update(over)
    return base


def request_row(**over):
    base = {"provider": "coderabbit", "requested_for_head": A,
            "intent_recorded_at": "2026-08-29T13:59:00Z",
            "request_carrier_id": 500, "acceptance_id": "acc-x",
            "baseline_id": "base-x"}
    base.update(over)
    return base


def test_the_real_preexisting_coderabbit_comment_is_inadmissible():
    """Comment 5462558501 on #32, posted 13:01:15Z — six seconds after the
    PR opened and before the Governor asked anything. If a carrier like
    this can answer a later request, the lineage is decorative."""
    real = carrier(id=5462558501, created_at="2026-08-29T13:01:15Z",
                   updated_at="2026-08-29T13:01:15Z",
                   carrier_was_rewritten=False,
                   baseline_digest_for_carrier=None, observed_digest=None,
                   body="skip review by coderabbit.ai")
    later = request_row(intent_recorded_at="2026-08-29T14:00:00Z")
    verdict = collector.admissibility(real, later, head_sha=A, generation=1)
    assert verdict["admissible"] is False
    assert verdict["state"] == collector.PREEXISTING
    assert any(r["code"] == collector.PREEXISTING for r in verdict["refusals"])


def test_a_carrier_from_the_wrong_provider_is_inadmissible():
    v = collector.admissibility(carrier(author_app_id=4669438),
                                request_row(), head_sha=A, generation=1)
    assert v["state"] == collector.WRONG_PROVIDER


def test_a_carrier_for_another_head_is_inadmissible():
    v = collector.admissibility(carrier(head_claim=B), request_row(),
                                head_sha=A, generation=1)
    assert any(r["code"] == collector.WRONG_HEAD for r in v["refusals"])


def test_a_carrier_of_another_generation_is_inadmissible():
    v = collector.admissibility(carrier(generation=2), request_row(),
                                head_sha=A, generation=1)
    assert any(r["code"] == collector.WRONG_GENERATION for r in v["refusals"])


def test_a_request_with_no_carrier_id_admits_nothing():
    v = collector.admissibility(carrier(),
                                request_row(request_carrier_id=None),
                                head_sha=A, generation=1)
    assert any(r["code"] == collector.UNASSOCIATED for r in v["refusals"])


def test_an_admissible_carrier_passes():
    v = collector.admissibility(carrier(), request_row(), head_sha=A,
                                generation=1)
    assert v["admissible"] is True and v["state"] == collector.ADMISSIBLE


def test_two_admissible_carriers_are_ambiguous_not_a_preference():
    out = collector.collect([carrier(id=1), carrier(id=2)], request_row(),
                            head_sha=A, generation=1)
    assert out["state"] == "AMBIGUOUS"


def test_collection_of_only_preexisting_carriers_finds_nothing():
    out = collector.collect(
        [carrier(id=5462558501, created_at="2026-08-29T13:01:15Z",
                 updated_at="2026-08-29T13:01:15Z",
                 carrier_was_rewritten=False,
                 baseline_digest_for_carrier=None, observed_digest=None)],
        request_row(intent_recorded_at="2026-08-29T14:00:00Z"),
        head_sha=A, generation=1)
    assert out["state"] == "NO_ADMISSIBLE_CARRIER"


# --- 6. what the answer says ---------------------------------------------------

def test_findings_cannot_qualify_positive():
    """The sharpest of the seven: a provider could report a critical bug on
    exactly the right commit and be scored ADVISORY_POSITIVE."""
    for provider in ("codex", "coderabbit"):
        v = predicates.evaluate(provider, {
            "id": 1, "body": "found a critical bug", "review_ran": True,
            "findings": [{"severity": "critical"}], "head_claim": A})
        assert v["state"] == predicates.NOT_POSITIVE
        assert v["findings_count"] == 1


def test_a_skipped_review_is_not_positive():
    v = predicates.evaluate("coderabbit", {
        "id": 5462558501, "body": "skip review by coderabbit.ai",
        "review_ran": False, "findings": [], "head_claim": A})
    assert v["state"] == predicates.NOT_POSITIVE
    assert any("skipped" in r for r in v["reasons"])


def test_review_completed_is_not_a_findings_statement():
    """A1b-R / A3a: the phrase describes the run, not the result."""
    v = predicates.evaluate("coderabbit", {
        "id": 1, "body": "Review completed. status: success",
        "review_ran": True, "findings": None, "head_claim": A})
    assert v["state"] == predicates.NOT_POSITIVE
    assert any("describes the run" in r for r in v["reasons"])


def test_a_review_that_did_not_run_is_not_positive():
    v = predicates.evaluate("codex", {
        "id": 1, "body": "no issues", "review_ran": False, "findings": [],
        "head_claim": A})
    assert v["state"] == predicates.NOT_POSITIVE


def test_rate_limited_is_not_a_verdict():
    v = predicates.evaluate("coderabbit", {
        "id": 1, "body": "Review rate limited", "review_ran": True,
        "findings": [], "head_claim": A})
    assert v["state"] == predicates.NOT_POSITIVE


def test_a_clean_reviewed_answer_is_positive():
    v = predicates.evaluate("codex", {
        "id": 1, "body": "no issues found", "review_ran": True,
        "findings": [], "head_claim": A})
    assert v["state"] == predicates.POSITIVE and v["findings_count"] == 0
    assert v["snapshot_digest"]


def test_the_snapshot_moves_when_the_comment_does():
    """A mutable carrier that rewrites itself must change the digest, or
    the bundle commits to nothing."""
    base = {"id": 1, "body": "no issues found", "review_ran": True,
            "findings": [], "head_claim": A}
    first = predicates.evaluate("codex", base)["snapshot_digest"]
    second = predicates.evaluate("codex", {**base, "body": "actually, a bug"})
    assert second["snapshot_digest"] != first


# --- 7. the bundle commits to evidence ----------------------------------------

STANDING = {"acceptance_id": "acc-x", "head_sha": A}


def qualified_record(snaps, provider, body="no issues found", findings=(),
                     generation=1):
    """A record whose digest comes from the durable row, not a re-derivation.

    The bundle used to cite the digest `predicates` computed over its own
    normalization, which committed it to a value the store never held. So
    the payload is frozen first and the record quotes the stored row.
    """
    payload = {"id": 900, "provider": provider, "body": body,
               "review_ran": True, "findings": list(findings), "head_claim": A}
    snap = snaps.freeze(repo=REPO, pr_number=32, head_sha=A, provider=provider,
                        generation=generation, request_id=f"req-{provider}",
                        payload=payload, frozen_at="2026-08-30T00:00:00Z")
    return {"provider": provider, "generation": generation,
            "requested_for_head": A, "state": "ANSWERED",
            "request_id": f"req-{provider}", "request_carrier_id": 500,
            "acceptance_id": STANDING["acceptance_id"], "baseline_id": "base-x",
            "terminal": {"carrier_id": 900, "state": "ADMISSIBLE",
                         "admissible": True},
            "predicate": predicates.evaluate(provider, payload),
            "snapshot_id": snap["snapshot_id"],
            "snapshot_digest": snap["snapshot_digest"]}


def test_bundle_carries_the_snapshot_digest_not_a_boolean(fresh, snaps):
    rec = qualified_record(snaps, "codex")
    b = evidence.build_bundle(repo=REPO, pr_number=32, head_sha=A,
                              lineage_records=[rec], auth_generation=5,
                              acceptance_id=STANDING["acceptance_id"])
    p = b["providers"][0]
    assert p["snapshot_digest"] and p["terminal_carrier_id"] == 900
    assert p["predicate_schema"] == predicates.SCHEMA_REVISION
    assert p["snapshot_digest"] == snaps.snapshot(rec["snapshot_id"])["snapshot_digest"]


def test_bundle_hash_moves_when_the_evidence_moves(fresh, snaps):
    a = evidence.build_bundle(repo=REPO, pr_number=32, head_sha=A,
                              lineage_records=[qualified_record(snaps, "codex")],
                              auth_generation=5,
                              acceptance_id=STANDING["acceptance_id"])
    changed = qualified_record(snaps, "codex", body="on reflection, a bug",
                               findings=[{"x": 1}], generation=2)
    b = evidence.build_bundle(repo=REPO, pr_number=32, head_sha=A,
                              lineage_records=[changed], auth_generation=5,
                              acceptance_id=STANDING["acceptance_id"])
    assert a["bundle_hash"] != b["bundle_hash"]


def test_inadmissible_evidence_cannot_reduce_to_success(fresh, snaps):
    rec = qualified_record(snaps, "codex")
    rec["terminal"] = {"carrier_id": 900, "state": collector.PREEXISTING,
                       "admissible": False}
    other = qualified_record(snaps, "coderabbit")
    b = evidence.build_bundle(repo=REPO, pr_number=32, head_sha=A,
                              lineage_records=[rec, other], auth_generation=5,
                              acceptance_id=STANDING["acceptance_id"])
    red = evidence.reduce(b, current_head_sha=A, permission=fresh,
                          standing_acceptance=STANDING)
    assert red["verdict"] == evidence.NOT_ESTABLISHED
    assert any("not admissible" in r for r in red["refusals"])


def test_findings_in_the_bundle_cannot_reduce_to_success(fresh, snaps):
    rec = qualified_record(snaps, "codex", body="bug", findings=[{"x": 1}])
    b = evidence.build_bundle(
        repo=REPO, pr_number=32, head_sha=A, auth_generation=5, acceptance_id=STANDING["acceptance_id"],
        lineage_records=[rec, qualified_record(snaps, "coderabbit")])
    red = evidence.reduce(b, current_head_sha=A, permission=fresh,
                          standing_acceptance=STANDING)
    assert red["verdict"] == evidence.NOT_ESTABLISHED
    assert any("findings reported" in r for r in red["refusals"])


def test_a_bundle_without_a_frozen_snapshot_cannot_reduce_to_success(fresh, snaps):
    rec = qualified_record(snaps, "codex")
    rec["snapshot_id"] = rec["snapshot_digest"] = None
    b = evidence.build_bundle(
        repo=REPO, pr_number=32, head_sha=A, auth_generation=5, acceptance_id=STANDING["acceptance_id"],
        lineage_records=[rec, qualified_record(snaps, "coderabbit")])
    red = evidence.reduce(b, current_head_sha=A, permission=fresh,
                          standing_acceptance=STANDING)
    assert red["verdict"] == evidence.NOT_ESTABLISHED
    assert any("commits to nothing" in r for r in red["refusals"])


def test_a_fully_qualified_bundle_reduces_to_success(fresh, snaps):
    b = evidence.build_bundle(
        repo=REPO, pr_number=32, head_sha=A, auth_generation=5, acceptance_id=STANDING["acceptance_id"],
        lineage_records=[qualified_record(snaps, "codex"),
                         qualified_record(snaps, "coderabbit")])
    red = evidence.reduce(b, current_head_sha=A, permission=fresh,
                          standing_acceptance=STANDING)
    assert red["verdict"] == evidence.SUCCESS, red["refusals"]


# --- 8. success projection ------------------------------------------------------

class FakeEpochs:
    def record_decision(self, **kw):
        return 1

    def project(self, **kw):
        self.last = kw


def _ok(fresh, snaps):
    b = evidence.build_bundle(
        repo=REPO, pr_number=32, head_sha=A, auth_generation=5, acceptance_id=STANDING["acceptance_id"],
        lineage_records=[qualified_record(snaps, "codex"),
                         qualified_record(snaps, "coderabbit")])
    return b, evidence.reduce(b, current_head_sha=A, permission=fresh,
                              standing_acceptance=STANDING)


def _health(all_fresh=True, not_fresh=(), head=A):
    return {"observations": {n: {"state": "FRESH" if n not in not_fresh
                                 else "STALE", "age_seconds": 5, "bound": 120}
                             for n in ("runtime", "reconciliation", "watchdog")},
            "all_fresh": all_fresh, "not_fresh": list(not_fresh),
            # Since A6f-c3 a health set must say which candidate it is
            # about; a recent pass over other PRs is not this PR's proof.
            "candidate_bound": True,
            "candidate": {"repo": REPO, "pr_number": 32, "head_sha": head}}


HEALTH = _health()


def test_success_may_not_create_a_second_carrier(fresh, snaps):
    b, red = _ok(fresh, snaps)
    with pytest.raises(publish.PublishRefused):
        publish.publish(lambda *a, **k: (201, {"id": 1}), repo=REPO,
                        epoch_id=EPOCH, head_sha=A, conclusion="success",
                        bundle=b, reduction=red, current_head_sha=A,
                        permission=fresh, store=FakeEpochs(),
                        existing_run=None, health=HEALTH)


@pytest.mark.parametrize("stale_component",
                         ["runtime", "reconciliation", "watchdog"])
def test_stale_runtime_health_refuses_success(fresh, snaps, stale_component):
    b, red = _ok(fresh, snaps)
    health = _health(all_fresh=False, not_fresh=[stale_component])
    checked = publish.guard(reduction=red, bundle=b, current_head_sha=A,
                            permission=fresh, health=health,
                            existing_run=99104297860)
    assert checked["may_publish_success"] is False
    assert any(stale_component in r for r in checked["refusals"])


def test_success_confirmed_only_on_full_identity(fresh, snaps):
    b, red = _ok(fresh, snaps)

    # The pre-write read sees the failure carrier the success will
    # transition; the readback sees the result.
    seen = {"patched": False}

    def request(method, path, body=None):
        if method == "PATCH":
            seen["patched"] = True
            return 200, {}
        if "/commits/" in path:
            return 200, {"check_runs": [{"id": 99104297860,
                                         "name": "ai/final-review",
                                         "app": {"id": 4669438},
                                         "head_sha": A}]}
        return 200, {"id": 99104297860, "name": "ai/final-review",
                     "app": {"id": 4669438}, "head_sha": A,
                     "external_id": EPOCH,
                     "conclusion": "success" if seen["patched"] else "failure"}

    r = publish.publish(request, repo=REPO, epoch_id=EPOCH, head_sha=A,
                        conclusion="success", bundle=b, reduction=red,
                        current_head_sha=A, permission=fresh,
                        store=FakeEpochs(), existing_run=99104297860,
                        health=HEALTH)
    assert r["state"] == "CONFIRMED" and all(r["identity"].values())


@pytest.mark.parametrize("field,bad", [
    ("app", {"id": 999}), ("head_sha", B), ("external_id", "pe-other"),
    ("name", "ai/something-else"),
])
def test_a_readback_that_differs_in_identity_is_not_confirmed(fresh, snaps, field, bad):
    b, red = _ok(fresh, snaps)
    good = {"id": 99104297860, "name": "ai/final-review",
            "app": {"id": 4669438}, "head_sha": A, "external_id": EPOCH,
            "conclusion": "failure"}
    seen = {"patched": False}

    def request(method, path, body=None):
        if method == "PATCH":
            seen["patched"] = True
            return 200, {}
        if "/commits/" in path:
            return 200, {"check_runs": [{"id": 99104297860,
                                         "name": "ai/final-review",
                                         "app": {"id": 4669438},
                                         "head_sha": A}]}
        # The readback differs in identity; the pre-write read does not,
        # because this test is about believing the write rather than about
        # patching the wrong carrier.
        if seen["patched"]:
            return 200, {**good, field: bad, "conclusion": "success"}
        return 200, good

    r = publish.publish(request, repo=REPO, epoch_id=EPOCH, head_sha=A,
                        conclusion="success", bundle=b, reduction=red,
                        current_head_sha=A, permission=fresh,
                        store=FakeEpochs(), existing_run=99104297860,
                        health=HEALTH)
    assert r["state"] == "FAILED"
    assert not all(r["identity"].values())
