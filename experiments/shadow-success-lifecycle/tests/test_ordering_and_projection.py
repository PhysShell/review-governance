"""Adversarial tests for corrections A3b-c3 and A3b-c4.

c3 is about ordering: the Governor must never knowingly leave a success
standing after it has itself done the thing that makes the basis
non-current. c4 is about not believing a write just because the call
returned: only an independent read of that exact run confirms a projection,
and an indeterminate write settles downward, never upward.
"""
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

import decisions as dec  # noqa: E402
import lifecycle as life  # noqa: E402

EPOCH = "epoch-test"
HEAD = "c" * 40
RUN = 12345
HASH_1 = "1" * 64


class FakeRepo:
    """Models the three interesting worlds: the write lands, the write is
    indeterminate, and the write silently does not take effect."""

    def __init__(self, mode="ok", conclusion=None):
        self.mode = mode
        self.conclusion = conclusion
        self.patches = []
        self.comments = []
        self.full_name = "PhysShell/evm-from-scratch"

    def conclude_check(self, check_run_id, conclusion, output, *,
                       evidence_hash=None):
        self.patches.append((check_run_id, conclusion))
        if self.mode == "lost_response":
            raise TimeoutError("connection reset after the request was sent")
        if self.mode == "no_effect":
            return 200, {"id": check_run_id, "conclusion": self.conclusion}
        self.conclusion = conclusion
        return 200, {"id": check_run_id, "conclusion": conclusion}

    def get_check(self, check_run_id):
        if self.mode == "readback_unavailable":
            return 502, None
        return 200, {"id": check_run_id, "conclusion": self.conclusion,
                     "head_sha": HEAD}

    def comment_as_user(self, pr, body):
        if self.mode == "request_lost":
            raise TimeoutError("provider request outcome unknown")
        comment = {"id": 999, "created_at": "2026-08-22T09:00:00Z",
                   "updated_at": "2026-08-22T09:00:00Z", "body": body,
                   "user": {"login": "PhysShell", "id": 45852143, "type": "User"},
                   "performed_via_github_app": {"id": 4669438,
                                                "slug": "physshell-review-governor"}}
        self.comments.append(comment)
        return comment


@pytest.fixture()
def history(tmp_path):
    h = dec.History(tmp_path / "d.sqlite3")
    yield h
    h.close()


def record_success(history, bundle_hash=HASH_1):
    return history.record(epoch_id=EPOCH, head_sha=HEAD, verdict="SUCCESS",
                          bundle_hash=bundle_hash, bundle_schema="v1",
                          decision_rule_revision="a3b.1", auth_generation=3,
                          decided_at="t0")


# --- c4: projection states --------------------------------------------------

def test_successful_write_is_confirmed_only_by_an_independent_readback(history):
    repo = FakeRepo(mode="ok", conclusion="failure")
    decision_id = record_success(history)
    result = life.apply_projection(repo, history, epoch_id=EPOCH, head_sha=HEAD,
                                   check_run_id=RUN, conclusion="success",
                                   output={"summary": HASH_1},
                                   decision_id=decision_id, evidence_hash=HASH_1)
    assert result["projection_state"] == "CONFIRMED"
    projection = history.projection(EPOCH)
    assert projection["state"] == "CONFIRMED"
    assert projection["intended_conclusion"] == "success"
    assert projection["observed_conclusion"] == "success"
    assert projection["confirmed_at"] is not None


def test_projection_is_pending_before_the_readback(history):
    decision_id = record_success(history)
    history.project_pending(EPOCH, HEAD, RUN, "success", decision_id, "t1")
    projection = history.projection(EPOCH)
    assert projection["state"] == "PENDING"
    assert projection["observed_conclusion"] is None
    assert projection["confirmed_at"] is None


def test_lost_response_after_an_accepted_write_settles_by_reading(history):
    """GitHub accepted the PATCH, the response vanished — the readback still
    tells the truth, so this resolves without guessing."""
    repo = FakeRepo(mode="lost_response", conclusion="success")
    decision_id = record_success(history)
    result = life.apply_projection(repo, history, epoch_id=EPOCH, head_sha=HEAD,
                                   check_run_id=RUN, conclusion="success",
                                   output={"summary": HASH_1},
                                   decision_id=decision_id, evidence_hash=HASH_1)
    assert result["write"]["error"].startswith("TimeoutError")
    assert result["projection_state"] == "CONFIRMED"


