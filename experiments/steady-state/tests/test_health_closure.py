"""A6f-c2: health as something produced, not as three recent files.

`last_complete_pass_at = now` is trivially writable by whatever is asked to
write it, so a guard consuming it learns only that a process ran — not that
the process did the thing the guard needs done. Both required signals are
therefore checked at their producer:

    reconciliation   must carry the scoped comparisons themselves
    watchdog         must carry a value the edge produced, relayed

and the driver rows check the two remaining places where a caller could
still supply a prerequisite's shape instead of establishing it.
"""
import datetime
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "harness"))
sys.path.insert(0, str(HERE.parents[0] / "operational-readiness" / "harness"))

import auth_policy as ap  # noqa: E402
import auth_state  # noqa: E402
import epochs as ep  # noqa: E402
import governed_round as gr  # noqa: E402
import health as health_mod  # noqa: E402
import rounds  # noqa: E402
import runtime  # noqa: E402
import sentinel  # noqa: E402
import snapshots  # noqa: E402

REPO = "PhysShell/evm-from-scratch"
A = "7ad19f5e72a13a8fbd10ba9f6a2b0ea4bf430f52"
B = "b" * 40


def now_stamp(delta=0):
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(seconds=delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- 1. the reconciliation signal is the comparisons ---------------------------

def _pass_result(reconciliations):
    return {"at": now_stamp(), "state": "OK", "pr_count": len(reconciliations),
            "writes": 0, "outcomes": [], "reconciliations": reconciliations}


def _compared(pr, stored=A, github=A):
    return {"repo": REPO, "pr_number": pr, "at": now_stamp(),
            "scope_state": ep.RESOLVED, "stored_head": stored,
            "github_head": github, "comparison_performed": True,
            "drift_detected": stored != github}


def test_the_reconciliation_signal_carries_the_comparisons(tmp_path):
    out = tmp_path / "reconciliation-health.json"
    runtime.write_reconciliation_health(
        str(out), _pass_result([_compared(8), _compared(32)]))
    blob = json.loads(out.read_text())
    assert blob["comparisons_attempted"] == 2
    assert blob["comparisons_performed"] == 2
    assert blob["all_compared"] is True
    assert {p["pr_number"] for p in blob["per_pr"]} == {8, 32}
    for p in blob["per_pr"]:
        assert p["scope_state"] == ep.RESOLVED
        assert p["stored_head"] and p["github_head"]
        assert p["comparison_performed"] is True


def test_a_pass_that_compared_nothing_says_so(tmp_path):
    """A file written on schedule proves the loop is alive. It must not be
    able to imply that the loop did the comparison the guard depends on."""
    out = tmp_path / "h.json"
    unresolved = {"repo": REPO, "pr_number": 32, "at": now_stamp(),
                  "scope_state": ep.UNRESOLVED, "stored_head": None,
                  "github_head": A, "comparison_performed": False,
                  "drift_detected": None}
    runtime.write_reconciliation_health(str(out), _pass_result([unresolved]))
    blob = json.loads(out.read_text())
    assert blob["all_compared"] is False
    assert blob["comparisons_performed"] == 0
    assert blob["per_pr"][0]["drift_detected"] is None


def test_a_pass_over_no_prs_is_not_all_compared(tmp_path):
    out = tmp_path / "h.json"
    runtime.write_reconciliation_health(str(out), _pass_result([]))
    assert json.loads(out.read_text())["all_compared"] is False


def test_the_signal_is_written_by_the_pass_not_by_a_caller(tmp_path,
                                                           monkeypatch):
    """Produced inside the loop that performs the comparisons, so it cannot
    be refreshed by anything that did not run them."""
    store = ep.EpochStore(tmp_path / "e.sqlite3")
    store.open_epoch(repo=REPO, pr_number=32, head_sha=A, opened_at="t")
    rs = rounds.RoundStore(tmp_path / "r.sqlite3")

    def request(method, path, tok=None, body=None):
        if path.endswith("/pulls?state=open&base=main&per_page=100"):
            return 200, [{"number": 32, "head": {"sha": A}, "draft": False,
                          "base": {"ref": "main"}}]
        if path.endswith("/pulls/32"):
            return 200, {"head": {"sha": A}}
        return 200, {"check_runs": []}

    result = runtime.pass_once(request, REPO, "main", store,
                              write_enabled=False, round_store=rs)
    out = tmp_path / "h.json"
    runtime.write_reconciliation_health(str(out), result)
    blob = json.loads(out.read_text())
    assert blob["all_compared"] is True
    assert blob["per_pr"][0]["stored_head"] == A
    assert blob["per_pr"][0]["github_head"] == A
    assert health_mod.observe("reconciliation", str(out)).state == health_mod.FRESH
    store.close()
    rs.close()


# --- 1b. and the sentinel reads what that producer writes ----------------------

class _Notifier:
    def __init__(self):
        self.raised, self.cleared = [], []

    def raise_(self, severity, cause, **kw):
        self.raised.append(cause)

    def clear(self, cause, **kw):
        self.cleared.append(cause)


def _args(tmp_path, blob=None):
    path = tmp_path / "reconciliation-health.json"
    if blob is not None:
        path.write_text(json.dumps(blob))

    class Args:
        repo = REPO
        health_file = str(path)
        auth_state_file = str(tmp_path / "none.json")
        reconciliation_max_age = 120
        startup_grace = 0

    return Args()


def test_the_sentinel_reads_the_field_the_runtime_writes(tmp_path):
    """The cut-in retired the legacy loop that wrote `last_complete_sweep_at`.
    A reader still looking for that name would page about a runtime that is
    reconciling perfectly."""
    out = tmp_path / "reconciliation-health.json"
    runtime.write_reconciliation_health(
        str(out), _pass_result([_compared(8), _compared(32)]))
    n = _Notifier()
    state = sentinel.check_reconciliation(_args(tmp_path), n)
    assert state["state"] == "HEALTHY", state
    assert n.raised == [] and n.cleared == ["reconciliation_stale"]


def test_a_recent_pass_that_compared_nothing_is_not_healthy(tmp_path):
    """Freshness says a process ran. A pass in which every PR was UNRESOLVED
    writes a timestamp exactly as recent as one that compared them all."""
    n = _Notifier()
    state = sentinel.check_reconciliation(
        _args(tmp_path, {"last_complete_pass_at": now_stamp(5),
                         "comparisons_attempted": 2,
                         "comparisons_performed": 0, "all_compared": False}), n)
    assert state["state"] == "NOT_COMPARED"
    assert n.raised == ["reconciliation_stale"]


def test_an_old_pass_still_pages_as_stale(tmp_path):
    n = _Notifier()
    state = sentinel.check_reconciliation(
        _args(tmp_path, {"last_complete_pass_at": now_stamp(9999),
                         "comparisons_attempted": 2,
                         "comparisons_performed": 2, "all_compared": True}), n)
    assert state["state"] == "STALE"
    assert n.raised == ["reconciliation_stale"]


# --- 2. the watchdog signal comes from the edge --------------------------------

def test_the_watchdog_signal_relays_the_edge_value(tmp_path):
    out = tmp_path / "watchdog-health.json"
    poll = now_stamp(3)
    sentinel.write_watchdog_health(
        str(out), {"last_watchdog_poll": poll, "watchdog_polls": 4211},
        now_stamp())
    blob = json.loads(out.read_text())
    assert blob["last_complete_pass_at"] == poll
    assert blob["watchdog_polls"] == 4211
    assert "produced by the watchdog process" in blob["source"]
    assert blob["relayed_by"] == "primary sentinel"
    assert blob["edge_reachable"] is True
    assert health_mod.observe("watchdog", str(out)).state == health_mod.FRESH


def test_a_stopped_watchdog_makes_the_signal_stale_even_while_relayed(tmp_path):
    """The primary keeps running and keeps writing. What must not happen is
    the relay refreshing a value the watchdog stopped producing."""
    out = tmp_path / "w.json"
    sentinel.write_watchdog_health(
        str(out), {"last_watchdog_poll": now_stamp(9999), "watchdog_polls": 7},
        now_stamp())
    obs = health_mod.observe("watchdog", str(out))
    assert obs.state == health_mod.STALE
    assert obs.age_seconds > obs.bound
    assert json.loads(out.read_text())["relayed_at"] > obs.observed_at


def test_an_unreachable_edge_produces_no_watchdog_value(tmp_path):
    out = tmp_path / "w.json"
    sentinel.write_watchdog_health(str(out), None, now_stamp())
    blob = json.loads(out.read_text())
    assert blob["last_complete_pass_at"] is None
    assert blob["edge_reachable"] is False
    assert health_mod.observe("watchdog", str(out)).state == health_mod.UNREADABLE


def test_the_sweep_writes_the_watchdog_signal_only_from_a_healthz(tmp_path,
                                                                  monkeypatch):
    """`sweep` relays when the edge answered and stays silent when it did
    not — an absent reading must not be replaced by the primary's own."""
    calls = []
    monkeypatch.setattr(sentinel, "write_watchdog_health",
                        lambda p, h, a: calls.append(h))
    monkeypatch.setattr(sentinel, "check_reconciliation", lambda *a: {})
    monkeypatch.setattr(sentinel, "check_installation_token", lambda *a: {})
    monkeypatch.setattr(sentinel, "check_auth_state", lambda *a: {})
    monkeypatch.setattr(sentinel, "check_watchdog", lambda *a: {})
    monkeypatch.setattr(sentinel, "check_ruleset_bypass", lambda *a: {})

    class Args:
        watchdog_health = str(tmp_path / "w.json")

    monkeypatch.setattr(sentinel, "check_edge_receiver",
                        lambda a, n: ({}, {"last_watchdog_poll": now_stamp()}))
    sentinel.sweep(Args(), None)
    assert len(calls) == 1

    monkeypatch.setattr(sentinel, "check_edge_receiver", lambda a, n: ({}, None))
    sentinel.sweep(Args(), None)
    assert len(calls) == 1, "no healthz, no relayed watchdog value"


# --- 3. the two remaining shapes the driver must establish itself --------------

@pytest.fixture()
def driver(tmp_path):
    auth = auth_state.AuthStore(tmp_path / "a.sqlite3")
    auth.record(state="AUTHORIZED", auth_generation=5,
                observed_at=now_stamp(), source="refresh")
    rs = rounds.RoundStore(tmp_path / "r.sqlite3")
    snaps = snapshots.SnapshotStore(tmp_path / "s.sqlite3")
    epochs = ep.EpochStore(tmp_path / "e.sqlite3")
    reads = []

    def read(method, path):
        reads.append(path)
        if path.endswith("/pulls/32"):
            return 200, {"head": {"sha": A}, "draft": False,
                         "base": {"ref": "main"}, "state": "open"}
        if "/issues/32/comments" in path:
            return 200, []
        return 404, None

    d = gr.GovernedRound(repo=REPO, pr_number=32, read=read,
                         post=lambda p, b: (_ for _ in ()).throw(
                             AssertionError("no provider may be contacted")),
                         auth_store=auth, round_store=rs, snapshot_store=snaps,
                         epoch_store=epochs, health_sources={})
    d.reads = reads
    yield d
    for s in (auth, rs, snaps, epochs):
        s.close()


def test_the_driver_captures_the_baseline_itself(driver):
    """Not accepted as an argument: a dict shaped like a baseline is not a
    reading, and only the reader knows whether the read succeeded."""
    row = driver.capture_baseline("coderabbit")
    assert row["baseline_id"] and row["read_ok"] is True
    assert row["captured_at"]
    assert any("/issues/32/comments" in p for p in driver.reads)


def test_an_unreadable_provider_surface_stops_the_round(driver):
    driver.read = lambda method, path: (503, None)
    out = driver.capture_baseline("coderabbit")
    assert out["state"] == gr.STOP
    assert "unread baseline is not an empty one" in out["cause"]


def test_conclude_refuses_without_a_standing_acceptance(driver):
    """A -> B -> A: the SHA matches again, but the acceptance for the first
    A was invalidated, and evidence must not outlive the transition that
    ended it."""
    permission = ap.evaluate(driver.auth)
    from conftest import FakeGitHub, record_observation
    from conftest import EPOCH
    obs = record_observation(driver.rounds, github=FakeGitHub())
    driver.rounds.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                    head_sha=A, permission=permission,
                                    observation_id=obs["observation_id"])
    driver.rounds.invalidate_for_head_move(REPO, 32, B)
    driver.rounds.invalidate_for_head_move(REPO, 32, A)

    out = driver.conclude([{"requested_for_head": A, "provider": "codex",
                            "generation": 1, "state": "ANSWERED"}],
                          epoch_id=EPOCH, existing_run=1,
                          patch=lambda *a, **k: (_ for _ in ()).throw(
                              AssertionError("nothing may be published")))
    assert out["state"] == gr.STOP
    assert "does not revive" in out["cause"]


def test_conclude_refuses_when_health_is_not_configured(driver):
    """The required set is exact: a driver with no health sources has three
    ABSENT signals, and ABSENT is not healthy."""
    out = health_mod.evaluate(driver.health_sources)
    assert out["all_fresh"] is False
    assert sorted(out["not_fresh"]) == sorted(health_mod.REQUIRED)
