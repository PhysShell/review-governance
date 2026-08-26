#!/usr/bin/env python3
"""Primary-side reconciliation: find the truth by reading GitHub.

This is the path that makes a missed webhook survivable, and it is
deliberately independent of the edge spool. It never asks the edge what it
received; it asks GitHub what is true now and compares that with the
Governor's own durable observation.

That independence is the point. If reconciliation consulted the spool, a
delivery that never arrived — or arrived and was never processed — would be
invisible to exactly the mechanism meant to catch it.
"""
import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path

import decisions as dec
import governor


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def last_known_head(history, repo, pr):
    """The most recent head the Governor durably recorded for this PR."""
    for row in reversed(history.chain()):
        if row["epoch_id"].startswith("a5a-"):
            return row["head_sha"]
    return None


def reconcile(repo, pr, history):
    started = time.monotonic()
    started_at = utcnow()
    token = governor.installation_token()
    status, pull = governor.request("GET", f"/repos/{repo}/pulls/{pr}", token)
    if status != 200:
        return {"error": f"cannot read PR: {status}", "at": started_at}
    github_head = pull["head"]["sha"]
    stored_head = last_known_head(history, repo, pr)

    runs_status, runs = governor.request(
        "GET", f"/repos/{repo}/commits/{github_head}/check-runs?per_page=100",
        token)
    governor_runs = [
        {"id": r["id"], "name": r["name"], "conclusion": r.get("conclusion")}
        for r in ((runs or {}).get("check_runs") or [])
        if (r.get("app") or {}).get("id") == governor.GOVERNOR_APP_ID
    ] if runs_status == 200 else []

    return {
        "started_at": started_at,
        "finished_at": utcnow(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "repo": repo, "pr_number": pr,
        "stored_head": stored_head,
        "github_head": github_head,
        "drift_detected": bool(stored_head) and stored_head != github_head,
        "governor_runs_on_current_head": governor_runs,
        "current_head_is_unreviewed": not governor_runs,
        "source": "GitHub read; the edge delivery spool was not consulted",
    }


def sweep(repo, history):
    """Reconcile every open PR, and say plainly whether the sweep completed.

    `complete: False` is the load-bearing field. A sweep that read three PRs
    out of four and stopped has not established anything about the fourth,
    and the health file must not imply otherwise.
    """
    started_at = utcnow()
    token = governor.installation_token()
    status, pulls = governor.request(
        "GET", f"/repos/{repo}/pulls?state=open&per_page=100", token)
    if status != 200:
        return {"started_at": started_at, "complete": False,
                "error": f"cannot list pulls: {status}", "results": []}
    results = []
    for pull in pulls or []:
        try:
            results.append(reconcile(repo, pull["number"], history))
        except Exception as exc:
            return {"started_at": started_at, "complete": False,
                    "error": f"{type(exc).__name__} on PR {pull['number']}",
                    "results": results}
    return {"started_at": started_at, "finished_at": utcnow(),
            "complete": True, "pr_count": len(results), "results": results,
            "drift": [r for r in results if r.get("drift_detected")]}


def write_health(path, sweep_result):
    """The heartbeat of reconciliation, written only on a complete sweep.

    Deliberately a separate file rather than an in-process flag: if this
    loop dies, its own alarm dies with it, so the thing that pages about
    reconciliation staleness has to be a different process reading this.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "last_complete_sweep_at": sweep_result.get("finished_at"),
        "pr_count": sweep_result.get("pr_count"),
        "drift_count": len(sweep_result.get("drift") or []),
    }, indent=2) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="PhysShell/evm-from-scratch")
    ap.add_argument("--pr", type=int, default=None,
                    help="reconcile one PR and exit; omit for a full sweep")
    ap.add_argument("--db", default=".captures/a5a/decisions.sqlite3")
    ap.add_argument("--loop", action="store_true",
                    help="sweep repeatedly; the deployed configuration")
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--window", type=int, default=0,
                    help="0 means until stopped")
    ap.add_argument("--health-file",
                    default=str(Path(os.environ.get(
                        "GOVERNOR_CONFIG_DIR",
                        os.path.expanduser("~/.config/review-governor")))
                        / "reconciliation-health.json"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    history = dec.History(args.db)
    try:
        if args.loop:
            deadline = None if args.window <= 0 else time.time() + args.window
            last = None
            sweeps = 0
            while deadline is None or time.time() < deadline:
                last = sweep(args.repo, history)
                sweeps += 1
                if last.get("complete"):
                    write_health(args.health_file, last)
                else:
                    print(json.dumps({"incomplete_sweep": last.get("error")}),
                          flush=True)
                time.sleep(args.interval)
            result = {**(last or {}), "sweeps": sweeps}
        elif args.pr:
            result = reconcile(args.repo, args.pr, history)
        else:
            result = sweep(args.repo, history)
            if result.get("complete"):
                write_health(args.health_file, result)
    finally:
        history.close()
    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
