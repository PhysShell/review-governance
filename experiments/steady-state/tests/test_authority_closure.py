"""A6f-c2/c3: a prerequisite is not established by supplying its shape.

`preconditions=[]`, then a hand-built `GateEvaluation`, then a caller
baseline dict: the same lie in three syntaxes, each time one layer further
out. A6f-c3 removed the last argument a caller could forge — the writer
now loads a durable observation and runs the gate itself — so the rows that
used to test "which fake result is refused" test "there is nothing to
hand in" instead.
"""
import datetime
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "harness"))
sys.path.insert(0, str(HERE.parents[0] / "operational-readiness" / "harness"))

import auth_policy as ap  # noqa: E402
import auth_state  # noqa: E402
import evidence  # noqa: E402
import gate  # noqa: E402
import parsers  # noqa: E402
import predicates  # noqa: E402
import rounds  # noqa: E402
import runtime  # noqa: E402
import snapshots  # noqa: E402

REPO = "PhysShell/evm-from-scratch"
A = "7ad19f5e72a13a8fbd10ba9f6a2b0ea4bf430f52"
B = "b" * 40
EPOCH = "pe-5a41d21a944c13836e1cc6ff"
OLD_RUN = "a3d2af24-8685-49a2-9e6e-728a59d8dcd4"
NEW_RUN = "a765cb7e-2018-4a07-b66f-66539b83f8cd"


from conftest import accept, record_observation  # noqa: E402


# --- 1. the gate is run by the writer, over a row it loads -------------------

def test_there_is_no_result_argument_left_to_forge(store, fresh):
    """A stricter type only moved the forgery to its constructor. The
    acceptance writer takes a pointer to a recorded reading instead."""
    import inspect
    params = inspect.signature(rounds.RoundStore.record_acceptance).parameters
    assert "preconditions" not in params
    assert "observation_id" in params
    assert not hasattr(gate, "GateEvaluation")
    assert not hasattr(gate, "require_matching")


def test_a_recorded_observation_accepts(store, fresh):
    acc = accept(store, fresh)
    assert acc["state"] == rounds.ACCEPTED
    assert acc["observation_id"]


@pytest.mark.parametrize("bogus", ["obs-invented", "", None])
def test_an_observation_id_that_names_nothing_is_refused(store, fresh, bogus):
    with pytest.raises(rounds.RoundError) as exc:
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                observation_id=bogus)
    assert "recorded reading" in str(exc.value)
    assert store.acceptances_for(REPO, 32) == []


def test_an_observation_of_another_head_is_refused(store, fresh):
    obs = record_observation(store, head=B)
    with pytest.raises(rounds.RoundError) as exc:
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                observation_id=obs["observation_id"])
    assert "not about this acceptance" in str(exc.value)


def test_an_observation_of_another_pr_is_refused(store, fresh):
    obs = record_observation(store, pr=8)
    with pytest.raises(rounds.RoundError):
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                observation_id=obs["observation_id"])


def test_a_carrier_bound_to_another_head_is_refused(store, fresh):
    """`carrier_run_id` was carried into the acceptance and never checked."""
    obs = record_observation(store, carrier_head=B)
    with pytest.raises(rounds.RoundError) as exc:
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                observation_id=obs["observation_id"])
    assert "carrier_head_sha" in str(exc.value)


@pytest.mark.parametrize("kw,fragment", [
    ({"draft": True}, "draft"),
    ({"base_ref": "release"}, "intended base"),
    ({"ruleset_verified": False}, "ruleset"),
    ({"carrier_state": "PENDING"}, "CONFIRMED failure carrier"),
])
def test_a_failed_gate_over_the_stored_row_is_refused(store, fresh, kw,
                                                      fragment):
    obs = record_observation(store, **kw)
    with pytest.raises(rounds.RoundError) as exc:
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                observation_id=obs["observation_id"])
    assert fragment in str(exc.value)
    assert store.acceptances_for(REPO, 32) == []


