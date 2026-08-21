"""Installation-auth transport for the Governor App.

    App private key -> GitHub App JWT (RS256 via openssl) ->
    installation access token -> GitHub REST

Tokens are minted in-process and never printed or written anywhere.
Response headers are reduced to a sanitized subset before they can reach
any capture file.
"""
import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
CONFIG_DIR = Path(os.environ.get(
    "GOVERNOR_CONFIG_DIR", os.path.expanduser("~/.config/review-governor")))
SAFE_RESPONSE_HEADERS = ("date", "x-github-request-id", "x-ratelimit-remaining")


def load_public() -> dict:
    return json.loads((CONFIG_DIR / "app-public.json").read_text())


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def app_jwt() -> str:
    public = load_public()
    now = int(time.time())
    header = _b64url(json.dumps(
        {"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps(
        {"iat": now - 60, "exp": now + 540, "iss": str(public["app_id"])},
        separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    signature = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", str(public["pem_path"])],
        input=signing_input, capture_output=True, check=True).stdout
    return f"{signing_input.decode()}.{_b64url(signature)}"


def request(method: str, path: str, *, bearer: str, body=None):
    """Returns (status, sanitized_headers, parsed_json_or_None)."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "review-governor-harness",
        "Authorization": f"Bearer {bearer}",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            meta = {k: resp.headers.get(k) for k in SAFE_RESPONSE_HEADERS
                    if resp.headers.get(k)}
            raw = resp.read().decode()
            return resp.status, meta, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        meta = {k: e.headers.get(k) for k in SAFE_RESPONSE_HEADERS if e.headers.get(k)}
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = {"raw": raw[:500]}
        return e.code, meta, parsed


def installation_token(installation_id) -> str:
    status, _, body = request(
        "POST", f"/app/installations/{installation_id}/access_tokens",
        bearer=app_jwt())
    if status != 201:
        raise RuntimeError(f"installation token mint failed: HTTP {status}: {body}")
    return body["token"]
