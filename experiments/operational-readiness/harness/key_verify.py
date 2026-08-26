#!/usr/bin/env python3
"""Prove a GitHub App private key, from the host that will use it.

Every line of the result is a readback. "The key was deployed" is not a
fact about authentication; only GitHub accepting a JWT signed by it is, and
only a minted installation token proves the installation is reachable.

The PEM never leaves this process. What comes out is a fingerprint — the
SHA-256 of the DER public key, truncated — which is enough to say *which*
key answered and useless to anyone who steals the evidence file.

Used three times in A5b-preflight: to qualify K_primary and K_edge before
K0 is deleted, and afterwards to prove K0 is refused while both still work.
"""
import argparse
import base64
import datetime
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
EXPECTED_APP_ID = 4669438
EXPECTED_INSTALLATION_ID = 155393018
EXPECTED_REPO = "PhysShell/evm-from-scratch"


def fingerprint(pem_path) -> str:
    """SHA-256 over the DER public key. Same key -> same value on any host,
    which is what makes "the runtime is using K_edge" checkable at all."""
    der = subprocess.run(
        ["openssl", "rsa", "-in", str(pem_path), "-pubout", "-outform", "DER"],
        capture_output=True, check=True).stdout
    return hashlib.sha256(der).hexdigest()[:16]


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def app_jwt(pem_path, app_id=EXPECTED_APP_ID) -> str:
    now = int(time.time())
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"},
                               separators=(",", ":")).encode())
    payload = b64url(json.dumps({"iat": now - 60, "exp": now + 540,
                                 "iss": str(app_id)},
                                separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    signature = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", str(pem_path)],
        input=signing_input, capture_output=True, check=True).stdout
    return f"{header}.{payload}.{b64url(signature)}"


def request(method, path, token, bearer=True):
    req = urllib.request.Request(f"{API}{path}", method=method, headers={
        "Authorization": f"Bearer {token}" if bearer else f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "governor-key-verify",
        "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode() or "null")
        except ValueError:
            body = None
        return e.code, body
    except Exception as exc:
        return None, {"error": f"{type(exc).__name__}: {exc}"}


def verify(pem_path, *, app_id=EXPECTED_APP_ID,
           installation_id=EXPECTED_INSTALLATION_ID, repo=EXPECTED_REPO,
           expect_success=True):
    """Returns a verdict per step. Nothing is inferred from a previous step:
    a mint that succeeds does not prove the repository is reachable."""
    result = {"checked_at": datetime.datetime.now(datetime.timezone.utc)
                            .strftime("%Y-%m-%dT%H:%M:%SZ"),
              "pem_path": str(pem_path),
              "expectation": "ACCEPTED" if expect_success else "REJECTED",
              "steps": {}}
    try:
        result["fingerprint"] = fingerprint(pem_path)
    except subprocess.CalledProcessError:
        result["fingerprint"] = None
        result["steps"]["readable_private_key"] = "FAIL"
        result["verdict"] = "FAIL"
        return result
    result["steps"]["readable_private_key"] = "PASS"

    jwt = app_jwt(pem_path, app_id)
    status, app = request("GET", "/app", jwt)
    result["steps"]["jwt_accepted"] = "PASS" if status == 200 else "FAIL"
    result["app_http_status"] = status
    if status != 200:
        # The interesting case for a revoked key. Record why, not the body.
        result["auth_error"] = (app or {}).get("message")
        result["verdict"] = "REJECTED" if not expect_success else "FAIL"
        result["matches_expectation"] = not expect_success
        return result

    result["steps"]["app_id_matches"] = "PASS" if app.get("id") == app_id else "FAIL"
    result["observed_app_id"] = app.get("id")
    result["observed_app_slug"] = app.get("slug")

    status, installs = request("GET", "/app/installations", jwt)
    ids = [i["id"] for i in (installs or [])] if status == 200 else []
    result["observed_installation_ids"] = ids
    result["steps"]["expected_installation_present"] = \
        "PASS" if installation_id in ids else "FAIL"

    status, minted = request(
        "POST", f"/app/installations/{installation_id}/access_tokens", jwt)
    result["steps"]["installation_token_minted"] = \
        "PASS" if status == 201 else "FAIL"
    result["mint_http_status"] = status
    if status != 201:
        result["verdict"] = "FAIL"
        result["matches_expectation"] = not expect_success
        return result

    token = minted["token"]
    status, repo_body = request("GET", f"/repos/{repo}", token, bearer=False)
    result["steps"]["expected_repository_accessible"] = \
        "PASS" if status == 200 else "FAIL"
    result["observed_repository"] = (repo_body or {}).get("full_name")

    result["verdict"] = "PASS" if all(
        v == "PASS" for v in result["steps"].values()) else "FAIL"
    result["matches_expectation"] = (result["verdict"] == "PASS") == expect_success
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pem", required=True)
    ap.add_argument("--app-id", type=int, default=EXPECTED_APP_ID)
    ap.add_argument("--installation-id", type=int,
                    default=EXPECTED_INSTALLATION_ID)
    ap.add_argument("--repo", default=EXPECTED_REPO)
    ap.add_argument("--expect-rejected", action="store_true",
                    help="invert the expectation: used to prove the revoked "
                         "shared key no longer authenticates")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    result = verify(args.pem, app_id=args.app_id,
                    installation_id=args.installation_id, repo=args.repo,
                    expect_success=not args.expect_rejected)
    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0 if result.get("matches_expectation") else 1


if __name__ == "__main__":
    sys.exit(main())
