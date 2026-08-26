#!/usr/bin/env python3
"""Synthetic incident and recovery, delivered to an actual human.

`curl /healthz` proves the endpoint answers. It proves nothing about whether
anyone would be told when it stops, which is the only property that matters
here. So this drill sends a real alert down the real channel and reports the
transport's own acknowledgement.

It uses a **separate** alert database from the running services, so a drill
can never close a real open incident or suppress one by making it look
already-sent.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import alerting

DRILL_CAUSE = "watchdog_incident"


def discover_chat_id(token):
    """Read the chat id from getUpdates, so nobody has to copy a number by
    hand and discover the typo during an incident."""
    import urllib.request
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/getUpdates",
        headers={"User-Agent": "governor-alert-drill"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read().decode() or "{}")
    chats = []
    for update in body.get("result") or []:
        message = update.get("message") or update.get("channel_post") or {}
        chat = message.get("chat") or {}
        if chat.get("id") is not None and chat["id"] not in [c["id"] for c in chats]:
            chats.append({"id": chat["id"], "type": chat.get("type"),
                          "title": chat.get("title") or chat.get("username")})
    return chats


def run(args):
    transport = alerting.transport_from_config(args.config_dir,
                                               dry_run=args.dry_run)
    if transport is None:
        return {"error": "no alerting channel configured",
                "looked_in": str(Path(args.config_dir) / "alerting.json")}
    notifier = alerting.Notifier(args.db, transport,
                                 origin=f"DRILL · {args.repo}", renotify=0)
    try:
        incident = notifier.raise_(
            alerting.CRITICAL, DRILL_CAUSE, repo=args.repo,
            incident_id=args.incident_id, detected_at=alerting.utcnow(),
            state="SYNTHETIC DRILL — no real incident")
        time.sleep(args.gap)
        recovery = notifier.clear(
            DRILL_CAUSE, repo=args.repo, incident_id=args.incident_id,
            detected_at=alerting.utcnow(), state="SYNTHETIC DRILL — recovered")
        return {
            "transport": transport.name,
            "incident_delivered": incident.get("delivered"),
            "incident_error": incident.get("error"),
            "recovery_delivered": recovery.get("delivered"),
            "recovery_error": recovery.get("error"),
            "condition_closed": notifier.open_causes() == [],
            "incident_payload": incident.get("payload"),
            "recovery_payload": recovery.get("payload"),
            "messages": getattr(transport, "sent", None),
            "verdict": "PASS" if incident.get("delivered")
                       and recovery.get("delivered")
                       and notifier.open_causes() == [] else "FAIL",
            "note": "delivery here is the transport's acknowledgement; the "
                    "human still has to confirm they saw both messages",
        }
    finally:
        notifier.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="PhysShell/evm-from-scratch")
    ap.add_argument("--config-dir", default=str(alerting.CONFIG_DIR))
    ap.add_argument("--db", default=".captures/a5b-preflight/drill.sqlite3",
                    help="separate from the live alert store on purpose")
    ap.add_argument("--incident-id", type=int, default=0)
    ap.add_argument("--gap", type=float, default=5.0,
                    help="seconds between incident and recovery, so the two "
                         "arrive as distinguishable messages")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--discover-chat-id", default=None,
                    metavar="BOT_TOKEN")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if args.discover_chat_id:
        result = {"chats": discover_chat_id(args.discover_chat_id)}
    else:
        result = run(args)
    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0 if result.get("verdict") in (None, "PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
