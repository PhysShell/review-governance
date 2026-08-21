#!/usr/bin/env python3
"""Create ONE issue comment on a PR as the Governor App installation identity
and capture a sanitized request envelope.

Captured: PR state and exact head SHA at request time, comment id, body,
server-side created_at, author login / numeric id / type,
performed_via_github_app slug, sanitized response headers.
Never captured: tokens, JWTs, authorization headers.

Aborts loudly if the readback shows the comment was NOT authored by the
recorded App bot identity — a wrong authorship is a stop condition, not
something to work around.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

import app_api


def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--pr", required=True, type=int)
    ap.add_argument("--body", required=True)
    ap.add_argument("--label", required=True,
                    help="codex | coderabbit | identity-probe")
    ap.add_argument("--out-dir", default=".captures")
    args = ap.parse_args()

    identity = json.loads(
        (Path(__file__).resolve().parent.parent / "app-identity.json").read_text())
    token = app_api.installation_token(identity["installation_id"])

    status, _, pr = app_api.request(
        "GET", f"/repos/{args.repo}/pulls/{args.pr}", bearer=token)
    assert status == 200, (status, pr)
    pr_snapshot = {
        "number": pr["number"],
        "state": pr["state"],
        "draft": pr["draft"],
        "head_sha": pr["head"]["sha"],
        "head_ref": pr["head"]["ref"],
        "updated_at": pr["updated_at"],
    }

    status, post_meta, created = app_api.request(
        "POST", f"/repos/{args.repo}/issues/{args.pr}/comments",
        bearer=token, body={"body": args.body})
    assert status == 201, (status, created)

    status, _, readback = app_api.request(
        "GET", f"/repos/{args.repo}/issues/comments/{created['id']}", bearer=token)
    assert status == 200, (status, readback)

    author = readback["user"]
    if not (author["id"] == identity["bot_user_id"]
            and author["login"] == identity["bot_login"]
            and author.get("type") == "Bot"):
        print(f"FATAL: comment {readback['id']} NOT authored by the App bot "
              f"identity: {author}", file=sys.stderr)
        return 1

    envelope = {
        "captured_at": utcnow(),
        "purpose": args.label,
        "repo": args.repo,
        "pr_number": args.pr,
        "pr_at_request": pr_snapshot,
        "request_comment": {
            "id": readback["id"],
            "body": readback["body"],
            "created_at": readback["created_at"],
            "html_url": readback["html_url"],
            "user": {"login": author["login"], "id": author["id"],
                     "type": author.get("type")},
            "performed_via_github_app":
                (readback.get("performed_via_github_app") or {}).get("slug"),
        },
        "response_headers": post_meta,
        "auth_transport": "github-app installation token (value withheld)",
    }
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"app_request_{args.label}.json"
    path.write_text(json.dumps(envelope, indent=2) + "\n")
    print(json.dumps({
        "written": str(path),
        "comment_id": envelope["request_comment"]["id"],
        "author": envelope["request_comment"]["user"],
        "created_at": envelope["request_comment"]["created_at"],
        "head_sha_at_request": pr_snapshot["head_sha"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
