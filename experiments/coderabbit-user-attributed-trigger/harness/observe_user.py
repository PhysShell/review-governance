#!/usr/bin/env python3
"""Bounded, read-only observation of CodeRabbit activity on the A1b-R probe PR.

Reads with the GitHub App user access token (same identity as the trigger;
reads do not affect attribution evidence). Records snapshots; it does not
interpret — classification happens in the protocol.

Exit codes: 0 a terminal artifact was observed for every watched request
(a reaction alone is only an acknowledgement signal and keeps the window
open) · 3 the observation window elapsed first.
"""
import argparse
import datetime
import json
import time
from pathlib import Path

import user_api

PROVIDER_HINTS = {"coderabbit": ("coderabbit",), "codex": ("codex",)}


def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slim_user(u) -> dict:
    return {"login": u["login"], "id": u["id"], "type": u.get("type")}


def fetch_state(repo, pr, watch_ids) -> dict:
    errors = []

    def get(path, want_list):
        status, _, body = user_api.request("GET", path)
        if status != 200:
            errors.append({"path": path, "status": status})
            return [] if want_list else {}
        return body if body is not None else ([] if want_list else {})

    pr_data = get(f"/repos/{repo}/pulls/{pr}", want_list=False)
    return {
        "taken_at": utcnow(),
        "observed_via": "github app user access token (read-only)",
        "pr": {"state": pr_data.get("state"), "draft": pr_data.get("draft"),
               "head_sha": (pr_data.get("head") or {}).get("sha")},
        "issue_comments": [
            {"id": c["id"], "user": slim_user(c["user"]),
             "created_at": c["created_at"], "updated_at": c.get("updated_at"),
             "body": c["body"],
             "performed_via_github_app":
                 (c.get("performed_via_github_app") or {}).get("slug")}
            for c in get(f"/repos/{repo}/issues/{pr}/comments?per_page=100",
                         want_list=True)],
        "reviews": [
            {"id": r["id"], "user": slim_user(r["user"]), "state": r["state"],
             "submitted_at": r.get("submitted_at"), "commit_id": r.get("commit_id"),
             "body": r.get("body")}
            for r in get(f"/repos/{repo}/pulls/{pr}/reviews?per_page=100",
                         want_list=True)],
        "review_comments": [
            {"id": c["id"], "user": slim_user(c["user"]),
             "created_at": c["created_at"], "path": c.get("path"),
             "commit_id": c.get("commit_id"), "body": c["body"]}
            for c in get(f"/repos/{repo}/pulls/{pr}/comments?per_page=100",
                         want_list=True)],
        "reactions": {
            str(cid): [
                {"content": r["content"], "user": slim_user(r["user"]),
                 "created_at": r["created_at"]}
                for r in get(f"/repos/{repo}/issues/comments/{cid}/reactions"
                             f"?per_page=100", want_list=True)]
            for cid in watch_ids},
        "fetch_errors": errors,
    }


def provider_signals(snap, watch, self_login) -> dict:
    base = watch["provider"].split("-")[0]
    hints = PROVIDER_HINTS.get(base, (base,))

    def hinted(user):
        return any(h in (user.get("login") or "").lower() for h in hints)

    def fresh(ts):
        return bool(ts) and ts >= watch["since"]

    return {
        "reactions_on_request": [
            r for r in snap["reactions"].get(str(watch["comment_id"]), [])
            if r["user"]["login"] != self_login],
        "issue_comments": [c for c in snap["issue_comments"]
                           if hinted(c["user"]) and fresh(c["created_at"])
                           and c["id"] != watch["comment_id"]],
        "reviews": [r for r in snap["reviews"]
                    if hinted(r["user"]) and fresh(r.get("submitted_at"))],
        "review_comments": [c for c in snap["review_comments"]
                            if hinted(c["user"]) and fresh(c["created_at"])],
    }


def is_terminal(sig) -> bool:
    return bool(sig["reviews"] or sig["issue_comments"] or sig["review_comments"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--pr", required=True, type=int)
    ap.add_argument("--envelope", action="append", default=[])
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--window-min", type=int, default=30)
    ap.add_argument("--interval-sec", type=int, default=45)
    ap.add_argument("--self-login", default="PhysShell")
    ap.add_argument("--out-dir", default=".captures/a1b/observation")
    args = ap.parse_args()

    watches = []
    for path in args.envelope:
        env = json.loads(Path(path).read_text())
        watches.append({"provider": env["purpose"],
                        "comment_id": env["request_comment"]["id"],
                        "since": env["request_comment"]["created_at"]})

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + args.window_min * 60
    poll = 0
    while True:
        poll += 1
        snap = fetch_state(args.repo, args.pr, [w["comment_id"] for w in watches])
        sigs = {w["provider"]: provider_signals(snap, w, args.self_login)
                for w in watches}
        snap["provider_signals"] = sigs
        stamp = snap["taken_at"].replace(":", "").replace("-", "")
        (out / f"snapshot_{stamp}_{poll:03d}.json").write_text(
            json.dumps(snap, indent=2) + "\n")
        line = " ".join(
            f"{p}[react={len(s['reactions_on_request'])}"
            f" cmt={len(s['issue_comments'])} rev={len(s['reviews'])}"
            f" inline={len(s['review_comments'])}]"
            for p, s in sigs.items())
        print(f"poll {poll:03d} {snap['taken_at']} {line or 'baseline'}", flush=True)
        if args.baseline:
            return 0
        if watches and all(is_terminal(s) for s in sigs.values()):
            print("terminal artifacts observed for all watched requests")
            return 0
        if time.time() > deadline:
            print("observation window elapsed; classification is done in the "
                  "protocol, not here")
            return 3
        time.sleep(args.interval_sec)


if __name__ == "__main__":
    raise SystemExit(main())
