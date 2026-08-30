#!/usr/bin/env python3
"""A5a primary-Governor instruments: publish Governor probe checks through the full
lifecycle, and record owner-side merge attempts.

Two separations are load-bearing and both are structural, not advisory:

  * the Governor publishes Check Runs and nothing else — its write path
    allowlists `/check-runs` and raises on anything else, so no commit
    status can leave this runtime even though GitHub would now accept one;
  * merges are performed by the **owner**, never here. This module has no
    merge function at all.

The evidence object is probe-only (`EnforcementProbeEvidence-v1`) because
the thing under measurement is the ruleset, not a provider verdict — but
the check lifecycle is unchanged: durable decision, projection PENDING,
PATCH, independent exact-run GET, projection CONFIRMED.
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import time
from pathlib import Path

import auth_state
import decisions as dec

API = "https://api.github.com"
CONFIG_DIR = Path(os.environ.get(
    "GOVERNOR_CONFIG_DIR", os.path.expanduser("~/.config/review-governor")))
REPO = "PhysShell/evm-from-scratch"
GOVERNOR_APP_ID = 4669438
GOVERNOR_INSTALLATION_ID = 155393018
CONTEXT = "ai/final-review-readiness-probe"
EVIDENCE_SCHEMA = "ReadinessProbeEvidence-v1"
GOVERNOR_WRITE_ALLOWLIST = ("/check-runs",)
ALLOWED_CONCLUSIONS = frozenset({"success", "failure", "cancelled"})
FORBIDDEN_CONCLUSIONS = frozenset({"neutral", "skipped"})


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
               "User-Agent": "review-governor-a4live",
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
            return e.code, {"raw": raw[:500]}


def governor_write(method, path, bearer, body=None):
    if method != "GET" and not any(a in path for a in GOVERNOR_WRITE_ALLOWLIST):
        raise PermissionError(
            f"Governor runtime may not write to {path}; allowlist is "
            f"{GOVERNOR_WRITE_ALLOWLIST}")
    return request(method, path, bearer, body)


class InstallationMismatch(Exception):
    """The App is installed somewhere this runtime was not told about."""


def installation_token(installation_id=GOVERNOR_INSTALLATION_ID):
    """Mint against a *pinned* installation.

    This used to take `installs[0]`, which is correct exactly as long as
    there is one installation and silently wrong the moment there are two —
    the same shape of guess as the `pulls[0]` defect A5a-c2 removed from the
    fast path. Pinning it means a second installation is an error someone
    reads, not a target the Governor drifts onto.
    """
    jwt = app_jwt()
    status, installs = request("GET", "/app/installations", jwt)
    assert status == 200 and installs, (status, installs)
    ids = [i["id"] for i in installs]
    if installation_id not in ids:
        raise InstallationMismatch(
            f"expected installation {installation_id}, GitHub reports {ids}")
    status, minted = request(
        "POST", f"/app/installations/{installation_id}/access_tokens", jwt)
    assert status == 201, (status, minted)
    return minted["token"]


def probe_evidence(head_sha, verdict):
    payload = {"schema": EVIDENCE_SCHEMA, "head_sha": head_sha,
               "verdict": verdict, "purpose": "a4-live enforcement fixture"}
    payload["fixture_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return payload


def output_for(evidence, verdict):
    summary = "\n".join([
        f"Governor verdict: {verdict}",
        f"Head: {evidence['head_sha']}",
        f"Evidence: {evidence['schema']} {evidence['fixture_hash']}",
        "",
        "A4 enforcement fixture.",
        "Not a provider review verdict.",
        "Not production evidence.",
    ])
    return {"title": f"Governor: {verdict}", "summary": summary}


def authorization_row(auth_db, conclusion):
    """A passing conclusion requires live user authorization; revoking never
    does.

    The asymmetry is the safety model in one function. Publishing a success
    asserts that somebody was in a position to watch the providers' mutable
    evidence, and that is exactly what a lost authorization removes. Refusing
    to *revoke* while unauthorized, on the other hand, would strand green
    checks precisely when nobody is watching them.

    Read from the authoritative store, never from `auth-state.json`: the
    mirror is for alerting a human, and a file anything on the host can edit
    must not decide whether the gate opens.
    """
    store = auth_state.AuthStore(auth_db)
    try:
        row = store.current()
        if conclusion in ("success", "neutral", "skipped"):
            sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                                   / "steady-state" / "harness"))
            import auth_policy
            auth_state.require_triggers_permitted(
                store, permission=auth_policy.evaluate(store))
        return dict(row) if row else None
    finally:
        store.close()


def publish(args):
    """Full lifecycle: durable decision -> PENDING -> PATCH -> exact GET ->
    CONFIRMED."""
    if args.conclusion in FORBIDDEN_CONCLUSIONS:
        raise SystemExit(f"{args.conclusion} can read as passing downstream")
    if args.conclusion not in ALLOWED_CONCLUSIONS:
        raise SystemExit(f"conclusion {args.conclusion} not permitted")
    auth_row = authorization_row(args.auth_db, args.conclusion)
    token = installation_token()
    history = dec.History(args.db)
    try:
        verdict = args.verdict
        evidence = probe_evidence(args.head, verdict)
        if args.conclusion == "success" and not evidence["fixture_hash"]:
            raise SystemExit("success requires a fixture hash")
        epoch_id = f"a5a-{args.head[:12]}"

        decision_id = history.record(
            epoch_id=epoch_id, head_sha=args.head, verdict=verdict,
            bundle_hash=evidence["fixture_hash"], bundle_schema=EVIDENCE_SCHEMA,
            decision_rule_revision="a5a.1",
            auth_generation=(auth_row or {}).get("auth_generation", 0),
            decided_at=utcnow(), cause=args.cause)

        run_id = args.check_run_id
        if not run_id:
            status, created = governor_write(
                "POST", f"/repos/{REPO}/check-runs", token,
                {"name": CONTEXT, "head_sha": args.head, "status": "in_progress",
                 "external_id": epoch_id, "started_at": utcnow(),
                 "output": output_for(evidence, verdict)})
            assert status == 201, (status, created)
            run_id = created["id"]

        history.project_pending(epoch_id, args.head, run_id, args.conclusion,
                                decision_id, utcnow())
        status, patched = governor_write(
            "PATCH", f"/repos/{REPO}/check-runs/{run_id}", token,
            {"status": "completed", "conclusion": args.conclusion,
             "completed_at": utcnow(), "output": output_for(evidence, verdict)})
        read_status, readback = governor_write(
            "GET", f"/repos/{REPO}/check-runs/{run_id}", token)
        observed = (readback or {}).get("conclusion")
        settled = ("CONFIRMED" if read_status == 200 and observed == args.conclusion
                   else "OUTCOME_UNKNOWN" if read_status != 200 else "FAILED")
        history.settle_projection(epoch_id, state=settled,
                                  observed_conclusion=observed, at=utcnow())
        app = (readback or {}).get("app") or {}
        return {"decision_id": decision_id, "check_run_id": run_id,
                "auth_state": (auth_row or {}).get("state", "NEVER_OBSERVED"),
                "auth_generation": (auth_row or {}).get("auth_generation"),
                "verdict": verdict, "intended": args.conclusion,
                "observed": observed, "projection_state": settled,
                "head_sha": (readback or {}).get("head_sha"),
                "app": {"id": app.get("id"), "slug": app.get("slug")},
                "fixture_hash": evidence["fixture_hash"],
                "hash_in_output": evidence["fixture_hash"] in
                                  ((readback or {}).get("output") or {}).get("summary", ""),
                "patch_status": status}
    finally:
        history.close()


def heartbeat(args):
    """The primary's liveness signal. Deliberately trivial: a durable file
    with a timestamp, so the watchdog needs no coupling to the primary
    beyond reading it."""
    path = Path(args.heartbeat_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    beat = {"runtime": "primary-governor", "at": utcnow(),
            "monotonic": time.monotonic()}
    path.write_text(json.dumps(beat, indent=2) + "\n")
    return beat


def state(args):
    history = dec.History(args.db)
    try:
        return {"chain": history.as_json(),
                "projections": [dict(p) for p in history.projections()]}
    finally:
        history.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["publish", "state", "heartbeat"])
    ap.add_argument("--head")
    ap.add_argument("--conclusion", default="failure")
    ap.add_argument("--verdict", default="NOT_ESTABLISHED")
    ap.add_argument("--cause", default=None)
    ap.add_argument("--check-run-id", type=int, default=None)
    ap.add_argument("--db", default=".captures/a5a/decisions.sqlite3")
    ap.add_argument("--auth-db", default=str(CONFIG_DIR / "auth.sqlite3"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--heartbeat-file",
                    default=os.path.expanduser("~/.config/review-governor/heartbeat.json"))
    args = ap.parse_args()
    result = {"publish": publish, "state": state,
              "heartbeat": heartbeat}[args.command](args)
    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
