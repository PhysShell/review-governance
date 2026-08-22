#!/usr/bin/env python3
"""Create ONE issue comment on a PR using the GitHub App **user access
token** and freeze a sanitized request envelope.

Captured: repository_id, PR number and exact head SHA at request time,
comment id, body, server-side created_at, author login/id/type,
`performed_via_github_app` verbatim when GitHub returns it (and an explicit
observability flag when it does not), and `auth_model`.
Never captured: the token, refresh token, or any authorization header.

Aborts if the readback does not attribute the comment to the expected
GitHub user — wrong attribution is a stop condition, not something to work
around.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

import user_api

EXPECTED_LOGIN = "PhysShell"
EXPECTED_ID = 45852143


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--pr", required=True, type=int)
    ap.add_argument("--body", required=True)
    ap.add_argument("--label", required=True, help="identity-probe | codex")
    ap.add_argument("--out-dir", default=".captures/a1b")
    args = ap.parse_args()

    status, _, repo = user_api.request("GET", f"/repos/{args.repo}")
    assert status == 200, (status, repo)

    status, _, pr = user_api.request("GET", f"/repos/{args.repo}/pulls/{args.pr}")
    assert status == 200, (status, pr)
    pr_snapshot = {
        "number": pr["number"], "state": pr["state"], "draft": pr["draft"],
        "head_sha": pr["head"]["sha"], "head_ref": pr["head"]["ref"],
        "updated_at": pr["updated_at"],
    }

    status, post_meta, created = user_api.request(
        "POST", f"/repos/{args.repo}/issues/{args.pr}/comments",
        body={"body": args.body})
    if status != 201:
        print(f"FATAL: comment creation failed: {status} {created}", file=sys.stderr)
        return 1

    status, _, readback = user_api.request(
        "GET", f"/repos/{args.repo}/issues/comments/{created['id']}")
    assert status == 200, (status, readback)

    author = readback["user"]
    if not (author["id"] == EXPECTED_ID and author["login"] == EXPECTED_LOGIN
            and author.get("type") == "User"):
        print(f"FATAL: comment {readback['id']} not attributed to the expected "
              f"user: {author}", file=sys.stderr)
        return 1

    via = readback.get("performed_via_github_app")
    envelope = {
        "captured_at": datetime.datetime.now(datetime.timezone.utc)
                       .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "purpose": args.label,
        "auth_model": "github_app_user_access_token",
        "repo": args.repo,
        "repository_id": repo["id"],
        "pr_number": args.pr,
        "pr_at_request": pr_snapshot,
        "request_comment": {
            "id": readback["id"],
            "body": readback["body"],
            "created_at": readback["created_at"],
            "html_url": readback["html_url"],
            "user": {"login": author["login"], "id": author["id"],
                     "type": author.get("type")},
            "performed_via_github_app": (
                {"id": via.get("id"), "slug": via.get("slug"),
                 "name": via.get("name")} if via else None),
        },
        "app_mediation_observability": (
            "PASS" if via else "NOT_AVAILABLE_ON_OBSERVED_CARRIER"),
        "response_headers": post_meta,
        "token_provenance": user_api.token_metadata(),
    }
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"user_request_{args.label}.json"
    path.write_text(json.dumps(envelope, indent=2) + "\n")
    print(json.dumps({
        "written": str(path),
        "comment_id": envelope["request_comment"]["id"],
        "author": envelope["request_comment"]["user"],
        "performed_via_github_app": envelope["request_comment"]["performed_via_github_app"],
        "app_mediation_observability": envelope["app_mediation_observability"],
        "created_at": envelope["request_comment"]["created_at"],
        "head_sha_at_request": pr_snapshot["head_sha"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
