"""A6f-c4: the reading is made here, or there is no reading.

A6f-c3 stopped the caller supplying the gate's *result*. It still let the
caller supply the gate's *facts*: `observe()` performed one `GET /pulls/{n}`
and wrote down whatever `ruleset_verified_fn()` and `carrier_fn()` returned.

    A durable observation is not an observation merely because somebody
    wrote it into the observations table.

These tests are about that sentence. Every failing precondition below is
expressed by changing what GitHub returns, because after this stage there
is no other way to express one.
"""
import inspect
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "harness"))

import gate as gate_mod  # noqa: E402
import governed_round as gr  # noqa: E402
import observation as obs_mod  # noqa: E402
import rounds  # noqa: E402
from conftest import (A, B, CARRIER_RUN, EPOCH, FakeGitHub, MAIN,  # noqa: E402
                      REPO, RULESET, accept, now_stamp, record_observation)


# --- 1. no semantic callbacks anywhere on the path ----------------------------

def test_the_observation_writer_takes_no_semantic_arguments():
    """`ruleset_verified` and `carrier` were parameters, so an immutable row
    could be assembled from four supplied values."""
    params = inspect.signature(rounds.RoundStore.record_observation).parameters
    assert "read" in params
    for banned in ("ruleset_verified", "carrier", "head_sha", "draft",
                   "base_ref", "pr_state"):
        assert banned not in params, banned


def test_the_driver_takes_no_verification_callbacks():
    for method, banned in (
            (gr.GovernedRound.observe, ("ruleset_verified_fn", "carrier_fn")),
            (gr.GovernedRound.accept_candidate, ("open_generations",)),
            (gr.GovernedRound.conclude, ("ruleset_verified_fn",))):
        params = inspect.signature(method).parameters
        for name in banned:
            assert name not in params, f"{method.__name__}({name})"


def test_the_acceptance_writer_derives_its_own_generations():
    """`open_generations=[]` was the last themed empty list."""
    assert "open_generations" not in inspect.signature(
        rounds.RoundStore.record_acceptance).parameters
    assert hasattr(rounds.RoundStore, "open_generations")


def test_a_fabricated_observation_cannot_be_written(store):
    """There is no path that writes a reading without performing it."""
    with pytest.raises(TypeError):
        store.record_observation(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                 head_sha=A, draft=False, base_ref="main",
                                 pr_state="open", ruleset_id=RULESET,
                                 ruleset_verified=True,
                                 carrier={"state": "CONFIRMED"})
    assert store.conn.execute(
        "SELECT COUNT(*) FROM observations").fetchone()[0] == 0


def test_an_unreadable_surface_records_nothing(store):
    def broken(method, path):
        return (200, {"number": 32, "head": {"sha": A}, "draft": False,
                      "base": {"ref": "main"}, "state": "open"}) \
            if path.endswith("/pulls/32") else (503, None)

    with pytest.raises(rounds.RoundError) as exc:
        store.record_observation(broken, repo=REPO, pr_number=32,
                                 epoch_id=EPOCH)
    assert "not an observed one" in str(exc.value)
    assert store.conn.execute(
        "SELECT COUNT(*) FROM observations").fetchone()[0] == 0


# --- 2. what the four readings establish --------------------------------------

def test_the_reading_records_all_four_surfaces(store):
    gh = FakeGitHub()
    row = record_observation(store, github=gh)
    paths = [c["path"] for c in row["reads"]]
    assert any("/pulls/32" in p for p in paths)
    assert any(p.endswith("/commits/main") for p in paths)
    assert any("/compare/" in p for p in paths)
    assert any("/rulesets/" in p for p in paths)
    assert any("/check-runs" in p for p in paths)
    f = row["facts"]
    assert f["base_sha"] == MAIN and f["merge_base_sha"] == MAIN
    assert f["ruleset_enforcement"] == "active"
    assert f["ruleset_visible_hash"] == obs_mod.APP_VISIBLE_RULESET_HASH
    assert f["carrier_count"] == 1 and f["carrier_run_id"] == CARRIER_RUN
    assert f["carrier_conclusion"] == "failure"
    assert f["carrier_external_id"] == EPOCH


def test_the_pinned_hash_is_the_reviewed_policy():
    """Recomputed from the canonical object rather than remembered."""
    projection = obs_mod.canonical_visible_ruleset("active")
    assert obs_mod.visible_hash(projection) == obs_mod.APP_VISIBLE_RULESET_HASH
    assert set(projection) == set(obs_mod.APP_VISIBLE_KEYS)


def test_a_base_ref_named_main_is_not_ancestry(store, fresh):
    """`base_current` used to mean `base_ref == "main"`. A candidate can
    target main and not contain it."""
    behind = FakeGitHub(merge_base="9" * 40)
    row = record_observation(store, github=behind)
    assert row["facts"]["base_ref"] == "main"
    assert row["facts"]["contains_current_base"] is False
    problems = obs_mod.base_findings(row["facts"])
    assert any("does not contain the current base" in p for p in problems)
    with pytest.raises(rounds.RoundError) as exc:
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                observation_id=row["observation_id"])
    assert "current base" in str(exc.value)


