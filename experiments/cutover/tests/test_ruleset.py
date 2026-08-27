"""Tests for A5b step 4 — the ruleset mutator.

The dangerous outcomes here are not exceptions. They are: a second ruleset
created because a response was lost, and a hash "fixed" by editing the
canonical object until it agrees with whatever GitHub returned.
"""
import json
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent.parent / "operational-readiness"
sys.path.insert(0, str(BASE / "harness"))

import cutover  # noqa: E402
import ruleset as rs  # noqa: E402

REPO = "PhysShell/evm-from-scratch"
NAME = "ai-final-review-enforcement"


def github_shaped(enforcement="disabled", **overrides):
    """What a readback actually looks like: the policy plus GitHub's own
    metadata."""
    obj = {**cutover.ruleset_with(enforcement),
           "id": 42, "node_id": "RRS_x", "source": REPO,
           "source_type": "Repository",
           "created_at": "2026-08-26T00:00:00Z",
           "updated_at": "2026-08-26T00:00:00Z",
           "_links": {"self": {"href": "..."}},
           "current_user_can_bypass": "never"}
    obj.update(overrides)
    return obj


# --- normalization ------------------------------------------------------------

def test_normalize_drops_metadata_but_keeps_policy():
    observed = rs.normalize(github_shaped())
    assert set(observed) == set(rs.CANONICAL_KEYS)
    assert cutover.canonical_hash(observed) == \
        cutover.hashes()["DISABLED_RULESET_HASH"]


def test_normalize_is_an_allowlist_not_a_denylist():
    """A denylist would silently absorb any new policy-bearing field GitHub
    introduces later, which is how a hash stops meaning anything."""
    source = (BASE / "harness" / "ruleset.py").read_text()
    assert "CANONICAL_KEYS" in source
    weird = rs.normalize(github_shaped(some_future_policy_field=True))
    assert "some_future_policy_field" not in weird


def test_active_readback_hashes_to_the_frozen_active_value():
    observed = rs.normalize(github_shaped("active"))
    assert cutover.canonical_hash(observed) == \
        cutover.hashes()["ACTIVE_RULESET_HASH"]


def test_policy_hash_is_identical_either_side_of_the_flip():
    assert rs.normalize(github_shaped("disabled")) != \
        rs.normalize(github_shaped("active"))
    assert cutover.policy_hash(rs.normalize(github_shaped("disabled"))) == \
        cutover.policy_hash(rs.normalize(github_shaped("active")))


# --- verification ---------------------------------------------------------------

def _verify(monkeypatch, readback, enforcement="disabled"):
    monkeypatch.setattr(rs, "read_one", lambda repo, rid: (readback, None))
    return rs.verify(REPO, 42, enforcement)


def test_clean_readback_verifies(monkeypatch):
    r = _verify(monkeypatch, github_shaped())
    assert r["state"] == "VERIFIED"
    assert r["POLICY_HASH"]["match"] and r["FULL_HASH"]["match"]


def test_tampered_context_fails_and_says_where(monkeypatch):
    bad = github_shaped()
    bad["rules"][0]["parameters"]["required_status_checks"][0]["context"] = \
        "ai/something-else"
    r = _verify(monkeypatch, bad)
    assert r["state"] == "MISMATCH"
    assert [d["key"] for d in r["diff"]] == ["rules"]


def test_a_bypass_actor_fails(monkeypatch):
    r = _verify(monkeypatch, github_shaped(bypass_actors=[{"actor_id": 1}]))
    assert r["state"] == "MISMATCH"
    assert any(d["key"] == "bypass_actors" for d in r["diff"])


def test_strict_turned_off_fails(monkeypatch):
    bad = github_shaped()
    bad["rules"][0]["parameters"]["strict_required_status_checks_policy"] = False
    assert _verify(monkeypatch, bad)["state"] == "MISMATCH"


def test_unreadable_readback_is_uncertain_not_verified(monkeypatch):
    monkeypatch.setattr(rs, "read_one", lambda repo, rid: (None, {"e": 1}))
    assert rs.verify(REPO, 42, "disabled")["state"] == "UNCERTAIN"


def test_mismatch_never_offers_to_edit_the_canonical_object(monkeypatch):
    r = _verify(monkeypatch, github_shaped(bypass_actors=[{"actor_id": 1}]))
    assert "Do not edit the canonical object" in r["required_action"]


# --- create: ambiguity is read, not rewritten ---------------------------------

