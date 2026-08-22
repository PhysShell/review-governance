#!/usr/bin/env python3
"""A3b lifecycle driver: publish a Governor success, revoke it, requalify,
then let a head change cancel it.

    freeze      read HEAD from GitHub, open the epoch, record auth generation
    trigger     post provider requests on the app-mediated user carrier
    observe     capture inventory and per-provider qualification
    bundle      build the immutable PositiveEvidenceBundle-v1
    settle      preregistered settling window + re-check
    publish     guard -> durable decision -> GitHub success -> re-validate
    supersede   newer provider generation -> EVIDENCE_INVALIDATED -> failure
    requalify   new bundle on the same head -> success again
    headchange  old epoch STALE + run cancelled, new epoch fails closed
    state       dump the append-only chain and projections

Every write to GitHub is a projection of a decision already committed to
the append-only history.
"""
import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import decisions as dec
import gh
import qualify

SETTLING_SECONDS = 120
STATE_DEFAULT = ".captures/a3b/state.json"
DB_DEFAULT = ".captures/a3b/decisions.sqlite3"
RULE = "a3b.1"
TRIGGER_PATTERNS = {"codex": re.compile(r"@codex\s+review", re.I),
                    "coderabbit": re.compile(r"@coderabbitai\s+full\s+review", re.I)}


def slim(comment):
    via = comment.get("performed_via_github_app")
    return {"id": comment["id"], "created_at": comment["created_at"],
            "updated_at": comment.get("updated_at"), "body": comment.get("body"),
            "user": {"login": comment["user"]["login"], "id": comment["user"]["id"],
                     "type": comment["user"].get("type")},
            "performed_via_github_app": ({"id": via.get("id"), "slug": via.get("slug")}
                                         if isinstance(via, dict) else None)}


def load(path):
    return json.loads(Path(path).read_text())


def save(path, state):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(state, indent=2) + "\n")


def capture_inventory(repo, pr):
    return {
        "captured_at": gh.utcnow(),
        "issue_comments": [slim(c) for c in repo.issue_comments(pr)],
        "reviews": [{"id": r["id"], "state": r["state"],
                     "submitted_at": r.get("submitted_at"),
                     "commit_id": r.get("commit_id"), "body": r.get("body"),
                     "user": {"login": r["user"]["login"], "id": r["user"]["id"],
                              "type": r["user"].get("type")}}
                    for r in repo.reviews(pr)],
        "review_comments": [{"id": c["id"], "created_at": c["created_at"],
                             "path": c.get("path"), "commit_id": c.get("commit_id"),
                             "body": c.get("body"),
                             "user": {"login": c["user"]["login"],
                                      "id": c["user"]["id"],
                                      "type": c["user"].get("type")}}
                            for c in repo.review_comments(pr)],
    }


def observe(state, inventory):
    head = state["head_sha"]
    others = [state["base_sha"]] + [c for c in state.get("pr_commit_shas", [])
                                    if c != head]
    return {
        "codex": qualify.qualify_codex(state["requests"]["codex"]["comment"],
                                       inventory, head, others),
        "coderabbit": qualify.qualify_coderabbit(
            state["requests"]["coderabbit"]["comment"], inventory, head,
            state["base_sha"]),
    }


def newer_request_generations(state, inventory):
    """A newer request for a mandatory provider means the frozen bundle is no
    longer current — regardless of what that new request eventually says."""
    newer = {}
    for provider, pattern in TRIGGER_PATTERNS.items():
        current = state["requests"][provider]["comment"]
        for comment in inventory["issue_comments"]:
            if comment["id"] == current["id"]:
                continue
            if qualify.carrier_of(comment) != "app_mediated_user":
                continue
            if pattern.search(comment.get("body") or "") and \
                    comment["created_at"] > current["created_at"]:
                newer[provider] = comment["id"]
    return newer


