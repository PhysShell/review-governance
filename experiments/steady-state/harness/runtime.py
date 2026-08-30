#!/usr/bin/env python3
"""The production composition root. The service, not a script somebody runs.

A6a built the pieces and qualified them by hand. The deployed Governor kept
running `reconcile.py --loop`, which filters epochs on an `a5a-` prefix left
over from fixtures — so the defect that stage reported as closed was still
the one in production, and the tests were describing modules the Governor
never executed. This is the file that makes the components the runtime.

One pass, per non-draft PR against the base:

    RESOLVED, same head, exactly one valid carrier -> adopt, zero writes
    RESOLVED, older head   -> record the transition, open an epoch for the
                              current head, ensure a failure carrier
    NO_EPOCH               -> open a scoped epoch, ensure a failure carrier
    UNRESOLVED             -> stop for that PR; no speculative write

Adoption is the load-bearing half. A producer that writes on every pass
would leave a head carrying several verdicts from the same App, and the
operator would have no way to tell which one the gate consulted — which is
why "zero writes on an unchanged head" is a stage acceptance row rather
than an implementation detail.

Drift is detected by reading GitHub, never by consulting the edge spool.
The webhook is an optimisation; if it never arrives, this loop still finds
the new head on its own schedule.
"""
import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[1] / "operational-readiness" / "harness"))

import carrier
import epochs as ep
import governor
import rounds
import scoped_reconcile as sr

CONFIG_DIR = Path(os.environ.get(
    "GOVERNOR_CONFIG_DIR", os.path.expanduser("~/.config/review-governor")))
HEALTH_FILE = CONFIG_DIR / "runtime-health.json"


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_request(token):
    def request(method, path, tok=None, body=None):
        return governor.request(method, path, token, body)
    return request


def open_prs(request, repo, base):
    status, pulls = request(
        "GET", f"/repos/{repo}/pulls?state=open&base={base}&per_page=100")
    if status != 200:
        return None
    return [{"pr_number": p["number"], "head_sha": p["head"]["sha"],
             "draft": bool(p["draft"]), "base": p["base"]["ref"]}
            for p in sorted(pulls or [], key=lambda p: p["number"])]


def handle(request, repo, pull, store, write_enabled=True, round_store=None):
    """One PR, one pass. Returns what was observed and what was done."""
    pr = pull["pr_number"]
    head = pull["head_sha"]
    outcome = {"pr_number": pr, "head_sha": head, "draft": pull["draft"],
               "at": utcnow(), "writes": 0}

    if pull["draft"]:
        outcome["action"] = "OBSERVED_ONLY"
        outcome["cause"] = "draft PRs are observed and never written to"
        return outcome

    known = store.last_known_head(repo, pr)
    outcome["scope_state"] = known["state"]

    if known["state"] == ep.UNRESOLVED:
        outcome["action"] = "STOP"
        outcome["cause"] = known.get("cause")
        return outcome

    if known["state"] == ep.RESOLVED and known["head_sha"] != head:
        # The head moved. Recorded as a transition rather than silently
        # overwritten: the question "when did this head become current"
        # must survive whatever happens next.
        outcome["head_transition"] = {"from": known["head_sha"], "to": head}
        # An acceptance is about a commit. When that commit stops being the
        # head, the acceptance must be marked here, in the loop that
        # actually observes the move — a method that can do it is not the
        # same thing as a production transition that does.
        if round_store is not None:
            outcome["invalidated_acceptances"] = \
                round_store.invalidate_for_head_move(repo, pr, head)

    if not write_enabled:
        outcome["action"] = "DRY_RUN"
        return outcome

    ensured = carrier.ensure(request, repo, pr, head, None, store)
    outcome["carrier"] = ensured
    outcome["action"] = ensured["state"]
    outcome["writes"] = 1 if ensured.get("wrote") else 0
    return outcome


