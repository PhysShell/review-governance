"""GitHub access for A3a: the App-mediated **user** carrier for requests,
the installation token for the Governor's own check run.

Two carriers, deliberately separate — A1b/A1b-R proved provider triggers
need the user carrier, A2b proved check runs belong to the installation.
Tokens are read at call time and never recorded.
"""
import base64
import datetime
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.github.com"
CONFIG_DIR = Path(os.environ.get(
    "GOVERNOR_CONFIG_DIR", os.path.expanduser("~/.config/review-governor")))

GOVERNOR_APP_ID = 4669438
GOVERNOR_APP_SLUG = "physshell-review-governor"
EXPECTED_USER = {"login": "PhysShell", "id": 45852143, "type": "User"}
CODEX_ACTOR_ID = 199175422
CODERABBIT_ACTOR_ID = 136622811
CHECK_NAME = "ai/final-review-shadow"
# A3b is the first stage allowed to write success — and only through a
# guarded path: the caller must hand over the bundle hash the verdict was
# derived from, and that hash must appear in the published output.
ALLOWED_CONCLUSIONS = frozenset({"success", "failure", "cancelled",
                                 "action_required", "timed_out"})
FORBIDDEN_CONCLUSIONS = frozenset({"neutral", "skipped"})


def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def user_credentials() -> dict:
    return json.loads((CONFIG_DIR / "user-credentials.json").read_text())["current"]


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


def request(method: str, path: str, bearer: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "review-governor-a3a",
               "Authorization": f"Bearer {bearer}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, method=method, data=data,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
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
    status, installs = request("GET", "/app/installations", jwt)
    assert status == 200 and installs, (status, installs)
    status, minted = request(
        "POST", f"/app/installations/{installs[0]['id']}/access_tokens", jwt)
    assert status == 201, (status, minted)
    return minted["token"]


class Repo:
    def __init__(self, full_name: str):
        self.full_name = full_name
        self._install = None

    # --- carriers --------------------------------------------------------
    @property
    def user_token(self) -> str:
        return user_credentials()["access_token"]

    @property
    def install_token(self) -> str:
        if self._install is None:
            self._install = installation_token()
        return self._install

    def as_user(self, method, path, body=None):
        return request(method, path, self.user_token, body)

    def as_app(self, method, path, body=None):
        return request(method, path, self.install_token, body)

    # --- reads -----------------------------------------------------------
    def pull_request(self, number: int) -> dict:
        status, body = self.as_user("GET", f"/repos/{self.full_name}/pulls/{number}")
        assert status == 200, (status, body)
        return body

    def commits(self, number: int) -> list:
        status, body = self.as_user(
            "GET", f"/repos/{self.full_name}/pulls/{number}/commits?per_page=100")
        assert status == 200, (status, body)
        return body

    def issue_comments(self, number: int) -> list:
        status, body = self.as_user(
            "GET", f"/repos/{self.full_name}/issues/{number}/comments?per_page=100")
        assert status == 200, (status, body)
        return body

    def reviews(self, number: int) -> list:
        status, body = self.as_user(
            "GET", f"/repos/{self.full_name}/pulls/{number}/reviews?per_page=100")
        assert status == 200, (status, body)
        return body

    def review_comments(self, number: int) -> list:
        status, body = self.as_user(
            "GET", f"/repos/{self.full_name}/pulls/{number}/comments?per_page=100")
        assert status == 200, (status, body)
        return body

    def whoami(self) -> dict:
        status, body = self.as_user("GET", "/user")
        assert status == 200, (status, body)
        return body

    # --- writes ----------------------------------------------------------
    def comment_as_user(self, number: int, body_text: str) -> dict:
        status, created = self.as_user(
            "POST", f"/repos/{self.full_name}/issues/{number}/comments",
            {"body": body_text})
        assert status == 201, (status, created)
        status, readback = self.as_user(
            "GET", f"/repos/{self.full_name}/issues/comments/{created['id']}")
        assert status == 200, (status, readback)
        return readback

    def create_check(self, head_sha: str, external_id: str, output: dict) -> dict:
        assert len(head_sha) == 40
        status, body = self.as_app(
            "POST", f"/repos/{self.full_name}/check-runs",
            {"name": CHECK_NAME, "head_sha": head_sha, "status": "in_progress",
             "external_id": external_id, "started_at": utcnow(),
             "output": output})
        assert status == 201, (status, body)
        return body

    def conclude_check(self, check_run_id: int, conclusion: str, output: dict,
                       *, evidence_hash: str = None):
        if conclusion in FORBIDDEN_CONCLUSIONS:
            raise ValueError(f"conclusion {conclusion!r} can read as passing "
                             "downstream and is never written by the Governor")
        if conclusion not in ALLOWED_CONCLUSIONS:
            raise ValueError(f"conclusion {conclusion!r} is not permitted")
        if conclusion == "success":
            if not evidence_hash or len(evidence_hash) != 64:
                raise ValueError("success requires the full evidence bundle "
                                 "hash it was derived from")
            if evidence_hash not in (output.get("summary") or ""):
                raise ValueError("a published success must carry its evidence "
                                 "hash in the output")
        return self.as_app(
            "PATCH", f"/repos/{self.full_name}/check-runs/{check_run_id}",
            {"status": "completed", "conclusion": conclusion,
             "completed_at": utcnow(), "output": output})
