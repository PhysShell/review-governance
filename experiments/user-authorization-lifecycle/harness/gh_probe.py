"""Credential probes for A1c: what does GitHub say about a given token?

Every probe records the actual HTTP status and a sanitized structured body.
Tokens are passed in from the store and never recorded — only the
generation fingerprints, which live in the evidence files.
"""
import datetime
import json
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
TOKEN_URL = "https://github.com/login/oauth/access_token"
SAFE_HEADERS = ("date", "x-github-request-id", "x-ratelimit-remaining",
                "x-accepted-github-permissions")


def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def api(method: str, path: str, bearer: str, body=None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "review-governor-a1c-probe",
               "Authorization": f"Bearer {bearer}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, method=method, data=data,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return {"at": utcnow(), "status": resp.status,
                    "headers": {k: resp.headers.get(k) for k in SAFE_HEADERS
                                if resp.headers.get(k)},
                    "body": json.loads(raw) if raw else None}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"raw": raw[:400]}
        return {"at": utcnow(), "status": e.code,
                "headers": {k: e.headers.get(k) for k in SAFE_HEADERS
                            if e.headers.get(k)},
                "body": body}


def probe_access(access_token: str) -> dict:
    """GET /user — sanitized: identity fields only, never the token."""
    result = api("GET", "/user", access_token)
    body = result.get("body") or {}
    return {
        "at": result["at"], "status": result["status"],
        "ok": result["status"] == 200,
        "identity": ({"login": body.get("login"), "id": body.get("id"),
                      "type": body.get("type")} if result["status"] == 200
                     else None),
        "error": (None if result["status"] == 200
                  else {"message": body.get("message"),
                        "documentation_url": body.get("documentation_url")}),
    }


def refresh_exchange(client_id: str, refresh_token: str) -> dict:
    """POST the refresh grant. Returns (sanitized_result, raw_payload).

    The raw payload contains the new tokens and is handed straight to the
    credential store; it is never written to evidence.
    """
    req = urllib.request.Request(
        TOKEN_URL, method="POST",
        data=urllib.parse.urlencode({
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }).encode(),
        headers={"Accept": "application/json",
                 "User-Agent": "review-governor-a1c-refresh"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"error": f"http_{e.code}", "error_description": raw[:300]}
        status = e.code
    sanitized = {
        "at": utcnow(),
        "http_status": status,
        "granted": "access_token" in payload,
        "error": payload.get("error"),
        "error_description": payload.get("error_description"),
        "token_type": payload.get("token_type"),
        "expires_in": payload.get("expires_in"),
        "refresh_token_expires_in": payload.get("refresh_token_expires_in"),
        "access_prefix_class": (payload.get("access_token") or "")[:4] or None,
        "refresh_prefix_class": (payload.get("refresh_token") or "")[:4] or None,
    }
    return sanitized, payload
