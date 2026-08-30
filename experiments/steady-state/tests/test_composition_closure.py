"""A6f-c1: the gaps between the components.

Every defect here was in code that already passed its own tests, because
each module was locally right and nothing forced them to agree with each
other.
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
import gate as gate_mod  # noqa: E402
import health as health_mod  # noqa: E402
import parsers  # noqa: E402
import predicates  # noqa: E402
import publish  # noqa: E402
import rounds  # noqa: E402
import snapshots  # noqa: E402
import triggers  # noqa: E402

REPO = "PhysShell/evm-from-scratch"
A = "7ad19f5e72a13a8fbd10ba9f6a2b0ea4bf430f52"
B = "b" * 40
EPOCH = "pe-5a41d21a944c13836e1cc6ff"
RUN = "e9bb8d72-00e8-4f67-9cb2-caf3b22574fe"
NEW_RUN = "a3d2af24-8685-49a2-9e6e-728a59d8dcd4"


@pytest.fixture()
def store(tmp_path):
    s = rounds.RoundStore(tmp_path / "r.sqlite3")
    yield s
    s.close()


@pytest.fixture()
def fresh(tmp_path):
    a = auth_state.AuthStore(tmp_path / "a.sqlite3")
    a.record(state="AUTHORIZED", auth_generation=5,
             observed_at=datetime.datetime.now(
                 datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             source="refresh")
    yield ap.evaluate(a)
    a.close()


def evaluated_gate(permission, head=A):
    return gate_mod.evaluate(
        repo=REPO, pr_number=32, head_sha=head, draft=False, base_ref="main",
        ruleset_id=21640654, ruleset_verified=True,
        carrier={"state": "CONFIRMED", "head_sha": head,
                 "check_run_id": 99104297860},
        permission=permission, open_generations=[])


# --- 1. the ACCEPT gate cannot be bypassed ------------------------------------

def test_durable_acceptance_refuses_without_the_evaluated_gate(store, fresh):
    """The store used to check a SHA and a permission, so preconditions
    could refuse and a durable ACCEPTED be written anyway."""
    with pytest.raises(rounds.RoundError) as exc:
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                preconditions=None)
    assert "not evidence that the gate ran" in str(exc.value)
    assert store.acceptances_for(REPO, 32) == []


def test_durable_acceptance_refuses_a_failed_gate(store, fresh):
    failed = gate_mod.evaluate(
        repo=REPO, pr_number=32, head_sha=A, draft=True, base_ref="main",
        ruleset_id=21640654, ruleset_verified=True,
        carrier={"state": "CONFIRMED", "head_sha": A},
        permission=fresh, open_generations=[])
    assert failed.passed is False
    with pytest.raises(rounds.RoundError):
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                preconditions=failed)
    assert store.acceptances_for(REPO, 32) == []


def test_a_passed_gate_records(store, fresh):
    acc = store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                  head_sha=A, permission=fresh,
                                  preconditions=evaluated_gate(fresh))
    assert acc["state"] == rounds.ACCEPTED


# --- 2. the request is bound to the observation that authorised it ------------

def _accepted(store, fresh):
    return store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                   head_sha=A, permission=fresh,
                                   preconditions=evaluated_gate(fresh))


def test_intent_records_observation_and_generation(store, fresh):
    acc = _accepted(store, fresh)
    row = store.record_intent(acceptance_id=acc["acceptance_id"], repo=REPO,
                              pr_number=32, provider="codex", generation=1,
                              requested_for_head=A, permission=fresh)
    assert row["auth_observation_id"] == fresh.observation_id
    assert row["auth_generation"] == fresh.auth_generation


def test_posting_under_a_different_observation_is_refused(store, fresh, tmp_path):
    acc = _accepted(store, fresh)
    row = store.record_intent(acceptance_id=acc["acceptance_id"], repo=REPO,
                              pr_number=32, provider="codex", generation=1,
                              requested_for_head=A, permission=fresh)
    other = auth_state.AuthStore(tmp_path / "other.sqlite3")
    other.record(state="AUTHORIZED", auth_generation=6,
                 observed_at=datetime.datetime.now(
                     datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 source="refresh")
    different = ap.evaluate(other)
    other.close()
    calls = {"n": 0}
    with pytest.raises(triggers.TriggerRefused) as exc:
        triggers.send(lambda p, b: calls.update(n=1) or (201, {"id": 1}),
                      store, request_row=row, permission=different, head_sha=A)
    assert calls["n"] == 0
    assert "does not match the recorded intent" in str(exc.value)


def test_intent_refuses_a_stale_permission(store, fresh, tmp_path):
    acc = _accepted(store, fresh)
    old = auth_state.AuthStore(tmp_path / "old.sqlite3")
    old.record(state="AUTHORIZED", auth_generation=5,
               observed_at="2020-01-01T00:00:00Z", source="refresh")
    stale = ap.evaluate(old)
    old.close()
    with pytest.raises(rounds.RoundError):
        store.record_intent(acceptance_id=acc["acceptance_id"], repo=REPO,
                            pr_number=32, provider="codex", generation=1,
                            requested_for_head=A, permission=stale)


# --- 3/4. generation and association fail closed ------------------------------

def request_row(**over):
    base = {"provider": "coderabbit", "requested_for_head": A,
            "intent_recorded_at": "2026-08-29T14:00:00Z",
            "request_carrier_id": 500}
    base.update(over)
    return base


def carrier(**over):
    base = {"id": 900, "created_at": "2026-08-29T14:05:00Z",
            "performed_via_github_app": {"id": 347564},
            "head_claim": A, "generation": 1, "new_run_ids": [NEW_RUN],
            "carrier_was_rewritten": True, "body": ""}
    base.update(over)
    return base


def test_missing_generation_is_not_a_match():
    """The old code substituted the expected value when the carrier had
    none, so absent evidence became agreement."""
    c = carrier()
    del c["generation"]
    v = collector.admissibility(c, request_row(), head_sha=A, generation=1)
    assert v["admissible"] is False
    assert any(r["code"] == collector.WRONG_GENERATION for r in v["refusals"])


def test_missing_association_evidence_is_not_a_match():
    """Issue comments have no in_reply_to_id at all — confirmed live — so
    the old branch never executed and right-bot-plus-later-timestamp was
    enough."""
    c = carrier(new_run_ids=[], carrier_was_rewritten=False)
    v = collector.admissibility(c, request_row(), head_sha=A, generation=1)
    assert v["admissible"] is False
    assert any(r["code"] == collector.UNASSOCIATED for r in v["refusals"])


def test_a_rewritten_sticky_with_a_new_run_is_associated():
    """The real CodeRabbit sticky on #8 was created 20 Aug and updated 29
    Aug: a genuine later review rewrites an older carrier."""
    c = carrier(created_at="2026-08-20T01:03:30Z", carrier_was_rewritten=True)
    v = collector.admissibility(c, request_row(), head_sha=A, generation=1)
    assert not any(r["code"] == collector.UNASSOCIATED for r in v["refusals"])


def test_a_reaction_on_our_request_is_associated():
    c = carrier(new_run_ids=[], carrier_was_rewritten=False,
                reaction_on_request_carrier=500,
                performed_via_github_app={"id": 199175422})
    v = collector.admissibility(c, request_row(provider="codex"),
                                head_sha=A, generation=1)
    assert not any(r["code"] == collector.UNASSOCIATED for r in v["refusals"])


# --- 5. raw GitHub to a provider observation ----------------------------------

def test_a_raw_carrier_carrying_conclusions_is_refused():
    """The fixtures invented review_ran/findings/head_claim. If production
    can be handed the same shape, nothing derived anything."""
    with pytest.raises(parsers.ParseRefused):
        parsers.reject_synthetic({"id": 1, "body": "x", "review_ran": True})
    with pytest.raises(parsers.ParseRefused):
        parsers.reject_synthetic({"id": 1, "findings": []})


def captured(snaps, comments, provider="coderabbit", read_ok=True,
             pr_number=32):
    """A baseline the way the driver produces one: read, then frozen.

    Written through the store rather than assembled inline, because
    `require_baseline` now refuses a caller's dict — an unread surface and
    an empty one are otherwise the same object.
    """
    app = triggers.PROVIDER_APP_ID[provider]
    row = snaps.capture_baseline(
        repo=REPO, pr_number=pr_number, provider=provider, read_ok=read_ok,
        payload=parsers.baseline(comments, provider_app=app),
        captured_at="2026-08-29T13:59:00Z")
    return {**row["payload"], "baseline_id": row["baseline_id"],
            "read_ok": row["read_ok"], "captured_at": row["captured_at"]}


def test_baseline_freezes_run_ids_before_the_trigger():
    base = parsers.baseline(
        [{"id": 5349895008, "user": {"login": "coderabbitai[bot]"},
          "body": f"**Run ID**: `{RUN}`", "updated_at": "2026-08-20T01:03:30Z",
          "performed_via_github_app": {"id": 347564}}],
        provider_app=347564)
    assert base["run_ids"] == [RUN]
    assert 5349895008 in base["carrier_ids"]


def test_a_sticky_rewritten_with_a_new_run_is_our_answer(snaps):
    base = captured(snaps, [
        {"id": 5349895008, "user": {"login": "coderabbitai[bot]"},
         "body": f"old body **Run ID**: `{RUN}`",
         "updated_at": "2026-08-20T01:03:30Z",
         "performed_via_github_app": {"id": 347564}}])
    out = parsers.parse_coderabbit(
        [{"id": 5349895008, "user": {"login": "coderabbitai[bot]"},
          "created_at": "2026-08-20T01:03:30Z",
          "updated_at": "2026-08-29T11:10:50Z",
          "body": f"Actionable comments posted: 0\n"
                  f"Reviewing files that changed between "
                  f"add0a0975eb499491eefe9f83d971152153d8106 and {A}.\n"
                  f"**Run ID**: `{NEW_RUN}`"}],
        base=base, requested_head=A, generation=1)
    assert out["carrier_was_rewritten"] is True
    assert out["new_run_ids"] == [NEW_RUN]
    assert out["head_claim"] == A and out["findings"] == []


def test_the_preexisting_skip_comment_yields_no_answer(snaps):
    base = captured(snaps, [])
    out = parsers.parse_coderabbit(
        [{"id": 5462558501, "user": {"login": "coderabbitai[bot]"},
          "created_at": "2026-08-29T13:01:15Z",
          "updated_at": "2026-08-29T13:01:15Z",
          "body": "skip review by coderabbit.ai"}],
        base=base, requested_head=A, generation=1)
    assert out is None, "a carrier with no new run id is not our answer"


def test_actionable_count_absent_is_not_zero(snaps):
    base = captured(snaps, [])
    out = parsers.parse_coderabbit(
        [{"id": 1, "user": {"login": "coderabbitai[bot]"},
          "created_at": "t", "updated_at": "t",
          "body": f"Review completed. status: success {A}\n"
                  f"**Run ID**: `{NEW_RUN}`"}],
        base=base, requested_head=A, generation=1)
    assert out["findings"] is None
    assert predicates.evaluate("coderabbit", out)["state"] == predicates.NOT_POSITIVE


def test_codex_clean_review_may_arrive_as_a_reaction(snaps):
    base = captured(snaps, [], provider="codex")
    out = parsers.parse_codex(
        [], [{"id": 7, "content": "+1", "created_at": "t",
              "user": {"login": "chatgpt-codex-connector[bot]"}}],
        base=base, requested_head=A, generation=1, request_carrier_id=500)
    assert out["findings"] == [] and out["review_ran"] is True
    assert out["reaction_on_request_carrier"] == 500
    assert out["head_claim"] is None, "a reaction attests no head"


def test_codex_findings_comment_is_parsed_as_findings(snaps):
    base = captured(snaps, [], provider="codex")
    out = parsers.parse_codex(
        [{"id": 9, "user": {"login": "chatgpt-codex-connector[bot]"},
          "created_at": "t", "updated_at": "t",
          "body": f"Found 2 issues in {A}"}], [],
        base=base, requested_head=A, generation=1, request_carrier_id=500)
    assert len(out["findings"]) == 2
    assert predicates.evaluate("codex", out)["state"] == predicates.NOT_POSITIVE


def test_no_answer_at_all_is_not_a_clean_review(snaps):
    base = captured(snaps, [], provider="codex")
    assert parsers.parse_codex([], [], base=base, requested_head=A,
                               generation=1, request_carrier_id=500) is None


# --- 6. durable snapshots and replay ------------------------------------------

@pytest.fixture()
def snaps(tmp_path):
    s = snapshots.SnapshotStore(tmp_path / "s.sqlite3")
    yield s
    s.close()


def test_a_snapshot_survives_the_process_and_replays(snaps, tmp_path):
    payload = {"id": 1, "provider": "codex", "body": "no issues found",
               "review_ran": True, "findings": [], "head_claim": A}
    snap = snaps.freeze(repo=REPO, pr_number=32, head_sha=A, provider="codex",
                        generation=1, request_id="req-1", payload=payload,
                        frozen_at="t")
    snaps.close()
    reopened = snapshots.SnapshotStore(tmp_path / "s.sqlite3")
    replay = reopened.replay(snap["snapshot_id"], predicates.evaluate)
    assert replay["digest_reproduced"] is True
    assert replay["predicate"]["state"] == predicates.POSITIVE
    reopened.close()


def test_a_snapshot_cannot_be_rewritten(snaps):
    snap = snaps.freeze(repo=REPO, pr_number=32, head_sha=A, provider="codex",
                        generation=1, request_id="r", payload={"a": 1},
                        frozen_at="t")
    with pytest.raises(sqlite3.IntegrityError):
        snaps.conn.execute("UPDATE evidence_snapshots SET payload='{}'")
    with pytest.raises(sqlite3.IntegrityError):
        snaps.conn.execute("DELETE FROM evidence_snapshots")


def test_a_tampered_payload_is_detected_on_replay(snaps):
    snap = snaps.freeze(repo=REPO, pr_number=32, head_sha=A, provider="codex",
                        generation=1, request_id="r",
                        payload={"id": 1, "body": "clean"}, frozen_at="t")
    snaps.conn.execute("DROP TRIGGER snapshots_immutable_update")
    snaps.conn.execute("UPDATE evidence_snapshots SET payload='{\"id\":1}'")
    snaps.conn.commit()
    with pytest.raises(snapshots.SnapshotError) as exc:
        snaps.replay(snap["snapshot_id"], predicates.evaluate)
    assert "does not hash to its recorded digest" in str(exc.value)


# --- 7. health is a required, provenance-carrying set -------------------------

def _health_file(tmp_path, name, at):
    p = tmp_path / name
    p.write_text('{"last_complete_pass_at": "%s"}' % at)
    return str(p)


def now_stamp(delta=0):
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(seconds=delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_all_three_fresh_passes(tmp_path):
    sources = {n: _health_file(tmp_path, f"{n}.json", now_stamp(5))
               for n in health_mod.REQUIRED}
    assert health_mod.evaluate(sources)["all_fresh"] is True


@pytest.mark.parametrize("missing", list(health_mod.REQUIRED))
def test_a_missing_source_is_absent_not_optional(tmp_path, missing):
    sources = {n: _health_file(tmp_path, f"{n}.json", now_stamp(5))
               for n in health_mod.REQUIRED if n != missing}
    out = health_mod.evaluate(sources)
    assert out["all_fresh"] is False
    assert out["observations"][missing]["state"] == health_mod.ABSENT


def test_an_unreadable_source_is_not_healthy(tmp_path):
    sources = {n: _health_file(tmp_path, f"{n}.json", now_stamp(5))
               for n in health_mod.REQUIRED}
    bad = tmp_path / "corrupt-runtime.json"
    bad.write_text("not json")
    sources["runtime"] = str(bad)
    out = health_mod.evaluate(sources)
    assert out["observations"]["runtime"]["state"] == health_mod.UNREADABLE


def test_a_stale_source_is_not_healthy(tmp_path):
    sources = {n: _health_file(tmp_path, f"{n}.json", now_stamp(5))
               for n in health_mod.REQUIRED}
    sources["watchdog"] = _health_file(tmp_path, "old.json", now_stamp(9999))
    out = health_mod.evaluate(sources)
    assert out["observations"]["watchdog"]["state"] == health_mod.STALE
    assert out["observations"]["watchdog"]["age_seconds"] > 120


def test_absent_health_refuses_success(fresh):
    checked = publish.guard(reduction={"verdict": "SUCCESS", "head_sha": A},
                            bundle={"head_sha": A}, current_head_sha=A,
                            permission=fresh, health=None, existing_run=1)
    assert checked["may_publish_success"] is False
    assert any("absence of a signal" in r for r in checked["refusals"])


def test_partial_health_refuses_success(fresh, tmp_path):
    sources = {"runtime": _health_file(tmp_path, "r.json", now_stamp(5))}
    checked = publish.guard(reduction={"verdict": "SUCCESS", "head_sha": A},
                            bundle={"head_sha": A}, current_head_sha=A,
                            permission=fresh,
                            health=health_mod.evaluate(sources),
                            existing_run=1)
    assert checked["may_publish_success"] is False


def test_an_uncaptured_baseline_is_refused():
    """Found live against #32: an empty baseline made the pre-existing skip
    comment parse as an answer, because every run id looked new."""
    with pytest.raises(parsers.ParseRefused) as exc:
        parsers.parse_coderabbit([], base={"run_ids": []}, requested_head=A,
                                 generation=1)
    assert "no baseline capture" in str(exc.value)
    with pytest.raises(parsers.ParseRefused):
        parsers.parse_codex([], [], base={}, requested_head=A, generation=1,
                            request_carrier_id=1)


def test_a_captured_baseline_containing_the_run_rejects_the_carrier(snaps):
    """The real skip comment, with its run id already in the baseline."""
    body = f"skip review by coderabbit.ai\n**Run ID**: `{RUN}`"
    base = captured(snaps, [
        {"id": 5462558501, "user": {"login": "coderabbitai[bot]"},
         "body": body, "updated_at": "2026-08-29T13:01:15Z",
         "performed_via_github_app": {"id": 347564}}])
    assert base["run_ids"] == [RUN]
    out = parsers.parse_coderabbit(
        [{"id": 5462558501, "user": {"login": "coderabbitai[bot]"},
          "created_at": "2026-08-29T13:01:15Z",
          "updated_at": "2026-08-29T13:01:15Z", "body": body}],
        base=base, requested_head=A, generation=1)
    assert out is None
