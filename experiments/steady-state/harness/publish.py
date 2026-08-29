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

PRODUCTION_CONTEXT = "ai/final-review"
GOVERNOR_APP_ID = 4669438
PASSING = frozenset({"success", "neutral", "skipped"})
PUBLISHABLE = frozenset({"success", "failure"})
FORBIDDEN = frozenset({"neutral", "skipped"})


class PublishRefused(Exception):
    """Raised where a passing conclusion would be published unguarded."""


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def guard(*, reduction, bundle, current_head_sha, authorized):
    """Evaluated immediately before the write, never earlier.

    A guard checked at the start of a round and trusted at the end is a
    guard about a moment that has passed.
    """
    refusals = []
    if reduction.get("verdict") != "SUCCESS":
        refusals.append(f"reducer returned {reduction.get('verdict')}")
    if bundle.get("head_sha") != current_head_sha:
        refusals.append("bundle head is no longer the current head")
    if reduction.get("head_sha") != current_head_sha:
        refusals.append("reduction was computed for another head")
    if not authorized:
        refusals.append("authorization does not permit a passing conclusion")
    return {"may_publish_success": not refusals, "refusals": refusals}


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
            reduction, current_head_sha, authorized, store, existing_run=None):
    """Publish, then believe the readback rather than the write."""
    if conclusion in FORBIDDEN:
        raise PublishRefused(
            f"{conclusion} can read as passing downstream and is excluded "
            "by construction")
    if conclusion not in PUBLISHABLE:
        raise PublishRefused(f"conclusion {conclusion!r} is not publishable")
    if conclusion in PASSING:
        checked = guard(reduction=reduction, bundle=bundle,
                        current_head_sha=current_head_sha,
                        authorized=authorized)
        if not checked["may_publish_success"]:
            raise PublishRefused(
                "pre-publication guard refused: " + "; ".join(checked["refusals"]))

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
    settled = ("CONFIRMED" if read_status == 200 and observed == conclusion
               else "OUTCOME_UNKNOWN" if read_status != 200 else "FAILED")
    store.project(epoch_id=epoch_id, check_run_id=run_id, intended=conclusion,
                  observed=observed, state=settled, decision_id=decision_id,
                  at=utcnow())
    return {"state": settled, "check_run_id": run_id, "intended": conclusion,
            "observed": observed, "write_status": write_status,
            "decision_id": decision_id, "retry_performed": False,
            "app_id": ((readback or {}).get("app") or {}).get("id"),
            "head_sha": (readback or {}).get("head_sha")}
