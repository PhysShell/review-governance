#!/usr/bin/env python3
"""Observe the real user-authorization lifecycle and record what it did.

This is the missing wire A5b-preflight found: the sentinel understood
`AUTH_LOST` and `REFRESH_OUTCOME_UNKNOWN`, and nothing produced them.

Four inputs. Three of them cost nothing:

  probe            GET /user with the stored access token. 200 means the
                   authorization is live right now.
  signals          `github_app_authorization` / `revoked` from the edge
                   feed. GitHub says the user took the authorization away;
                   that is definitive, and it costs nothing to hear.
  report           whatever performs a refresh elsewhere tells this store
                   what happened, including "I do not know".

The fourth, `refresh`, is the exception and is never automatic:

  refresh          spends the single-use refresh token and classifies the
                   outcome. Only ever run when explicitly invoked, because
                   the token is consumed the moment GitHub answers.

**A 401 is not a revocation.** Device Flow access tokens last eight hours,
so expiry is the normal condition, not an incident. Treating it as
`AUTH_LOST` would revoke every standing green check twice a day and teach
everyone that the alert means nothing. On a 401 the producer records
nothing and says `needs_refresh`; only the refresh path can turn that into
a verdict, because only the refresh path can find out.

**It never refreshes on its own initiative.** Refresh tokens are single-use
with rotation, so an unsolicited refresh is a write that can strand the
credential — the one thing worse than not knowing. `probe` will never
escalate to `refresh`; an operator has to ask for it.

**Anything unclassifiable becomes REFRESH_OUTCOME_UNKNOWN.** The two
mistakes do not cost the same: calling a live authorization lost costs a
review round, calling a dead one live opens the gate.
"""
import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import auth_state

CONFIG_DIR = Path(os.environ.get(
    "GOVERNOR_CONFIG_DIR", os.path.expanduser("~/.config/review-governor")))
CREDENTIALS = CONFIG_DIR / "user-credentials.json"
API = "https://api.github.com"


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def current_credential():
    if not CREDENTIALS.exists():
        return None
    return json.loads(CREDENTIALS.read_text()).get("current")


