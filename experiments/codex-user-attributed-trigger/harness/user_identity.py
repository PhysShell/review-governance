#!/usr/bin/env python3
"""Readback of the identity a GitHub App user access token acts as.

Necessary-but-not-sufficient step of A1b: proves the token resolves to the
GitHub user (`PhysShell`, id 45852143, type User) *and* records what the
App mediation looks like from the token's own side —
`GET /user/installations` shows which App installations this user token can
act through, and the per-installation repository list shows the effective
scope (App permissions ∩ user permissions ∩ installation selection).

Writes the non-secret descriptor to
experiments/codex-user-attributed-trigger/user-identity.json.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

import user_api

EXPECTED_LOGIN = "PhysShell"
EXPECTED_ID = 45852143
GOVERNOR_APP_ID = 4669438


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(
        Path(__file__).resolve().parent.parent / "user-identity.json"))
    args = ap.parse_args()

    status, meta, me = user_api.request("GET", "/user")
    if status != 200:
        print(f"FATAL: GET /user failed: {status} {me}", file=sys.stderr)
        return 1
    if not (me["login"] == EXPECTED_LOGIN and me["id"] == EXPECTED_ID
            and me["type"] == "User"):
        print(f"FATAL: unexpected identity: {me['login']}/{me['id']}/{me['type']}",
              file=sys.stderr)
        return 1

    status, _, installs = user_api.request("GET", "/user/installations")
    if status != 200:
        print(f"FATAL: GET /user/installations failed: {status} {installs}",
              file=sys.stderr)
        return 1
    governor = next((i for i in installs.get("installations", [])
                     if i.get("app_id") == GOVERNOR_APP_ID), None)

    repos = None
    if governor:
        status, _, listing = user_api.request(
            "GET", f"/user/installations/{governor['id']}/repositories")
        if status == 200:
            repos = sorted(r["full_name"] for r in listing["repositories"])

    identity = {
        "captured_at": datetime.datetime.now(datetime.timezone.utc)
                       .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "auth_model": "github_app_user_access_token",
        "token": user_api.token_metadata(),
        "user": {"login": me["login"], "id": me["id"], "type": me["type"]},
        "governor_installation_visible_to_user_token": bool(governor),
        "installation": None if not governor else {
            "id": governor["id"],
            "app_id": governor["app_id"],
            "app_slug": governor.get("app_slug"),
            "repository_selection": governor.get("repository_selection"),
            "permissions": governor.get("permissions"),
        },
        "repositories_reachable_through_installation": repos,
        "installations_total": len(installs.get("installations", [])),
        "response_headers": meta,
    }
    Path(args.out).write_text(json.dumps(identity, indent=2) + "\n")
    print(json.dumps(identity, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
