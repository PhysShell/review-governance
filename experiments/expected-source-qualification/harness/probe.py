#!/usr/bin/env python3
"""A4a-1 instruments: publish the Governor probe check, then attempt to bind
it as an expected source under the App's current permissions.

Two credentials, deliberately separated:

  * the **Governor installation token** publishes the Check Run — and only
    ever touches the Checks API. There is no Commit Status call anywhere in
    this file, now or later.
  * the **owner's** token administers the ruleset. The Governor has no
    `administration` permission and must never acquire one: creating a rule
    is an owner act, satisfying it is the Governor's.
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
REPO = "PhysShell/evm-from-scratch"
GOVERNOR_APP_ID = 4669438
PROBE_CONTEXT = "ai/final-review-expected-source-probe"
TARGET_REF = "refs/heads/governor/a4a-expected-source-target"

# endpoints the Governor is allowed to write to; the Commit Status API is
# absent on purpose and its absence is asserted by test
GOVERNOR_WRITE_ALLOWLIST = ("/check-runs",)


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def app_jwt():
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


def request(method, path, bearer, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "review-governor-a4a",
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
            return e.code, {"raw": raw[:600]}


def governor_write(method, path, bearer, body=None):
    """Every Governor write goes through here, and the allowlist is the
    architectural boundary — not a comment expressing good intentions."""
    if method != "GET" and not any(path.endswith(a) or a in path
                                   for a in GOVERNOR_WRITE_ALLOWLIST):
        raise PermissionError(
            f"Governor runtime may not write to {path}; allowlist is "
            f"{GOVERNOR_WRITE_ALLOWLIST}")
    return request(method, path, bearer, body)


def installation_token():
    jwt = app_jwt()
    status, installs = request("GET", "/app/installations", jwt)
    assert status == 200 and installs, (status, installs)
    status, minted = request(
        "POST", f"/app/installations/{installs[0]['id']}/access_tokens", jwt)
    assert status == 201, (status, minted)
    return minted["token"], installs[0]


def owner_token():
    """The owner's gh credential, used only for ruleset administration."""
    result = subprocess.run(["gh", "auth", "token"], capture_output=True,
                            text=True, check=True)
    return result.stdout.strip()


def cmd_publish_check(args):
    token, install = installation_token()
    status, ref = request("GET", f"/repos/{REPO}/git/ref/"
                                 f"{TARGET_REF[5:]}", token)
    assert status == 200, (status, ref)
    head = ref["object"]["sha"]

    summary = "\n".join([
        "Governor verdict: NOT_ESTABLISHED",
        f"Head: {head}",
        "Context: expected-source qualification probe (A4a-1)",
        "",
        "No provider evidence exists for this ref; this run exists only to "
        "satisfy the documented prerequisite that the App has recently sent "
        "a check run. Published via the Checks API only — the Governor makes "
        "no Commit Status API call.",
    ])
    status, created = governor_write(
        "POST", f"/repos/{REPO}/check-runs", token,
        {"name": PROBE_CONTEXT, "head_sha": head, "status": "in_progress",
         "external_id": "a4a-1", "started_at": utcnow(),
         "output": {"title": "Governor: NOT_ESTABLISHED", "summary": summary}})
    assert status == 201, (status, created)
    status, completed = governor_write(
        "PATCH", f"/repos/{REPO}/check-runs/{created['id']}", token,
        {"status": "completed", "conclusion": "failure",
         "completed_at": utcnow(),
         "output": {"title": "Governor: NOT_ESTABLISHED", "summary": summary}})
    app = (completed or {}).get("app") or {}
    return {"captured_at": utcnow(), "head_sha": head,
            "check_run_id": completed.get("id"), "name": completed.get("name"),
            "conclusion": completed.get("conclusion"),
            "app": {"id": app.get("id"), "slug": app.get("slug")},
            "installation_permissions": install.get("permissions"),
            "statuses_permission_present": "statuses" in install.get("permissions", {}),
            "http_status": status}


def ruleset_payload(with_integration_id):
    check = {"context": PROBE_CONTEXT}
    if with_integration_id:
        check["integration_id"] = GOVERNOR_APP_ID
    return {
        "name": "governor-a4a-expected-source-probe",
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": [TARGET_REF], "exclude": []}},
        "rules": [{"type": "required_status_checks",
                   "parameters": {"required_status_checks": [check],
                                  "strict_required_status_checks_policy": False}}],
    }


