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
import predicates  # noqa: E402
import publish  # noqa: E402
import rounds  # noqa: E402
import triggers  # noqa: E402

REPO = "PhysShell/evm-from-scratch"
A = "7ad19f5e72a13a8fbd10ba9f6a2b0ea4bf430f52"
B = "b" * 40
EPOCH = "pe-5a41d21a944c13836e1cc6ff"


@pytest.fixture()
def store(tmp_path):
    s = rounds.RoundStore(tmp_path / "rounds.sqlite3")
    yield s
    s.close()


@pytest.fixture()
def fresh(tmp_path):
    a = auth_state.AuthStore(tmp_path / "auth.sqlite3")
    a.record(state="AUTHORIZED", auth_generation=5,
             observed_at=datetime.datetime.now(
                 datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             source="refresh")
    yield ap.evaluate(a)
    a.close()


def accept(store, fresh, head=A):
    return store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                   head_sha=head, permission=fresh,
                                   preconditions=[])


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

def test_intent_is_recorded_before_any_post(store, fresh):
    acc = accept(store, fresh)
    row = store.record_intent(acceptance_id=acc["acceptance_id"], repo=REPO,
                              pr_number=32, provider="codex", generation=1,
                              requested_for_head=A, permission=fresh)
    assert row["request_outcome"] == rounds.INTENT_RECORDED
    assert row["request_carrier_id"] is None


def test_a_provider_is_never_contacted_without_a_recorded_intent(store, fresh):
    posted = {"n": 0}

    def post(path, body):
        posted["n"] += 1
        return 201, {"id": 1}

    with pytest.raises(triggers.TriggerRefused):
        triggers.send(post, store, request_row=None, permission=fresh,
                      head_sha=A)
    assert posted["n"] == 0


def test_a_request_for_another_head_is_refused(store, fresh):
    acc = accept(store, fresh)
    with pytest.raises(rounds.RoundError):
        store.record_intent(acceptance_id=acc["acceptance_id"], repo=REPO,
                            pr_number=32, provider="codex", generation=1,
                            requested_for_head=B, permission=fresh)


def test_recorded_intent_cannot_be_rewritten(store, fresh):
    acc = accept(store, fresh)
    row = store.record_intent(acceptance_id=acc["acceptance_id"], repo=REPO,
                              pr_number=32, provider="codex", generation=1,
                              requested_for_head=A, permission=fresh)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "UPDATE provider_requests SET requested_for_head=? WHERE request_id=?",
            (B, row["request_id"]))


# --- 3. trigger adapters, injected transport only ------------------------------

def _intent(store, fresh, provider="codex"):
    acc = accept(store, fresh)
    return store.record_intent(acceptance_id=acc["acceptance_id"], repo=REPO,
                               pr_number=32, provider=provider, generation=1,
                               requested_for_head=A, permission=fresh)


@pytest.mark.parametrize("provider", ["codex", "coderabbit"])
def test_adapter_sends_exactly_one_post(store, fresh, provider):
    calls = []

    def post(path, body):
        calls.append((path, body))
        return 201, {"id": 777}

    row = _intent(store, fresh, provider)
    r = triggers.send(post, store, request_row=row, permission=fresh,
                      head_sha=A)
    assert len(calls) == 1
    assert triggers.INVOCATION[provider] in calls[0][1]["body"]
    assert A in calls[0][1]["body"]
    assert r["state"] == rounds.SENT and r["request_carrier_id"] == 777
    assert store.request(row["request_id"])["request_carrier_id"] == 777


def test_transport_failure_is_unknown_and_never_retried(store, fresh):
    calls = {"n": 0}

    def post(path, body):
        calls["n"] += 1
        raise TimeoutError("boom")

    row = _intent(store, fresh)
    r = triggers.send(post, store, request_row=row, permission=fresh,
                      head_sha=A)
    assert calls["n"] == 1
    assert r["state"] == rounds.OUTCOME_UNKNOWN
    assert r["retry_performed"] is False
    assert store.request(row["request_id"])["request_outcome"] == rounds.OUTCOME_UNKNOWN


def test_a_response_without_a_carrier_id_is_unknown(store, fresh):
    row = _intent(store, fresh)
    r = triggers.send(lambda p, b: (201, {}), store, request_row=row,
                      permission=fresh, head_sha=A)
    assert r["state"] == rounds.OUTCOME_UNKNOWN


