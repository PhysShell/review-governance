#!/usr/bin/env python3
"""A5b step 2: freeze the inventory. Read-only, and only read-only.

This module has no write path at all — not a guarded one, not an allowlisted
one. The stage that follows it mutates production enforcement, so the
artifact it produces must be impossible to confuse with the thing that acts
on it.

**What "atomic" means here, precisely.** GitHub offers no transaction over a
PR list, so this cannot be atomic in the database sense and does not claim
to be. It enumerates, then re-enumerates, and refuses to emit a frozen
artifact if the set of PRs or any head moved during the observation. That
is a *detected-quiescence* freeze: it proves nothing changed while we were
looking, which is a weaker and honest claim.

Divergence after the freeze is not this module's problem to hide — step 3b
re-reads reality immediately before activation and stops the stage on any
delta, and a head that moves later simply carries no check, which fails
closed.

The artifact carries its own hash so later steps can name exactly which
freeze they acted on, rather than "the inventory".
"""
import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

import governor

PRODUCTION_CONTEXT = "ai/final-review"


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def enumerate_open(token, repo, base):
    """One pass: every open PR against `base`, with its full head."""
    status, pulls = governor.request(
        "GET", f"/repos/{repo}/pulls?state=open&base={base}&per_page=100",
        token)
    if status != 200:
        raise RuntimeError(f"cannot list pulls: {status}")
    return [{"repo": repo,
             "base": p["base"]["ref"],
             "pr_number": p["number"],
             "head_sha": p["head"]["sha"],          # full, never abbreviated
             "head_ref": p["head"]["ref"],
             "draft": bool(p["draft"]),
             "title": p["title"]}
            for p in sorted(pulls or [], key=lambda p: p["number"])]


def production_context_runs(token, repo, head_sha):
    """The zero-point assertion: the production context must not exist yet.

    Recorded per head rather than as one global claim, because "the context
    is unused" is only meaningful against the exact commits about to be
    bootstrapped.
    """
    status, body = governor.request(
        "GET", f"/repos/{repo}/commits/{head_sha}/check-runs?per_page=100",
        token)
    if status != 200:
        return None
    return [{"id": r["id"], "conclusion": r.get("conclusion"),
             "app_id": (r.get("app") or {}).get("id")}
            for r in (body or {}).get("check_runs", [])
            if r.get("name") == PRODUCTION_CONTEXT]


def freeze(repo, base):
    token = governor.installation_token()
    started_at = utcnow()
    first = enumerate_open(token, repo, base)
    context_runs = {item["head_sha"]: production_context_runs(
        token, repo, item["head_sha"]) for item in first}
    second = enumerate_open(token, repo, base)
    finished_at = utcnow()

    def identity(items):
        return [(i["pr_number"], i["head_sha"], i["base"], i["draft"])
                for i in items]

    quiescent = identity(first) == identity(second)
    unreadable = [sha for sha, runs in context_runs.items() if runs is None]
    already_present = {sha: runs for sha, runs in context_runs.items() if runs}

    artifact = {
        "artifact": "A5bInventoryFreeze-v1",
        "protocol_head": "5a0842ed86addff03f1c4d114248960a46510d5d",
        "repo": repo,
        "base": base,
        "observed_at": started_at,
        "observation_finished_at": finished_at,
        "quiescent_during_observation": quiescent,
        "inventory": first,
        "pr_count": len(first),
        "production_context": PRODUCTION_CONTEXT,
        "production_context_runs_per_head": context_runs,
        "production_context_unused": not already_present and not unreadable,
        "heads_with_unreadable_check_runs": unreadable,
        "frozen": quiescent and not already_present and not unreadable,
    }
    if not quiescent:
        artifact["refusal"] = ("the PR set or a head moved during the "
                               "observation; this is not a freeze")
        artifact["second_pass"] = second
    if already_present:
        artifact["refusal"] = (f"{PRODUCTION_CONTEXT} already exists on "
                               "some head; the zero point is not clean")
    if unreadable:
        artifact["refusal"] = ("check runs unreadable for some heads; "
                               "absence was not established")
    payload = {k: v for k, v in artifact.items()
               if k not in ("observation_finished_at",)}
    artifact["inventory_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return artifact


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="PhysShell/evm-from-scratch")
    ap.add_argument("--base", default="main")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    artifact = freeze(args.repo, args.base)
    rendered = json.dumps(artifact, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0 if artifact["frozen"] else 1


if __name__ == "__main__":
    sys.exit(main())