def guard(state, repo, bundle, phase):
    """The pre/post publication guard: every predicate, no best effort."""
    failures = []
    started = gh.utcnow()
    pull = repo.pull_request(state["pr_number"])
    inventory = capture_inventory(repo, state["pr_number"])
    fresh = observe(state, inventory)

    if pull["head"]["sha"] != bundle["head_sha"]:
        failures.append("current head differs from the bundle head")
    if state["epoch"]["state"] != "CURRENT":
        failures.append("epoch is not CURRENT")
    if state["auth"]["state"] != "AUTHORIZED":
        failures.append(f"authorization is {state['auth']['state']}")

    recomputed = qualify.build_bundle(
        state["epoch"], bundle["head_sha"], bundle["auth_generation"],
        bundle["requests"], bundle["observations"], bundle["inventory_cutoff"])
    if recomputed["evidence_hash"] != bundle["evidence_hash"]:
        failures.append("evidence hash does not recompute")

    for provider in ("codex", "coderabbit"):
        if not fresh[provider]["qualified"]:
            failures.extend(f"{provider}: {r}" for r in fresh[provider]["reasons"])

    mutation = qualify.detect_mutation(bundle, fresh)
    if not mutation["stable"]:
        failures.extend(mutation["changes"])

    newer = newer_request_generations(state, inventory)
    for provider, comment_id in newer.items():
        failures.append(f"newer {provider} request generation exists "
                        f"(comment {comment_id})")

    for provider in ("codex", "coderabbit"):
        if any(fresh[provider]["findings_seen"].values()):
            failures.append(f"{provider} findings present")

    return {"phase": phase, "started_at": started, "finished_at": gh.utcnow(),
            "passed": not failures, "failures": failures,
            "head_at_guard": pull["head"]["sha"],
            "inventory_counts": {k: len(inventory[k]) for k in
                                 ("issue_comments", "reviews", "review_comments")},
            "newer_generations": newer}, inventory, fresh


def summary_for(state, bundle, verdict, extra=None):
    lines = [
        f"Governor verdict: {verdict}",
        f"Head: {bundle['head_sha']}",
        f"Evidence bundle: {bundle['evidence_hash']}",
        f"Decision rule: {bundle['decision_rule_revision']}",
        f"Epoch: {bundle['epoch_id']}",
        f"Authorization generation: {bundle['auth_generation']}",
        "",
        f"Codex: {bundle['observations']['codex']['state']}",
        f"CodeRabbit: {bundle['observations']['coderabbit']['state']}",
    ]
    if extra:
        lines += ["", extra]
    lines += [
        "",
        "This is a Governor verdict derived from frozen advisory evidence. "
        "It is not provider-issued CLEAN provenance.",
    ]
    return {"title": f"Governor: {verdict}", "summary": "\n".join(lines)}


# --- commands ---------------------------------------------------------------

def cmd_freeze(args):
    repo = gh.Repo(args.repo)
    me = repo.whoami()
    assert me["id"] == gh.EXPECTED_USER["id"], me
    pull = repo.pull_request(args.pr)
    creds = gh.user_credentials()
    head = pull["head"]["sha"]
    epoch_id = "epoch-" + hashlib.sha256(
        f"{pull['base']['repo']['id']}:{args.pr}:{head}".encode()).hexdigest()[:16]
    state = {
        "frozen_at": gh.utcnow(), "repo": args.repo,
        "repo_id": pull["base"]["repo"]["id"], "pr_number": args.pr,
        "base_sha": pull["base"]["sha"], "head_sha": head,
        "pr_commit_shas": [c["sha"] for c in repo.commits(args.pr)],
        "epoch": {"epoch_id": epoch_id, "generation": 1, "state": "CURRENT"},
        "auth": {"generation": creds["generation"], "label": creds["label"],
                 "state": "AUTHORIZED", "carrier": "app_mediated_user"},
        "decision_rule_revision": RULE, "requests": {}, "bundles": {},
        "check_run_id": None, "timings": {},
    }
    save(args.state, state)
    return {k: state[k] for k in ("repo", "pr_number", "base_sha", "head_sha",
                                  "epoch", "auth", "decision_rule_revision")}