def test_a_stale_permission_stops_the_trigger_before_the_network(store, fresh,
                                                                 tmp_path):
    a = auth_state.AuthStore(tmp_path / "old2.sqlite3")
    a.record(state="AUTHORIZED", auth_generation=5,
             observed_at="2020-01-01T00:00:00Z", source="refresh")
    stale = ap.evaluate(a)
    a.close()
    row = _intent(store, fresh)
    calls = {"n": 0}
    with pytest.raises(triggers.TriggerRefused):
        triggers.send(lambda p, b: calls.update(n=calls["n"] + 1) or (201, {"id": 1}),
                      store, request_row=row, permission=stale, head_sha=A)
    assert calls["n"] == 0


# --- 4/5. admissibility, and the live pre-existing carrier ---------------------

def carrier(**over):
    base = {"id": 900, "created_at": "2026-08-29T14:00:00Z",
            "performed_via_github_app": {"id": 347564},
            "head_claim": A, "body": "Review completed", "generation": 1,
            # association evidence is now required rather than assumed:
            # a run id absent from the pre-request baseline
            "new_run_ids": ["a3d2af24-8685-49a2-9e6e-728a59d8dcd4"],
            "carrier_was_rewritten": True}
    base.update(over)
    return base


def request_row(**over):
    base = {"provider": "coderabbit", "requested_for_head": A,
            "intent_recorded_at": "2026-08-29T13:59:00Z",
            "request_carrier_id": 500}
    base.update(over)
    return base


def test_the_real_preexisting_coderabbit_comment_is_inadmissible():
    """Comment 5462558501 on #32, posted 13:01:15Z — six seconds after the
    PR opened and before the Governor asked anything. If a carrier like
    this can answer a later request, the lineage is decorative."""
    real = carrier(id=5462558501, created_at="2026-08-29T13:01:15Z",
                   body="skip review by coderabbit.ai")
    later = request_row(intent_recorded_at="2026-08-29T14:00:00Z")
    verdict = collector.admissibility(real, later, head_sha=A, generation=1)
    assert verdict["admissible"] is False
    assert verdict["state"] == collector.PREEXISTING
    assert any(r["code"] == collector.PREEXISTING for r in verdict["refusals"])


