#!/usr/bin/env python3
"""The A2b Governor: epochs, fail-closed verdicts, and reconciliation.

Policy in one sentence: the Governor publishes *its own* verdict about what
it can currently establish, bound to an exact full head SHA, and it can
never establish anything positive in A2b because no provider evidence is
collected here.

    open-epoch   read the PR from GitHub, open an epoch, publish a check
    conclude     write the fail-closed verdict for an epoch
    reconcile    compare stored head against GitHub, supersede and rebuild
    state        dump durable state
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import checks
import store

DECISION_RULE_REVISION = "a2b.1"

# Governor verdicts (never provider verdicts)
NOT_ESTABLISHED = "NOT_ESTABLISHED"
AUTHORIZATION_UNAVAILABLE = "AUTHORIZATION_UNAVAILABLE"
SUPERSEDED = "SUPERSEDED"

TRIGGER_ALLOWED_AUTH_STATES = frozenset({"AUTHORIZED"})


def epoch_id_for(repo_id: int, pr_number: int, head_sha: str) -> str:
    digest = hashlib.sha256(f"{repo_id}:{pr_number}:{head_sha}".encode())
    return f"epoch-{digest.hexdigest()[:16]}"


def decide(auth_state: str, provider_state: dict) -> tuple:
    """The whole decision rule for A2b. There is no path to success."""
    if auth_state not in TRIGGER_ALLOWED_AUTH_STATES:
        return AUTHORIZATION_UNAVAILABLE, "failure"
    # provider evidence is never collected in A2b, so nothing can be
    # established even when authorization is healthy
    if any(v == "PRESENT" for v in provider_state.values()):
        # A2b collects no provider evidence; if a caller claims otherwise the
        # Governor still refuses to establish a positive verdict here.
        return NOT_ESTABLISHED, "failure"
    return NOT_ESTABLISHED, "failure"


def output_for(verdict, conclusion, epoch, auth_state, provider_state,
               evidence_refs) -> dict:
    lines = [
        f"Governor verdict: {verdict}",
        f"Epoch: {epoch['epoch_id']} (generation {epoch['generation']})",
        f"Head: {epoch['head_sha']}",
        f"Authorization: {auth_state}",
        f"Codex evidence: {provider_state.get('codex', 'ABSENT')}",
        f"CodeRabbit evidence: {provider_state.get('coderabbit', 'ABSENT')}",
        f"Decision rule: {DECISION_RULE_REVISION}",
        f"Evidence refs: {json.dumps(evidence_refs, sort_keys=True)}",
        "Gate: FAIL CLOSED",
        "",
        "This is the Governor's own verdict about what it can establish for "
        "this exact head. It is not a provider verdict, and it never "
        "upgrades advisory provider evidence into provider provenance.",
    ]
    return {"title": f"Governor: {verdict}", "summary": "\n".join(lines)}


class Governor:
    def __init__(self, repo: str, db_path: str, auth_state: str = "AUTHORIZED"):
        self.repo = repo
        self.db = store.Store(db_path)
        self.api = checks.Checks(repo)
        self.auth_state = auth_state

    def close(self):
        self.db.close()

    # --- epoch A ---------------------------------------------------------
    def open_epoch(self, pr_number: int) -> dict:
        pull = self.api.pull_request(pr_number)          # GitHub is the truth
        head_sha = pull["head"]["sha"]
        repo_id = pull["base"]["repo"]["id"]
        epoch_id = epoch_id_for(repo_id, pr_number, head_sha)
        at = checks.utcnow()

        current = self.db.current_epoch(repo_id, pr_number)
        if current and current["head_sha"] != head_sha:
            self._supersede(dict(current), at)

        epoch = self.db.open_epoch(epoch_id, repo_id, self.repo, pr_number,
                                   head_sha, at)
        existing = self.db.check_for_epoch(epoch_id)
        if existing and existing["check_run_id"]:
            return {"epoch": epoch, "check": dict(existing),
                    "created": False}

        provider_state = {"codex": "ABSENT", "coderabbit": "ABSENT"}
        run = self.api.create(
            head_sha, external_id=epoch_id,
            output=output_for("IN_PROGRESS", None, epoch, self.auth_state,
                              provider_state, {"epoch": epoch_id}))
        assert checks.is_governor_owned(run), "created run is not Governor-owned"
        assert run["head_sha"] == head_sha, "check bound to a different head"
        self.db.record_check(epoch_id, run["id"], checks.CHECK_NAME, repo_id,
                             pr_number, head_sha, (run.get("app") or {}).get("id"),
                             epoch_id, run["status"], run.get("conclusion"), at)
        return {"epoch": epoch, "check": checks.slim(run), "created": True}

    def conclude_epoch(self, epoch_id: str) -> dict:
        epoch = dict(self.db.epoch(epoch_id))
        record = self.db.check_for_epoch(epoch_id)
        provider_state = {"codex": "ABSENT", "coderabbit": "ABSENT"}
        verdict, conclusion = decide(self.auth_state, provider_state)
        evidence_refs = {"epoch": epoch_id, "head": epoch["head_sha"],
                         "provider_evidence": []}
        code, body = self.api.conclude(
            record["check_run_id"], conclusion,
            output_for(verdict, conclusion, epoch, self.auth_state,
                       provider_state, evidence_refs))
        at = checks.utcnow()
        self.db.record_decision(epoch_id, verdict, conclusion, self.auth_state,
                                provider_state, DECISION_RULE_REVISION,
                                evidence_refs, at)
        self.db.record_check(epoch_id, record["check_run_id"], checks.CHECK_NAME,
                             epoch["repo_id"], epoch["pr_number"],
                             epoch["head_sha"],
                             (body.get("app") or {}).get("id"), epoch_id,
                             body.get("status"), body.get("conclusion"), at)
        return {"http_status": code, "verdict": verdict,
                "check": checks.slim(body)}

    # --- supersession ----------------------------------------------------
    def _supersede(self, epoch: dict, at: str) -> dict:
        """Internal epoch -> STALE; its GitHub run -> cancelled.

        `stale` is GitHub's own conclusion and cannot be written by an
        integrator, so supersession is expressed as `cancelled`.
        """
        actions = []
        self.db.mark_stale(epoch["epoch_id"], at)
        actions.append({"epoch_stale": epoch["epoch_id"]})
        record = self.db.check_for_epoch(epoch["epoch_id"])
        if record and record["check_run_id"]:
            provider_state = {"codex": "ABSENT", "coderabbit": "ABSENT"}
            code, body = self.api.conclude(
                record["check_run_id"], "cancelled",
                output_for(SUPERSEDED, "cancelled", epoch, self.auth_state,
                           provider_state,
                           {"epoch": epoch["epoch_id"],
                            "superseded_at": at}))
            self.db.record_decision(epoch["epoch_id"], SUPERSEDED, "cancelled",
                                    self.auth_state, provider_state,
                                    DECISION_RULE_REVISION,
                                    {"superseded_at": at}, at)
            self.db.record_check(epoch["epoch_id"], record["check_run_id"],
                                 checks.CHECK_NAME, epoch["repo_id"],
                                 epoch["pr_number"], epoch["head_sha"],
                                 (body.get("app") or {}).get("id"),
                                 epoch["epoch_id"], body.get("status"),
                                 body.get("conclusion"), at)
            actions.append({"check_cancelled": record["check_run_id"],
                            "http_status": code,
                            "conclusion": body.get("conclusion")})
        return actions

    # --- reconciliation --------------------------------------------------
    def reconcile(self, pr_number: int) -> dict:
        at = checks.utcnow()
        run_id = self.db.start_reconciliation(self.repo, pr_number, at)
        pull = self.api.pull_request(pr_number)
        github_head = pull["head"]["sha"]
        repo_id = pull["base"]["repo"]["id"]
        stored = self.db.current_epoch(repo_id, pr_number)
        stored_head = stored["head_sha"] if stored else None
        actions = []

        if stored_head == github_head:
            actions.append({"noop": "stored head already current"})
            recovered = self._recover_check_mapping(dict(stored), github_head)
            actions.extend(recovered)
            self.db.finish_reconciliation(run_id, github_head, stored_head,
                                          actions, checks.utcnow())
            return {"github_head": github_head, "stored_head": stored_head,
                    "actions": actions, "changed": bool(recovered)}

        if stored:
            actions.extend(self._supersede(dict(stored), at))

        opened = self.open_epoch(pr_number)
        actions.append({"epoch_opened": opened["epoch"]["epoch_id"],
                        "head": github_head,
                        "check_created": opened["created"]})
        concluded = self.conclude_epoch(opened["epoch"]["epoch_id"])
        actions.append({"verdict": concluded["verdict"],
                        "conclusion": concluded["check"]["conclusion"]})
        self.db.finish_reconciliation(run_id, github_head, stored_head, actions,
                                      checks.utcnow())
        return {"github_head": github_head, "stored_head": stored_head,
                "actions": actions, "changed": True}

    def _recover_check_mapping(self, epoch: dict, head_sha: str) -> list:
        """Restore a lost check_run_id — only from a run whose App identity,
        external id, head and name all match. Ambiguity fails closed."""
        record = self.db.check_for_epoch(epoch["epoch_id"])
        if record and record["check_run_id"]:
            return []
        candidates = [c for c in self.api.for_ref(head_sha)
                      if checks.matches_epoch(c, epoch["epoch_id"], head_sha)]
        at = checks.utcnow()
        if len(candidates) == 1:
            run = candidates[0]
            self.db.record_check(epoch["epoch_id"], run["id"], checks.CHECK_NAME,
                                 epoch["repo_id"], epoch["pr_number"], head_sha,
                                 (run.get("app") or {}).get("id"),
                                 epoch["epoch_id"], run["status"],
                                 run.get("conclusion"), at)
            return [{"check_mapping_recovered": run["id"]}]
        if len(candidates) == 0:
            opened = self.open_epoch(epoch["pr_number"])
            return [{"check_recreated": opened["check"].get("id")}]
        return [{"uncertain": f"{len(candidates)} matching Governor runs",
                 "fail_closed": True}]

    # --- inspection ------------------------------------------------------
    def dump(self, pr_number: int, repo_id: int = None) -> dict:
        repo_id = repo_id or self.api.repository_id()
        return {
            "epochs": [dict(e) for e in self.db.epochs_for(repo_id, pr_number)],
            "checks": [dict(c) for c in self.db.all_checks()],
            "decisions": [dict(d) for e in self.db.epochs_for(repo_id, pr_number)
                          for d in self.db.decisions_for(e["epoch_id"])],
            "reconciliations": [dict(r) for r in self.db.reconciliations()],
        }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["open-epoch", "conclude", "reconcile",
                                        "state"])
    ap.add_argument("--repo", default="PhysShell/evm-from-scratch")
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--db", default=".captures/a2b/governor.sqlite3")
    ap.add_argument("--epoch", default=None)
    ap.add_argument("--auth-state", default="AUTHORIZED")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    gov = Governor(args.repo, args.db, args.auth_state)
    try:
        if args.command == "open-epoch":
            result = gov.open_epoch(args.pr)
        elif args.command == "conclude":
            epoch_id = args.epoch
            if not epoch_id:
                pull = gov.api.pull_request(args.pr)
                epoch_id = epoch_id_for(pull["base"]["repo"]["id"], args.pr,
                                        pull["head"]["sha"])
            result = gov.conclude_epoch(epoch_id)
        elif args.command == "reconcile":
            result = gov.reconcile(args.pr)
        else:
            result = gov.dump(args.pr)
    finally:
        gov.close()

    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
