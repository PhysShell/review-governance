"""Tests for A5b step 5 — the negative production smoke test.

The failure mode worth guarding is not a crash. It is a green report
produced by a block that happened for the wrong reason, in a stage whose
own protocol warns that GitHub returns the identical message for two
different causes.
"""
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent.parent / "operational-readiness"
sys.path.insert(0, str(BASE / "harness"))

import smoke  # noqa: E402

REPO = "PhysShell/evm-from-scratch"
MAIN = "m" * 40
HEAD = "h" * 40


def wire(monkeypatch, *, before=MAIN, after=None, base=MAIN, runs=None,
         verified=True, enforcement="active", merged=False,
         probe_head_after=HEAD, base_ref="main"):
    after = before if after is None else after
    mains = iter([before, after])
    monkeypatch.setattr(smoke, "main_sha", lambda repo: next(mains))
    monkeypatch.setattr(smoke, "merge_base", lambda r, b, h: base)
    monkeypatch.setattr(smoke, "production_runs",
                        lambda r, h: [] if runs is None else runs)
    monkeypatch.setattr(smoke.rs, "verify", lambda r, rid, enf: {
        "state": "VERIFIED" if verified else "MISMATCH",
        "observed_enforcement": enforcement})

    calls = {"merge": 0, "pr_reads": 0}

    def fake_gh_raw(*args, expect_json=True):
        if args[0] == "pr":
            return True, {"mergeStateStatus": "BLOCKED"}, ""
        if "-X" in args and "PUT" in args:
            calls["merge"] += 1
            return False, {"message": "… is expected."}, ""
        # the PR is read twice: once for the predicate, once after the
        # attempt. They must be allowed to differ, or a head that moved
        # mid-attempt is invisible to the test that exists to catch it.
        calls["pr_reads"] += 1
        sha = HEAD if calls["pr_reads"] == 1 else probe_head_after
        return True, {"head": {"sha": sha}, "base": {"ref": base_ref},
                      "merged": merged}, ""

    monkeypatch.setattr(smoke, "gh_raw", fake_gh_raw)
    return calls


class Args:
    repo = REPO
    pr = 31
    ruleset_id = 21640654


# --- the predicate gates the attempt ------------------------------------------

def test_blocked_on_a_fresh_checkless_probe_passes(monkeypatch):
    calls = wire(monkeypatch)
    r = smoke.run(Args())
    assert r["verdict"] == "NEGATIVE_SMOKE_TEST_BLOCKED"
    assert r["counted"] is True
    assert calls["merge"] == 1


def test_stale_base_never_reaches_the_attempt(monkeypatch):
    """A probe whose base drifted would be blocked by drift, and recording
    that as required-check evidence is the defect r1 caught."""
    calls = wire(monkeypatch, base="d" * 40)
    r = smoke.run(Args())
    assert r["verdict"] == "SMOKE_FIXTURE_STALE"
    assert r["counted"] is False
    assert calls["merge"] == 0


def test_any_existing_check_invalidates_the_fixture(monkeypatch):
    """Not 'no success' — no run of ANY conclusion. A failing check blocks
    for a different reason than a missing one, and the missing-check path is
    what this stage tests."""
    calls = wire(monkeypatch, runs=[{"id": 1, "conclusion": "failure"}])
    r = smoke.run(Args())
    assert r["verdict"] == "SMOKE_FIXTURE_STALE"
    assert calls["merge"] == 0


def test_inactive_ruleset_invalidates_the_fixture(monkeypatch):
    calls = wire(monkeypatch, enforcement="disabled")
    assert smoke.run(Args())["verdict"] == "SMOKE_FIXTURE_STALE"
    assert calls["merge"] == 0


def test_unverified_ruleset_invalidates_the_fixture(monkeypatch):
    calls = wire(monkeypatch, verified=False)
    assert smoke.run(Args())["verdict"] == "SMOKE_FIXTURE_STALE"
    assert calls["merge"] == 0


def test_unreadable_checks_never_become_absence(monkeypatch):
    calls = wire(monkeypatch, runs=None)
    monkeypatch.setattr(smoke, "production_runs", lambda r, h: None)
    r = smoke.run(Args())
    assert r["verdict"] == "SMOKE_FIXTURE_STALE"
    assert calls["merge"] == 0


def test_a_probe_not_targeting_main_is_stale(monkeypatch):
    calls = wire(monkeypatch, base_ref="governor/somewhere")
    assert smoke.run(Args())["verdict"] == "SMOKE_FIXTURE_STALE"
    assert calls["merge"] == 0


# --- movement during the attempt ----------------------------------------------

def test_main_moving_during_the_attempt_is_stale(monkeypatch):
    wire(monkeypatch, before=MAIN, after="n" * 40)
    r = smoke.run(Args())
    assert r["verdict"] == "SMOKE_FIXTURE_STALE"
    assert r["counted"] is False


def test_probe_head_moving_during_the_attempt_is_stale(monkeypatch):
    wire(monkeypatch, probe_head_after="z" * 40)
    assert smoke.run(Args())["verdict"] == "SMOKE_FIXTURE_STALE"


# --- a merge that succeeds is an incident, not a result -----------------------

def test_a_merged_probe_is_a_failure_and_says_so(monkeypatch):
    wire(monkeypatch, merged=True)
    r = smoke.run(Args())
    assert r["verdict"] == "FAIL"
    assert "incident, not a test result" in r["incident"]


# --- staleness can only invalidate, never rescue -------------------------------

def test_stale_is_neither_pass_nor_fail(monkeypatch):
    wire(monkeypatch, base="d" * 40)
    r = smoke.run(Args())
    assert r["counted"] is False
    assert r["verdict"] not in ("NEGATIVE_SMOKE_TEST_BLOCKED", "FAIL")


def test_merge_state_status_is_recorded_as_corroboration_only(monkeypatch):
    wire(monkeypatch)
    r = smoke.run(Args())
    assert r["merge_state_status"] == "BLOCKED"
    assert "rests on the freshness predicate" in r["corroborating_only"]


def test_module_has_no_path_that_forces_a_merge():
    source = (BASE / "harness" / "smoke.py").read_text()
    assert "merge_method" not in source
    assert "admin" not in source.lower()
    assert source.count('"PUT"') == 1, "exactly one merge attempt site"
