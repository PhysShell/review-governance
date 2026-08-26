#!/usr/bin/env python3
"""Primary-side fast path: pull signals from the edge, then read GitHub.

This closes the gap A5a-c2 found — the edge was storing deliveries durably
and nobody was reading them, so the healthy path was still polling.

The network direction is unchanged: the primary only makes outbound
requests. It asks the edge "anything after cursor N?", gets **metadata
only**, and then derives every fact from GitHub itself. The edge never
tells the primary what happened, only that something did.

Two properties this must not quietly lose:

  * the cursor advances only after the observation is durable, so a crash
    re-processes rather than skips;
  * reconciliation is untouched and still reads GitHub on its own schedule,
    so a signal that never arrives is still caught.
"""
import argparse
import datetime
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import governor
import observations

CONFIG_DIR = Path(os.environ.get(
    "GOVERNOR_CONFIG_DIR", os.path.expanduser("~/.config/review-governor")))
SECRET_PATH = CONFIG_DIR / "heartbeat-secret"
RELEVANT = ("pull_request",)


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value):
    return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc)


def fetch_signals(endpoint, secret, after):
    signature = "sha256=" + hmac.new(secret, f"signals:{after}".encode(),
                                     hashlib.sha256).hexdigest()
    url = f"{endpoint.rstrip('/')}/signals?after={after}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "governor-primary-signals",
        "X-Governor-Signature": signature})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:200]}
    except Exception as exc:
        return None, {"error": f"{type(exc).__name__}: {exc}"}


def open_pr_snapshot(repo, token=None):
    """Re-read the open PR set from GitHub.

    The signal carries no PR number by design, so the primary must not guess
    which PR moved — it re-reads all of them and records what it saw. An
    earlier draft took `pulls[0]`, which recorded an arbitrary PR's head as
    if it were the observation for this signal; that is the kind of quiet
    fiction that later reads as evidence.

    Returns None on a failed read so the caller can leave the cursor alone
    rather than record an empty snapshot as though the repo had no PRs.
    """
    token = token or governor.installation_token()
    status, pulls = governor.request(
        "GET", f"/repos/{repo}/pulls?state=open&per_page=100", token)
    if status != 200:
        return None
    return [{"number": p["number"], "head": p["head"]["sha"]}
            for p in pulls or []]


def drain(endpoint, secret, store, repo, limit_batches=1):
    processed = []
    for _ in range(limit_batches):
        after = store.cursor()
        status, body = fetch_signals(endpoint, secret, after)
        if status != 200:
            return {"error": body.get("error"), "http_status": status,
                    "processed": processed}
        signals = body.get("signals") or []
        if not signals:
            break
        for signal in signals:
            seq = signal["seq"]
            if signal["event"] not in RELEVANT or \
                    (signal.get("repository") and signal["repository"] != repo):
                store.advance(seq, utcnow())     # nothing to derive, but seen
                continue
            snapshot = open_pr_snapshot(repo)
            if snapshot is None:
                # GitHub unreadable: stop here rather than advance past a
                # signal we never actually derived anything from.
                return {"processed": processed, "cursor": store.cursor(),
                        "stopped_at_seq": seq,
                        "reason": "github read failed; cursor not advanced"}
            observed_at = utcnow()
            latency = (parse_ts(observed_at) -
                       parse_ts(signal["received_at"])).total_seconds()
            store.record(seq=seq, delivery_guid=signal["delivery_guid"],
                         event=signal["event"], action=signal.get("action"),
                         repository=signal.get("repository"),
                         received_at=signal["received_at"],
                         observed_at=observed_at, latency_seconds=latency,
                         pr_snapshot=snapshot)
            store.advance(seq, utcnow())          # only after it is durable
            processed.append({"seq": seq, "guid": signal["delivery_guid"],
                              "event": signal["event"],
                              "action": signal.get("action"),
                              "received_at": signal["received_at"],
                              "observed_at": observed_at,
                              "latency_seconds": round(latency, 3),
                              "pr_count": len(snapshot)})
    return {"processed": processed, "cursor": store.cursor()}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--endpoint", required=True)
    ap.add_argument("--repo", default="PhysShell/evm-from-scratch")
    ap.add_argument("--db", default=".captures/a5a/observations.sqlite3")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--window", type=int, default=120,
                    help="seconds to keep pulling; 0 means until stopped, "
                         "which is what the deployed service uses")
    ap.add_argument("--stop-after-observations", type=int, default=None,
                    help="stop once this many *relevant* signals have been "
                         "turned into observations. Deliberately not a cursor "
                         "position: the cursor also advances past irrelevant "
                         "events, so stopping on it can end the watch before "
                         "anything was actually observed.")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if not SECRET_PATH.exists():
        print(f"missing shared secret at {SECRET_PATH}", file=sys.stderr)
        return 2
    secret = SECRET_PATH.read_bytes().strip()
    store = observations.ObservationStore(args.db)
    try:
        if args.once:
            result = drain(args.endpoint, secret, store, args.repo)
        else:
            # `--window 0` is the deployed configuration: a detector with an
            # expiry date stops detecting, quietly, exactly like the
            # watchdog defect A5a-c2-1a found.
            deadline = None if args.window <= 0 else time.time() + args.window
            all_processed = []
            while deadline is None or time.time() < deadline:
                batch = drain(args.endpoint, secret, store, args.repo)
                all_processed.extend(batch.get("processed") or [])
                if args.stop_after_observations and \
                        len(all_processed) >= args.stop_after_observations:
                    break
                time.sleep(args.interval)
            result = {"processed": all_processed, "cursor": store.cursor(),
                      "window_expired": deadline is not None}
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
