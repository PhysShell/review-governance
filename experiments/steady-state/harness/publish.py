"""Production projection: the only path that may publish a passing verdict.

Kept separate from `governor.py` on purpose. That module can publish
success but is hardcoded to `ai/final-review-readiness-probe`, and its
evidence schema is `ReadinessProbeEvidence-v1`. Renaming its context would
have made a probe instrument into a production gate by editing a string —
"it almost fits already" is the sentence that precedes most of the defects
this programme has recorded.

Three guards stand between a reduced verdict and a green check, and each
one exists because of a specific earlier finding:

    the reducer must have returned SUCCESS          (evidence.py)
    the bundle's head must still be the current head, re-read immediately
      before the write                              (A3a: stale evidence
                                                     reads clean)
    authorization must permit it                    (A1c: a lost user
                                                     authorization must
                                                     never publish a pass)

and the write is never the confirmation: an independent GET of that exact
run decides (A3b-c4).
"""
import datetime

import auth_policy
import health as health_mod

PRODUCTION_CONTEXT = "ai/final-review"
GOVERNOR_APP_ID = 4669438
PASSING = frozenset({"success", "neutral", "skipped"})
PUBLISHABLE = frozenset({"success", "failure"})
FORBIDDEN = frozenset({"neutral", "skipped"})


class PublishRefused(Exception):
    """Raised where a passing conclusion would be published unguarded."""


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def guard(*, reduction, bundle, current_head_sha, permission, health=None,
          existing_run=None):
    """Evaluated immediately before the write, never earlier.

    A guard checked at the start of a round and trusted at the end is a
    guard about a moment that has passed.
    """
    # Refused here rather than deeper down, so a caller that lost the
    # provenance fails at the place it lost it.
    permission = auth_policy.require(permission)
    refusals = []
    if reduction.get("verdict") != "SUCCESS":
        refusals.append(f"reducer returned {reduction.get('verdict')}")
    if bundle.get("head_sha") != current_head_sha:
        refusals.append("bundle head is no longer the current head")
    if reduction.get("head_sha") != current_head_sha:
        refusals.append("reduction was computed for another head")
    if not permission.permits_action:
        refusals.append(
            f"authorization permission is {permission.state} "
            f"(age={permission.age_seconds}s): {permission.cause}")
    if not existing_run:
        refusals.append(
            "no existing scoped failure run to patch; a success is a "
            "transition of the carrier the gate already consults, never a "
            "second carrier on the same head")
    # An absent health set used to pass exactly like a healthy one.
    if health is None:
        refusals.append(
            "no runtime health evaluation supplied; absence of a signal is "
            "not the presence of a healthy one")
    else:
        missing = [n for n in health_mod.REQUIRED
                   if n not in (health.get("observations") or {})]
        if missing:
            refusals.append(f"required health signals missing: {missing}")
        # A health set evaluated without the candidate answers "is the
        # reconciler alive", not "did it compare this PR at this head".
        if not health.get("candidate_bound"):
            refusals.append(
                "health was evaluated without the candidate; a success needs "
                "the reconciliation of this PR at this head, not a recent "
                "pass over somebody else's")
        elif (health.get("candidate") or {}).get("head_sha") != current_head_sha:
            refusals.append(
                "health was evaluated for another head "
                f"({(health.get('candidate') or {}).get('head_sha')})")
        for name in sorted(health.get("not_fresh") or []):
            obs = (health.get("observations") or {}).get(name, {})
            refusals.append(
                f"{name} health is {obs.get('state')} "
                f"(age={obs.get('age_seconds')}s, bound={obs.get('bound')}s)"
                + (f": {obs['cause']}" if obs.get("cause") else ""))
    return {"may_publish_success": not refusals, "refusals": refusals,
            "authorization": permission.as_dict()}


def preread_carrier(request, *, repo, run_id, head_sha, epoch_id,
                    intended="success"):
    """Prove the target is ours BEFORE mutating it.

    The identity check used to run on the readback, which is a fine proof
    of what happened and a useless guard against it: given a wrong run id
    the Governor would PATCH somebody else's carrier and then discover, in
    detail, that it had. A mutation guard has to precede the mutation.

    Also counts the `ai/final-review` runs on the head from this App: the
    steady-state design has exactly one applicable carrier, and patching
    one of several would leave the gate reading a verdict nobody chose.

    The starting conclusion is required only for a success, because that is
    the transition the design is about — a pass is the promotion of the
    exact failure the gate is already reading. Re-stating a failure on our
    own carrier is maintenance, not a transition.
    """
    status, run = request("GET", f"/repos/{repo}/check-runs/{run_id}", None)
    if status != 200 or not run:
        return {"may_patch": False, "http_status": status,
                "refusals": [f"cannot read check run {run_id} before writing "
                             f"to it (status {status})"]}
    identity = {
        "run_id": run.get("id") == run_id,
        "name": run.get("name") == PRODUCTION_CONTEXT,
        "app_id": (run.get("app") or {}).get("id") == GOVERNOR_APP_ID,
        "head_sha": run.get("head_sha") == head_sha,
        "external_id": run.get("external_id") == epoch_id,
    }
    if intended in PASSING:
        identity["conclusion_is_failure"] = run.get("conclusion") == "failure"
    refusals = [f"pre-write identity mismatch: {k}" for k, v in identity.items()
                if not v]

    runs_status, runs = request(
        "GET", f"/repos/{repo}/commits/{head_sha}/check-runs?per_page=100", None)
    ours = [r for r in ((runs or {}).get("check_runs") or [])
            if (r.get("app") or {}).get("id") == GOVERNOR_APP_ID
            and r.get("name") == PRODUCTION_CONTEXT
            and r.get("head_sha") == head_sha]
    if runs_status != 200:
        refusals.append(
            f"cannot list the check runs on {head_sha[:12]} (status "
            f"{runs_status}); the carrier count is unknown")
    elif len(ours) != 1:
        refusals.append(
            f"{len(ours)} ai/final-review runs on this head from app "
            f"{GOVERNOR_APP_ID}; exactly one applicable carrier is required")
    elif ours[0].get("id") != run_id:
        refusals.append(
            f"the single applicable carrier on this head is {ours[0].get('id')}, "
            f"not {run_id}")
    return {"may_patch": not refusals, "refusals": refusals,
            "identity": identity, "carrier_count": len(ours),
            "http_status": status}


