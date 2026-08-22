"""Independent installation-identity probe for A1c.

Self-contained (the A1b tree is frozen and lives on another branch): App
private key → RS256 JWT → installation access token → REST. Used only to
show that installation-side coordination is unaffected by whatever happens
to the *user* authorization. Tokens are minted in-process and never
recorded.
"""
import base64
import json
import subprocess
import time

import creds
import gh_probe


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def app_jwt() -> str:
    public = json.loads((creds.CONFIG_DIR / "app-public.json").read_text())
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"},
                                separators=(",", ":")).encode())
    payload = _b64url(json.dumps({"iat": now - 60, "exp": now + 540,
                                  "iss": str(public["app_id"])},
                                 separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    signature = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", str(public["pem_path"])],
        input=signing_input, capture_output=True, check=True).stdout
    return f"{signing_input.decode()}.{_b64url(signature)}"


def probe(repo: str, pr: int) -> dict:
    jwt = app_jwt()
    installs = gh_probe.api("GET", "/app/installations", jwt)
    installation_id = (installs["body"] or [{}])[0].get("id") \
        if installs["status"] == 200 else None
    minted = (gh_probe.api("POST",
                           f"/app/installations/{installation_id}/access_tokens",
                           jwt) if installation_id else None)
    token = (minted["body"] or {}).get("token") if minted and \
        minted["status"] == 201 else None
    pr_probe = gh_probe.api("GET", f"/repos/{repo}/pulls/{pr}", token) \
        if token else None
    return {
        "at": gh_probe.utcnow(),
        "installations_status": installs["status"],
        "installation_id": installation_id,
        "token_minted": bool(token),
        "pr_probe_status": (pr_probe or {}).get("status"),
        "pr_number": pr,
        "pr_head_sha": ((pr_probe or {}).get("body") or {}).get("head", {})
                       .get("sha"),
        "usable": bool(token) and (pr_probe or {}).get("status") == 200,
    }