def _create(monkeypatch, before, after, ok=True):
    calls = {"post": 0}
    states = iter([before, after])

    def fake_find(repo, name):
        nxt = next(states)
        return (None, {"e": 1}) if nxt is None else (nxt, None)

    def fake_gh(*args, body=None):
        calls["post"] += 1
        return ok, {"id": 42}

    monkeypatch.setattr(rs, "find_by_name", fake_find)
    monkeypatch.setattr(rs, "gh", fake_gh)
    return rs.create_disabled(REPO, NAME), calls


def test_exactly_one_after_create_proceeds(monkeypatch):
    r, calls = _create(monkeypatch, before=[], after=[{"id": 42}])
    assert r["state"] == "CREATED" and r["ruleset_id"] == 42
    assert calls["post"] == 1


def test_lost_response_is_uncertain_not_reposted(monkeypatch):
    """Zero objects after one POST. A second create would risk two rules on
    main with the same name."""
    r, calls = _create(monkeypatch, before=[], after=[], ok=False)
    assert r["state"] == "UNCERTAIN"
    assert calls["post"] == 1


def test_two_rulesets_after_one_post_is_uncertain(monkeypatch):
    r, calls = _create(monkeypatch, before=[],
                       after=[{"id": 42}, {"id": 43}])
    assert r["state"] == "UNCERTAIN"
    assert "NOT posting again" in r["cause"]
    assert calls["post"] == 1


def test_existing_ruleset_refuses_without_writing(monkeypatch):
    r, calls = _create(monkeypatch, before=[{"id": 7}], after=[])
    assert r["state"] == "REFUSED"
    assert calls["post"] == 0


def test_unlistable_rulesets_never_become_absence(monkeypatch):
    r, calls = _create(monkeypatch, before=None, after=[])
    assert r["state"] == "UNCERTAIN"
    assert "absence not established" in r["cause"]
    assert calls["post"] == 0


# --- ordering -------------------------------------------------------------------

def test_activation_never_runs_when_disabled_verification_fails(monkeypatch):
    monkeypatch.setattr(rs, "create_disabled",
                        lambda repo, name: {"state": "CREATED", "ruleset_id": 42})
    monkeypatch.setattr(rs, "verify",
                        lambda repo, rid, enf: {"state": "MISMATCH",
                                                "POLICY_HASH": {}, "diff": []})

    def explode(*a, **k):
        raise AssertionError("must not activate on a failed verification")

    monkeypatch.setattr(rs, "activate", explode)

    class Args:
        repo = REPO
        stop_after_disabled = False

    result = rs.run(Args())
    assert result["verdict"] == "STOP"
    assert "nothing is enforced" in result["note"]


def test_stop_after_disabled_halts_before_the_flip(monkeypatch):
    monkeypatch.setattr(rs, "create_disabled",
                        lambda repo, name: {"state": "CREATED", "ruleset_id": 42})
    monkeypatch.setattr(rs, "verify", lambda repo, rid, enf: {
        "state": "VERIFIED", "POLICY_HASH": {"observed": "p"}})

    def explode(*a, **k):
        raise AssertionError("must not activate when halted")

    monkeypatch.setattr(rs, "activate", explode)

    class Args:
        repo = REPO
        stop_after_disabled = True

    assert rs.run(Args())["verdict"] == "HALTED_BEFORE_ACTIVATION"


# --- A5b-r2: the new field is protected, not decorative -----------------------

def test_do_not_enforce_on_create_is_pinned_false():
    params = cutover.canonical_ruleset()["rules"][0]["parameters"]
    assert params["do_not_enforce_on_create"] is False


@pytest.mark.parametrize("enforcement", ["disabled", "active"])
def test_flipping_do_not_enforce_on_create_moves_both_hashes(enforcement):
    """The field was added because it is policy-bearing — at True, branch
    creation escapes the rule. If the hashes did not move with it, adding it
    would have been decoration to make GitHub agree, which is the exact
    substitution A5b-r2 refused."""
    tampered = cutover.ruleset_with(enforcement)
    tampered["rules"][0]["parameters"]["do_not_enforce_on_create"] = True

    digests = cutover.hashes()
    assert cutover.policy_hash(tampered) != digests["POLICY_HASH"]
    expected_full = digests["DISABLED_RULESET_HASH" if enforcement == "disabled"
                            else "ACTIVE_RULESET_HASH"]
    assert cutover.canonical_hash(tampered) != expected_full


