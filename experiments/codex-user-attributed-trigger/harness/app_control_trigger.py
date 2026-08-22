#!/usr/bin/env python3
"""Matched identity control for A1b (amendment A1b-c1).

Posts one command as the **App installation identity** — the A1 carrier —
on the same PR, same frozen HEAD, same repository state as the primary
user-attributed request, so that requester identity can be separated from
the repository's Codex configuration.

Self-contained on purpose: the A1 experiment tree stays frozen and is not
imported. This is an observation instrument; its result never becomes the
primary A1b result.
"""
import argparse
import base64
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
CONFIG_DIR = Path(os.environ.get(
    "GOVERNOR_CONFIG_DIR", os.path.expanduser("~/.config/review-governor")))
SAFE_HEADERS = ("date", "x-github-request-id", "x-ratelimit-remaining")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def app_jwt(app_id, pem_path) -> str:
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"},
                                separators=(",", ":")).encode())
    payload = _b64url(json.dumps({"iat": now - 60, "exp": now + 540,
                                  "iss": str(app_id)},
                                 separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    signature = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", str(pem_path)],
        input=signing_input, capture_output=True, check=True).stdout
    return f"{signing_input.decode()}.{_b64url(signature)}"


def request(method, path, bearer, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "review-governor-a1b-control",
               "Authorization": f"Bearer {bearer}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, method=method, data=data,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            meta = {k: resp.headers.get(k) for k in SAFE_HEADERS
                    if resp.headers.get(k)}
            raw = resp.read().decode()
            return resp.status, meta, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        meta = {k: e.headers.get(k) for k in SAFE_HEADERS if e.headers.get(k)}
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = {"raw": raw[:500]}
        return e.code, meta, parsed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", required=True, type=int)
    ap.add_argument("--body", required=True)
    ap.add_argument("--label", default="codex-app-control")
    ap.add_argument("--out-dir", default=".captures/a1b")
    args = ap.parse_args()

    public = json.loads((CONFIG_DIR / "app-public.json").read_text())
    jwt = app_jwt(public["app_id"], public["pem_path"])
    status, _, installs = request("GET", "/app/installations", jwt)
    assert status == 200 and installs, (status, installs)
    installation_id = installs[0]["id"]
    status, _, minted = request(
        "POST", f"/app/installations/{installation_id}/access_tokens", jwt)
    assert status == 201, (status, minted)
    token = minted["token"]

    status, _, pr = request("GET", f"/repos/{args.repo}/pulls/{args.pr}", token)
    assert status == 200, (status, pr)
    status, meta, created = request(
        "POST", f"/repos/{args.repo}/issues/{args.pr}/comments", token,
        {"body": args.body})
    if status != 201:
        print(f"FATAL: control comment failed: {status} {created}", file=sys.stderr)
        return 1
    status, _, readback = request(
        "GET", f"/repos/{args.repo}/issues/comments/{created['id']}", token)
    assert status == 200, (status, readback)

    author = readback["user"]
    if author.get("type") != "Bot":
        print(f"FATAL: control not authored by the App bot: {author}",
              file=sys.stderr)
        return 1

    via = readback.get("performed_via_github_app")
    envelope = {
        "captured_at": datetime.datetime.now(datetime.timezone.utc)
                       .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "purpose": args.label,
        "role": "matched identity control per amendment A1b-c1 — never the "
                "primary result",
        "auth_model": "github_app_installation_token",
        "repo": args.repo,
        "pr_number": args.pr,
        "pr_at_request": {"number": pr["number"], "state": pr["state"],
                          "draft": pr["draft"], "head_sha": pr["head"]["sha"]},
        "request_comment": {
            "id": readback["id"], "body": readback["body"],
            "created_at": readback["created_at"],
            "html_url": readback["html_url"],
            "user": {"login": author["login"], "id": author["id"],
                     "type": author.get("type")},
            "performed_via_github_app": (
                {"id": via.get("id"), "slug": via.get("slug")} if via else None),
        },
        "response_headers": meta,
    }
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"app_control_request_{args.label}.json"
    path.write_text(json.dumps(envelope, indent=2) + "\n")
    print(json.dumps({
        "written": str(path),
        "comment_id": envelope["request_comment"]["id"],
        "author": envelope["request_comment"]["user"],
        "created_at": envelope["request_comment"]["created_at"],
        "head_sha_at_request": envelope["pr_at_request"]["head_sha"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