def test_a_carrier_from_the_wrong_provider_is_inadmissible():
    v = collector.admissibility(carrier(performed_via_github_app={"id": 4669438}),
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
        [carrier(id=5462558501, created_at="2026-08-29T13:01:15Z")],
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

def qualified_record(provider):
    return {"provider": provider, "generation": 1, "requested_for_head": A,
            "state": "ANSWERED", "request_id": "req-x",
            "request_carrier_id": 500,
            "terminal": {"carrier_id": 900, "state": "ADMISSIBLE",
                         "admissible": True},
            "predicate": predicates.evaluate(provider, {
                "id": 900, "body": "no issues found", "review_ran": True,
                "findings": [], "head_claim": A})}


def test_bundle_carries_the_snapshot_digest_not_a_boolean(fresh):
    b = evidence.build_bundle(repo=REPO, pr_number=32, head_sha=A,
                              lineage_records=[qualified_record("codex")],
                              auth_generation=5)
    p = b["providers"][0]
    assert p["snapshot_digest"] and p["terminal_carrier_id"] == 900
    assert p["predicate_schema"] == predicates.SCHEMA_REVISION


def test_bundle_hash_moves_when_the_evidence_moves(fresh):
    a = evidence.build_bundle(repo=REPO, pr_number=32, head_sha=A,
                              lineage_records=[qualified_record("codex")],
                              auth_generation=5)
    changed = qualified_record("codex")
    changed["predicate"] = predicates.evaluate("codex", {
        "id": 900, "body": "on reflection, a bug", "review_ran": True,
        "findings": [{"x": 1}], "head_claim": A})
    b = evidence.build_bundle(repo=REPO, pr_number=32, head_sha=A,
                              lineage_records=[changed], auth_generation=5)
    assert a["bundle_hash"] != b["bundle_hash"]


def test_inadmissible_evidence_cannot_reduce_to_success(fresh):
    rec = qualified_record("codex")
    rec["terminal"] = {"carrier_id": 900, "state": collector.PREEXISTING,
                       "admissible": False}
    other = qualified_record("coderabbit")
    b = evidence.build_bundle(repo=REPO, pr_number=32, head_sha=A,
                              lineage_records=[rec, other], auth_generation=5)
    red = evidence.reduce(b, current_head_sha=A, permission=fresh,
                          auth_generation=5)
    assert red["verdict"] == evidence.NOT_ESTABLISHED
    assert any("not admissible" in r for r in red["refusals"])


def test_findings_in_the_bundle_cannot_reduce_to_success(fresh):
    rec = qualified_record("codex")
    rec["predicate"] = predicates.evaluate("codex", {
        "id": 900, "body": "bug", "review_ran": True,
        "findings": [{"x": 1}], "head_claim": A})
    b = evidence.build_bundle(repo=REPO, pr_number=32, head_sha=A,
                              lineage_records=[rec, qualified_record("coderabbit")],
                              auth_generation=5)
    red = evidence.reduce(b, current_head_sha=A, permission=fresh,
                          auth_generation=5)
    assert red["verdict"] == evidence.NOT_ESTABLISHED
    assert any("findings reported" in r for r in red["refusals"])


def test_a_fully_qualified_bundle_reduces_to_success(fresh):
    b = evidence.build_bundle(
        repo=REPO, pr_number=32, head_sha=A, auth_generation=5,
        lineage_records=[qualified_record("codex"),
                         qualified_record("coderabbit")])
    red = evidence.reduce(b, current_head_sha=A, permission=fresh,
                          auth_generation=5)
    assert red["verdict"] == evidence.SUCCESS, red["refusals"]


# --- 8. success projection ------------------------------------------------------

class FakeEpochs:
    def record_decision(self, **kw):
        return 1

    def project(self, **kw):
        self.last = kw


def _ok(fresh):
    b = evidence.build_bundle(
        repo=REPO, pr_number=32, head_sha=A, auth_generation=5,
        lineage_records=[qualified_record("codex"),
                         qualified_record("coderabbit")])
    return b, evidence.reduce(b, current_head_sha=A, permission=fresh,
                              auth_generation=5)


def _health(all_fresh=True, not_fresh=()):
    return {"observations": {n: {"state": "FRESH" if n not in not_fresh
                                 else "STALE", "age_seconds": 5, "bound": 120}
                             for n in ("runtime", "reconciliation", "watchdog")},
            "all_fresh": all_fresh, "not_fresh": list(not_fresh)}


HEALTH = _health()


def test_success_may_not_create_a_second_carrier(fresh):
    b, red = _ok(fresh)
    with pytest.raises(publish.PublishRefused):
        publish.publish(lambda *a, **k: (201, {"id": 1}), repo=REPO,
                        epoch_id=EPOCH, head_sha=A, conclusion="success",
                        bundle=b, reduction=red, current_head_sha=A,
                        permission=fresh, store=FakeEpochs(),
                        existing_run=None, health=HEALTH)


@pytest.mark.parametrize("stale_component",
                         ["runtime", "reconciliation", "watchdog"])
def test_stale_runtime_health_refuses_success(fresh, stale_component):
    b, red = _ok(fresh)
    health = _health(all_fresh=False, not_fresh=[stale_component])
    checked = publish.guard(reduction=red, bundle=b, current_head_sha=A,
                            permission=fresh, health=health,
                            existing_run=99104297860)
    assert checked["may_publish_success"] is False
    assert any(stale_component in r for r in checked["refusals"])


def test_success_confirmed_only_on_full_identity(fresh):
    b, red = _ok(fresh)

    def request(method, path, body=None):
        if method == "GET":
            return 200, {"id": 99104297860, "name": "ai/final-review",
                         "app": {"id": 4669438}, "head_sha": A,
                         "external_id": EPOCH, "conclusion": "success"}
        return 200, {}

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
def test_a_readback_that_differs_in_identity_is_not_confirmed(fresh, field, bad):
    b, red = _ok(fresh)
    good = {"id": 99104297860, "name": "ai/final-review",
            "app": {"id": 4669438}, "head_sha": A, "external_id": EPOCH,
            "conclusion": "success"}
    good[field] = bad

    def request(method, path, body=None):
        return (200, good) if method == "GET" else (200, {})

    r = publish.publish(request, repo=REPO, epoch_id=EPOCH, head_sha=A,
                        conclusion="success", bundle=b, reduction=red,
                        current_head_sha=A, permission=fresh,
                        store=FakeEpochs(), existing_run=99104297860,
                        health=HEALTH)
    assert r["state"] == "FAILED"
    assert not all(r["identity"].values())
