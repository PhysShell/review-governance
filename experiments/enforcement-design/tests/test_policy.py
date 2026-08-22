"""Adversarial tests for the normative authorization predicate.

Each clause gets its own falsification, and the last group is the point of
A4: the cases where GitHub would happily allow a merge that the Governor's
own state does not authorize.
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

import policy  # noqa: E402

HEAD = "a" * 40
OTHER_HEAD = "b" * 40
HASH = "c" * 64


def snapshot(**overrides):
    base = dict(
        current_full_head=HEAD,
        epoch_state="CURRENT",
        epoch_head=HEAD,
        auth_state="AUTHORIZED",
        decision_verdict="SUCCESS",
        decision_bundle_hash=HASH,
        projection_state=policy.CONFIRMED,
        projection_conclusion="success",
        projection_head=HEAD,
        projection_app_id=policy.GOVERNOR_APP_ID,
        projection_bundle_hash=HASH,
        known_invalidations=(),
    )
    base.update(overrides)
    return policy.Snapshot(**base)


def test_the_fully_aligned_snapshot_authorizes():
    result = policy.evaluate(snapshot())
    assert result["may_authorize_action"] is True
    assert result["reasons"] == []


# --- one falsification per clause -------------------------------------------

def test_stale_epoch_refuses():
    result = policy.evaluate(snapshot(epoch_state="STALE"))
    assert result["may_authorize_action"] is False
    assert any("epoch is STALE" in r for r in result["reasons"])


def test_epoch_covering_another_head_refuses():
    result = policy.evaluate(snapshot(epoch_head=OTHER_HEAD))
    assert result["may_authorize_action"] is False


def test_any_non_authorized_state_refuses():
    for lost in ("AUTH_LOST", "REAUTH_REQUIRED", "REFRESH_OUTCOME_UNKNOWN"):
        assert policy.evaluate(snapshot(auth_state=lost))["may_authorize_action"] \
            is False


def test_non_success_verdict_refuses():
    for verdict in ("NOT_ESTABLISHED", "EVIDENCE_INVALIDATED", "STALE",
                    "AUTHORIZATION_UNAVAILABLE"):
        assert policy.evaluate(
            snapshot(decision_verdict=verdict))["may_authorize_action"] is False


def test_unconfirmed_projection_refuses():
    for state in (policy.PENDING, policy.OUTCOME_UNKNOWN, policy.FAILED):
        result = policy.evaluate(snapshot(projection_state=state))
        assert result["may_authorize_action"] is False
        assert any("projection is" in r for r in result["reasons"])


def test_projection_bound_to_another_head_refuses():
    result = policy.evaluate(snapshot(projection_head=OTHER_HEAD))
    assert result["may_authorize_action"] is False
    assert any("different head" in r for r in result["reasons"])


def test_short_head_is_refused():
    result = policy.evaluate(snapshot(current_full_head="a" * 10,
                                      epoch_head="a" * 10,
                                      projection_head="a" * 10))
    assert result["may_authorize_action"] is False
    assert any("full 40-character SHA" in r for r in result["reasons"])


def test_foreign_app_projection_refuses():
    result = policy.evaluate(snapshot(projection_app_id=999999))
    assert result["may_authorize_action"] is False
    assert any("not owned by the Governor App" in r for r in result["reasons"])


def test_bundle_hash_disagreement_refuses():
    result = policy.evaluate(snapshot(projection_bundle_hash="d" * 64))
    assert result["may_authorize_action"] is False
    assert any("bundle hash differs" in r for r in result["reasons"])


def test_any_known_invalidation_refuses():
    result = policy.evaluate(snapshot(
        known_invalidations=("newer coderabbit request generation",)))
    assert result["may_authorize_action"] is False
    assert any("locally known invalidation" in r for r in result["reasons"])


def test_all_reasons_are_reported_not_just_the_first():
    result = policy.evaluate(snapshot(epoch_state="STALE",
                                      auth_state="AUTH_LOST",
                                      projection_state=policy.OUTCOME_UNKNOWN))
    assert len(result["reasons"]) >= 3


# --- visibility is not authorization ----------------------------------------

def test_visibility_and_authorization_disagree_when_unsettled():
    snap = snapshot(projection_state=policy.OUTCOME_UNKNOWN,
                    projection_conclusion="success")
    result = policy.evaluate(snap)
    assert result["external_success_may_exist"] is True
    assert result["may_authorize_action"] is False


def test_visibility_is_true_for_an_unsettled_success_even_without_a_conclusion():
    snap = snapshot(projection_state=policy.PENDING, projection_conclusion=None)
    assert policy.external_success_may_exist(snap) is True


def test_visibility_is_false_once_the_projection_reads_failure():
    snap = snapshot(projection_state=policy.CONFIRMED,
                    projection_conclusion="failure")
    assert policy.external_success_may_exist(snap) is False


# --- what GitHub can and cannot see -----------------------------------------

def test_github_blocks_when_the_latest_head_has_no_passing_check():
    snap = snapshot(projection_head=OTHER_HEAD)
    assert policy.enforcement_expectation(snap).startswith("BLOCK")


def test_github_blocks_a_same_name_check_from_another_app():
    snap = snapshot(projection_app_id=999999)
    assert "expected source" in policy.enforcement_expectation(snap)


def test_github_blocks_once_the_confirmed_conclusion_is_failure():
    snap = snapshot(projection_conclusion="failure")
    assert policy.enforcement_expectation(snap).startswith("BLOCK")


def test_g1_observed_invalidation_is_enforceable_once_projected():
    """The Governor knows, projects failure, and GitHub then blocks — the
    whole of G1 in one assertion pair."""
    known = snapshot(known_invalidations=("provider carrier mutated",),
                     projection_conclusion="success")
    assert policy.evaluate(known)["may_authorize_action"] is False
    projected = snapshot(known_invalidations=("provider carrier mutated",),
                         projection_conclusion="failure")
    assert policy.enforcement_expectation(projected).startswith("BLOCK")


def test_g2_unobserved_mutation_leaves_github_allowing_the_merge():
    """The residual window: the provider has changed, the Governor has not
    yet observed it, so nothing in GitHub's view has changed."""
    unobserved = snapshot()          # Governor still believes everything holds
    window = policy.residual_window(unobserved)
    assert window["governor_authorizes"] is True
    assert window["github_expectation"].startswith("ALLOW")
    assert window["hazardous"] is False   # not yet hazardous — nothing is known

    observed = snapshot(known_invalidations=("carrier mutated at T0",))
    still_green = policy.residual_window(observed)
    assert still_green["governor_authorizes"] is False
    assert still_green["github_expectation"].startswith("ALLOW")
    assert still_green["hazardous"] is True     # known-bad, still green in GitHub


def test_hazard_disappears_once_the_projection_catches_up():
    caught_up = snapshot(known_invalidations=("carrier mutated at T0",),
                         projection_conclusion="failure")
    window = policy.residual_window(caught_up)
    assert window["hazardous"] is False
    assert window["github_expectation"].startswith("BLOCK")
