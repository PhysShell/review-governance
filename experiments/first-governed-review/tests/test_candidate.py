"""Tests for candidate selection before the first governed review.

Each one guards a load-bearing invariant, and each corresponds to a way
this programme has already been wrong once.
"""
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent.parent / "operational-readiness"
sys.path.insert(0, str(BASE / "harness"))

import bootstrap  # noqa: E402
import candidate  # noqa: E402

REPO = "PhysShell/evm-from-scratch"
NEW = "2" * 40
OLD = "8" * 40


def pr(number, head=NEW, draft=False, base="main"):
    return {"repo": REPO, "base": base, "pr_number": number,
            "head_sha": head, "head_ref": "x", "draft": draft, "title": "t"}


def run(head=NEW, run_id=1, conclusion="failure"):
    return {"id": run_id, "head_sha": head, "name": "ai/final-review",
            "app": {"id": 4669438}, "conclusion": conclusion,
            "output": {"title": "Governor: NOT_ESTABLISHED",
                       "summary": bootstrap.SUMMARY}}


# --- selection does not depend on ordering ------------------------------------

def test_selection_is_identical_under_any_ordering():
    """`pulls[0]` is the defect this programme has found most often. The
    result must not depend on what the API happened to return first."""
    items = [pr(8), pr(12, draft=True), pr(20, base="develop")]
    forward = candidate.select(items)["selected"]
    backward = candidate.select(list(reversed(items)))["selected"]
    assert forward["pr_number"] == backward["pr_number"] == 8


def test_two_eligible_candidates_is_a_human_decision_not_a_tie_break():
    result = candidate.select([pr(8), pr(9, head="9" * 40)])
    assert result["selected"] is None
    assert "human decision" in result["cause"]


def test_no_eligible_candidate_selects_nothing():
    result = candidate.select([pr(12, draft=True)])
    assert result["selected"] is None
    assert "no open non-draft PR" in result["cause"]


# --- a draft is never a candidate ---------------------------------------------

def test_draft_is_excluded_with_a_named_reason():
    scored = candidate.select([pr(12, draft=True)])["scored"][0]
    assert scored["eligible"] is False
    assert "draft" in scored["excluded_because"]


def test_a_pr_against_another_base_is_excluded():
    scored = candidate.select([pr(20, base="develop")])["scored"][0]
    assert scored["eligible"] is False
    assert any("not main" in r for r in scored["excluded_because"])


# --- the preselection is confirmed, never adjusted to ------------------------

def test_a_stale_preselection_stops_rather_than_adapting(monkeypatch):
    """If the named PR is no longer the one the snapshot selects, that is a
    STOP. Silently following the snapshot would make the roadmap the
    authority; silently following the roadmap would make it `pulls[0]`."""
    monkeypatch.setattr(candidate, "carrier_for",
                        lambda r, h, t: {"state": "ABSENT"})
    art = candidate.build(REPO, 999, "tok", [pr(8)], {"state": "VERIFIED_ACTIVE"})
    assert art["candidate_state"] == "STOP_PRESELECTION_STALE"
    assert "head_sha" not in art


def test_matching_preselection_proceeds(monkeypatch):
    monkeypatch.setattr(candidate, "carrier_for",
                        lambda r, h, t: {"state": "ABSENT"})
    art = candidate.build(REPO, 8, "tok", [pr(8)], {"state": "VERIFIED_ACTIVE"})
    assert art["candidate_state"] == "READY_FOR_ACCEPT_CANDIDATE"
    assert art["pr_number"] == 8


# --- binding is to a full head ------------------------------------------------

def test_artifact_binds_the_full_head_not_an_abbreviation(monkeypatch):
    monkeypatch.setattr(candidate, "carrier_for",
                        lambda r, h, t: {"state": "ABSENT"})
    art = candidate.build(REPO, 8, "tok", [pr(8)], {"state": "VERIFIED_ACTIVE"})
    assert art["head_sha"] == NEW
    assert len(art["head_sha"]) == 40


def test_an_abbreviated_head_is_refused(monkeypatch):
    monkeypatch.setattr(candidate, "carrier_for",
                        lambda r, h, t: {"state": "ABSENT"})
    art = candidate.build(REPO, 8, "tok", [pr(8, head="2d834870")],
                          {"state": "VERIFIED_ACTIVE"})
    assert art["candidate_state"] == "STOP_ABBREVIATED_HEAD"


# --- evidence does not migrate when a branch moves ----------------------------