def test_a_stale_permission_is_refused_over_a_perfect_observation(store, stale):
    obs = record_observation(store)
    with pytest.raises(rounds.RoundError) as exc:
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=stale,
                                observation_id=obs["observation_id"])
    assert "STALE" in str(exc.value)


# --- 2. baseline provenance ---------------------------------------------------

def test_an_observed_empty_baseline_is_valid(snaps):
    """A brand-new PR really has no previous runs; emptiness is not the
    problem, an unobserved surface is."""
    payload = parsers.baseline([], provider_app=347564)
    row = snaps.capture_baseline(repo=REPO, pr_number=32, provider="coderabbit",
                                 read_ok=True, payload=payload,
                                 captured_at="t")
    base = {**row["payload"], "baseline_id": row["baseline_id"],
            "read_ok": True, "captured_at": "t"}
    assert parsers.require_baseline(base) is base


def test_an_unread_baseline_is_refused(snaps):
    payload = parsers.baseline([], provider_app=347564)
    row = snaps.capture_baseline(repo=REPO, pr_number=32, provider="coderabbit",
                                 read_ok=False, payload=payload,
                                 captured_at="t")
    base = {**row["payload"], "baseline_id": row["baseline_id"],
            "read_ok": False, "captured_at": "t"}
    with pytest.raises(parsers.ParseRefused) as exc:
        parsers.require_baseline(base)
    assert "not an empty one" in str(exc.value)


def test_a_caller_dict_is_not_a_captured_baseline():
    with pytest.raises(parsers.ParseRefused) as exc:
        parsers.require_baseline({"captured_at": "now", "read_ok": True,
                                  "run_ids": []})
    assert "not a captured one" in str(exc.value)


def test_identical_payloads_in_two_scopes_are_two_captures(snaps):
    """Content-addressing made these one row, so the second scope silently
    inherited the first's provenance. A capture is an event: same bytes,
    same digest, different reading."""
    payload = parsers.baseline([], provider_app=347564)
    here = snaps.capture_baseline(repo=REPO, pr_number=32,
                                  provider="coderabbit", read_ok=True,
                                  payload=payload, captured_at="t")
    there = snaps.capture_baseline(repo=REPO, pr_number=8,
                                   provider="coderabbit", read_ok=True,
                                   payload=payload, captured_at="t")
    assert here["baseline_id"] != there["baseline_id"]
    assert here["baseline_digest"] == there["baseline_digest"]
    assert (here["pr_number"], there["pr_number"]) == (32, 8)


def test_scope_is_enforced_where_the_request_cites_the_capture(store, snaps,
                                                               fresh):
    """The store no longer refuses the capture, so the binding has to."""
    acc = accept(store, fresh)
    payload = parsers.baseline([], provider_app=347564)
    elsewhere = snaps.capture_baseline(repo=REPO, pr_number=8,
                                       provider="coderabbit", read_ok=True,
                                       payload=payload,
                                       captured_at="2020-01-01T00:00:00Z")
    with pytest.raises(rounds.RoundError) as exc:
        store.record_intent(acceptance_id=acc["acceptance_id"], repo=REPO,
                            pr_number=32, provider="coderabbit", generation=1,
                            requested_for_head=A, permission=fresh,
                            baseline=elsewhere)
    assert "another scope" in str(exc.value)


# --- 3. the real CodeRabbit surface -------------------------------------------

REAL_STICKY = (
    "<!-- This is an auto-generated comment: skip review by coderabbit.ai -->\n"
    "> This repository does not receive automatic reviews because it has "
    "fewer than 10 stars.\n"
    f"**Run ID**: `{OLD_RUN}`\n"
    "<!-- end of auto-generated comment: skip review by coderabbit.ai -->\n"
    "No actionable comments were generated in the recent review.\n"
    f"**Run ID**: `{NEW_RUN}`\n"
    f"Reviewing files that changed from the base of the PR and between "
    f"add0a0975eb499491eefe9f83d971152153d8106 and {A}.\n"
    "Example uuid in the diff: 6ba7b810-9dad-11d1-80b4-00c04fd430c8\n")


