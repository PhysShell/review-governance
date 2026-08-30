"""A6f-c2: a prerequisite is not established by supplying its shape.

`preconditions=[]` and a caller-built baseline dict were the same lie in
two syntaxes. These tests are about the difference between a result and
something that looks like one.
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


@pytest.fixture()
def store(tmp_path):
    s = rounds.RoundStore(tmp_path / "r.sqlite3")
    yield s
    s.close()


@pytest.fixture()
def snaps(tmp_path):
    s = snapshots.SnapshotStore(tmp_path / "s.sqlite3")
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


def good_gate(fresh, head=A, pr=32):
    return gate.evaluate(repo=REPO, pr_number=pr, head_sha=head, draft=False,
                         base_ref="main", ruleset_id=21640654,
                         ruleset_verified=True,
                         carrier={"state": "CONFIRMED", "head_sha": head,
                                  "check_run_id": 99104297860},
                         permission=fresh, open_generations=[])


# --- 1. the gate result cannot be asserted by the caller ---------------------

@pytest.mark.parametrize("bogus", [[], None, ["ok"], {"failures": []}, True])
def test_a_caller_asserted_gate_is_refused(store, fresh, bogus):
    """`preconditions=[]` was a capability: a fresh permission plus an empty
    list produced a durable ACCEPTED without the gate ever running."""
    with pytest.raises(rounds.RoundError):
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                preconditions=bogus)
    assert store.acceptances_for(REPO, 32) == []


def test_a_real_gate_evaluation_accepts(store, fresh):
    acc = store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                  head_sha=A, permission=fresh,
                                  preconditions=good_gate(fresh))
    assert acc["state"] == rounds.ACCEPTED


def test_a_gate_for_another_head_is_refused(store, fresh):
    with pytest.raises(rounds.RoundError) as exc:
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                preconditions=good_gate(fresh, head=B))
    assert "does not match" in str(exc.value)


def test_a_gate_for_another_pr_is_refused(store, fresh):
    with pytest.raises(rounds.RoundError):
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                preconditions=good_gate(fresh, pr=8))


def test_a_gate_from_another_authorization_is_refused(store, fresh, tmp_path):
    other = auth_state.AuthStore(tmp_path / "o.sqlite3")
    other.record(state="AUTHORIZED", auth_generation=6,
                 observed_at=datetime.datetime.now(
                     datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 source="refresh")
    stale_gate = good_gate(ap.evaluate(other))
    other.close()
    with pytest.raises(rounds.RoundError):
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                preconditions=stale_gate)


def test_a_failed_gate_is_refused(store, fresh):
    failed = gate.evaluate(repo=REPO, pr_number=32, head_sha=A, draft=True,
                           base_ref="main", ruleset_id=1, ruleset_verified=True,
                           carrier={"state": "CONFIRMED", "head_sha": A},
                           permission=fresh, open_generations=[])
    assert failed.passed is False
    with pytest.raises(rounds.RoundError):
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                preconditions=failed)


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


def test_a_baseline_digest_from_another_scope_is_refused(snaps):
    payload = parsers.baseline([], provider_app=347564)
    snaps.capture_baseline(repo=REPO, pr_number=32, provider="coderabbit",
                           read_ok=True, payload=payload, captured_at="t")
    with pytest.raises(snapshots.SnapshotError):
        snaps.capture_baseline(repo=REPO, pr_number=8, provider="coderabbit",
                               read_ok=True, payload=payload, captured_at="t")


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

def _record(snaps, provider, head=A, body="no issues found"):
    payload = {"id": 1, "provider": provider, "body": body, "review_ran": True,
               "findings": [], "head_claim": head}
    snap = snaps.freeze(repo=REPO, pr_number=32, head_sha=head,
                        provider=provider, generation=1, request_id="req-1",
                        payload=payload, frozen_at="t")
    return {"provider": provider, "generation": 1, "requested_for_head": head,
            "state": "ANSWERED", "request_id": "req-1",
            "request_carrier_id": 500,
            "terminal": {"carrier_id": 1, "state": "ADMISSIBLE",
                         "admissible": True},
            "predicate": predicates.evaluate(provider, payload),
            "snapshot_id": snap["snapshot_id"],
            "snapshot_digest": snap["snapshot_digest"]}


def test_the_bundle_carries_the_stored_digest(snaps, fresh):
    rec = _record(snaps, "codex")
    b = evidence.build_bundle(repo=REPO, pr_number=32, head_sha=A,
                              lineage_records=[rec], auth_generation=5)
    p = b["providers"][0]
    assert p["snapshot_id"] == rec["snapshot_id"]
    assert p["snapshot_digest"] == snaps.snapshot(rec["snapshot_id"])["snapshot_digest"]


def test_the_bundle_replays_from_the_durable_snapshots(snaps, fresh):
    b = evidence.build_bundle(
        repo=REPO, pr_number=32, head_sha=A, auth_generation=5,
        lineage_records=[_record(snaps, "codex"),
                         _record(snaps, "coderabbit")])
    out = evidence.verify_against_snapshots(b, snaps, predicates.evaluate)
    assert out["all_reproduced"] is True


def test_a_bundle_citing_a_foreign_snapshot_does_not_replay(snaps, fresh):
    rec = _record(snaps, "codex")
    rec["snapshot_digest"] = "0" * 64
    b = evidence.build_bundle(repo=REPO, pr_number=32, head_sha=A,
                              lineage_records=[rec], auth_generation=5)
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
    store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                            head_sha=A, permission=fresh,
                            preconditions=good_gate(fresh))

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
    store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                            head_sha=A, permission=fresh,
                            preconditions=good_gate(fresh))
    store.invalidate_for_head_move(REPO, 32, B)
    assert store.current_acceptance(REPO, 32, A) is None
    store.invalidate_for_head_move(REPO, 32, A)
    assert store.current_acceptance(REPO, 32, A) is None, \
        "an invalidated acceptance must not revive when the head returns"
