"""User-to-server transport: GitHub App **user access token** -> GitHub REST.

Distinct trust model from A1's installation token: requests act on behalf of
the authorizing user and are bounded by App permissions ∩ user permissions.
The token is read from 0600 storage at call time, never printed, never
returned, and never written into captures. Response headers are reduced to
a sanitized subset before anything can reach a capture file.
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
CONFIG_DIR = Path(os.environ.get(
    "GOVERNOR_CONFIG_DIR", os.path.expanduser("~/.config/review-governor")))
TOKEN_PATH = CONFIG_DIR / "user-token.json"
SAFE_RESPONSE_HEADERS = ("date", "x-github-request-id", "x-ratelimit-remaining",
                         "x-oauth-scopes", "x-accepted-oauth-scopes")


def _token() -> str:
    return json.loads(TOKEN_PATH.read_text())["access_token"]


def token_metadata() -> dict:
    """Non-secret descriptor of the stored token, safe for evidence."""
    stored = json.loads(TOKEN_PATH.read_text())
    token = stored["access_token"]
    prefix = next((p for p in ("ghu_", "ghs_", "gho_", "ghp_", "github_pat_")
                   if token.startswith(p)), "unknown")
    return {
        "token_prefix": prefix,
        "token_type": stored.get("token_type"),
        "expires_in_at_issue": stored.get("expires_in"),
        "refresh_token_issued": bool(stored.get("refresh_token")),
        "refresh_token_used_in_a1b": False,
        "obtained_via": stored.get("obtained_via"),
        "obtained_at": stored.get("obtained_at"),
        "scope_field": stored.get("scope"),
    }


def request(method: str, path: str, *, body=None, bearer: str = None):
    """Returns (status, sanitized_headers, parsed_json_or_None)."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "review-governor-a1b-harness",
        "Authorization": f"Bearer {bearer or _token()}",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, method=method, data=data,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            meta = {k: resp.headers.get(k) for k in SAFE_RESPONSE_HEADERS
                    if resp.headers.get(k)}
            raw = resp.read().decode()
            return resp.status, meta, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        meta = {k: e.headers.get(k) for k in SAFE_RESPONSE_HEADERS
                if e.headers.get(k)}
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = {"raw": raw[:500]}
        return e.code, meta, parsed