def _base(run_ids, carrier_ids=(), digests=None):
    return {"captured_at": "t", "read_ok": True, "baseline_id": "base-x",
            "run_ids": list(run_ids), "carrier_ids": list(carrier_ids),
            "digests": digests or {}, "updated_at": {}}


def test_the_old_skip_block_does_not_poison_the_new_run():
    """The real sticky carries a skip block for one run above a completed
    review for another. Judging the whole body let the older marker decide
    `review_ran` for a review that had run."""
    out = parsers.parse_coderabbit(
        [{"id": 1, "user": {"login": "coderabbitai[bot]"},
          "created_at": "t", "updated_at": "t", "body": REAL_STICKY}],
        base=_base([OLD_RUN]), requested_head=A, generation=1)
    assert out["review_ran"] is True
    assert out["new_run_ids"] == [NEW_RUN]
    assert OLD_RUN in out["skipped_run_ids"]


def test_no_actionable_comments_parses_as_zero():
    out = parsers.parse_coderabbit(
        [{"id": 1, "user": {"login": "coderabbitai[bot]"},
          "created_at": "t", "updated_at": "t", "body": REAL_STICKY}],
        base=_base([OLD_RUN]), requested_head=A, generation=1)
    assert out["findings"] == []
    assert predicates.evaluate("coderabbit", out)["state"] == predicates.POSITIVE


def test_the_head_comes_from_the_reviewed_range_end():
    out = parsers.parse_coderabbit(
        [{"id": 1, "user": {"login": "coderabbitai[bot]"},
          "created_at": "t", "updated_at": "t", "body": REAL_STICKY}],
        base=_base([OLD_RUN]), requested_head=A, generation=1)
    assert out["reviewed_range"]["to"] == A
    assert out["head_claim"] == A


def test_a_range_ending_elsewhere_attests_nothing():
    out = parsers.parse_coderabbit(
        [{"id": 1, "user": {"login": "coderabbitai[bot]"},
          "created_at": "t", "updated_at": "t", "body": REAL_STICKY}],
        base=_base([OLD_RUN]), requested_head=B, generation=1)
    assert out["head_claim"] is None


def test_a_uuid_in_the_diff_is_not_a_run_id():
    """Two of the seven UUIDs in the real sticky are RFC 4122 examples
    quoted inside the reviewed code. Content under review must not be able
    to manufacture the identifiers that prove a review happened."""
    blocks = parsers.split_run_blocks(REAL_STICKY)
    assert blocks["review_run_ids"] == [NEW_RUN]
    assert "6ba7b810-9dad-11d1-80b4-00c04fd430c8" not in blocks["review_run_ids"]
    assert len(set(parsers.BARE_UUID.findall(REAL_STICKY))) == 3


def test_two_new_runs_are_ambiguous():
    body = REAL_STICKY + "\n**Run ID**: `f47ac10b-58cc-4372-a567-0e02b2c3d479`\n"
    out = parsers.parse_coderabbit(
        [{"id": 1, "user": {"login": "coderabbitai[bot]"},
          "created_at": "t", "updated_at": "t", "body": body}],
        base=_base([OLD_RUN]), requested_head=A, generation=1)
    assert out["ambiguous"] is True
    assert len(out["new_run_ids"]) == 2


# --- 4. the bundle cites the durable row --------------------------------------

STANDING = {"acceptance_id": "acc-x", "head_sha": A}


def _record(snaps, provider, head=A, body="no issues found"):
    payload = {"id": 1, "provider": provider, "body": body, "review_ran": True,
               "findings": [], "head_claim": head}
    snap = snaps.freeze(repo=REPO, pr_number=32, head_sha=head,
                        provider=provider, generation=1, request_id="req-1",
                        payload=payload, frozen_at="t")
    return {"provider": provider, "generation": 1, "requested_for_head": head,
            "state": "ANSWERED", "request_id": "req-1",
            "acceptance_id": STANDING["acceptance_id"], "baseline_id": "base-x",
            "request_carrier_id": 500,
            "terminal": {"carrier_id": 1, "state": "ADMISSIBLE",
                         "admissible": True},
            "predicate": predicates.evaluate(provider, payload),
            "snapshot_id": snap["snapshot_id"],
            "snapshot_digest": snap["snapshot_digest"]}


