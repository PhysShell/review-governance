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


def rotate_credential(new_token: dict, previous: dict):
    """Persist the rotated credential BEFORE anything else believes in it.

    Refresh is single-use with rotation: the moment GitHub answers, the old
    refresh token is spent. Losing the new one between the response and the
    disk is how a working credential becomes a Device Flow re-run at an
    inconvenient hour, so this write happens first and everything else
    happens after.
    """
    blob = json.loads(CREDENTIALS.read_text())
    blob.setdefault("history", []).append(previous)
    generation = int(previous.get("generation", 0)) + 1
    blob["current"] = {**new_token, "generation": generation,
                       "label": f"G{generation}",
                       "obtained_at": utcnow(),
                       "obtained_via": "github_app_refresh_token"}
    tmp = CREDENTIALS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(blob, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, CREDENTIALS)          # atomic; no window with no file
    return blob["current"]


def cmd_refresh(args, store):
    """Spend the refresh token, and classify the outcome honestly.

    A1c established that GitHub reports failure here as HTTP 200 with an
    `error` field, so a 2xx is not success. Anything this cannot positively
    classify becomes REFRESH_OUTCOME_UNKNOWN — the token may or may not have
    been consumed, and that uncertainty is itself the safety-relevant fact.
    """
    credential = current_credential()
    if not credential:
        return {"action": "refresh", "recorded": False,
                "result": "NO_CREDENTIAL_STORED"}
    app = json.loads((CONFIG_DIR / "app-credentials.json").read_text())
    body = urllib.parse.urlencode({
        "client_id": app["client_id"], "client_secret": app["client_secret"],
        "grant_type": "refresh_token",
        "refresh_token": credential["refresh_token"]}).encode()
    req = urllib.request.Request(
        "https://github.com/login/oauth/access_token", data=body,
        headers={"Accept": "application/json",
                 "User-Agent": "governor-auth-producer"})
    generation = credential.get("generation", 0)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            status = resp.status
        parsed = json.loads(raw)
    except Exception as exc:
        # The request left this host. Whether GitHub consumed the token is
        # unknowable from here, and guessing "it failed" would leave a
        # possibly-live authorization looking dead, or vice versa.
        obs = store.record(state=auth_state.REFRESH_OUTCOME_UNKNOWN,
                           auth_generation=generation, observed_at=utcnow(),
                           source="refresh",
                           cause=f"refresh transport failure: {type(exc).__name__}")
        return {"action": "refresh", "recorded": True, "observation_id": obs,
                "state": auth_state.REFRESH_OUTCOME_UNKNOWN,
                "reason": type(exc).__name__}

    if parsed.get("access_token"):
        rotated = rotate_credential(parsed, credential)
        obs = store.record(state=auth_state.AUTHORIZED,
                           auth_generation=rotated["generation"],
                           observed_at=utcnow(), source="refresh",
                           cause="refresh returned a new access token")
        return {"action": "refresh", "recorded": True, "observation_id": obs,
                "state": auth_state.AUTHORIZED,
                "auth_generation": rotated["generation"],
                "rotated": True}

    error = parsed.get("error")
    if error in DEFINITIVE_REFRESH_ERRORS:
        obs = store.record(state=auth_state.AUTH_LOST,
                           auth_generation=generation, observed_at=utcnow(),
                           source="refresh", cause=f"refresh error: {error}")
        return {"action": "refresh", "recorded": True, "observation_id": obs,
                "state": auth_state.AUTH_LOST, "error": error,
                "http_status": status}
    obs = store.record(state=auth_state.REFRESH_OUTCOME_UNKNOWN,
                       auth_generation=generation, observed_at=utcnow(),
                       source="refresh",
                       cause=f"unclassified refresh response: {error!r}")
    return {"action": "refresh", "recorded": True, "observation_id": obs,
            "state": auth_state.REFRESH_OUTCOME_UNKNOWN, "error": error,
            "http_status": status,
            "note": "not on the definitive-error list, so treated as "
                    "ambiguous rather than assumed"}


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
        result["permits_triggers"] = store.permits_triggers()
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