def probe_access_token(token):
    """Non-destructive. Reads identity, spends nothing, rotates nothing."""
    req = urllib.request.Request(f"{API}/user", headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "governor-auth-producer",
        "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as exc:
        return None, {"error": f"{type(exc).__name__}: {exc}"}


def cmd_probe(args, store):
    credential = current_credential()
    if not credential:
        return {"action": "probe", "result": "NO_CREDENTIAL_STORED",
                "recorded": False,
                "note": "nothing to observe; this is not evidence of loss"}
    status, body = probe_access_token(credential["access_token"])
    generation = credential.get("generation", 0)
    if status == 200:
        obs = store.record(state=auth_state.AUTHORIZED,
                           auth_generation=generation, observed_at=utcnow(),
                           source="device_flow",
                           cause="access token accepted by GET /user")
        return {"action": "probe", "http_status": 200, "recorded": True,
                "observation_id": obs, "state": auth_state.AUTHORIZED,
                "login": (body or {}).get("login")}
    if status == 401:
        return {"action": "probe", "http_status": 401, "recorded": False,
                "result": "needs_refresh",
                "note": "an expired access token is the normal eight-hour "
                        "condition, not a revocation. Only the refresh path "
                        "can turn this into a verdict."}
    return {"action": "probe", "http_status": status, "recorded": False,
            "result": "INDETERMINATE", "detail": body,
            "note": "unreadable is not revoked; state left unchanged"}


#: Errors GitHub returns that definitively mean the authorization is gone.
#: Everything *not* on this list is treated as ambiguous, because the cost
#: of the two mistakes is not symmetric: calling a live authorization lost
#: costs a review round, calling a dead one live opens the gate.
DEFINITIVE_REFRESH_ERRORS = frozenset({
    "bad_refresh_token", "incorrect_client_credentials",
    "unauthorized_client", "invalid_grant", "access_denied",
})


def token_fingerprint(value) -> str:
    """Non-secret identity for a spent credential.

    Retaining a superseded refresh token teaches a filesystem to keep
    secrets it no longer needs, and every one of them is a candidate an
    attacker can try. A fingerprint answers "was this the one" without
    being usable.
    """
    import hashlib
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


def as_audit_metadata(credential: dict) -> dict:
    """Strip a generation down to what an audit actually needs."""
    return {
        "generation": credential.get("generation"),
        "label": credential.get("label"),
        "obtained_at": credential.get("obtained_at"),
        "obtained_via": credential.get("obtained_via"),
        "access_token_fingerprint": token_fingerprint(
            credential.get("access_token")),
        "refresh_token_fingerprint": token_fingerprint(
            credential.get("refresh_token")),
        "superseded_at": utcnow(),
        "secrets_removed": True,
    }


def rotate_credential(new_token: dict, previous: dict, *, validated: bool):
    """Persist atomically, fsync, and hand back nothing until it is on disk.

    Refresh is single-use with rotation: the moment GitHub answers, the old
    refresh token is spent. Losing the new one between the response and the
    disk turns a working credential into a Device Flow re-run, so this
    happens before anything else and is verified by reading the file back
    rather than by the absence of an exception.

    The superseded generation is kept as audit metadata only. Two files
    holding two live refresh tokens is how a rotation becomes an archive.
    """
    blob = json.loads(CREDENTIALS.read_text())
    history = [as_audit_metadata(h) if not h.get("secrets_removed") else h
               for h in blob.get("history", [])]
    history.append(as_audit_metadata(previous))
    generation = int(previous.get("generation", 0)) + 1
    blob = {"current": {**new_token, "generation": generation,
                        "label": f"G{generation}",
                        "obtained_at": utcnow(),
                        "obtained_via": "github_app_refresh_token",
                        "validated": validated},
            "history": history}
    tmp = CREDENTIALS.with_suffix(".json.tmp")
    with open(tmp, "w") as fh:
        json.dump(blob, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, CREDENTIALS)
    dir_fd = os.open(str(CREDENTIALS.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)              # the rename itself must survive a crash
    finally:
        os.close(dir_fd)
    return blob["current"], generation


def read_back_credential(expected_generation, expected_access_token):
    """Independent readback. The write not raising is not the same fact as
    the bytes being there."""
    blob = json.loads(CREDENTIALS.read_text())
    current = blob.get("current") or {}
    return {
        "generation_on_disk": current.get("generation"),
        "generation_matches": current.get("generation") == expected_generation,
        "access_token_matches":
            current.get("access_token") == expected_access_token,
        "history_entries": len(blob.get("history") or []),
        "history_holds_no_secrets": all(
            h.get("secrets_removed") for h in blob.get("history") or []),
    }



#: A response is structurally valid only with all of these. HTTP 200 is
#: not a verdict here; A1c observed failures arriving as 200 with an
#: `error` field.
REQUIRED_RESPONSE_FIELDS = ("access_token", "refresh_token", "expires_in",
                            "refresh_token_expires_in")


def refresh_preflight(store):
    """Everything checked before a single-use token is spent."""
    credential = current_credential()
    config_writable = os.access(CREDENTIALS.parent, os.W_OK)
    app_path = CONFIG_DIR / "app-credentials.json"
    app = json.loads(app_path.read_text()) if app_path.exists() else {}
    checks = {
        "credential_present": bool(credential),
        "refresh_token_present": bool((credential or {}).get("refresh_token")),
        "client_credentials_present": bool(app.get("client_id")
                                           and app.get("client_secret")),
        "destination_writable": config_writable,
        "auth_store_reachable": store.current() is not None or True,
        "old_auth_generation": (credential or {}).get("generation"),
        "started_at": utcnow(),
    }
    checks["ready"] = all([checks["credential_present"],
                           checks["refresh_token_present"],
                           checks["client_credentials_present"],
                           checks["destination_writable"]])
    return checks, credential, app


def cmd_refresh(args, store):
    """Spend the refresh token exactly once, and classify honestly.

    There is no retry anywhere in this function, deliberately. If the
    connection dies at the worst possible millisecond, the token may
    already have been consumed, and a second request would either fail
    against a spent token or — worse — succeed and discard a rotation
    nobody recorded. That uncertainty is what REFRESH_OUTCOME_UNKNOWN is
    for; it is not a problem to be retried away.
    """
    checks, credential, app = refresh_preflight(store)
    if not checks["ready"]:
        return {"action": "refresh", "attempted": False, "recorded": False,
                "preflight": checks,
                "result": "PREFLIGHT_FAILED — token not spent"}

    generation = credential.get("generation", 0)
    body = urllib.parse.urlencode({
        "client_id": app["client_id"], "client_secret": app["client_secret"],
        "grant_type": "refresh_token",
        "refresh_token": credential["refresh_token"]}).encode()
    req = urllib.request.Request(
        "https://github.com/login/oauth/access_token", data=body,
        headers={"Accept": "application/json",
                 "User-Agent": "governor-auth-producer"})

    def ambiguous(cause, **extra):
        obs = store.record(state=auth_state.REFRESH_OUTCOME_UNKNOWN,
                           auth_generation=generation, observed_at=utcnow(),
                           source="refresh", cause=cause)
        return {"action": "refresh", "attempted": True, "recorded": True,
                "observation_id": obs, "preflight": checks,
                "state": auth_state.REFRESH_OUTCOME_UNKNOWN, "cause": cause,
                "retry_performed": False,
                "recovery": "fresh Device Flow only", **extra}

    # ---- exactly one request ------------------------------------------------
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            status = resp.status
    except Exception as exc:
        return ambiguous(f"transport failure: {type(exc).__name__}")
    try:
        parsed = json.loads(raw)
    except ValueError:
        return ambiguous("response was not JSON")

    error = parsed.get("error")
    if error in DEFINITIVE_REFRESH_ERRORS:
        obs = store.record(state=auth_state.AUTH_LOST,
                           auth_generation=generation, observed_at=utcnow(),
                           source="refresh", cause=f"refresh error: {error}")
        return {"action": "refresh", "attempted": True, "recorded": True,
                "observation_id": obs, "preflight": checks,
                "state": auth_state.AUTH_LOST, "error": error,
                "http_status": status, "recovery": "fresh Device Flow"}

    # ---- secrets first, verdict second --------------------------------------
    # Deviation from the literal order, stated rather than slipped in: the
    # contract validates before persisting, which would discard a live
    # refresh token whenever an expiry field is missing. Persisting both
    # secrets the moment they exist loses nothing — the gate stays closed
    # through the auth store, not through the credential file — and keeps a
    # recovery option that discarding destroys.
    if not (parsed.get("access_token") and parsed.get("refresh_token")):
        return ambiguous(f"no usable token pair in response; error={error!r}",
                         http_status=status)

    missing = [f for f in REQUIRED_RESPONSE_FIELDS if not parsed.get(f)]
    rotated, new_generation = rotate_credential(parsed, credential,
                                                validated=not missing)
    readback = read_back_credential(new_generation, parsed["access_token"])

    if missing:
        return ambiguous(f"structurally incomplete response, missing {missing}",
                         http_status=status, readback=readback,
                         credential_persisted=True,
                         note="stored unvalidated so the material is not lost; "
                              "the gate stays closed through the auth store")
    if not (readback["generation_matches"] and readback["access_token_matches"]):
        return ambiguous("credential did not survive independent readback",
                         readback=readback)

    # ---- prove it actually works, with the new token ------------------------
    probe_status, probe_body = probe_access_token(parsed["access_token"])
    if probe_status != 200:
        return ambiguous(
            f"new access token failed a real authenticated call: {probe_status}",
            readback=readback, credential_persisted=True)

    obs = store.record(state=auth_state.AUTHORIZED,
                       auth_generation=new_generation, observed_at=utcnow(),
                       source="refresh",
                       cause="refresh rotated, readback confirmed, "
                             "authenticated call succeeded")
    return {"action": "refresh", "attempted": True, "recorded": True,
            "observation_id": obs, "preflight": checks,
            "state": auth_state.AUTHORIZED, "http_status": status,
            "old_auth_generation": generation,
            "new_auth_generation": new_generation,
            "readback": readback,
            "authenticated_as": (probe_body or {}).get("login"),
            "retry_performed": False}



def cmd_report(args, store):
    """The refresh path reporting its own outcome, including uncertainty."""
    credential = current_credential() or {}
    obs = store.record(state=args.state,
                       auth_generation=(args.auth_generation
                                        if args.auth_generation is not None
                                        else credential.get("generation", 0)),
                       observed_at=utcnow(), source=args.source,
                       cause=args.cause)
    return {"action": "report", "recorded": True, "observation_id": obs,
            "state": args.state, "source": args.source}


def cmd_consume_signals(args, store):
    """`github_app_authorization` with action `revoked` is definitive.

    The edge already receives this event; the primary re-reads nothing here
    because there is nothing to re-read — GitHub is telling us the user
    withdrew consent, and no later API call makes that more true.
    """
    import signal_client
    secret_path = CONFIG_DIR / "heartbeat-secret"
    secret = secret_path.read_bytes().strip()
    after = args.after
    status, body = signal_client.fetch_signals(args.endpoint, secret, after)
    if status != 200:
        return {"action": "consume-signals", "error": body.get("error"),
                "http_status": status}
    revocations = [s for s in (body.get("signals") or [])
                   if s["event"] == "github_app_authorization"
                   and s.get("action") == "revoked"]
    recorded = []
    for signal in revocations:
        credential = current_credential() or {}
        recorded.append(store.record(
            state=auth_state.AUTH_LOST,
            auth_generation=credential.get("generation", 0),
            observed_at=signal["received_at"], source="authorization_webhook",
            cause=f"github_app_authorization revoked, delivery "
                  f"{signal['delivery_guid'][:12]}"))
    return {"action": "consume-signals", "scanned": len(body.get("signals") or []),
            "revocations_found": len(revocations), "recorded": recorded}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command",
                    choices=["probe", "refresh", "report", "consume-signals"])
    ap.add_argument("--db", default=str(CONFIG_DIR / "auth.sqlite3"))
    ap.add_argument("--projection", default=str(CONFIG_DIR / "auth-state.json"))
    ap.add_argument("--state", choices=auth_state.STATES,
                    help="report only: what the refresh path observed")
    ap.add_argument("--source", choices=auth_state.SOURCES, default="refresh")
    ap.add_argument("--auth-generation", type=int, default=None)
    ap.add_argument("--cause", default=None)
    ap.add_argument("--endpoint", default="https://192-248-184-141.sslip.io")
    ap.add_argument("--after", type=int, default=0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if args.command == "report" and not args.state:
        ap.error("report needs --state")

    store = auth_state.AuthStore(args.db)
    try:
        result = {"probe": cmd_probe, "refresh": cmd_refresh,
                  "report": cmd_report,
                  "consume-signals": cmd_consume_signals}[args.command](args, store)
        # The mirror is refreshed on every run, including runs that recorded
        # nothing: a projection that only updates on change goes stale
        # silently and then disagrees with the store nobody is reading.
        result["projection"] = store.project(args.projection)
        # The derived permission, not a boolean: an operator reading this
        # output must be able to see how old the observation is.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                               / "steady-state" / "harness"))
        import auth_policy
        permission = auth_policy.evaluate(store)
        result["permission"] = {
            "state": permission.state,
            "age_seconds": permission.age_seconds,
            "auth_generation": permission.auth_generation,
            "observed_at": permission.observed_at,
            "permits_action": permission.permits_action,
        }
        result["demands_invalidation"] = store.demands_invalidation()
    finally:
        store.close()
    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