def test_r2_hashes_are_the_reviewed_constants():
    """Frozen in the A5b-r2 amendment. The A5a values are historical and are
    not rewritten anywhere."""
    assert cutover.hashes() == {
        "POLICY_HASH":
            "7e086ae89e2e80e2063046596318ac08867e3ca74af59c16723b514827fa4b04",
        "DISABLED_RULESET_HASH":
            "3b907b822d9f2e276399b627fe2bb76bb2c4f2c13168c3e01157a2813e6738c7",
        "ACTIVE_RULESET_HASH":
            "3f1ddecaa689b56a0e3c59e7a0b3d11864c5d38b983131c61bae391e90292a20",
    }


def test_omitting_the_field_entirely_also_mismatches():
    """The pre-r2 shape must no longer verify, or the amendment would be a
    no-op that happens to agree with GitHub."""
    old_shape = cutover.ruleset_with("disabled")
    del old_shape["rules"][0]["parameters"]["do_not_enforce_on_create"]
    assert cutover.canonical_hash(old_shape) != \
        cutover.hashes()["DISABLED_RULESET_HASH"]
    assert cutover.policy_hash(old_shape) != cutover.hashes()["POLICY_HASH"]


def test_create_records_what_it_asserted(monkeypatch):
    """The provenance argument for recreating the ruleset is only checkable
    if the asserted body is kept."""
    r, _ = _create(monkeypatch, before=[], after=[{"id": 42}])
    params = r["request_body"]["rules"][0]["parameters"]
    assert params["do_not_enforce_on_create"] is False
    assert r["request_body"]["enforcement"] == "disabled"


# --- activation: the PUT is never the verdict ---------------------------------

def _activate(monkeypatch, before, after, flip_ok=True, named=None):
    calls = {"put": 0}
    monkeypatch.setattr(rs, "find_by_name",
                        lambda repo, name: (named if named is not None
                                            else [{"id": 42}], None))
    verdicts = iter([before, after])
    monkeypatch.setattr(rs, "verify", lambda repo, rid, enf: next(verdicts))

    def fake_activate(repo, rid):
        calls["put"] += 1
        return {"flip_ok": flip_ok}

    monkeypatch.setattr(rs, "activate", fake_activate)
    return rs.activate_existing(REPO, 42), calls


def _v(state="VERIFIED", enforcement="disabled", policy="p"):
    return {"state": state, "observed_enforcement": enforcement,
            "POLICY_HASH": {"observed": policy}}


def test_activation_confirmed_only_by_readback(monkeypatch):
    r, calls = _activate(monkeypatch, _v(), _v(enforcement="active"))
    assert r["state"] == "CONFIRMED"
    assert r["policy_hash_unchanged_across_flip"] is True
    assert calls["put"] == 1


def test_lost_put_response_does_not_stop_a_successful_flip(monkeypatch):
    """The response is not consulted; the readback decides."""
    r, calls = _activate(monkeypatch, _v(), _v(enforcement="active"),
                         flip_ok=False)
    assert r["state"] == "CONFIRMED"
    assert calls["put"] == 1


def test_readback_still_disabled_is_did_not_establish(monkeypatch):
    r, calls = _activate(monkeypatch, _v(), _v(enforcement="disabled"))
    assert r["state"] == "DID_NOT_ESTABLISH"
    assert calls["put"] == 1, "and no second PUT"


def test_unreadable_after_flip_is_outcome_unknown(monkeypatch):
    r, calls = _activate(monkeypatch, _v(), {"state": "UNCERTAIN"})
    assert r["state"] == "OUTCOME_UNKNOWN"
    assert "No second mutation" in r["cause"]
    assert r["retry_performed"] is False


def test_policy_hash_moving_across_the_flip_stops(monkeypatch):
    r, _ = _activate(monkeypatch, _v(policy="before"),
                     _v(enforcement="active", policy="after"))
    assert r["state"] == "STOP"
    assert "POLICY_HASH moved" in r["cause"]


def test_refuses_to_flip_an_unverified_disabled_object(monkeypatch):
    r, calls = _activate(monkeypatch, _v(state="MISMATCH"), _v())
    assert r["state"] == "STOP"
    assert calls["put"] == 0


def test_refuses_when_the_named_set_is_not_exactly_this_one(monkeypatch):
    r, calls = _activate(monkeypatch, _v(), _v(), named=[{"id": 42}, {"id": 43}])
    assert r["state"] == "STOP"
    assert calls["put"] == 0
