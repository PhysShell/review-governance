"""Tests for A5b step 3b — pre-activation closure.

Its only useful outputs are "closed" and "stop", so every test here is
about a way reality can have moved and whether the module notices. A
closure check that quietly passes is worse than none: it would license the
ruleset flip on the strength of a look nobody took.
"""
import json
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent.parent / "operational-readiness"
sys.path.insert(0, str(BASE / "harness"))

import bootstrap  # noqa: E402
import closure  # noqa: E402

REPO = "PhysShell/evm-from-scratch"
A, B = "a" * 40, "b" * 40


def item(pr=8, head=A, base="main", draft=False):
    return {"repo": REPO, "base": base, "pr_number": pr, "head_sha": head,
            "draft": draft, "head_ref": "x", "title": "t"}


def good_run(head=A, run_id=1):
    return {"id": run_id, "head_sha": head, "name": "ai/final-review",
            "app": {"id": 4669438}, "conclusion": "failure",
            "output": {"summary": bootstrap.SUMMARY}}


# --- every delta category is named --------------------------------------------

def test_new_pr_is_a_delta():
    d = closure.diff_inventories([item(8)], [item(8), item(9, head=B)])
    assert [x["kind"] for x in d] == ["new_pr"]


def test_closed_pr_is_a_delta():
    d = closure.diff_inventories([item(8), item(9, head=B)], [item(8)])
    assert [x["kind"] for x in d] == ["closed_pr"]


def test_head_moved_is_a_delta():
    d = closure.diff_inventories([item(8, head=A)], [item(8, head=B)])
    assert d[0]["kind"] == "head_moved"
    assert d[0]["was"] == A and d[0]["now"] == B


def test_base_change_is_a_delta():
    d = closure.diff_inventories([item(8)], [item(8, base="develop")])
    assert d[0]["kind"] == "base_changed"


def test_draft_change_is_a_delta():
    """Included because the freeze committed to it, not because it changes
    the gate."""
    d = closure.diff_inventories([item(8, draft=False)], [item(8, draft=True)])
    assert d[0]["kind"] == "draft_changed"


def test_identical_inventories_have_no_delta():
    assert closure.diff_inventories([item(8), item(9, head=B)],
                                    [item(8), item(9, head=B)]) == []


# --- carrier problems are named separately ------------------------------------

def _carrier(monkeypatch, runs):
    monkeypatch.setattr(bootstrap, "carriers", lambda t, r, h: runs)
    return closure.carrier_state("t", REPO, item())


def test_exactly_one_matching_carrier_is_confirmed(monkeypatch):
    assert _carrier(monkeypatch, [good_run()])["state"] == "CONFIRMED"


def test_missing_carrier(monkeypatch):
    assert _carrier(monkeypatch, [])["state"] == "MISSING"


def test_duplicate_carrier(monkeypatch):
    s = _carrier(monkeypatch, [good_run(run_id=1), good_run(run_id=2)])
    assert s["state"] == "DUPLICATE"


def test_unreadable_is_not_absence(monkeypatch):
    s = _carrier(monkeypatch, None)
    assert s["state"] == "UNREADABLE"
    assert "not established" in s["cause"]


def test_wrong_conclusion_is_a_mismatch(monkeypatch):
    run = {**good_run(), "conclusion": "success"}
    assert _carrier(monkeypatch, [run])["state"] == "MISMATCH"


def test_carrier_on_another_head_is_a_mismatch(monkeypatch):
    assert _carrier(monkeypatch, [good_run(head=B)])["state"] == "MISMATCH"


# --- the verdict ---------------------------------------------------------------

def _close(monkeypatch, tmp_path, frozen, current, runs, second=None):
    art = tmp_path / "inv.json"
    art.write_text(json.dumps({"repo": REPO, "base": "main",
                               "inventory": frozen, "inventory_hash": "h",
                               "frozen": True}))
    monkeypatch.setattr(closure.governor, "installation_token", lambda: "t")
    passes = iter([current, second if second is not None else current])
    monkeypatch.setattr(closure.inv, "enumerate_open",
                        lambda t, r, b: next(passes))
    monkeypatch.setattr(bootstrap, "carriers",
                        lambda t, r, h: runs.get(h))

    class Args:
        inventory = str(art)

    return closure.close(Args())


def test_closed_when_everything_matches(monkeypatch, tmp_path):
    r = _close(monkeypatch, tmp_path, [item(8)], [item(8)],
               {A: [good_run()]})
    assert r["verdict"] == "CLOSED"
    assert r["preactivation_current_inventory_closed"] is True
    assert "not a lock" in r["scope_note"]


def test_stop_on_a_new_pr_even_if_it_has_a_carrier(monkeypatch, tmp_path):
    """A new PR is a delta whether or not somebody bootstrapped it — the
    freeze did not commit to it."""
    r = _close(monkeypatch, tmp_path, [item(8)], [item(8), item(9, head=B)],
               {A: [good_run()], B: [good_run(head=B)]})
    assert r["verdict"] == "STOP"
    assert [d["kind"] for d in r["deltas"]] == ["new_pr"]


def test_stop_on_a_missing_carrier(monkeypatch, tmp_path):
    r = _close(monkeypatch, tmp_path, [item(8)], [item(8)], {A: []})
    assert r["verdict"] == "STOP"
    assert r["carrier_states"][0]["state"] == "MISSING"


def test_stop_when_the_set_moves_during_the_observation(monkeypatch, tmp_path):
    """A change during the read invalidates the read."""
    r = _close(monkeypatch, tmp_path, [item(8)], [item(8)], {A: [good_run()]},
               second=[item(8, head=B)])
    assert r["verdict"] == "STOP"
    assert any(d["kind"] == "changed_during_observation" for d in r["deltas"])


def test_stop_never_offers_to_repair(monkeypatch, tmp_path):
    r = _close(monkeypatch, tmp_path, [item(8)], [item(8)], {A: []})
    assert "Do not bootstrap the delta in place" in r["required_action"]


# --- read-only ------------------------------------------------------------------

def test_closure_has_no_write_path():
    """Asserted against the code, not the prose — the module's own docstring
    necessarily names the ruleset it must not touch."""
    import ast
    source = (BASE / "harness" / "closure.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            node.value.value = ""          # blank every docstring
    code = ast.unparse(tree)
    for forbidden in ("POST", "PATCH", "PUT", "DELETE", "governor_write"):
        assert forbidden not in code, forbidden
    # and it reaches GitHub only through readers
    assert "bootstrap.carriers" in code
    assert "inv.enumerate_open" in code


def test_one_good_carrier_beside_a_bad_one_is_ambiguous(monkeypatch):
    """A head carrying two Governor runs of the same context shows an
    operator two verdicts from the same App, with no way to tell which one
    the gate consulted."""
    bad = {**good_run(run_id=2), "conclusion": "cancelled"}
    s = _carrier(monkeypatch, [good_run(run_id=1), bad])
    assert s["state"] == "AMBIGUOUS"
    assert s["matching"] == [1]


def test_clean_head_still_confirms(monkeypatch):
    assert _carrier(monkeypatch, [good_run()])["state"] == "CONFIRMED"
