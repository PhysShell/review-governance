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
    art = candidate.build(REPO, 8, "tok", [pr(8)], {"state": "VERIFIED_ACTIVE"},
                          runtime={"complete": True, "missing": []})
    assert art["candidate_state"] == "ESTABLISHED"
    assert art["accept_candidate"] == "READY_FOR_ACCEPT_CANDIDATE"
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

    Matched against call sites rather than words: the first version of this
    test flagged the very module written to prove there is no trigger,
    because that module names the endpoints it searches for."""
    assert candidate.runtime_readiness(BASE / "harness")[
        "provider_trigger_lineage"] is False


def test_candidate_module_declares_the_round_not_started(monkeypatch):
    monkeypatch.setattr(candidate, "carrier_for",
                        lambda r, h, t: {"state": "ABSENT"})
    art = candidate.build(REPO, 8, "tok", [pr(8)], {"state": "VERIFIED_ACTIVE"})
    assert art["provider_round"] == "NOT_STARTED"


def test_candidate_module_is_read_only():
    """Asserted by call shape, not by word.

    The module must contain the strings "POST" and "/issues/" in order to
    search for them, so a substring ban would forbid the checker from doing
    its job. What matters is that no call *opens* with a write method and
    that gh is never handed an explicit method flag."""
    import re
    source = (BASE / "harness" / "candidate.py").read_text()
    assert '"-X"' not in source, "gh method flag present"
    for method in ("POST", "PATCH", "PUT", "DELETE"):
        opens_with = re.compile(r'\(\s*"%s"\s*,' % method)
        assert not opens_with.search(source), method


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


# --- the readiness checker must not find itself -------------------------------

def test_readiness_check_excludes_itself():
    """The first version matched its own search literals and reported that
    every capability existed — including a provider trigger, inside the
    module written to prove there is none. Substring matching over source
    is how an instrument becomes a mirror."""
    harness = BASE / "harness"
    r = candidate.runtime_readiness(harness)
    assert "candidate.py" not in r["production_carrier_producers"]
    assert r["provider_trigger_lineage"] is False


def test_readiness_matches_call_sites_not_words(tmp_path):
    decoy = tmp_path / "decoy.py"
    decoy.write_text('# mentions "POST" and "/issues/comments" and\n'
                     '# PRODUCTION_CONTEXT = "ai/final-review" in prose only\n')
    (tmp_path / "decisions.py").write_text('SCHEMA = ""\n')
    r = candidate.runtime_readiness(tmp_path, exclude=())
    assert r["provider_trigger_lineage"] is False
    assert r["steady_state_carrier_producer"] is False


def test_bootstrap_is_not_a_steady_state_producer():
    """It posts the production context but only from a frozen inventory —
    a one-shot cutover instrument, not a runtime."""
    r = candidate.runtime_readiness(BASE / "harness")
    assert "bootstrap.py" in r["production_carrier_producers"]
    assert r["steady_state_carrier_producer"] is False


def test_incomplete_runtime_holds_accept_candidate(monkeypatch):
    monkeypatch.setattr(candidate, "carrier_for",
                        lambda r, h, t: {"state": "ABSENT"})
    art = candidate.build(REPO, 8, "tok", [pr(8)], {"state": "VERIFIED_ACTIVE"},
                          runtime={"complete": False, "missing": ["x"]})
    assert art["candidate_state"] == "ESTABLISHED"
    assert art["accept_candidate"] == "HOLD"
    assert art["cause"] == "PRODUCTION_RUNTIME_INCOMPLETE"


# --- the naive reconcile fix would be worse than the bug ----------------------

def test_naive_epoch_prefix_fix_would_return_another_prs_head():
    """Documents why swapping the a5a- filter for bootstrap- must not be
    the fix. `decisions` carries no repo or pr_number, so the predicate
    cannot be PR-scoped at all: reversed history returns the most recent
    bootstrap epoch of ANY PR. Inert becomes actively wrong — phantom
    drift on a branch that never moved."""
    import decisions as dec
    assert "pr_number" not in dec.SCHEMA
    assert '"repo"' not in dec.SCHEMA

    history = [{"epoch_id": "bootstrap-8aeafa9c28b9", "head_sha": "8" * 40},
               {"epoch_id": "bootstrap-e29621f54a63", "head_sha": "e" * 40}]
    naive = next((r["head_sha"] for r in reversed(history)
                  if r["epoch_id"].startswith("bootstrap-")), None)
    assert naive == "e" * 40, "asked about PR #8, answered with PR #12"


def test_last_known_head_ignores_the_pr_it_claims_to_scope():
    """The docstring says 'for this PR'; the body never reads `pr`. A claim
    of scoping that the code does not implement."""
    import inspect
    import reconcile
    source = inspect.getsource(reconcile.last_known_head)
    body = source.split('"""')[2]
    assert "pr" not in body.replace("epoch_id", "").replace("startswith", "")