def cmd_create_ruleset(args):
    token = owner_token()
    status, created = request("POST", f"/repos/{REPO}/rulesets", token,
                              ruleset_payload(with_integration_id=False))
    result = {"captured_at": utcnow(), "http_status": status,
              "ruleset_id": (created or {}).get("id"),
              "name": (created or {}).get("name"),
              "enforcement": (created or {}).get("enforcement"),
              "conditions": (created or {}).get("conditions"),
              "rules": (created or {}).get("rules"),
              "error": None if status in (201, 200) else created}
    return result


def cmd_readback(args):
    """Isolation evidence.

    The documented `/rules/branch/{branch}` endpoint answers 404 on this
    account for every ref, including `main`, so it is recorded as
    unavailable rather than read as "no rules apply". Isolation is therefore
    established from what does answer: the ruleset's own scope, the complete
    ruleset list for the repository, and the continued absence of branch
    protection on `main`.
    """
    token = owner_token()
    status, ruleset = request("GET", f"/repos/{REPO}/rulesets/{args.ruleset_id}",
                              token)
    all_status, all_rulesets = request("GET", f"/repos/{REPO}/rulesets", token)
    probe_status, probe_body = request("GET", f"/repos/{REPO}/rules/branch/main",
                                       token)
    prot_status, prot_body = request("GET", f"/repos/{REPO}/branches/main/protection",
                                     token)
    include = (((ruleset or {}).get("conditions") or {}).get("ref_name") or {}) \
        .get("include", [])
    every_scope = [(((r or {}).get("conditions") or {}).get("ref_name") or {})
                   .get("include") for r in (all_rulesets or [])
                   if isinstance(r, dict)]
    return {
        "captured_at": utcnow(),
        "ruleset": {"id": (ruleset or {}).get("id"),
                    "enforcement": (ruleset or {}).get("enforcement"),
                    "conditions": (ruleset or {}).get("conditions"),
                    "rules": (ruleset or {}).get("rules")},
        "scope_is_exactly_the_target_ref": include == [TARGET_REF],
        "rulesets_in_repository": [{"id": r.get("id"), "name": r.get("name"),
                                    "target": r.get("target"),
                                    "enforcement": r.get("enforcement")}
                                   for r in (all_rulesets or [])
                                   if isinstance(r, dict)],
        "ruleset_count": len(all_rulesets or []),
        "every_ruleset_scope": every_scope,
        "main_appears_in_no_ruleset_scope": all(
            scope == [TARGET_REF] for scope in every_scope if scope is not None),
        "main_branch_protection_status": prot_status,
        "main_branch_protection_message": (prot_body or {}).get("message"),
        "rules_branch_endpoint_status": probe_status,
        "rules_branch_endpoint_note": "documented endpoint answers 404 on this "
                                      "account for every ref; not used as "
                                      "evidence",
        "http": {"ruleset": status, "rulesets": all_status},
    }


def cmd_attempt_expected_source(args):
    """The single preregistered attempt: bind integration_id while the App
    still has no statuses permission."""
    token = owner_token()
    _, install = installation_token()
    before_permissions = install.get("permissions")
    payload = ruleset_payload(with_integration_id=True)
    attempted_at = utcnow()
    status, response = request("PUT", f"/repos/{REPO}/rulesets/{args.ruleset_id}",
                               token, payload)
    after_status, after = request("GET", f"/repos/{REPO}/rulesets/{args.ruleset_id}",
                                  token)
    applied = json.dumps(after.get("rules") if after else None)
    return {
        "attempted_at": attempted_at,
        "app_permissions_at_attempt": before_permissions,
        "statuses_permission_present": "statuses" in (before_permissions or {}),
        "request_payload": payload,
        "http_status": status,
        "response": response,
        "readback_http_status": after_status,
        "readback_rules": after.get("rules") if after else None,
        "integration_id_present_after": f'"integration_id": {GOVERNOR_APP_ID}'
                                        in applied or f'"integration_id":{GOVERNOR_APP_ID}'
                                        in applied,
        "verdict": None,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["publish-check", "create-ruleset",
                                        "readback", "attempt-expected-source"])
    ap.add_argument("--ruleset-id", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    result = {"publish-check": cmd_publish_check,
              "create-ruleset": cmd_create_ruleset,
              "readback": cmd_readback,
              "attempt-expected-source": cmd_attempt_expected_source}[args.command](args)
    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
