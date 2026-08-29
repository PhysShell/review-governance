"""Steady-state production carrier lifecycle.

A5b's bootstrap did everything right except stop being a bootstrap: it is
bound to a frozen activation inventory, so it can establish a carrier once
and never again. Everything it got right is kept here; the one-shot
binding is what goes.

    exact current HEAD
      ├── carriers unreadable    -> OUTCOME_UNKNOWN, no write
      ├── >1 applicable          -> AMBIGUOUS, no write
      ├── exactly 1 valid        -> ADOPTED, by reading, no write
      └── 0                      -> durable decision, PENDING projection,
                                    exactly ONE POST, independent readback

Adoption matters as much as creation. A producer that posts whenever it is
run accumulates duplicate carriers on a head, and a head carrying two
verdicts from the same App shows an operator two answers and no way to
tell which one the gate consulted.

Capability is enforced in code, not intention: this module can create a
check run in exactly one context with exactly one conclusion. It cannot
publish anything passing, which is why it is separate from the projection
that can.
"""
import datetime

PRODUCTION_CONTEXT = "ai/final-review"
GOVERNOR_APP_ID = 4669438
ONLY_CONCLUSION = "failure"
VERDICT = "NOT_ESTABLISHED"

SUMMARY = "\n".join([
    f"Governor verdict: {VERDICT}",
    "",
    "No final-review evidence has been established for this head.",
    "Fresh qualification is required before this check can pass.",
    "",
    "This is not a review result. No provider round has been started, and",
    "none starts without an explicit ACCEPT-CANDIDATE transition.",
])


class CarrierCapability(Exception):
    """Raised where this module is asked to step outside its one job."""


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def guarded(request, method, path, token, body=None):
    """The capability boundary. `request` is injected so it is testable
    without pretending a network exists."""
    if method == "GET":
        return request(method, path, token, body)
    if method != "POST" or not path.endswith("/check-runs"):
        raise CarrierCapability(
            f"carrier producer may not {method} {path}")
    if (body or {}).get("name") != PRODUCTION_CONTEXT:
        raise CarrierCapability(
            f"may not write the name {(body or {}).get('name')!r}")
    if (body or {}).get("conclusion") != ONLY_CONCLUSION:
        raise CarrierCapability(
            f"may not set conclusion {(body or {}).get('conclusion')!r}: "
            "this producer can only fail closed")
    return request(method, path, token, body)


def applicable(run, head_sha):
    """A carrier applies to a head only if it is bound to that head.

    Evidence does not migrate when a branch moves. A run on the previous
    head is evidence about the previous commit, which is exactly why a
    moved head must fail closed rather than inherit.
    """
    return (run.get("head_sha") == head_sha
            and run.get("name") == PRODUCTION_CONTEXT
            and (run.get("app") or {}).get("id") == GOVERNOR_APP_ID)


def valid_failure(run, head_sha):
    return (applicable(run, head_sha)
            and run.get("conclusion") == ONLY_CONCLUSION
            and VERDICT in ((run.get("output") or {}).get("summary") or ""))


def read_carriers(request, repo, head_sha, token):
    status, body = guarded(
        request, "GET", f"/repos/{repo}/commits/{head_sha}/check-runs?per_page=100",
        token)
    if status != 200:
        return None
    return [r for r in (body or {}).get("check_runs", [])
            if applicable(r, head_sha)]


def ensure(request, repo, pr_number, head_sha, token, store):
    """Establish exactly one confirmed failure carrier on this exact head."""
    runs = read_carriers(request, repo, head_sha, token)
    if runs is None:
        return {"state": "OUTCOME_UNKNOWN", "wrote": False,
                "cause": "carriers unreadable; presence not established"}
    if len(runs) > 1:
        return {"state": "AMBIGUOUS", "wrote": False,
                "carriers": [r["id"] for r in runs],
                "cause": f"{len(runs)} production carriers on one head"}
    if len(runs) == 1:
        run = runs[0]
        if not valid_failure(run, head_sha):
            return {"state": "MISMATCH", "wrote": False,
                    "carrier": run["id"],
                    "observed_conclusion": run.get("conclusion"),
                    "cause": "a carrier exists but is not this producer's "
                             "fail-closed verdict; adoption would claim "
                             "authorship of somebody else's state"}
        return {"state": "ADOPTED", "wrote": False, "carrier": run["id"],
                "head_sha": head_sha,
                "cause": "exactly one valid carrier already present; read, "
                         "not rewritten"}

    # zero carriers: decide durably first, then write exactly once
    epoch = store.open_epoch(repo=repo, pr_number=pr_number,
                             head_sha=head_sha, opened_at=utcnow())
    decision_id = store.record_decision(
        epoch_id=epoch["epoch_id"], verdict=VERDICT, decided_at=utcnow(),
        cause="steady-state: no evidence established for this head")
    store.project(epoch_id=epoch["epoch_id"], check_run_id=None,
                  intended=ONLY_CONCLUSION, state="PENDING",
                  decision_id=decision_id, at=utcnow())

    post_status, _ = guarded(
        request, "POST", f"/repos/{repo}/check-runs", token,
        {"name": PRODUCTION_CONTEXT, "head_sha": head_sha,
         "status": "completed", "conclusion": ONLY_CONCLUSION,
         "completed_at": utcnow(), "external_id": epoch["epoch_id"],
         "output": {"title": f"Governor: {VERDICT}", "summary": SUMMARY}})

    after = read_carriers(request, repo, head_sha, token)
    if after is None:
        store.project(epoch_id=epoch["epoch_id"], check_run_id=None,
                      intended=ONLY_CONCLUSION, state="OUTCOME_UNKNOWN",
                      decision_id=decision_id, at=utcnow())
        return {"state": "OUTCOME_UNKNOWN", "wrote": True,
                "epoch_id": epoch["epoch_id"], "post_status": post_status,
                "retry_performed": False,
                "cause": "readback failed after one POST; not retrying"}
    matching = [r for r in after if valid_failure(r, head_sha)]
    if len(matching) == 1:
        run = matching[0]
        store.project(epoch_id=epoch["epoch_id"], check_run_id=run["id"],
                      intended=ONLY_CONCLUSION, observed=run.get("conclusion"),
                      state="CONFIRMED", decision_id=decision_id, at=utcnow())
        return {"state": "CONFIRMED", "wrote": True, "carrier": run["id"],
                "epoch_id": epoch["epoch_id"], "head_sha": head_sha,
                "post_status": post_status, "retry_performed": False}
    store.project(epoch_id=epoch["epoch_id"], check_run_id=None,
                  intended=ONLY_CONCLUSION, state="OUTCOME_UNKNOWN",
                  decision_id=decision_id, at=utcnow())
    return {"state": "OUTCOME_UNKNOWN", "wrote": True,
            "epoch_id": epoch["epoch_id"], "post_status": post_status,
            "matching": [r["id"] for r in matching], "retry_performed": False,
            "cause": f"{len(matching)} matching carriers after one POST; a "
                     "second write would turn a lost response into a "
                     "duplicate or a second unknown"}
