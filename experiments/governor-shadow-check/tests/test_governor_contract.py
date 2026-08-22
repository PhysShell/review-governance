"""Adversarial tests for the A2b Governor contract.

The live run proved the mechanics against GitHub. These tests pin the
invariants that must hold in situations we refuse to create on GitHub —
above all: a superseded epoch carrying a *hypothetical* provider CLEAN must
never turn into a success on the new head, and nothing may adopt a check
run just because its name matches.
"""
import json
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

import checks  # noqa: E402
import governor as gov  # noqa: E402
import store  # noqa: E402

REPO = "PhysShell/evm-from-scratch"
REPO_ID = 1335599563
PR = 18
HEAD_A = "c9416bd778b0ec375c8b7e40470192d48f645894"
HEAD_B = "11b0b5d143b3c787a543cb5d7c014a4ab629fd75"
FOREIGN_APP = 1234567


def governor_run(check_id, head_sha, epoch_id, *, app_id=checks.GOVERNOR_APP_ID,
                 slug=checks.GOVERNOR_APP_SLUG, name=checks.CHECK_NAME,
                 status="completed", conclusion="failure"):
    return {"id": check_id, "name": name, "head_sha": head_sha,
            "status": status, "conclusion": conclusion,
            "external_id": epoch_id, "app": {"id": app_id, "slug": slug}}


class FakeApi:
    """Stands in for the Checks client; records everything it is asked to do."""

    def __init__(self, head_sha, runs=None):
        self.head_sha = head_sha
        self.runs = runs or []
        self.created = []
        self.concluded = []
        self._next_id = 900000

    def pull_request(self, number):
        return {"head": {"sha": self.head_sha},
                "base": {"repo": {"id": REPO_ID}}}

    def repository_id(self):
        return REPO_ID

    def create(self, head_sha, external_id, output, status="in_progress"):
        self._next_id += 1
        run = governor_run(self._next_id, head_sha, external_id,
                           status=status, conclusion=None)
        run["output"] = output
        self.created.append(run)
        self.runs.append(run)
        return run

    def conclude(self, check_run_id, conclusion, output):
        if conclusion not in checks.ALLOWED_CONCLUSIONS:
            raise ValueError(f"conclusion {conclusion!r} is not permitted")
        self.concluded.append((check_run_id, conclusion))
        for run in self.runs:
            if run["id"] == check_run_id:
                run["status"] = "completed"
                run["conclusion"] = conclusion
                run["output"] = output
                return 200, run
        return 404, {}

    def for_ref(self, ref, name=checks.CHECK_NAME):
        return [r for r in self.runs
                if r["head_sha"] == ref and r["name"] == name]


@pytest.fixture()
def governor(tmp_path):
    g = gov.Governor.__new__(gov.Governor)
    g.repo = REPO
    g.db = store.Store(tmp_path / "governor.sqlite3")
    g.api = FakeApi(HEAD_A)
    g.auth_state = "AUTHORIZED"
    yield g
    g.db.close()


# --- no success exists anywhere --------------------------------------------

def test_decision_rule_has_no_success_path():
    for auth in ("AUTHORIZED", "AUTH_LOST", "REFRESH_OUTCOME_UNKNOWN",
                 "REAUTH_REQUIRED"):
        for providers in ({"codex": "ABSENT", "coderabbit": "ABSENT"},
                          {"codex": "PRESENT", "coderabbit": "PRESENT"}):
            verdict, conclusion = gov.decide(auth, providers)
            assert conclusion == "failure"
            assert verdict in (gov.NOT_ESTABLISHED, gov.AUTHORIZATION_UNAVAILABLE)


def test_success_neutral_skipped_are_refused_by_the_client():
    api = FakeApi(HEAD_A, [governor_run(1, HEAD_A, "e")])
    for forbidden in ("success", "neutral", "skipped"):
        with pytest.raises(ValueError):
            api.conclude(1, forbidden, {})
    assert "success" not in checks.ALLOWED_CONCLUSIONS
    assert "neutral" not in checks.ALLOWED_CONCLUSIONS
    assert "skipped" not in checks.ALLOWED_CONCLUSIONS