def test_the_bundle_carries_the_stored_digest(snaps, fresh):
    rec = _record(snaps, "codex")
    b = evidence.build_bundle(repo=REPO, pr_number=32, head_sha=A,
                              lineage_records=[rec], auth_generation=5,
                              acceptance_id=STANDING["acceptance_id"])
    p = b["providers"][0]
    assert p["snapshot_id"] == rec["snapshot_id"]
    assert p["snapshot_digest"] == snaps.snapshot(rec["snapshot_id"])["snapshot_digest"]


def test_the_bundle_replays_from_the_durable_snapshots(snaps, fresh):
    b = evidence.build_bundle(
        repo=REPO, pr_number=32, head_sha=A, auth_generation=5,
        acceptance_id=STANDING["acceptance_id"],
        lineage_records=[_record(snaps, "codex"),
                         _record(snaps, "coderabbit")])
    out = evidence.verify_against_snapshots(b, snaps, predicates.evaluate)
    assert out["all_reproduced"] is True


def test_a_bundle_citing_a_foreign_snapshot_does_not_replay(snaps, fresh):
    rec = _record(snaps, "codex")
    rec["snapshot_digest"] = "0" * 64
    b = evidence.build_bundle(repo=REPO, pr_number=32, head_sha=A,
                              lineage_records=[rec], auth_generation=5,
                              acceptance_id=STANDING["acceptance_id"])
    out = evidence.verify_against_snapshots(b, snaps, predicates.evaluate)
    assert out["all_reproduced"] is False


def test_an_identical_payload_in_another_scope_is_refused(snaps):
    payload = {"id": 1, "provider": "codex", "body": "clean"}
    snaps.freeze(repo=REPO, pr_number=32, head_sha=A, provider="codex",
                 generation=1, request_id="req-1", payload=payload,
                 frozen_at="t")
    with pytest.raises(snapshots.SnapshotError) as exc:
        snaps.freeze(repo=REPO, pr_number=32, head_sha=A, provider="codex",
                     generation=2, request_id="req-2", payload=payload,
                     frozen_at="t")
    assert "different scope" in str(exc.value)


# --- 5. the runtime performs the invalidation ---------------------------------

def test_the_runtime_invalidates_acceptances_on_a_head_move(store, fresh,
                                                            tmp_path):
    """A method that can invalidate is not a production transition that
    does. The loop that observes the move must perform it."""
    import epochs as ep
    epochs = ep.EpochStore(tmp_path / "e.sqlite3")
    epochs.open_epoch(repo=REPO, pr_number=32, head_sha=A, opened_at="t")
    accept(store, fresh)

    def request(method, path, tok=None, body=None):
        return 200, {"check_runs": []}

    out = runtime.handle(request, REPO, {"pr_number": 32, "head_sha": B,
                                         "draft": False, "base": "main"},
                         epochs, write_enabled=False, round_store=store)
    assert out["head_transition"] == {"from": A, "to": B}
    assert out["invalidated_acceptances"][0]["was_for_head"] == A
    assert store.current_acceptance(REPO, 32, A) is None
    epochs.close()


def test_a_head_returning_to_a_previous_value_does_not_resurrect(store, fresh):
    """A -> B -> A. The SHA agrees again; the acceptance does not."""
    accept(store, fresh)
    store.invalidate_for_head_move(REPO, 32, B)
    assert store.current_acceptance(REPO, 32, A) is None
    store.invalidate_for_head_move(REPO, 32, A)
    assert store.current_acceptance(REPO, 32, A) is None, \
        "an invalidated acceptance must not revive when the head returns"