def summary_for(verdict, bundle):
    return "\n".join([
        f"Governor verdict: {verdict}",
        f"Head: {bundle['head_sha']}",
        f"Evidence: {bundle['schema']} {bundle['bundle_hash']}",
        "",
        "Derived by the Governor from advisory provider evidence bound to",
        "this exact head. The provider carriers are not promoted into",
        "authoritative provenance.",
    ])


def publish(request, *, repo, epoch_id, head_sha, conclusion, bundle,
            reduction, current_head_sha, permission, store, existing_run=None,
            health=None):
    """Publish, then believe the readback rather than the write.

    A success may only PATCH the existing scoped failure run. Creating a
    second carrier on a head is forbidden here even though the API allows
    it: the whole steady-state runtime is built around exactly one
    applicable carrier, and an optional "or post a new one" path would
    quietly undo that.
    """
    if conclusion in FORBIDDEN:
        raise PublishRefused(
            f"{conclusion} can read as passing downstream and is excluded "
            "by construction")
    if conclusion not in PUBLISHABLE:
        raise PublishRefused(f"conclusion {conclusion!r} is not publishable")
    if conclusion in PASSING:
        checked = guard(reduction=reduction, bundle=bundle,
                        current_head_sha=current_head_sha,
                        permission=permission, health=health,
                        existing_run=existing_run)
        if not checked["may_publish_success"]:
            raise PublishRefused(
                "pre-publication guard refused: " + "; ".join(checked["refusals"]))

    # Before any durable decision or projection, and before the write: is
    # the thing we are about to change the thing we think it is?
    preread = None
    if existing_run:
        preread = preread_carrier(request, repo=repo, run_id=existing_run,
                                  head_sha=head_sha, epoch_id=epoch_id,
                                  intended=conclusion)
        if not preread["may_patch"]:
            raise PublishRefused(
                "pre-write carrier identity refused, nothing was written: "
                + "; ".join(preread["refusals"]))

    verdict = reduction.get("verdict", "NOT_ESTABLISHED")
    decision_id = store.record_decision(
        epoch_id=epoch_id, verdict=verdict, decided_at=utcnow(),
        bundle_hash=bundle.get("bundle_hash"),
        bundle_schema=bundle.get("schema"),
        cause="production projection")
    store.project(epoch_id=epoch_id, check_run_id=existing_run,
                  intended=conclusion, state="PENDING",
                  decision_id=decision_id, at=utcnow())

    body = {"name": PRODUCTION_CONTEXT, "head_sha": head_sha,
            "status": "completed", "conclusion": conclusion,
            "completed_at": utcnow(), "external_id": epoch_id,
            "output": {"title": f"Governor: {verdict}",
                       "summary": summary_for(verdict, bundle)}}
    if existing_run:
        write_status, _ = request(
            "PATCH", f"/repos/{repo}/check-runs/{existing_run}", body)
        run_id = existing_run
    elif conclusion in PASSING:
        raise PublishRefused(
            "a passing conclusion may only patch an existing scoped run")
    else:
        write_status, created = request(
            "POST", f"/repos/{repo}/check-runs", body)
        run_id = (created or {}).get("id")

    if not run_id:
        store.project(epoch_id=epoch_id, check_run_id=None,
                      intended=conclusion, state="OUTCOME_UNKNOWN",
                      decision_id=decision_id, at=utcnow())
        return {"state": "OUTCOME_UNKNOWN", "write_status": write_status,
                "retry_performed": False,
                "cause": "no run id after one write; not retrying"}

    read_status, readback = request(
        "GET", f"/repos/{repo}/check-runs/{run_id}", None)
    observed = (readback or {}).get("conclusion")
    # The conclusion alone is not the identity of the thing that changed.
    identity = {
        "run_id": (readback or {}).get("id") == run_id,
        "name": (readback or {}).get("name") == PRODUCTION_CONTEXT,
        "app_id": ((readback or {}).get("app") or {}).get("id") == GOVERNOR_APP_ID,
        "head_sha": (readback or {}).get("head_sha") == head_sha,
        "external_id": (readback or {}).get("external_id") == epoch_id,
        "conclusion": observed == conclusion,
    }
    settled = ("CONFIRMED" if read_status == 200 and all(identity.values())
               else "OUTCOME_UNKNOWN" if read_status != 200 else "FAILED")
    store.project(epoch_id=epoch_id, check_run_id=run_id, intended=conclusion,
                  observed=observed, state=settled, decision_id=decision_id,
                  at=utcnow())
    return {"state": settled, "check_run_id": run_id, "intended": conclusion,
            "observed": observed, "identity": identity,
            "preread": preread, "write_status": write_status,
            "decision_id": decision_id, "retry_performed": False,
            "app_id": ((readback or {}).get("app") or {}).get("id"),
            "head_sha": (readback or {}).get("head_sha")}