def test_auth_loss_is_fail_closed():
    for lost in ("AUTH_LOST", "REFRESH_OUTCOME_UNKNOWN", "REAUTH_REQUIRED"):
        verdict, conclusion = gov.decide(lost, {"codex": "ABSENT",
                                                "coderabbit": "ABSENT"})
        assert verdict == gov.AUTHORIZATION_UNAVAILABLE
        assert conclusion == "failure"


# --- the stale-head invariant ----------------------------------------------

def test_hypothetical_clean_on_a_stale_epoch_never_reaches_the_new_head(governor):
    governor.open_epoch(PR)
    epoch_a = dict(governor.db.current_epoch(REPO_ID, PR))
    # pretend the old epoch had a complete provider CLEAN bundle
    governor.db.record_decision(epoch_a["epoch_id"], "HYPOTHETICAL_CLEAN",
                                "failure", "AUTHORIZED",
                                {"codex": "CLEAN", "coderabbit": "CLEAN"},
                                gov.DECISION_RULE_REVISION, {}, "t")

    governor.api.head_sha = HEAD_B
    governor.reconcile(PR)

    epochs = {e["head_sha"]: e["state"] for e in governor.db.epochs_for(REPO_ID, PR)}
    assert epochs[HEAD_A] == store.STALE
    assert epochs[HEAD_B] == store.CURRENT
    for run in governor.api.runs:
        assert run["conclusion"] != "success"
    new_runs = [r for r in governor.api.runs if r["head_sha"] == HEAD_B]
    assert len(new_runs) == 1
    assert new_runs[0]["conclusion"] == "failure"
    assert new_runs[0]["external_id"] != epoch_a["epoch_id"]


def test_old_check_is_cancelled_not_migrated(governor):
    governor.open_epoch(PR)
    old_check = dict(governor.db.check_for_epoch(
        governor.db.current_epoch(REPO_ID, PR)["epoch_id"]))
    governor.api.head_sha = HEAD_B
    governor.reconcile(PR)

    old_run = next(r for r in governor.api.runs if r["id"] == old_check["check_run_id"])
    assert old_run["head_sha"] == HEAD_A       # binding never moved
    assert old_run["conclusion"] == "cancelled"
    assert any(r["head_sha"] == HEAD_B for r in governor.api.runs)


def test_integrator_never_writes_stale_as_a_conclusion():
    assert "stale" not in checks.ALLOWED_CONCLUSIONS
    source = (BASE / "harness" / "governor.py").read_text()
    assert '"stale"' not in source and "'stale'" not in source


# --- idempotency and durability --------------------------------------------

def test_reconciliation_is_idempotent(governor):
    governor.open_epoch(PR)
    governor.api.head_sha = HEAD_B
    governor.reconcile(PR)
    runs_after_first = len(governor.api.runs)
    epochs_after_first = len(governor.db.epochs_for(REPO_ID, PR))

    second = governor.reconcile(PR)
    assert second["changed"] is False
    assert len(governor.api.runs) == runs_after_first
    assert len(governor.db.epochs_for(REPO_ID, PR)) == epochs_after_first


def test_state_survives_a_process_restart(tmp_path):
    db_path = tmp_path / "governor.sqlite3"
    first = gov.Governor.__new__(gov.Governor)
    first.repo, first.db, first.auth_state = REPO, store.Store(db_path), "AUTHORIZED"
    first.api = FakeApi(HEAD_A)
    first.open_epoch(PR)
    created_runs = list(first.api.runs)
    first.db.close()                                     # process dies here

    second = gov.Governor.__new__(gov.Governor)
    second.repo, second.db, second.auth_state = REPO, store.Store(db_path), "AUTHORIZED"
    second.api = FakeApi(HEAD_B, created_runs)           # fresh process, same GitHub
    result = second.reconcile(PR)

    epochs = {e["head_sha"]: e["state"] for e in second.db.epochs_for(REPO_ID, PR)}
    assert epochs[HEAD_A] == store.STALE                 # remembered across restart
    assert epochs[HEAD_B] == store.CURRENT
    assert len([r for r in second.api.runs if r["head_sha"] == HEAD_A]) == 1
    assert len([r for r in second.api.runs if r["head_sha"] == HEAD_B]) == 1
    assert result["stored_head"] == HEAD_A
    second.db.close()


