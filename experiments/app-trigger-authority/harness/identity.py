#!/usr/bin/env python3
"""Readback of the identity this harness acts as.

Proves — before any trigger experiment — that the acting identity is the
Governor App installation bot, not a human/OAuth user, and records the
non-secret identity descriptor (app id, installation id, bot login, bot
numeric actor id, granted permissions, repository scope) into
experiments/app-trigger-authority/app-identity.json.
"""
import argparse
import datetime
import json
import urllib.parse
from pathlib import Path

import app_api


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(
        Path(__file__).resolve().parent.parent / "app-identity.json"))
    args = ap.parse_args()

    jwt = app_api.app_jwt()
    status, _, app = app_api.request("GET", "/app", bearer=jwt)
    assert status == 200, (status, app)

    status, _, installs = app_api.request("GET", "/app/installations", bearer=jwt)
    assert status == 200 and installs, (status, installs)
    assert len(installs) == 1, f"expected exactly one installation, got {len(installs)}"
    inst = installs[0]

    token = app_api.installation_token(inst["id"])
    status, _, repos = app_api.request("GET", "/installation/repositories", bearer=token)
    assert status == 200, (status, repos)

    bot_login = f"{app['slug']}[bot]"
    status, _, bot_user = app_api.request(
        "GET", f"/users/{urllib.parse.quote(bot_login, safe='')}", bearer=token)
    assert status == 200, (status, bot_user)
    assert bot_user["type"] == "Bot", bot_user

    identity = {
        "captured_at": datetime.datetime.now(datetime.timezone.utc)
                       .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "app_id": app["id"],
        "app_slug": app["slug"],
        "app_name": app["name"],
        "owner": {"login": app["owner"]["login"], "id": app["owner"]["id"]},
        "installation_id": inst["id"],
        "installation_account": {"login": inst["account"]["login"],
                                 "id": inst["account"]["id"]},
        "repository_selection": inst["repository_selection"],
        "repositories": sorted(r["full_name"] for r in repos["repositories"]),
        "permissions_granted": inst["permissions"],
        "bot_login": bot_login,
        "bot_user_id": bot_user["id"],
        "bot_user_type": bot_user["type"],
    }
    Path(args.out).write_text(json.dumps(identity, indent=2) + "\n")
    print(json.dumps(identity, indent=2))


if __name__ == "__main__":
    main()
