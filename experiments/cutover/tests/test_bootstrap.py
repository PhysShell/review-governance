"""Tests for the A5b bootstrap writer — the first production write.

Written against what it must refuse, because the failure that matters here
is not an exception. A bootstrap that emitted a passing conclusion would
open the gate on every PR it touched, at the exact moment nobody is
watching for that.
"""
import json
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent.parent / "operational-readiness"
sys.path.insert(0, str(BASE / "harness"))

import bootstrap  # noqa: E402
import decisions as dec  # noqa: E402

REPO = "PhysShell/evm-from-scratch"
HEAD = "a" * 40
APP = 4669438


def run_body(conclusion="failure", name="ai/final-review", head=HEAD,
             app_id=APP, summary=None, run_id=1):
    return {"id": run_id, "head_sha": head, "name": name,
            "app": {"id": app_id}, "conclusion": conclusion,
            "output": {"summary": bootstrap.SUMMARY if summary is None
                       else summary}}


# --- the capability boundary --------------------------------------------------

@pytest.mark.parametrize("body", [
    {"name": "ai/final-review", "conclusion": "success"},
    {"name": "ai/final-review", "conclusion": "neutral"},
    {"name": "ai/final-review", "conclusion": "skipped"},
    {"name": "ai/final-review", "conclusion": None},
])
def test_bootstrap_cannot_publish_anything_but_failure(body):
    with pytest.raises(bootstrap.BootstrapCapability):
        bootstrap.guarded("POST", "/repos/x/check-runs", "t", body)


def test_bootstrap_cannot_write_another_context():
    with pytest.raises(bootstrap.BootstrapCapability):
        bootstrap.guarded("POST", "/repos/x/check-runs", "t",
                          {"name": "ai/something-else", "conclusion": "failure"})


@pytest.mark.parametrize("method,path", [
    ("PATCH", "/repos/x/check-runs/1"),
    ("DELETE", "/repos/x/check-runs/1"),
    ("POST", "/repos/x/statuses/abc"),
    ("PUT", "/repos/x/rulesets"),
])
def test_bootstrap_cannot_do_anything_else(method, path):
    with pytest.raises(bootstrap.BootstrapCapability):
        bootstrap.guarded(method, path, "t",
                          {"name": "ai/final-review", "conclusion": "failure"})


def test_module_has_no_path_to_a_passing_conclusion():
    """Structural: a future edit that adds one should have to delete this
    test to get away with it."""
    source = (BASE / "harness" / "bootstrap.py").read_text()
    assert 'ONLY_CONCLUSION = "failure"' in source
    assert '"conclusion": "success"' not in source
    assert "ruleset" not in source.lower()
    assert "merge" not in source.lower()


# --- exact matching -----------------------------------------------------------

def test_a_run_on_another_head_does_not_count():
    """Evidence about a commit nobody bootstrapped is not evidence."""
    assert bootstrap.matches(run_body(head="b" * 40), HEAD) is False


def test_another_app_does_not_count():
    assert bootstrap.matches(run_body(app_id=999), HEAD) is False


def test_missing_verdict_in_summary_does_not_count():
    """The conclusion says failing; only the summary says *why*."""
    assert bootstrap.matches(run_body(summary="something else"), HEAD) is False


def test_exact_match_counts():
    assert bootstrap.matches(run_body(), HEAD) is True


# --- ambiguity is never cured by another write --------------------------------

def _harness(monkeypatch, tmp_path, before, after, post=(201, {"id": 1})):
    calls = {"post": 0}
    states = iter([before, after])

    def fake_guarded(method, path, token, body=None):
        if method == "GET":
            nxt = next(states)
            if nxt is None:
                return 500, None
            return 200, {"check_runs": nxt}
        calls["post"] += 1
        return post

    monkeypatch.setattr(bootstrap, "guarded", fake_guarded)
    history = dec.History(tmp_path / "d.sqlite3")
    item = {"pr_number": 8, "head_sha": HEAD, "draft": False}
    result = bootstrap.bootstrap_one("t", REPO, item, history)
    history.close()
    return result, calls


def test_lost_response_is_uncertain_not_retried(monkeypatch, tmp_path):
    """Zero carriers after one POST: the write may or may not have landed.
    Re-POSTing would turn that into a duplicate or a second unknown."""
    result, calls = _harness(monkeypatch, tmp_path, before=[], after=[],
                             post=(None, None))
    assert result["state"] == "UNCERTAIN"
    assert calls["post"] == 1
    assert "NOT retrying" in result["cause"]


def test_duplicate_carriers_are_uncertain(monkeypatch, tmp_path):
    result, calls = _harness(
        monkeypatch, tmp_path, before=[],
        after=[run_body(run_id=1), run_body(run_id=2)])
    assert result["state"] == "UNCERTAIN"
    assert result["matching"] == [1, 2]
    assert calls["post"] == 1


def test_exactly_one_carrier_is_confirmed(monkeypatch, tmp_path):
    result, calls = _harness(monkeypatch, tmp_path, before=[],
                             after=[run_body(run_id=7)])
    assert result["state"] == "CONFIRMED"
    assert result["check_run_id"] == 7
    assert result["observed"]["conclusion"] == "failure"
    assert calls["post"] == 1


def test_unreadable_precheck_never_becomes_absence(monkeypatch, tmp_path):
    """A failed read is not proof that nothing is there."""
    result, calls = _harness(monkeypatch, tmp_path, before=None, after=[])
    assert result["state"] == "UNCERTAIN"
    assert "absence not established" in result["cause"]
    assert calls["post"] == 0, "must not write when the zero point is unknown"


def test_existing_carrier_refuses_rather_than_stacking(monkeypatch, tmp_path):
    result, calls = _harness(monkeypatch, tmp_path,
                             before=[run_body(run_id=3)], after=[])
    assert result["state"] == "REFUSED"
    assert "zero point is not clean" in result["cause"]
    assert calls["post"] == 0


# --- it acts on the freeze, not on a fresh list -------------------------------

def test_run_refuses_an_invalid_freeze(tmp_path):
    bad = tmp_path / "inv.json"
    bad.write_text(json.dumps({"frozen": False, "refusal": "not quiescent"}))

    class Args:
        inventory = str(bad)
        db = str(tmp_path / "d.sqlite3")

    result = bootstrap.run(Args())
    assert "error" in result
    assert result["refusal"] == "not quiescent"


def test_run_never_enumerates_pulls_itself():
    """Re-reading GitHub here would make the bootstrap its own baseline —
    the pulls[0] shape, one layer up."""
    source = (BASE / "harness" / "bootstrap.py").read_text()
    assert "/pulls" not in source
    assert "inventory" in source