def test_store_enforces_one_logical_check_per_head_and_name(tmp_path):
    db = store.Store(tmp_path / "s.sqlite3")
    db.open_epoch("e1", REPO_ID, REPO, PR, HEAD_A, "t")
    db.record_check("e1", 1, checks.CHECK_NAME, REPO_ID, PR, HEAD_A,
                    checks.GOVERNOR_APP_ID, "e1", "completed", "failure", "t")
    db.open_epoch("e2", REPO_ID, REPO, PR, HEAD_B, "t")
    with pytest.raises(Exception):
        db.record_check("e2", 2, checks.CHECK_NAME, REPO_ID, PR, HEAD_A,
                        checks.GOVERNOR_APP_ID, "e2", "completed", "failure", "t")
    db.close()


# --- provenance and spoofing -----------------------------------------------

def test_same_name_from_another_app_is_not_governor_owned():
    impostor = governor_run(42, HEAD_B, "epoch-whatever", app_id=FOREIGN_APP,
                            slug="not-the-governor")
    assert checks.is_governor_owned(impostor) is False
    assert checks.matches_epoch(impostor, "epoch-whatever", HEAD_B) is False


def test_matching_requires_app_external_id_head_and_name():
    epoch_id = "epoch-a22f7efbe6ecfe9d"
    good = governor_run(1, HEAD_B, epoch_id)
    assert checks.matches_epoch(good, epoch_id, HEAD_B) is True
    assert checks.matches_epoch(governor_run(1, HEAD_A, epoch_id),
                                epoch_id, HEAD_B) is False
    assert checks.matches_epoch(governor_run(1, HEAD_B, "epoch-other"),
                                epoch_id, HEAD_B) is False
    assert checks.matches_epoch(governor_run(1, HEAD_B, epoch_id, name="other"),
                                epoch_id, HEAD_B) is False


def test_spoofed_run_is_not_adopted_during_mapping_recovery(governor):
    governor.open_epoch(PR)
    epoch = dict(governor.db.current_epoch(REPO_ID, PR))
    governor.db.forget_check_run_id(epoch["epoch_id"], "t")
    # an impostor with the same display name sits on the same head
    governor.api.runs.append(governor_run(4242, HEAD_A, epoch["epoch_id"],
                                          app_id=FOREIGN_APP,
                                          slug="not-the-governor"))
    actions = governor._recover_check_mapping(epoch, HEAD_A)
    recovered = governor.db.check_for_epoch(epoch["epoch_id"])
    assert recovered["check_run_id"] != 4242
    assert not any(a.get("check_mapping_recovered") == 4242 for a in actions)


def test_ambiguous_recovery_fails_closed(governor):
    governor.open_epoch(PR)
    epoch = dict(governor.db.current_epoch(REPO_ID, PR))
    governor.db.forget_check_run_id(epoch["epoch_id"], "t")
    governor.api.runs.append(governor_run(777, HEAD_A, epoch["epoch_id"]))
    actions = governor._recover_check_mapping(epoch, HEAD_A)
    assert any(a.get("fail_closed") for a in actions)
    assert governor.db.check_for_epoch(epoch["epoch_id"])["check_run_id"] is None


# --- reconciliation repairs state, never evidence --------------------------

def test_reconciliation_never_manufactures_provider_evidence(governor):
    governor.open_epoch(PR)
    governor.api.head_sha = HEAD_B
    governor.reconcile(PR)
    for epoch in governor.db.epochs_for(REPO_ID, PR):
        for decision in governor.db.decisions_for(epoch["epoch_id"]):
            providers = json.loads(decision["provider_state"])
            assert set(providers.values()) <= {"ABSENT"}, providers
            assert json.loads(decision["evidence_refs"]).get(
                "provider_evidence", []) == []


def test_check_output_is_a_projection_carrying_provenance(governor):
    governor.open_epoch(PR)
    epoch = dict(governor.db.current_epoch(REPO_ID, PR))
    governor.conclude_epoch(epoch["epoch_id"])
    run = next(r for r in governor.api.runs if r["head_sha"] == HEAD_A)
    summary = run["output"]["summary"]
    assert epoch["epoch_id"] in summary
    assert HEAD_A in summary                        # full SHA, not a prefix
    assert "Decision rule: " + gov.DECISION_RULE_REVISION in summary
    assert "Gate: FAIL CLOSED" in summary
    assert "Codex evidence: ABSENT" in summary