def cmd_trigger(args):
    state = load(args.state)
    repo = gh.Repo(state["repo"])
    if repo.pull_request(state["pr_number"])["head"]["sha"] != state["head_sha"]:
        raise SystemExit("head moved; round is STALE")
    bodies = {"codex": "@codex review", "coderabbit": "@coderabbitai full review"}
    targets = [args.only] if args.only else list(bodies)
    for provider in targets:
        comment = repo.comment_as_user(state["pr_number"], bodies[provider])
        carrier = qualify.carrier_of(slim(comment))
        if carrier != "app_mediated_user":
            raise SystemExit(f"request carrier is {carrier}")
        state["requests"][provider] = {
            "provider_request_id":
                f"{state['epoch']['epoch_id']}:{provider}:g{args.generation}",
            "request_generation": args.generation,
            "epoch_id": state["epoch"]["epoch_id"],
            "auth_generation": state["auth"]["generation"],
            "head_at_request": state["head_sha"],
            "comment": slim(comment),
        }
    save(args.state, state)
    return {p: {"comment_id": r["comment"]["id"], "generation": r["request_generation"],
                "created_at": r["comment"]["created_at"],
                "carrier": qualify.carrier_of(r["comment"])}
            for p, r in state["requests"].items()}


def cmd_observe(args):
    state = load(args.state)
    repo = gh.Repo(state["repo"])
    inventory = capture_inventory(repo, state["pr_number"])
    state["inventory"] = inventory
    state["observations"] = observe(state, inventory)
    save(args.state, state)
    return {p: {"qualified": o["qualified"], "state": o["state"],
                "reasons": o["reasons"]} for p, o in state["observations"].items()}


def cmd_bundle(args):
    state = load(args.state)
    bundle = qualify.build_bundle(
        state["epoch"], state["head_sha"], state["auth"]["generation"],
        {p: {k: v for k, v in r.items() if k != "comment"}
         for p, r in state["requests"].items()},
        state["observations"], state["inventory"]["captured_at"])
    bundle["decision_rule_revision"] = RULE
    bundle["evidence_hash"] = hashlib.sha256(
        json.dumps({k: v for k, v in bundle.items() if k != "evidence_hash"},
                   sort_keys=True).encode()).hexdigest()
    state["bundles"][args.label] = bundle
    save(args.state, state)
    return {"label": args.label, "evidence_hash": bundle["evidence_hash"],
            "head_sha": bundle["head_sha"]}


def cmd_settle(args):
    state = load(args.state)
    repo = gh.Repo(state["repo"])
    wait = args.seconds if args.seconds is not None else SETTLING_SECONDS
    time.sleep(wait)
    bundle = state["bundles"][args.label]
    result, _, _ = guard(state, repo, bundle, phase=f"settling:{args.label}")
    state.setdefault("settling", {})[args.label] = {"waited_seconds": wait,
                                                    **result}
    save(args.state, state)
    return state["settling"][args.label]


