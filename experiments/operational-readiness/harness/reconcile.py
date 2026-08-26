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


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="PhysShell/evm-from-scratch")
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--db", default=".captures/a5a/decisions.sqlite3")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    history = dec.History(args.db)
    try:
        result = reconcile(args.repo, args.pr, history)
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