def test_write_that_never_took_effect_is_not_confirmed(history):
    repo = FakeRepo(mode="no_effect", conclusion="failure")
    decision_id = record_success(history)
    result = life.apply_projection(repo, history, epoch_id=EPOCH, head_sha=HEAD,
                                   check_run_id=RUN, conclusion="success",
                                   output={"summary": HASH_1},
                                   decision_id=decision_id, evidence_hash=HASH_1)
    assert result["projection_state"] == "FAILED"
    assert history.projection(EPOCH)["observed_conclusion"] == "failure"


def test_unreadable_run_settles_to_outcome_unknown_not_to_success(history):
    repo = FakeRepo(mode="readback_unavailable")
    decision_id = record_success(history)
    result = life.apply_projection(repo, history, epoch_id=EPOCH, head_sha=HEAD,
                                   check_run_id=RUN, conclusion="success",
                                   output={"summary": HASH_1},
                                   decision_id=decision_id, evidence_hash=HASH_1)
    assert result["projection_state"] == "OUTCOME_UNKNOWN"
    assert history.projection(EPOCH)["state"] == "OUTCOME_UNKNOWN"


def test_unsettled_projections_are_listed_for_reconciliation(history):
    decision_id = record_success(history)
    history.project_pending(EPOCH, HEAD, RUN, "success", decision_id, "t1")
    assert [row["epoch_id"] for row in history.unsettled_projections()] == [EPOCH]
    history.settle_projection(EPOCH, state="CONFIRMED",
                              observed_conclusion="success", at="t2")
    assert history.unsettled_projections() == []


# --- c3: ordering -----------------------------------------------------------

def test_a_standing_confirmed_success_is_detected(history):
    decision_id = record_success(history)
    history.project_pending(EPOCH, HEAD, RUN, "success", decision_id, "t1")
    history.settle_projection(EPOCH, state="CONFIRMED",
                              observed_conclusion="success", at="t2")
    assert standing_id(history) == decision_id


def standing_id(history):
    standing = life.standing_success(history, EPOCH)
    return standing["decision_id"] if standing else None


def test_an_unsettled_success_also_counts_as_standing(history):
    """Until a projection is resolved, the Governor must assume the green
    light may be live out there."""
    decision_id = record_success(history)
    history.project_pending(EPOCH, HEAD, RUN, "success", decision_id, "t1")
    assert standing_id(history) == decision_id
    history.settle_projection(EPOCH, state="OUTCOME_UNKNOWN",
                              observed_conclusion=None, at="t2")
    assert standing_id(history) == decision_id


def test_a_revoked_success_no_longer_stands(history):
    decision_id = record_success(history)
    history.project_pending(EPOCH, HEAD, RUN, "success", decision_id, "t1")
    history.settle_projection(EPOCH, state="CONFIRMED",
                              observed_conclusion="success", at="t2")
    revocation = history.record(epoch_id=EPOCH, head_sha=HEAD,
                                verdict="EVIDENCE_INVALIDATED", bundle_hash=None,
                                bundle_schema="v1", decision_rule_revision="a3b.1",
                                auth_generation=3, decided_at="t3",
                                cause="rerun", invalidates_decision_id=decision_id)
    history.project_pending(EPOCH, HEAD, RUN, "failure", revocation, "t3")
    history.settle_projection(EPOCH, state="CONFIRMED",
                              observed_conclusion="failure", at="t4")
    assert standing_id(history) is None


def test_rerun_orders_invalidation_before_the_provider_request(tmp_path, history):
    """The whole point of c3: the check must already read failure before the
    request that invalidates the old evidence exists."""
    repo = FakeRepo(mode="ok", conclusion="success")
    decision_id = record_success(history)
    history.project_pending(EPOCH, HEAD, RUN, "success", decision_id, "t1")
    history.settle_projection(EPOCH, state="CONFIRMED",
                              observed_conclusion="success", at="t2")

    projection = life.apply_projection(
        repo, history, epoch_id=EPOCH, head_sha=HEAD, check_run_id=RUN,
        conclusion="failure", output={"summary": ""},
        decision_id=history.record(
            epoch_id=EPOCH, head_sha=HEAD, verdict="EVIDENCE_INVALIDATED",
            bundle_hash=None, bundle_schema="v1",
            decision_rule_revision="a3b.1", auth_generation=3, decided_at="t3",
            cause="rerun_requested_pre_request_invalidation",
            invalidates_decision_id=decision_id, invalidates_bundle_hash=HASH_1))
    assert projection["projection_state"] == "CONFIRMED"
    assert repo.conclusion == "failure"
    assert repo.comments == []                    # nothing requested yet

    repo.comment_as_user(21, "@codex review")
    assert repo.conclusion == "failure"           # still failure when it lands
    assert len(repo.comments) == 1


