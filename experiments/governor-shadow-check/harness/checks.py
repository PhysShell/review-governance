"""Checks API client for A2b, using the Governor **installation** token.

Provenance is verified on every read: a run counts as Governor-owned only
when `app.id` matches. A matching display name means nothing — anyone can
name a check `ai/final-review-shadow`.
"""
import base64
import datetime
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
GOVERNOR_APP_ID = 4669438
GOVERNOR_APP_SLUG = "physshell-review-governor"
CHECK_NAME = "ai/final-review-shadow"

# conclusions this experiment is allowed to write; success/neutral/skipped
# are deliberately absent (neutral and skipped can read as passing
# downstream, which would poison a future fail-closed gate)
ALLOWED_CONCLUSIONS = frozenset({"failure", "cancelled", "action_required",
                                 "timed_out"})


def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def app_jwt() -> str:
    public = json.loads((CONFIG_DIR / "app-public.json").read_text())
    now = int(time.time())
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"},
                             separators=(",", ":")).encode())
    payload = _b64(json.dumps({"iat": now - 60, "exp": now + 540,
                               "iss": str(public["app_id"])},
                              separators=(",", ":")).encode())
    signing = f"{header}.{payload}".encode()
    signature = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", str(public["pem_path"])],
        input=signing, capture_output=True, check=True).stdout
    return f"{signing.decode()}.{_b64(signature)}"


def _request(method, path, bearer, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "review-governor-a2b",
               "Authorization": f"Bearer {bearer}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, method=method, data=data,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw[:400]}


def installation_token() -> str:
    jwt = app_jwt()
    status, installs = _request("GET", "/app/installations", jwt)
    assert status == 200 and installs, (status, installs)
    status, minted = _request(
        "POST", f"/app/installations/{installs[0]['id']}/access_tokens", jwt)
    assert status == 201, (status, minted)
    return minted["token"]


class Checks:
    def __init__(self, repo: str):
        self.repo = repo
        self._token = None

    @property
    def token(self) -> str:
        if self._token is None:
            self._token = installation_token()
        return self._token

    def api(self, method, path, body=None):
        return _request(method, path, self.token, body)

    # --- pull request truth ---------------------------------------------
    def pull_request(self, number: int) -> dict:
        status, body = self.api("GET", f"/repos/{self.repo}/pulls/{number}")
        assert status == 200, (status, body)
        return body

    def repository_id(self) -> int:
        status, body = self.api("GET", f"/repos/{self.repo}")
        assert status == 200, (status, body)
        return body["id"]

    # --- check runs ------------------------------------------------------
    def create(self, head_sha: str, external_id: str, output: dict,
               status: str = "in_progress") -> dict:
        assert len(head_sha) == 40, "check runs bind to the full head SHA"
        payload = {"name": CHECK_NAME, "head_sha": head_sha,
                   "status": status, "external_id": external_id,
                   "started_at": utcnow(), "output": output}
        code, body = self.api("POST", f"/repos/{self.repo}/check-runs", payload)
        assert code == 201, (code, body)
        return body

    def conclude(self, check_run_id: int, conclusion: str, output: dict) -> dict:
        if conclusion not in ALLOWED_CONCLUSIONS:
            raise ValueError(
                f"conclusion {conclusion!r} is not permitted in A2b "
                f"(allowed: {sorted(ALLOWED_CONCLUSIONS)})")
        code, body = self.api(
            "PATCH", f"/repos/{self.repo}/check-runs/{check_run_id}",
            {"status": "completed", "conclusion": conclusion,
             "completed_at": utcnow(), "output": output})
        return code, body

    def get(self, check_run_id: int):
        return self.api("GET", f"/repos/{self.repo}/check-runs/{check_run_id}")

    def for_ref(self, ref: str, name: str = CHECK_NAME) -> list:
        code, body = self.api(
            "GET", f"/repos/{self.repo}/commits/{ref}/check-runs"
                   f"?check_name={urllib.parse.quote(name)}&per_page=100")
        assert code == 200, (code, body)
        return (body or {}).get("check_runs", [])


def is_governor_owned(check_run: dict) -> bool:
    """Provenance: App identity, never the display name."""
    app = check_run.get("app") or {}
    return app.get("id") == GOVERNOR_APP_ID and app.get("slug") == GOVERNOR_APP_SLUG


def matches_epoch(check_run: dict, external_id: str, head_sha: str) -> bool:
    """Everything must line up before a run may be adopted as ours."""
    return (is_governor_owned(check_run)
            and check_run.get("external_id") == external_id
            and check_run.get("head_sha") == head_sha
            and check_run.get("name") == CHECK_NAME)


def slim(check_run: dict) -> dict:
    app = check_run.get("app") or {}
    return {"id": check_run.get("id"), "name": check_run.get("name"),
            "head_sha": check_run.get("head_sha"),
            "status": check_run.get("status"),
            "conclusion": check_run.get("conclusion"),
            "external_id": check_run.get("external_id"),
            "started_at": check_run.get("started_at"),
            "completed_at": check_run.get("completed_at"),
            "app": {"id": app.get("id"), "slug": app.get("slug")},
            "output_title": (check_run.get("output") or {}).get("title")}