@pytest.mark.parametrize("kw,fragment", [
    ({"enforcement": "disabled"}, "enforcement"),
    ({"ruleset_over": {"conditions": {"ref_name": {"include": ["refs/heads/x"],
                                                   "exclude": []}}}},
     "reviewed policy"),
    ({"ruleset_over": {"rules": [{"type": "required_status_checks",
                                  "parameters": {"required_status_checks": [
                                      {"context": "ai/final-review",
                                       "integration_id": 99}],
                                      "strict_required_status_checks_policy": True,
                                      "do_not_enforce_on_create": False}}]}},
     "reviewed policy"),
    ({"ruleset_over": {"rules": [{"type": "required_status_checks",
                                  "parameters": {"required_status_checks": [
                                      {"context": "ai/final-review",
                                       "integration_id": 4669438}],
                                      "strict_required_status_checks_policy": False,
                                      "do_not_enforce_on_create": False}}]}},
     "reviewed policy"),
])
def test_a_ruleset_that_is_not_the_reviewed_policy_refuses(store, fresh, kw,
                                                           fragment):
    row = record_observation(store, github=FakeGitHub(**kw))
    with pytest.raises(rounds.RoundError) as exc:
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                observation_id=row["observation_id"])
    assert fragment in str(exc.value)


def test_a_visible_bypass_actor_refuses(store, fresh):
    gh = FakeGitHub(bypass_visible=True)
    gh.ruleset_over = {"bypass_actors": [{"actor_id": 5, "actor_type": "Team"}]}
    row = record_observation(store, github=gh)
    assert obs_mod.ruleset_findings(row["facts"])
    with pytest.raises(rounds.RoundError) as exc:
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                observation_id=row["observation_id"])
    assert "bypass actor" in str(exc.value)


def test_the_runtime_identity_records_that_it_cannot_see_bypass(store):
    """Confirmed live on 2026-08-30: the owner token returns
    `bypass_actors: []` and the installation token omits the key. So the
    empty-bypass precondition is NOT established here, and the row says so
    rather than hashing a smaller object and calling it a match."""
    row = record_observation(store, github=FakeGitHub(bypass_visible=False))
    assert row["facts"]["ruleset_bypass"] == obs_mod.BYPASS_UNOBSERVABLE
    assert obs_mod.ruleset_findings(row["facts"]) == []
    assert obs_mod.APP_VISIBLE_RULESET_HASH != \
        "3f1ddecaa689b56a0e3c59e7a0b3d11864c5d38b983131c61bae391e90292a20", \
        "the owner-view ACTIVE_RULESET_HASH is not reproducible from here"


@pytest.mark.parametrize("kw,fragment", [
    ({"carrier_runs": []}, "0 ai/final-review runs"),
    ({"carrier_conclusion": "success"}, "carrier conclusion"),
    ({"carrier_status": "in_progress"}, "carrier status"),
    ({"carrier_external_id": "pe-somebody-else"}, "external_id"),
])
def test_a_carrier_that_is_not_the_one_to_transition_refuses(store, fresh, kw,
                                                             fragment):
    row = record_observation(store, github=FakeGitHub(**kw))
    with pytest.raises(rounds.RoundError) as exc:
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                observation_id=row["observation_id"])
    assert fragment in str(exc.value)


def test_two_governor_carriers_on_the_head_refuse(store, fresh):
    twice = [{"id": CARRIER_RUN, "name": "ai/final-review",
              "app": {"id": 4669438}, "head_sha": A, "external_id": EPOCH,
              "status": "completed", "conclusion": "failure"},
             {"id": CARRIER_RUN + 1, "name": "ai/final-review",
              "app": {"id": 4669438}, "head_sha": A, "external_id": EPOCH,
              "status": "completed", "conclusion": "failure"}]
    row = record_observation(store, github=FakeGitHub(carrier_runs=twice))
    assert row["facts"]["carrier_count"] == 2
    with pytest.raises(rounds.RoundError) as exc:
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                observation_id=row["observation_id"])
    assert "exactly one applicable carrier" in str(exc.value)


def test_a_closed_or_draft_pr_refuses(store, fresh):
    for kw, fragment in (({"pr_state": "closed"}, "PR state"),
                         ({"draft": True}, "draft")):
        row = record_observation(store, github=FakeGitHub(**kw))
        with pytest.raises(rounds.RoundError) as exc:
            store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                    head_sha=A, permission=fresh,
                                    observation_id=row["observation_id"])
        assert fragment in str(exc.value)


# --- 3. a reading is about now ------------------------------------------------

def test_a_stale_observation_cannot_accept(store, fresh):
    """A fresh OAuth permission paired with an arbitrarily old reading was
    legal: the observation bound is the same shape as the auth bound."""
    row = record_observation(store, github=FakeGitHub())
    store.conn.execute("PRAGMA writable_schema=ON")
    store.conn.execute("DROP TRIGGER observations_no_update")
    store.conn.execute("UPDATE observations SET observed_at=?",
                       ("2020-01-01T00:00:00Z",))
    store.conn.commit()
    with pytest.raises(rounds.RoundError) as exc:
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                observation_id=row["observation_id"])
    assert "bound is 60s" in str(exc.value)