def pass_once(request, repo, base, store, write_enabled=True,
              round_store=None):
    pulls = open_prs(request, repo, base)
    if pulls is None:
        return {"at": utcnow(), "state": "UNREADABLE",
                "cause": "cannot list open PRs; nothing is assumed"}
    outcomes = [handle(request, repo, p, store, write_enabled, round_store)
                for p in pulls]
    # A PR that leaves the open set leaves this loop's view with it. Until
    # A6g-c1 that meant closing a PR removed the object while the
    # acceptance about it stayed ACCEPTED — the permission outliving the
    # thing it permitted. Standing acceptances are enumerated from the
    # store, and any whose PR is no longer open is terminalized from the
    # observed state.
    terminalized = []
    if round_store is not None:
        open_numbers = {p["pr_number"] for p in pulls}
        for pr in round_store.prs_with_standing_acceptances(repo):
            if pr in open_numbers:
                continue
            status, pull = request("GET", f"/repos/{repo}/pulls/{pr}")
            if status != 200 or not pull:
                # Unreadable is not closed. Leaving the acceptance standing
                # is the fail-closed choice: it blocks nothing and asserts
                # nothing.
                terminalized.append({"pr_number": pr, "state": "UNREADABLE",
                                     "http_status": status})
                continue
            if pull.get("state") == "open":
                continue
            ended = round_store.terminalize(
                repo, pr, cause=f"PR_{pull['state'].upper()}"
                + ("_MERGED" if pull.get("merged") else ""))
            terminalized.append({"pr_number": pr, "pr_state": pull["state"],
                                 "merged": bool(pull.get("merged")),
                                 "acceptances": ended})
    reconciliations = [
        sr.reconcile(request, repo, p["pr_number"], store)
        for p in pulls if not p["draft"]]
    return {"at": utcnow(), "state": "OK", "pr_count": len(pulls),
            "outcomes": outcomes, "reconciliations": reconciliations,
            "terminalized": terminalized,
            "writes": sum(o["writes"] for o in outcomes)}


def write_health(path, result):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({
        "last_complete_pass_at": result.get("at"),
        "state": result.get("state"),
        "pr_count": result.get("pr_count"),
        "writes_last_pass": result.get("writes"),
        "composition": "steady-state runtime (scoped epochs)",
    }, indent=2) + "\n")


def write_reconciliation_health(path, result):
    """Asserts that scoped comparisons actually ran, not that a file is new.

    An age check on a file proves a process is alive. What a success guard
    needs to know is narrower: that for every non-draft PR a scoped
    comparison was performed, with which stored and current heads. So the
    signal carries the comparisons themselves.
    """
    recs = [r for r in (result.get("reconciliations") or [])]
    compared = [r for r in recs if r.get("comparison_performed")]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({
        "last_complete_pass_at": result.get("at"),
        "comparisons_attempted": len(recs),
        "comparisons_performed": len(compared),
        "all_compared": len(recs) > 0 and len(compared) == len(recs),
        "per_pr": [{"pr_number": r["pr_number"],
                    "scope_state": r.get("scope_state"),
                    "stored_head": r.get("stored_head"),
                    "github_head": r.get("github_head"),
                    "comparison_performed": r.get("comparison_performed"),
                    "drift_detected": r.get("drift_detected")} for r in recs],
        "source": "steady-state runtime, scoped reconciliation",
    }, indent=2) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="PhysShell/evm-from-scratch")
    ap.add_argument("--base", default="main")
    ap.add_argument("--db", default=str(CONFIG_DIR / "production.sqlite3"))
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--window", type=int, default=0,
                    help="0 means until stopped, which is what the unit uses")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="observe and report without creating any carrier")
    ap.add_argument("--rounds-db", default=str(CONFIG_DIR / "rounds.sqlite3"))
    ap.add_argument("--health-file", default=str(HEALTH_FILE))
    ap.add_argument("--reconciliation-health",
                    default=str(CONFIG_DIR / "reconciliation-health.json"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    store = ep.EpochStore(args.db)
    round_store = rounds.RoundStore(args.rounds_db)
    try:
        token = governor.installation_token()
        request = make_request(token)
        if args.once:
            result = pass_once(request, args.repo, args.base, store,
                               write_enabled=not args.dry_run,
                               round_store=round_store)
            if result["state"] == "OK" and not args.dry_run:
                write_health(args.health_file, result)
                write_reconciliation_health(args.reconciliation_health, result)
            rendered = json.dumps(result, indent=2, default=str)
            if args.out:
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.out).write_text(rendered + "\n")
            print(rendered)
            return 0

        deadline = None if args.window <= 0 else time.time() + args.window
        while deadline is None or time.time() < deadline:
            # The token is re-minted each pass rather than held for the
            # lifetime of the process: an installation token expires, and a
            # loop that runs for days on one is a loop that stops working
            # quietly.
            request = make_request(governor.installation_token())
            result = pass_once(request, args.repo, args.base, store,
                               write_enabled=not args.dry_run,
                               round_store=round_store)
            if result["state"] == "OK" and not args.dry_run:
                write_health(args.health_file, result)
                write_reconciliation_health(args.reconciliation_health, result)
            if result.get("writes"):
                print(json.dumps(result, default=str), flush=True)
            time.sleep(args.interval)
        return 0
    finally:
        store.close()
        round_store.close()


if __name__ == "__main__":
    sys.exit(main())