def cmd_publish(args):
    state = load(args.state)
    repo = gh.Repo(state["repo"])
    history = dec.History(args.db)
    bundle = state["bundles"][args.label]
    try:
        pre, _, _ = guard(state, repo, bundle, phase="pre-publication")
        if not pre["passed"]:
            return {"published": False, "guard": pre}

        decision_id = history.record(
            epoch_id=bundle["epoch_id"], head_sha=bundle["head_sha"],
            verdict="SUCCESS", bundle_hash=bundle["evidence_hash"],
            bundle_schema=bundle["bundle_version"], decision_rule_revision=RULE,
            auth_generation=bundle["auth_generation"], decided_at=gh.utcnow())

        output = summary_for(state, bundle, "SUCCESS")
        if state.get("check_run_id"):
            status, body = repo.conclude_check(
                state["check_run_id"], "success", output,
                evidence_hash=bundle["evidence_hash"])
        else:
            run = repo.create_check(bundle["head_sha"], bundle["epoch_id"], output)
            state["check_run_id"] = run["id"]
            status, body = repo.conclude_check(
                run["id"], "success", output,
                evidence_hash=bundle["evidence_hash"])
        github_at = gh.utcnow()
        history.project(bundle["epoch_id"], bundle["head_sha"], body["id"],
                        body.get("conclusion"), decision_id, github_at)

        post, _, _ = guard(state, repo, bundle, phase="post-publication")
        state["timings"][args.label] = {
            "pre_publish_validation_at": pre["finished_at"],
            "github_success_at": github_at,
            "post_publish_validation_at": post["finished_at"],
        }
        state["published"] = {"check_run_id": body["id"],
                              "conclusion": body.get("conclusion"),
                              "decision_id": decision_id,
                              "bundle_hash": bundle["evidence_hash"]}
        save(args.state, state)
        app = body.get("app") or {}
        return {"published": True, "decision_id": decision_id,
                "check_run_id": body["id"], "head_sha": body.get("head_sha"),
                "conclusion": body.get("conclusion"),
                "external_id": body.get("external_id"),
                "app": {"id": app.get("id"), "slug": app.get("slug")},
                "bundle_hash_in_output":
                    bundle["evidence_hash"] in (body.get("output") or {}).get("summary", ""),
                "http_status": status, "pre_guard": pre, "post_guard": post,
                "timings": state["timings"][args.label]}
    finally:
        history.close()


def cmd_supersede(args):
    """A newer provider request generation invalidates the standing success
    immediately — without waiting for that request's outcome."""
    state = load(args.state)
    repo = gh.Repo(state["repo"])
    history = dec.History(args.db)
    try:
        inventory = capture_inventory(repo, state["pr_number"])
        newer = newer_request_generations(state, inventory)
        if not newer:
            return {"superseded": False, "reason": "no newer generation observed"}
        detected_at = gh.utcnow()
        standing = history.latest_success()
        bundle = state["bundles"][args.label]
        decision_id = history.record(
            epoch_id=bundle["epoch_id"], head_sha=bundle["head_sha"],
            verdict="EVIDENCE_INVALIDATED", bundle_hash=None,
            bundle_schema=bundle["bundle_version"], decision_rule_revision=RULE,
            auth_generation=bundle["auth_generation"], decided_at=detected_at,
            cause="newer_provider_request_generation",
            invalidates_decision_id=standing["decision_id"] if standing else None,
            invalidates_bundle_hash=bundle["evidence_hash"])
        output = summary_for(
            state, bundle, "EVIDENCE_INVALIDATED",
            extra=("Cause: a newer provider request generation exists for "
                   f"{', '.join(sorted(newer))}; the previous evidence bundle "
                   f"{bundle['evidence_hash'][:16]}… is no longer current. The "
                   "Governor does not wait for the new review's outcome."))
        status, body = repo.conclude_check(state["check_run_id"], "failure", output)
        revoked_at = gh.utcnow()
        history.project(bundle["epoch_id"], bundle["head_sha"], body["id"],
                        body.get("conclusion"), decision_id, revoked_at)
        state.setdefault("revocations", []).append({
            "label": args.label, "detected_at": detected_at,
            "revoked_at": revoked_at, "newer": newer,
            "decision_id": decision_id, "conclusion": body.get("conclusion")})
        save(args.state, state)
        return {"superseded": True, "decision_id": decision_id,
                "newer_generations": newer, "http_status": status,
                "check_run_id": body["id"], "head_sha": body.get("head_sha"),
                "conclusion": body.get("conclusion"),
                "detected_at": detected_at, "revoked_at": revoked_at}
    finally:
        history.close()