def test_a_superseded_observation_cannot_be_reused(store, fresh):
    first = record_observation(store, github=FakeGitHub())
    second = record_observation(store, github=FakeGitHub())
    assert first["observation_id"] != second["observation_id"]
    with pytest.raises(rounds.RoundError) as exc:
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                observation_id=first["observation_id"])
    assert "not the latest reading" in str(exc.value)
    assert store.record_acceptance(
        repo=REPO, pr_number=32, epoch_id=EPOCH, head_sha=A,
        permission=fresh,
        observation_id=second["observation_id"])["state"] == rounds.ACCEPTED


def test_the_production_entrypoint_leaves_no_id_to_choose(tmp_path, store,
                                                          snaps, epochs):
    from test_end_to_end_round import driver
    gh = FakeGitHub()
    d = driver(tmp_path, store, snaps, epochs, gh)
    out = d.observe_and_accept(epoch_id=EPOCH, ruleset_id=gh.ruleset_id)
    assert out["acceptance"]["state"] == rounds.ACCEPTED
    assert out["acceptance"]["observation_id"] == \
        store.latest_observation(REPO, 32)["observation_id"]


# --- 4. the pre-success ruleset gate is a read, not a lambda ------------------

def test_conclude_rereads_the_ruleset(tmp_path, store, snaps, epochs):
    from test_end_to_end_round import driver
    gh = FakeGitHub()
    d = driver(tmp_path, store, snaps, epochs, gh)
    before = len([p for m, p in gh.calls if "/rulesets/" in p])
    out = d.reread_ruleset(ruleset_id=gh.ruleset_id)
    assert out["verified"] is True
    assert len([p for m, p in gh.calls if "/rulesets/" in p]) == before + 1


def test_a_ruleset_disabled_after_acceptance_refuses_success(tmp_path, store,
                                                             snaps, epochs):
    """The strongest guard in the system used to be a caller's lambda."""
    from test_end_to_end_round import driver, run_round
    gh = FakeGitHub()
    d = driver(tmp_path, store, snaps, epochs, gh)
    accepted = d.observe_and_accept(epoch_id=EPOCH, ruleset_id=gh.ruleset_id)
    assert accepted["acceptance"]["state"] == rounds.ACCEPTED
    records = []
    for provider in ("coderabbit", "codex"):
        baseline = d.capture_baseline(provider)
        sent = d.request_provider(accepted, provider, 1, baseline=baseline)
        records.append(d.collect_evidence(sent, provider, 1))
    gh.enforcement = "disabled"          # somebody turned the rule off
    from test_end_to_end_round import FakeEpochs
    d.epochs = FakeEpochs()
    out = d.conclude(records, epoch_id=EPOCH, existing_run=CARRIER_RUN,
                     patch=gh.request, ruleset_id=gh.ruleset_id)
    assert out["state"] == gr.STOP
    assert "reviewed policy" in out["cause"]
    assert gh.patched == []


# --- 5. what this identity cannot see, checked by one that can ---------------

def test_the_bypass_check_lives_with_the_owner_credential(monkeypatch):
    """`bypass == []` is a real precondition and the runtime cannot observe
    it, so it is checked by a process that cannot publish anything."""
    sys.path.insert(0, str(HERE.parents[0] / "operational-readiness" / "harness"))
    import sentinel

    class Args:
        repo = REPO
        ruleset_id = RULESET
        startup_grace = 0

    class Notifier:
        def __init__(self):
            self.raised, self.cleared = [], []

        def raise_(self, severity, cause, **kw):
            self.raised.append(cause)

        def clear(self, cause, **kw):
            self.cleared.append(cause)

    seen = Notifier()
    monkeypatch.setattr(sentinel.ruleset_mod, "gh",
                        lambda *a, **k: (True, {"bypass_actors": [],
                                                "enforcement": "active"}))
    assert sentinel.check_ruleset_bypass(Args(), seen)["state"] == "EMPTY"
    assert seen.cleared == ["ruleset_bypass_present"]

    seen = Notifier()
    monkeypatch.setattr(sentinel.ruleset_mod, "gh",
                        lambda *a, **k: (True, {"bypass_actors": [{"actor_id": 1}],
                                                "enforcement": "active"}))
    assert sentinel.check_ruleset_bypass(Args(), seen)["state"] == "POPULATED"
    assert seen.raised == ["ruleset_bypass_present"]

    seen = Notifier()
    monkeypatch.setattr(sentinel.ruleset_mod, "gh",
                        lambda *a, **k: (True, {"enforcement": "active"}))
    out = sentinel.check_ruleset_bypass(Args(), seen)
    assert out["state"] == "UNOBSERVABLE"
    assert seen.raised == ["ruleset_bypass_present"], \
        "an identity that cannot see the field has not seen an empty list"