def test_a_carrier_on_the_old_head_does_not_count_for_the_new_one(monkeypatch):
    """Evidence is commit-bound. A run on the pre-merge head is evidence
    about that commit, and a moved head must fail closed rather than
    inherit."""
    monkeypatch.setattr(bootstrap, "carriers", lambda t, r, h: [run(head=OLD)])
    state = candidate.carrier_for(REPO, NEW, "tok")
    assert state["state"] == "ABSENT"


def test_a_carrier_on_the_exact_head_is_present(monkeypatch):
    monkeypatch.setattr(bootstrap, "carriers", lambda t, r, h: [run(head=NEW)])
    state = candidate.carrier_for(REPO, NEW, "tok")
    assert state["state"] == "PRESENT"
    assert state["conclusion"] == "failure"


def test_two_carriers_on_one_head_are_ambiguous(monkeypatch):
    monkeypatch.setattr(bootstrap, "carriers",
                        lambda t, r, h: [run(run_id=1), run(run_id=2)])
    assert candidate.carrier_for(REPO, NEW, "tok")["state"] == "AMBIGUOUS"


def test_unreadable_carriers_never_become_absence(monkeypatch):
    monkeypatch.setattr(bootstrap, "carriers", lambda t, r, h: None)
    assert candidate.carrier_for(REPO, NEW, "tok")["state"] == "UNREADABLE"


# --- a provider round cannot start from here ----------------------------------

def test_no_provider_trigger_code_exists_on_this_branch():
    """`provider_round: NOT_STARTED` is structural here, not merely current.
    Nothing on this branch can post the comment that invokes a provider."""
    harness = BASE / "harness"
    offenders = []
    for path in harness.glob("*.py"):
        text = path.read_text().lower()
        if "issues/" in text and "comments" in text:
            offenders.append(path.name)
        if "@coderabbitai" in text or "@codex" in text:
            offenders.append(path.name)
    assert offenders == [], offenders


def test_candidate_module_declares_the_round_not_started(monkeypatch):
    monkeypatch.setattr(candidate, "carrier_for",
                        lambda r, h, t: {"state": "ABSENT"})
    art = candidate.build(REPO, 8, "tok", [pr(8)], {"state": "VERIFIED_ACTIVE"})
    assert art["provider_round"] == "NOT_STARTED"


def test_candidate_module_is_read_only():
    import ast
    source = (BASE / "harness" / "candidate.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            node.value.value = ""
    code = ast.unparse(tree)
    for forbidden in ("POST", "PATCH", "PUT", "DELETE"):
        assert forbidden not in code, forbidden


# --- the base update is re-derived, not transcribed ---------------------------

def test_ancestry_detects_a_rewritten_history(monkeypatch):
    """If the old head is not an ancestor of the new one, the branch was
    rebased or force-pushed and the audit chain is broken."""
    def fake_gh(*args, body=None):
        if "compare" in args[-1] and args[-1].endswith("8" * 40 + "..." + "2" * 40):
            return True, {"status": "diverged", "ahead_by": 3, "behind_by": 2,
                          "merge_base_commit": {"sha": "f" * 40}}
        return True, {"status": "ahead", "ahead_by": 5, "behind_by": 0,
                      "merge_base_commit": {"sha": "m" * 40}}

    monkeypatch.setattr(candidate.rs, "gh", fake_gh)
    a = candidate.ancestry(REPO, OLD, NEW, "m" * 40)
    assert a["old_head_is_ancestor"] is False
    assert a["history_rewritten"] is True


def test_ancestry_confirms_a_clean_merge(monkeypatch):
    monkeypatch.setattr(candidate.rs, "gh", lambda *a, body=None: (
        True, {"status": "ahead", "ahead_by": 5, "behind_by": 0,
               "merge_base_commit": {"sha": "m" * 40}}))
    a = candidate.ancestry(REPO, OLD, NEW, "m" * 40)
    assert a["old_head_is_ancestor"] is True
    assert a["main_is_ancestor"] is True
    assert a["merge_base_is_main"] is True
    assert a["history_rewritten"] is False


def test_merge_base_not_main_means_still_behind(monkeypatch):
    """A branch whose merge base is not current main can be blocked by
    drift, which would make any later gate observation ambiguous."""
    monkeypatch.setattr(candidate.rs, "gh", lambda *a, body=None: (
        True, {"status": "diverged", "ahead_by": 5, "behind_by": 1,
               "merge_base_commit": {"sha": "f" * 40}}))
    a = candidate.ancestry(REPO, OLD, NEW, "m" * 40)
    assert a["merge_base_is_main"] is False
    assert a["main_is_ancestor"] is False