def test_request_outcome_unknown_leaves_the_check_failed(history):
    """A lost provider POST must never be resolved by restoring the old
    success — the Governor cannot know whether a review is now running."""
    repo = FakeRepo(mode="request_lost", conclusion="failure")
    with pytest.raises(TimeoutError):
        repo.comment_as_user(21, "@codex review")
    assert repo.conclusion == "failure"
    assert dec.expected_conclusion("EVIDENCE_INVALIDATED") == "failure"


def test_aborted_rerun_never_reaches_the_provider(history):
    """If the failure cannot be confirmed, no provider request is created —
    otherwise a green check could coexist with a running new review."""
    repo = FakeRepo(mode="no_effect", conclusion="success")
    decision_id = record_success(history)
    projection = life.apply_projection(
        repo, history, epoch_id=EPOCH, head_sha=HEAD, check_run_id=RUN,
        conclusion="failure", output={"summary": ""}, decision_id=decision_id)
    assert projection["projection_state"] == "FAILED"
    assert repo.comments == []


# --- c4 terminology: visibility is not validity -----------------------------

def test_confirmed_success_is_the_only_state_that_authorizes_action(history):
    decision_id = record_success(history)
    history.project_pending(EPOCH, HEAD, RUN, "success", decision_id, "t1")
    history.settle_projection(EPOCH, state="CONFIRMED",
                              observed_conclusion="success", at="t2")
    status = life.governor_authorization(history, EPOCH)
    assert status["effective_gate_validity"] == "ESTABLISHED"
    assert status["may_authorize_action"] is True
    assert status["external_success_may_exist"] is True
    assert status["hazard"] is None


def test_unsettled_projection_is_hazardous_not_established_either_way(history):
    """OUTCOME_UNKNOWN while GitHub may physically show success: neither an
    established success nor an established revocation."""
    decision_id = record_success(history)
    history.project_pending(EPOCH, HEAD, RUN, "success", decision_id, "t1")
    history.settle_projection(EPOCH, state="OUTCOME_UNKNOWN",
                              observed_conclusion=None, at="t2")
    status = life.governor_authorization(history, EPOCH)

    assert status["external_success_may_exist"] is True      # may be green out there
    assert status["effective_gate_validity"] == "NOT_ESTABLISHED"   # …but not to us
    assert status["may_authorize_action"] is False
    assert "neither an established success" in status["hazard"]


def test_pending_projection_never_authorizes_an_action(history):
    decision_id = record_success(history)
    history.project_pending(EPOCH, HEAD, RUN, "success", decision_id, "t1")
    status = life.governor_authorization(history, EPOCH)
    assert status["projection_state"] == "PENDING"
    assert status["may_authorize_action"] is False
    assert status["effective_gate_validity"] == "NOT_ESTABLISHED"


def test_uncertainty_still_forces_cleanup_before_a_new_request(history):
    """Fail-closed in both directions: no authorization, and no new provider
    request until the standing green is dealt with."""
    decision_id = record_success(history)
    history.project_pending(EPOCH, HEAD, RUN, "success", decision_id, "t1")
    history.settle_projection(EPOCH, state="OUTCOME_UNKNOWN",
                              observed_conclusion=None, at="t2")
    assert life.standing_success(history, EPOCH)["decision_id"] == decision_id
    assert life.governor_authorization(history, EPOCH)["may_authorize_action"] is False


def test_a_failed_projection_authorizes_nothing_and_needs_no_cleanup(history):
    decision_id = record_success(history)
    history.project_pending(EPOCH, HEAD, RUN, "success", decision_id, "t1")
    history.settle_projection(EPOCH, state="FAILED",
                              observed_conclusion="failure", at="t2")
    status = life.governor_authorization(history, EPOCH)
    assert status["may_authorize_action"] is False
    assert status["effective_gate_validity"] == "NOT_ESTABLISHED"
    assert status["external_success_may_exist"] is False
