"""A6g-c1: evidence can be withdrawn, and a PR can stop existing.

Two independent gaps, both found by a live round rather than by reasoning
about one.

**The surface is not monotonic.** A6g watched a CodeRabbit run id appear at
13:49:29 and vanish by 13:50:55 when the same carrier was rewritten, and
watched Codex withdraw the acknowledgement reaction it had left on our
request. `new_run_ids = current - baseline` answers "did a marker appear".
It was being used to answer "does a marker stand".

**Closing a PR removed it from the runtime's view before the acceptance
about it had a terminal state.** The loop lists open PRs; close one and the
object is gone while the permission is still ACCEPTED. Cleanup would have
left a standing acceptance for a pull request that no longer exists.
"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "harness"))

import collector  # noqa: E402
import epochs as ep  # noqa: E402
import evidence  # noqa: E402
import governed_round as gr  # noqa: E402
import parsers  # noqa: E402
import predicates  # noqa: E402
import revisions as rev  # noqa: E402
import rounds  # noqa: E402
import runtime  # noqa: E402
from conftest import (A, B, CARRIER_RUN, EPOCH, FakeGitHub, REPO,  # noqa: E402
                      RULESET, accept, captured_baseline, flat_baseline,
                      now_stamp, record_observation)

LIVE = json.loads(
    (HERE / "fixtures" / "a6g-live-round-carriers.json").read_text())
BY_ID = {c["id"]: c for c in LIVE["carriers"]}
CR_REPLY, CR_STICKY = 5469070667, 5462558501
CODEX_ANSWER = 5469076456
CR_REQUEST, CODEX_REQUEST = 5469066573, 5469066806
HEAD_A = "7ad19f5e72a13a8fbd10ba9f6a2b0ea4bf430f52"


def base_for(provider="coderabbit", run_ids=(), carrier_ids=(), digests=None):
    return {"captured_at": "2026-08-30T13:48:25Z", "read_ok": True,
            "baseline_id": "base-a6g", "run_ids": list(run_ids),
            "carrier_ids": list(carrier_ids), "digests": digests or {},
            "updated_at": {}}


def request_row(provider="coderabbit", carrier=CR_REQUEST, **over):
    row = {"provider": provider, "requested_for_head": HEAD_A,
           "intent_recorded_at": "2026-08-30T13:48:29Z",
           "request_carrier_id": carrier, "acceptance_id": "acc-a6g",
           "baseline_id": "base-a6g"}
    row.update(over)
    return row


# --- A. the surface is a revision history ------------------------------------

def observed(run_ids=("r1",), carrier_id=900, findings=(), head=A,
             digest="d1", updated="2026-08-30T13:49:29Z"):
    return {"id": carrier_id, "provider": "coderabbit", "updated_at": updated,
            "observed_digest": digest, "new_run_ids": list(run_ids),
            "head_claim": head, "head_binding": collector.ATTESTED,
            "review_ran": True, "findings": list(findings)}


def test_a_marker_that_survives_a_rewrite_still_stands():
    frozen = rev.revision_of(observed(), observed_at="t1")
    current = rev.revision_of(observed(digest="d2"), observed_at="t2")
    out = rev.compare(frozen, current)
    assert out["state"] == rev.STANDING
    assert out["body_changed"] is True


def test_a_withdrawn_marker_is_retracted():
    """The live case: run id 8dcf9c5c present at 13:49:29, gone by 13:50:55
    after the same carrier was rewritten twice."""
    frozen = rev.revision_of(observed(run_ids=("8dcf9c5c",)), observed_at="t1")
    current = rev.revision_of(observed(run_ids=(), digest="d2"), observed_at="t2")
    out = rev.compare(frozen, current)
    assert out["state"] == rev.RETRACTED
    assert out["lost_markers"] == ["8dcf9c5c"]
    assert "no longer on the surface" in out["cause"]


def test_a_carrier_that_moved_on_is_superseded():
    frozen = rev.revision_of(observed(run_ids=("r1",)), observed_at="t1")
    current = rev.revision_of(observed(run_ids=("r2",), digest="d2"),
                              observed_at="t2")
    assert rev.compare(frozen, current)["state"] == rev.SUPERSEDED


def test_a_vanished_carrier_is_absent():
    frozen = rev.revision_of(observed(), observed_at="t1")
    assert rev.compare(frozen, None)["state"] == rev.ABSENT


def test_a_withdrawn_reaction_is_retracted():
    """Codex removed its acknowledgement reaction after commenting."""
    frozen = rev.revision_of(
        {"id": "reaction:1:7001", "provider": "codex",
         "reaction_on_request_carrier": CODEX_REQUEST, "findings": [],
         "review_ran": True, "head_binding": collector.REQUEST_DERIVED},
        observed_at="t1")
    current = rev.revision_of(
        {"id": "reaction:1:7001", "provider": "codex", "findings": [],
         "review_ran": True, "head_binding": collector.REQUEST_DERIVED},
        observed_at="t2")
    assert rev.compare(frozen, current)["state"] == rev.RETRACTED


def test_a_changed_verdict_is_superseded():
    frozen = rev.revision_of(observed(findings=()), observed_at="t1")
    current = rev.revision_of(observed(findings=({"x": 1},), digest="d2"),
                              observed_at="t2")
    out = rev.compare(frozen, current)
    assert out["state"] == rev.SUPERSEDED
    assert "1 finding" in out["cause"]


@pytest.mark.parametrize("state", [rev.RETRACTED, rev.SUPERSEDED, rev.ABSENT,
                                   rev.UNREADABLE])
def test_only_standing_evidence_qualifies(state):
    out = rev.reconfirmation([{"provider": "codex",
                               "comparison": {"state": state}}])
    assert out["all_standing"] is False
    assert out["not_standing"] == ["codex"]
    assert state not in rev.QUALIFYING


def test_a_retracted_marker_refuses_success(tmp_path, store, snaps, epochs):
    """The whole point: frozen evidence stays a historical fact and stops
    being a standing verdict."""
    from test_end_to_end_round import FakeEpochs, driver, run_round
    github = FakeGitHub()
    d = driver(tmp_path, store, snaps, epochs, github)
    accepted = d.observe_and_accept(epoch_id=EPOCH, ruleset_id=github.ruleset_id)
    records = []
    for provider in ("coderabbit", "codex"):
        baseline = d.capture_baseline(provider)
        sent = d.request_provider(accepted, provider, 1, baseline=baseline)
        records.append(d.collect_evidence(sent, provider, 1))
    assert all(r.get("state") == "ANSWERED" for r in records)

    # The provider withdraws the review it had published.
    from conftest import STICKY_BEFORE, STICKY_ID
    for c in github.comments:
        if c["id"] == STICKY_ID:
            c["body"] = STICKY_BEFORE
            c["updated_at"] = now_stamp(9)

    d.epochs = FakeEpochs()
    out = d.conclude(records, epoch_id=EPOCH, existing_run=CARRIER_RUN,
                     patch=github.request, ruleset_id=github.ruleset_id)
    assert out["state"] == gr.STOP
    assert "no longer standing" in out["cause"]
    assert out["reconfirmation"]["not_standing"] == ["coderabbit"]
    assert github.patched == []


def test_the_frozen_snapshot_survives_the_retraction(tmp_path, store, snaps,
                                                     epochs):
    """What was seen is still on file; what it means has changed."""
    from test_end_to_end_round import driver
    github = FakeGitHub()
    d = driver(tmp_path, store, snaps, epochs, github)
    accepted = d.observe_and_accept(epoch_id=EPOCH, ruleset_id=github.ruleset_id)
    baseline = d.capture_baseline("coderabbit")
    sent = d.request_provider(accepted, "coderabbit", 1, baseline=baseline)
    rec = d.collect_evidence(sent, "coderabbit", 1)
    snapshot = snaps.snapshot(rec["snapshot_id"])
    assert snapshot["payload"]["new_run_ids"]

    from conftest import STICKY_BEFORE, STICKY_ID
    for c in github.comments:
        if c["id"] == STICKY_ID:
            c["body"] = STICKY_BEFORE
            c["updated_at"] = now_stamp(9)
    out = d.reconfirm_providers([rec])
    assert out["states"]["coderabbit"] in (rev.RETRACTED, rev.SUPERSEDED,
                                           rev.ABSENT)
    assert snaps.snapshot(rec["snapshot_id"])["payload"] == snapshot["payload"]
    kinds = [r["kind"] for r in snaps.revisions_for(rec["snapshot_id"])]
    assert kinds[0] == "FROZEN" and kinds[-1].startswith("RECONFIRM_")


# --- B. the CodeRabbit command-response shape --------------------------------

def test_the_live_command_response_parses_its_content():
    """Carrier 5469070667: App identity, exact full target SHA, an explicit
    finding, `Review finished` — and no run-id protocol at all."""
    out = parsers.parse_coderabbit_command_response(
        [BY_ID[CR_REPLY]], base=base_for(), requested_head=HEAD_A,
        generation=1, request_carrier_id=CR_REQUEST)
    assert out["shape"] == "COMMAND_RESPONSE"
    assert out["author_app_id"] == 347564
    assert out["head_claim"] == HEAD_A
    assert out["review_ran"] is True
    assert len(out["findings"]) == 1
    assert out["new_run_ids"] == []
    assert predicates.evaluate("coderabbit", out)["state"] == \
        predicates.NOT_POSITIVE


def test_the_invocation_hash_correlates_with_nothing_we_can_derive():
    """Tested against every preimage the request offers, over three
    command/response pairs on two PRs. None matched, so it is carried as an
    opaque id and used for nothing."""
    out = parsers.parse_coderabbit_command_response(
        [BY_ID[CR_REPLY]], base=base_for(), requested_head=HEAD_A,
        generation=1, request_carrier_id=CR_REQUEST)
    assert out["invocation_id"].startswith("v2:")
    assert out["invocation_correlation"] == "NOT_DERIVED"
    import inspect
    source = inspect.getsource(collector)
    assert "invocation_id" not in source, \
        "an underived marker must not reach the admissibility decision"


def test_the_live_command_response_stays_unassociated():
    """The A6g-c1 answer: association UNRESOLVED, content FINDING_PRESENT,
    overall NOT_ADMISSIBLE. It is not repaired by noticing that the bot is
    right, the timestamp is later and the SHA matches."""
    out = parsers.parse_coderabbit_command_response(
        [BY_ID[CR_REPLY]], base=base_for(), requested_head=HEAD_A,
        generation=1, request_carrier_id=CR_REQUEST)
    v = collector.admissibility(out, request_row(), head_sha=HEAD_A,
                                generation=1)
    assert v["admissible"] is False
    assert v["association"] is None
    assert any(r["code"] == collector.UNASSOCIATED for r in v["refusals"])
    assert any("posts unprompted" in r["detail"] for r in v["refusals"])


def test_coderabbit_may_not_be_admitted_on_novelty_alone():
    assert collector.NEW_CARRIER_ABSENT_FROM_BASELINE not in \
        collector.ADMISSIBLE_ASSOCIATIONS["coderabbit"]
    assert collector.STRENGTH[collector.NEW_CARRIER_ABSENT_FROM_BASELINE] == "WEAK"


def test_the_sticky_names_the_comment_it_was_answering():
    """Derived from a corpus: `radioGroupId` carries the triggering comment
    id. On #32 that is our request; the Codex request eleven seconds later
    is not there, so the handle is selective."""
    ids = parsers.triggering_comment_ids(BY_ID[CR_STICKY]["body"])
    assert ids == [CR_REQUEST]
    assert CODEX_REQUEST not in ids


def test_the_handle_admits_our_request_and_refuses_another():
    sticky = {**BY_ID[CR_STICKY]}
    out = parsers.parse_coderabbit_command_response(
        [BY_ID[CR_REPLY]], base=base_for(), requested_head=HEAD_A,
        generation=1, request_carrier_id=CR_REQUEST)
    named = {**out, "triggering_comment_ids": [CR_REQUEST]}
    v = collector.admissibility(named, request_row(), head_sha=HEAD_A,
                                generation=1)
    assert v["association"] == collector.PROVIDER_NAMED_OUR_REQUEST
    assert v["association_strength"] == "STRONG"

    stray = {**out, "triggering_comment_ids": [CODEX_REQUEST]}
    w = collector.admissibility(stray, request_row(), head_sha=HEAD_A,
                                generation=1)
    assert w["admissible"] is False
    assert any("not our request" in r["detail"] for r in w["refusals"])


def test_the_live_codex_answer_is_admitted_on_the_weak_kind():
    """Recorded honestly: Codex offers nothing stronger on this surface, and
    the admission says so."""
    out = parsers.parse_codex(
        [BY_ID[CODEX_ANSWER]], [], base=base_for("codex"),
        requested_head=HEAD_A, generation=1, request_carrier_id=CODEX_REQUEST)
    v = collector.admissibility(out, request_row("codex", CODEX_REQUEST),
                                head_sha=HEAD_A, generation=1)
    assert v["admissible"] is True
    assert v["association"] == collector.NEW_CARRIER_ABSENT_FROM_BASELINE
    assert v["association_strength"] == "WEAK"


# --- C. closing a PR is a terminal transition --------------------------------

def test_closing_a_pr_terminalizes_its_acceptance(store, fresh):
    acc = accept(store, fresh)
    assert store.current_acceptance(REPO, 32, A) is not None
    ended = store.terminalize(REPO, 32, cause="PR_CLOSED")
    assert ended[0]["acceptance_id"] == acc["acceptance_id"]
    assert store.acceptance(acc["acceptance_id"])["state"] == rounds.TERMINATED
    assert store.current_acceptance(REPO, 32, A) is None


def test_a_terminated_acceptance_cannot_return(store, fresh):
    """Reopening the PR on the exact same commit must not revive it."""
    import sqlite3
    acc = accept(store, fresh)
    store.terminalize(REPO, 32, cause="PR_CLOSED")
    with pytest.raises(sqlite3.IntegrityError) as exc:
        store.conn.execute(
            "UPDATE acceptances SET state='ACCEPTED' WHERE acceptance_id=?",
            (acc["acceptance_id"],))
    assert "does not return to ACCEPTED" in str(exc.value)
    assert store.current_acceptance(REPO, 32, A) is None


def test_a_reopened_pr_needs_a_fresh_reading_and_a_fresh_acceptance(store,
                                                                    fresh):
    acc = accept(store, fresh)
    store.terminalize(REPO, 32, cause="PR_CLOSED")
    assert store.current_acceptance(REPO, 32, A) is None
    again = accept(store, fresh)
    assert again["acceptance_id"] != acc["acceptance_id"]
    assert again["observation_id"] != acc["observation_id"]
    assert store.current_acceptance(REPO, 32, A)["acceptance_id"] == \
        again["acceptance_id"]


def test_the_runtime_terminalizes_a_pr_that_left_the_open_set(tmp_path, store,
                                                              fresh):
    """The gap itself: the loop lists open PRs, so a closed one simply
    stopped being visible while its acceptance stayed ACCEPTED."""
    epochs = ep.EpochStore(tmp_path / "e.sqlite3")
    epochs.open_epoch(repo=REPO, pr_number=32, head_sha=A, opened_at="t")
    acc = accept(store, fresh)

    def request(method, path, tok=None, body=None):
        if "pulls?state=open" in path:
            return 200, []          # #32 has been closed
        if path.endswith("/pulls/32"):
            return 200, {"number": 32, "state": "closed", "merged": False,
                         "head": {"sha": A}, "draft": False,
                         "base": {"ref": "main"}}
        return 200, {"check_runs": []}

    result = runtime.pass_once(request, REPO, "main", epochs,
                               write_enabled=False, round_store=store)
    assert result["terminalized"][0]["pr_number"] == 32
    assert result["terminalized"][0]["pr_state"] == "closed"
    assert result["terminalized"][0]["merged"] is False
    assert store.acceptance(acc["acceptance_id"])["state"] == rounds.TERMINATED
    assert store.current_acceptance(REPO, 32, A) is None
    epochs.close()


def test_an_unreadable_pr_does_not_terminalize(tmp_path, store, fresh):
    """Unreadable is not closed. Leaving it standing asserts nothing."""
    epochs = ep.EpochStore(tmp_path / "e.sqlite3")
    acc = accept(store, fresh)

    def request(method, path, tok=None, body=None):
        if "pulls?state=open" in path:
            return 200, []
        if path.endswith("/pulls/32"):
            return 503, None
        return 200, {"check_runs": []}

    result = runtime.pass_once(request, REPO, "main", epochs,
                               write_enabled=False, round_store=store)
    assert result["terminalized"][0]["state"] == "UNREADABLE"
    assert store.acceptance(acc["acceptance_id"])["state"] == rounds.ACCEPTED
    epochs.close()


def test_an_open_pr_is_never_terminalized(tmp_path, store, fresh):
    epochs = ep.EpochStore(tmp_path / "e.sqlite3")
    epochs.open_epoch(repo=REPO, pr_number=32, head_sha=A, opened_at="t")
    acc = accept(store, fresh)

    def request(method, path, tok=None, body=None):
        if "pulls?state=open" in path:
            return 200, [{"number": 32, "head": {"sha": A}, "draft": False,
                          "base": {"ref": "main"}}]
        if path.endswith("/pulls/32"):
            return 200, {"number": 32, "state": "open", "head": {"sha": A}}
        return 200, {"check_runs": []}

    result = runtime.pass_once(request, REPO, "main", epochs,
                               write_enabled=False, round_store=store)
    assert result["terminalized"] == []
    assert store.acceptance(acc["acceptance_id"])["state"] == rounds.ACCEPTED
    epochs.close()
