#!/usr/bin/env python3
"""Post a benign attribution comment with a given credential generation and
capture the resulting carrier.

No provider mentions are ever posted by this tool: the body is checked and
refused if it contains one. A1c triggers no providers.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import creds
import gh_probe

PROVIDER_MENTION = re.compile(r"@(codex|coderabbitai)\b", re.IGNORECASE)
EXPECTED = {"login": "PhysShell", "id": 45852143, "type": "User"}
GOVERNOR = {"slug": "physshell-review-governor", "id": 4669438}


def capture_carrier(repo: str, pr: int, body: str, generation: int,
                    label: str) -> dict:
    if PROVIDER_MENTION.search(body):
        raise SystemExit("refused: A1c must not trigger providers")
    token = creds.generation(generation)["access_token"]

    pr_state = gh_probe.api("GET", f"/repos/{repo}/pulls/{pr}", token)
    assert pr_state["status"] == 200, pr_state
    created = gh_probe.api("POST", f"/repos/{repo}/issues/{pr}/comments",
                           token, {"body": body})
    assert created["status"] == 201, created
    readback = gh_probe.api(
        "GET", f"/repos/{repo}/issues/comments/{created['body']['id']}", token)
    assert readback["status"] == 200, readback

    comment = readback["body"]
    user = comment["user"]
    via = comment.get("performed_via_github_app") or {}
    return {
        "captured_at": readback["at"],
        "carrier_label": label,
        "credential_generation": generation,
        "generation_fingerprints": {
            k: v for k, v in creds._public(creds.generation(generation)).items()
            if k in ("access_fingerprint", "refresh_fingerprint",
                     "access_prefix_class", "refresh_prefix_class")},
        "repo": repo,
        "pr_number": pr,
        "head_sha_at_request": pr_state["body"]["head"]["sha"],
        "comment": {
            "id": comment["id"], "body": comment["body"],
            "created_at": comment["created_at"],
            "html_url": comment["html_url"],
            "user": {"login": user["login"], "id": user["id"],
                     "type": user.get("type")},
            "performed_via_github_app": ({"id": via.get("id"),
                                          "slug": via.get("slug")}
                                         if via else None),
        },
        "matches_expected_user": (user["login"] == EXPECTED["login"]
                                  and user["id"] == EXPECTED["id"]
                                  and user.get("type") == EXPECTED["type"]),
        "matches_governor_mediation": (via.get("slug") == GOVERNOR["slug"]
                                       and via.get("id") == GOVERNOR["id"]),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", required=True, type=int)
    ap.add_argument("--generation", required=True, type=int)
    ap.add_argument("--label", required=True, help="C0 | C1 | C2")
    ap.add_argument("--body", required=True)
    ap.add_argument("--out-dir", default=".captures/a1c")
    args = ap.parse_args()

    carrier = capture_carrier(args.repo, args.pr, args.body, args.generation,
                              args.label)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"carrier_{args.label}.json"
    path.write_text(json.dumps(carrier, indent=2) + "\n")
    print(json.dumps({"written": str(path),
                      "comment_id": carrier["comment"]["id"],
                      "user": carrier["comment"]["user"],
                      "performed_via_github_app":
                          carrier["comment"]["performed_via_github_app"],
                      "matches_expected_user": carrier["matches_expected_user"],
                      "matches_governor_mediation":
                          carrier["matches_governor_mediation"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
