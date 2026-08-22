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


def newer_request_generations(bundle, inventory):
    """A newer request for a mandatory provider means the frozen bundle is no
    longer current — regardless of what that new request eventually says.

    The baseline is taken from the **bundle**, never from live state: if it
    came from live state, issuing a new request would quietly become its own
    baseline and erase the evidence that anything is newer (correction
    A3b-c1).
    """
    newer = {}
    for provider, pattern in TRIGGER_PATTERNS.items():
        baseline_id = bundle["observations"][provider]["request_comment_id"]
        baseline = next((c for c in inventory["issue_comments"]
                         if c["id"] == baseline_id), None)
        if baseline is None:
            newer[provider] = "baseline request comment no longer visible"
            continue
        for comment in inventory["issue_comments"]:
            if comment["id"] == baseline_id:
                continue
            if qualify.carrier_of(comment) != "app_mediated_user":
                continue
            if pattern.search(comment.get("body") or "") and \
                    comment["created_at"] > baseline["created_at"]:
                newer[provider] = comment["id"]
    return newer


def build_bundle(epoch, head_sha, auth_generation, requests, observations,
                 cutoff):
    """The single canonical bundle builder for A3b.

    One function, used by both construction and every re-verification —
    otherwise "the evidence hash recomputes" only ever proves that two
    slightly different builders happened to agree.
    """
    payload = {
        "bundle_version": qualify.BUNDLE_VERSION,
        "epoch_id": epoch["epoch_id"],
        "epoch_generation": epoch["generation"],
        "head_sha": head_sha,
        "auth_generation": auth_generation,
        "decision_rule_revision": RULE,
        "requests": requests,
        "observations": observations,
        "inventory_cutoff": cutoff,
    }
    payload["evidence_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return payload


def apply_projection(repo, history, *, epoch_id, head_sha, check_run_id,
                     conclusion, output, decision_id, evidence_hash=None):
    """Durable decision -> PENDING -> PATCH -> independent readback -> settle.

    The PATCH response is never treated as confirmation (A3b-c4). Only a
    separate GET of that exact run can move a projection to CONFIRMED, and
    an indeterminate write settles to OUTCOME_UNKNOWN rather than to
    anything optimistic.
    """
    attempted_at = gh.utcnow()
    history.project_pending(epoch_id, head_sha, check_run_id, conclusion,
                            decision_id, attempted_at)
    write = {"attempted_at": attempted_at, "http_status": None, "error": None}
    try:
        status, body = repo.conclude_check(check_run_id, conclusion, output,
                                           evidence_hash=evidence_hash)
        write["http_status"] = status
    except Exception as exc:                      # indeterminate write
        write["error"] = f"{type(exc).__name__}: {exc}"
        body = None

    readback_status, readback = repo.get_check(check_run_id)
    observed = (readback or {}).get("conclusion")
    if readback_status != 200:
        settled = "OUTCOME_UNKNOWN"
    elif observed == conclusion:
        settled = "CONFIRMED"
    else:
        settled = "FAILED"
    history.settle_projection(epoch_id, state=settled,
                              observed_conclusion=observed, at=gh.utcnow())
    return {"projection_state": settled, "intended": conclusion,
            "observed": observed, "readback_status": readback_status,
            "write": write, "readback": readback}


def governor_authorization(history, epoch_id):
    """Two different questions that must never share one variable (A3b-c4).

    `external_success_may_exist` is about the *outside world*: GitHub may be
    showing a green check right now, so the Governor must clean up before
    doing anything that would make that green misleading.

    `effective_gate_validity` is about *Governor policy*: it is
    ESTABLISHED only when a success is durably decided AND confirmed by an
    independent readback. Every uncertain projection is NOT_ESTABLISHED —
    fail closed.

    Keeping these apart matters because a single ambiguously named flag
    eventually gets read as permission. `may_authorize_action` is the only
    field that may ever gate an action, and it is true only for a confirmed
    success.
    """
    latest = None
    for row in history.chain():
        if row["epoch_id"] == epoch_id:
            latest = row
    projection = history.projection(epoch_id)
    state = projection["state"] if projection else None
    observed = projection["observed_conclusion"] if projection else None
    is_success_decision = bool(latest) and latest["verdict"] == "SUCCESS"

    confirmed_success = (is_success_decision and state == "CONFIRMED"
                         and observed == "success")
    uncertain = state in ("PENDING", "OUTCOME_UNKNOWN")
    may_exist_externally = bool(confirmed_success
                                or (is_success_decision and uncertain)
                                or (uncertain and observed == "success"))

    if confirmed_success:
        validity, hazard = "ESTABLISHED", None
    elif uncertain:
        validity = "NOT_ESTABLISHED"
        hazard = ("projection unsettled: GitHub may still physically show a "
                  "success, and this is neither an established success nor an "
                  "established revocation")
    else:
        validity, hazard = "NOT_ESTABLISHED", None

    return {
        "external_success_may_exist": may_exist_externally,
        "effective_gate_validity": validity,
        "projection_state": state,
        "hazard": hazard,
        "may_authorize_action": confirmed_success,
        "decision": dict(latest) if latest else None,
    }


def standing_success(history, epoch_id):
    """Fail-closed guard for *write ordering*: returns the decision whenever a
    success may still be visible in GitHub, including while its projection is
    unresolved. This answers "must I clean up first?", never "may I proceed?"
    — that second question belongs to `may_authorize_action`.
    """
    status = governor_authorization(history, epoch_id)
    return status["decision"] if status["external_success_may_exist"] else None


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

    recomputed = build_bundle(
        {"epoch_id": bundle["epoch_id"],
         "generation": bundle["epoch_generation"]},
        bundle["head_sha"], bundle["auth_generation"], bundle["requests"],
        bundle["observations"], bundle["inventory_cutoff"])
    if recomputed["evidence_hash"] != bundle["evidence_hash"]:
        failures.append("evidence hash does not recompute")

    for provider in ("codex", "coderabbit"):
        if not fresh[provider]["qualified"]:
            failures.extend(f"{provider}: {r}" for r in fresh[provider]["reasons"])

    mutation = qualify.detect_mutation(bundle, fresh)
    if not mutation["stable"]:
        failures.extend(mutation["changes"])

    newer = newer_request_generations(bundle, inventory)
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
    history = dec.History(args.db)
    try:
        standing = standing_success(history, state["epoch"]["epoch_id"])
    finally:
        history.close()
    if standing and not args.allow_standing_success:
        raise SystemExit(
            "refusing to post a provider request while a success stands for "
            f"epoch {state['epoch']['epoch_id']} (decision "
            f"{standing['decision_id']}). Use `rerun`, which extinguishes and "
            "confirms the failure first (A3b-c3).")
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
    bundle = build_bundle(
        state["epoch"], state["head_sha"], state["auth"]["generation"],
        {p: {k: v for k, v in r.items() if k != "comment"}
         for p, r in state["requests"].items()},
        state["observations"], state["inventory"]["captured_at"])
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
        if not state.get("check_run_id"):
            run = repo.create_check(bundle["head_sha"], bundle["epoch_id"], output)
            state["check_run_id"] = run["id"]
        projection = apply_projection(
            repo, history, epoch_id=bundle["epoch_id"],
            head_sha=bundle["head_sha"], check_run_id=state["check_run_id"],
            conclusion="success", output=output, decision_id=decision_id,
            evidence_hash=bundle["evidence_hash"])
        github_at = gh.utcnow()
        body = projection["readback"] or {}
        status = projection["write"]["http_status"]

        post, _, _ = guard(state, repo, bundle, phase="post-publication")
        state["timings"][args.label] = {
            "pre_publish_validation_at": pre["finished_at"],
            "github_success_at": github_at,
            "post_publish_validation_at": post["finished_at"],
        }
        state["published"] = {"check_run_id": state["check_run_id"],
                              "conclusion": body.get("conclusion"),
                              "projection_state": projection["projection_state"],
                              "decision_id": decision_id,
                              "bundle_hash": bundle["evidence_hash"]}
        save(args.state, state)
        app = body.get("app") or {}
        return {"published": projection["projection_state"] == "CONFIRMED",
                "projection_state": projection["projection_state"],
                "decision_id": decision_id,
                "check_run_id": state["check_run_id"],
                "head_sha": body.get("head_sha"),
                "conclusion": body.get("conclusion"),
                "external_id": body.get("external_id"),
                "app": {"id": app.get("id"), "slug": app.get("slug")},
                "bundle_hash_in_output":
                    bundle["evidence_hash"] in (body.get("output") or {}).get("summary", ""),
                "readback_status": projection["readback_status"],
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
        bundle = state["bundles"][args.label]
        inventory = capture_inventory(repo, state["pr_number"])
        newer = newer_request_generations(bundle, inventory)
        if not newer:
            return {"superseded": False, "reason": "no newer generation observed"}
        detected_at = gh.utcnow()
        standing = history.latest_success()
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
        projection = apply_projection(
            repo, history, epoch_id=bundle["epoch_id"],
            head_sha=bundle["head_sha"], check_run_id=state["check_run_id"],
            conclusion="failure", output=output, decision_id=decision_id)
        revoked_at = gh.utcnow()
        body = projection["readback"] or {}
        status = projection["write"]["http_status"]
        state.setdefault("revocations", []).append({
            "label": args.label, "detected_at": detected_at,
            "revoked_at": revoked_at, "newer": newer,
            "decision_id": decision_id, "conclusion": body.get("conclusion")})
        save(args.state, state)
        return {"superseded": True, "decision_id": decision_id,
                "newer_generations": newer, "http_status": status,
                "projection_state": projection["projection_state"],
                "check_run_id": state["check_run_id"],
                "head_sha": body.get("head_sha"),
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
        old_projection = apply_projection(
            repo, history, epoch_id=old_bundle["epoch_id"],
            head_sha=state["head_sha"], check_run_id=state["check_run_id"],
            conclusion="cancelled", decision_id=stale_id,
            output=summary_for(state, old_bundle, "STALE",
                               extra=f"Superseded by head {new_head}."))
        old_body = old_projection["readback"] or {}
        status_old = old_projection["write"]["http_status"]
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
        new_projection = apply_projection(
            repo, history, epoch_id=new_epoch_id, head_sha=new_head,
            check_run_id=run["id"], conclusion="failure", decision_id=new_id,
            output=summary_for(state, empty_bundle, "NOT_ESTABLISHED"))
        new_body = new_projection["readback"] or {}
        status_new = new_projection["write"]["http_status"]
        state["new_epoch"] = {"epoch_id": new_epoch_id, "head_sha": new_head,
                              "check_run_id": new_body["id"]}
        save(args.state, state)
        return {"changed": True,
                "old": {"check_run_id": state["check_run_id"],
                        "head_sha": old_body.get("head_sha"),
                        "conclusion": old_body.get("conclusion"),
                        "http_status": status_old},
                "new": {"check_run_id": run["id"],
                        "head_sha": new_body.get("head_sha"),
                        "conclusion": new_body.get("conclusion"),
                        "external_id": new_body.get("external_id"),
                        "http_status": status_new}}
    finally:
        history.close()


def cmd_rerun(args):
    """A3b-c3: extinguish first, confirm, and only then ask for a new review.

    Ordering is the contract. The Governor never knowingly leaves a success
    standing after performing the act that makes its basis non-current.
    """
    state = load(args.state)
    repo = gh.Repo(state["repo"])
    history = dec.History(args.db)
    try:
        bundle = state["bundles"][args.label]
        epoch_id = bundle["epoch_id"]
        standing = standing_success(history, epoch_id)
        steps = []

        clock = {"monotonic_start": time.monotonic()}
        invalidated_at = gh.utcnow()
        clock["invalidation_decided_monotonic"] = time.monotonic()
        decision_id = history.record(
            epoch_id=epoch_id, head_sha=bundle["head_sha"],
            verdict="EVIDENCE_INVALIDATED", bundle_hash=None,
            bundle_schema=bundle["bundle_version"], decision_rule_revision=RULE,
            auth_generation=bundle["auth_generation"], decided_at=invalidated_at,
            cause="rerun_requested_pre_request_invalidation",
            invalidates_decision_id=standing["decision_id"] if standing else None,
            invalidates_bundle_hash=bundle["evidence_hash"])
        steps.append({"step": "durable_invalidation", "at": invalidated_at,
                      "decision_id": decision_id})

        projection = apply_projection(
            repo, history, epoch_id=epoch_id, head_sha=bundle["head_sha"],
            check_run_id=state["check_run_id"], conclusion="failure",
            decision_id=decision_id,
            output=summary_for(state, bundle, "EVIDENCE_INVALIDATED",
                               extra=("Cause: a rerun was requested. The "
                                      "standing success is extinguished and "
                                      "confirmed BEFORE the new provider "
                                      "request is created (A3b-c3).")))
        failure_confirmed_at = gh.utcnow()
        clock["failure_confirmed_monotonic"] = time.monotonic()
        steps.append({"step": "check_failure_projection",
                      "at": failure_confirmed_at,
                      "patch_attempted_at": projection["write"]["attempted_at"],
                      "projection_state": projection["projection_state"],
                      "observed": projection["observed"]})

        if projection["projection_state"] != "CONFIRMED":
            state.setdefault("reruns", []).append(
                {"label": args.label, "aborted": True, "steps": steps})
            save(args.state, state)
            return {"rerun": False,
                    "reason": "failure was not confirmed; no provider request "
                              "was created",
                    "steps": steps}

        # only now may the provider be asked for a new review
        request_started_at = gh.utcnow()
        outcome = {"state": "REQUEST_OUTCOME_UNKNOWN", "comment": None}
        try:
            comment = repo.comment_as_user(state["pr_number"], "@codex review")
            outcome = {"state": "REQUEST_CREATED", "comment": slim(comment)}
        except Exception as exc:
            outcome["error"] = f"{type(exc).__name__}: {exc}"
        clock["provider_request_monotonic"] = time.monotonic()
        steps.append({"step": "provider_request", "started_at": request_started_at,
                      "at": gh.utcnow(), "outcome": outcome["state"]})

        if outcome["state"] == "REQUEST_CREATED":
            state["requests"]["codex"] = {
                "provider_request_id":
                    f"{epoch_id}:codex:g{args.generation}",
                "request_generation": args.generation, "epoch_id": epoch_id,
                "auth_generation": state["auth"]["generation"],
                "head_at_request": state["head_sha"],
                "comment": outcome["comment"]}
        else:
            history.record(
                epoch_id=epoch_id, head_sha=bundle["head_sha"],
                verdict="EVIDENCE_INVALIDATED", bundle_hash=None,
                bundle_schema=bundle["bundle_version"],
                decision_rule_revision=RULE,
                auth_generation=bundle["auth_generation"], decided_at=gh.utcnow(),
                cause="request_outcome_unknown_check_remains_failure")

        state.setdefault("reruns", []).append(
            {"label": args.label, "aborted": False, "steps": steps,
             "outcome": outcome["state"]})
        save(args.state, state)
        ordering = {
            "invalidation_decided_at": invalidated_at,
            "failure_patch_attempted_at": projection["write"]["attempted_at"],
            "failure_confirmed_at": failure_confirmed_at,
            "provider_request_created_at":
                (outcome["comment"] or {}).get("created_at"),
            "monotonic_seconds": {
                "invalidation_to_failure_confirmed": round(
                    clock["failure_confirmed_monotonic"]
                    - clock["invalidation_decided_monotonic"], 3),
                "failure_confirmed_to_provider_request": round(
                    clock["provider_request_monotonic"]
                    - clock["failure_confirmed_monotonic"], 3),
            },
        }
        state.setdefault("orderings", {})[args.label] = ordering
        return {"rerun": True, "decision_id": decision_id,
                "ordering": ordering,
                "check_conclusion_before_request": projection["observed"],
                "projection_state": projection["projection_state"],
                "request_outcome": outcome["state"],
                "request_comment_id": (outcome["comment"] or {}).get("id"),
                "request_created_at": (outcome["comment"] or {}).get("created_at"),
                "steps": steps}
    finally:
        history.close()


def cmd_reconcile_projection(args):
    """Resolve PENDING / OUTCOME_UNKNOWN projections by reading the exact run.

    This is what distinguishes "GitHub accepted the write and the response
    was lost" from "the write never took effect" — and it never resolves
    upward into a success on its own.
    """
    state = load(args.state)
    repo = gh.Repo(state["repo"])
    history = dec.History(args.db)
    try:
        resolved = []
        for row in history.unsettled_projections():
            status, run = repo.get_check(row["check_run_id"])
            observed = (run or {}).get("conclusion")
            if status != 200:
                settled = "OUTCOME_UNKNOWN"
            elif observed == row["intended_conclusion"]:
                settled = "CONFIRMED"
            else:
                settled = "FAILED"
            history.settle_projection(row["epoch_id"], state=settled,
                                      observed_conclusion=observed,
                                      at=gh.utcnow())
            resolved.append({"epoch_id": row["epoch_id"],
                             "check_run_id": row["check_run_id"],
                             "intended": row["intended_conclusion"],
                             "observed": observed, "settled": settled})
        return {"reconciled": resolved,
                "note": "an unknown projection never becomes a success here; "
                        "it is only ever resolved by reading GitHub"}
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
                                        "rerun", "reconcile-projection",
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
    ap.add_argument("--allow-standing-success", action="store_true",
                    help="escape hatch used only by tests; never in a round")
    args = ap.parse_args()

    result = {"freeze": cmd_freeze, "trigger": cmd_trigger, "observe": cmd_observe,
              "bundle": cmd_bundle, "settle": cmd_settle, "publish": cmd_publish,
              "supersede": cmd_supersede, "headchange": cmd_headchange,
              "rerun": cmd_rerun,
              "reconcile-projection": cmd_reconcile_projection,
              "state": cmd_state}[args.command](args)
    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