def cmd_headchange(args):
    """Old epoch STALE, its run cancelled and still bound to the old head;
    a new epoch opens on the new head and fails closed."""
    state = load(args.state)
    repo = gh.Repo(state["repo"])
    history = dec.History(args.db)
    try:
        pull = repo.pull_request(state["pr_number"])
        new_head = pull["head"]["sha"]
        if new_head == state["head_sha"]:
            return {"changed": False, "reason": "head has not moved"}
        old_bundle = state["bundles"][args.label]

        stale_id = history.record(
            epoch_id=old_bundle["epoch_id"], head_sha=state["head_sha"],
            verdict="STALE", bundle_hash=None, bundle_schema=old_bundle["bundle_version"],
            decision_rule_revision=RULE, auth_generation=state["auth"]["generation"],
            decided_at=gh.utcnow(), cause="head_superseded",
            invalidates_bundle_hash=old_bundle["evidence_hash"],
            invalidates_decision_id=(history.latest_success() or {})["decision_id"]
            if history.latest_success() else None)
        status_old, old_body = repo.conclude_check(
            state["check_run_id"], "cancelled",
            summary_for(state, old_bundle, "STALE",
                        extra=f"Superseded by head {new_head}."))
        history.project(old_bundle["epoch_id"], state["head_sha"], old_body["id"],
                        old_body.get("conclusion"), stale_id, gh.utcnow())
        state["epoch"]["state"] = "STALE"

        new_epoch_id = "epoch-" + hashlib.sha256(
            f"{state['repo_id']}:{state['pr_number']}:{new_head}".encode()
        ).hexdigest()[:16]
        empty_bundle = {"epoch_id": new_epoch_id, "head_sha": new_head,
                        "evidence_hash": "-" * 0 or "none",
                        "decision_rule_revision": RULE,
                        "auth_generation": state["auth"]["generation"],
                        "bundle_version": qualify.BUNDLE_VERSION,
                        "observations": {"codex": {"state": "ABSENT"},
                                         "coderabbit": {"state": "ABSENT"}}}
        new_id = history.record(
            epoch_id=new_epoch_id, head_sha=new_head, verdict="NOT_ESTABLISHED",
            bundle_hash=None, bundle_schema=qualify.BUNDLE_VERSION,
            decision_rule_revision=RULE, auth_generation=state["auth"]["generation"],
            decided_at=gh.utcnow(), cause="no provider evidence on the new head")
        run = repo.create_check(new_head, new_epoch_id,
                                summary_for(state, empty_bundle, "NOT_ESTABLISHED"))
        status_new, new_body = repo.conclude_check(
            run["id"], "failure",
            summary_for(state, empty_bundle, "NOT_ESTABLISHED"))
        history.project(new_epoch_id, new_head, new_body["id"],
                        new_body.get("conclusion"), new_id, gh.utcnow())
        state["new_epoch"] = {"epoch_id": new_epoch_id, "head_sha": new_head,
                              "check_run_id": new_body["id"]}
        save(args.state, state)
        return {"changed": True,
                "old": {"check_run_id": old_body["id"],
                        "head_sha": old_body.get("head_sha"),
                        "conclusion": old_body.get("conclusion"),
                        "http_status": status_old},
                "new": {"check_run_id": new_body["id"],
                        "head_sha": new_body.get("head_sha"),
                        "conclusion": new_body.get("conclusion"),
                        "external_id": new_body.get("external_id"),
                        "http_status": status_new}}
    finally:
        history.close()


def cmd_state(args):
    history = dec.History(args.db)
    try:
        return {"chain": history.as_json(),
                "projections": [dict(p) for p in history.projections()],
                "replayed": history.replay()}
    finally:
        history.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["freeze", "trigger", "observe", "bundle",
                                        "settle", "publish", "supersede",
                                        "headchange", "state"])
    ap.add_argument("--repo", default="PhysShell/evm-from-scratch")
    ap.add_argument("--pr", type=int)
    ap.add_argument("--state", default=STATE_DEFAULT)
    ap.add_argument("--db", default=DB_DEFAULT)
    ap.add_argument("--generation", type=int, default=1)
    ap.add_argument("--only", default=None)
    ap.add_argument("--label", default="bundle_1")
    ap.add_argument("--seconds", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    result = {"freeze": cmd_freeze, "trigger": cmd_trigger, "observe": cmd_observe,
              "bundle": cmd_bundle, "settle": cmd_settle, "publish": cmd_publish,
              "supersede": cmd_supersede, "headchange": cmd_headchange,
              "state": cmd_state}[args.command](args)
    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
