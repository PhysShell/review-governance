"""The production composition root.

A6a proved the components and qualified them by hand while the deployed
service kept running the legacy loop. These tests are about the loop
itself, because "the modules are correct" and "the Governor runs them" are
different sentences and only the second one gates anything.
"""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "harness"))
sys.path.insert(0, str(HERE.parents[0] / "operational-readiness" / "harness"))

import carrier  # noqa: E402
import epochs as ep  # noqa: E402
import runtime  # noqa: E402

REPO = "PhysShell/evm-from-scratch"
A = "a" * 40
B = "b" * 40


@pytest.fixture()
def store(tmp_path):
    s = ep.EpochStore(tmp_path / "p.sqlite3")
    yield s
    s.close()


def pull(pr=8, head=A, draft=False):
    return {"pr_number": pr, "head_sha": head, "draft": draft, "base": "main"}


def good_run(head=A, run_id=1):
    return {"id": run_id, "head_sha": head, "name": "ai/final-review",
            "app": {"id": 4669438}, "conclusion": "failure",
            "output": {"summary": carrier.SUMMARY}}


def requester(states):
    calls = {"post": 0}

    def request(method, path, tok=None, body=None):
        if method == "GET":
            nxt = states.pop(0)
            return (500, None) if nxt is None else (200, {"check_runs": nxt})
        calls["post"] += 1
        return 201, {"id": 99}
    return request, calls


# --- the load-bearing half: an unchanged head is read, not rewritten ---------

def test_unchanged_head_with_a_valid_carrier_writes_nothing(store):
    store.open_epoch(repo=REPO, pr_number=8, head_sha=A, opened_at="t")
    request, calls = requester([[good_run()]])
    out = runtime.handle(request, REPO, pull(), store)
    assert out["action"] == "ADOPTED"
    assert out["writes"] == 0 and calls["post"] == 0


def test_a_second_pass_still_writes_nothing(store):
    """A producer that posts every pass leaves a head carrying several
    verdicts from the same App, and nobody can say which one the gate
    consulted."""
    store.open_epoch(repo=REPO, pr_number=8, head_sha=A, opened_at="t")
    request, calls = requester([[good_run()], [good_run()], [good_run()]])
    for _ in range(3):
        runtime.handle(request, REPO, pull(), store)
    assert calls["post"] == 0


# --- head movement -------------------------------------------------------------

def test_a_moved_head_records_the_transition_and_fails_closed(store):
    store.open_epoch(repo=REPO, pr_number=8, head_sha=A, opened_at="t")
    request, calls = requester([[], [good_run(head=B, run_id=99)]])
    out = runtime.handle(request, REPO, pull(head=B), store)
    assert out["head_transition"] == {"from": A, "to": B}
    assert out["action"] == "CONFIRMED" and calls["post"] == 1
    assert store.last_known_head(REPO, 8)["head_sha"] == B


def test_old_head_evidence_does_not_satisfy_the_new_head(store):
    store.open_epoch(repo=REPO, pr_number=8, head_sha=A, opened_at="t")
    request, calls = requester([[good_run(head=A)],
                                [good_run(head=B, run_id=99)]])
    out = runtime.handle(request, REPO, pull(head=B), store)
    assert out["action"] == "CONFIRMED", "a carrier on the old head is not adopted"
    assert calls["post"] == 1


def test_no_epoch_opens_one_and_fails_closed(store):
    request, calls = requester([[], [good_run(run_id=99)]])
    out = runtime.handle(request, REPO, pull(), store)
    assert out["scope_state"] == ep.NO_EPOCH
    assert out["action"] == "CONFIRMED" and calls["post"] == 1


# --- refusals -------------------------------------------------------------------

def test_unresolved_scope_stops_without_writing(store):
    store.record_migration(legacy_epoch="x", legacy_head="y" * 40,
                           mapped_to=None, justification="j",
                           source_artifact="a", at="t")
    request, calls = requester([])
    out = runtime.handle(request, REPO, pull(), store)
    assert out["action"] == "STOP" and out["writes"] == 0
    assert calls["post"] == 0


def test_a_draft_is_observed_and_never_written_to(store):
    request, calls = requester([])
    out = runtime.handle(request, REPO, pull(pr=12, draft=True), store)
    assert out["action"] == "OBSERVED_ONLY" and calls["post"] == 0


def test_an_unreadable_pr_list_asserts_nothing(store, monkeypatch):
    monkeypatch.setattr(runtime, "open_prs", lambda *a: None)
    result = runtime.pass_once(lambda *a, **k: None, REPO, "main", store)
    assert result["state"] == "UNREADABLE"
    assert "nothing is assumed" in result["cause"]


def test_dry_run_never_writes(store):
    request, calls = requester([])
    out = runtime.handle(request, REPO, pull(), store, write_enabled=False)
    assert out["action"] == "DRY_RUN" and calls["post"] == 0


# --- it is the composition, not a harness ---------------------------------------

def test_runtime_uses_the_scoped_store_and_not_the_legacy_history():
    source = (HERE / "harness" / "runtime.py").read_text()
    assert "decisions" not in source
    assert "EpochStore" in source
    assert "scoped_reconcile" in source


def test_runtime_re_mints_its_token_each_pass():
    """An installation token expires; a loop that runs for days on one is a
    loop that stops working quietly."""
    source = (HERE / "harness" / "runtime.py").read_text()
    loop = source.split("deadline = None if args.window")[1]
    assert "installation_token()" in loop
