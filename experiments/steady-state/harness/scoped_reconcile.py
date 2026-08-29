"""PR-scoped reconciliation. The repair of the inert comparison.

The old `reconcile.last_known_head(history, repo, pr)` read neither `repo`
nor `pr`, and could not: the table had no column for either. It filtered
`epoch_id` on the `a5a-` prefix left over from fixtures, so with production
`bootstrap-` epochs it always returned `None`, and `drift_detected` was
always `False`. That is not a finding of no drift. It is a comparison that
never ran, printing a reassuring word.

The obvious repair would have been worse. Swapping the prefix makes
reversed history return the most recent bootstrap of *any* PR: asked about
#8 it answers with #12's head, and reconciliation reports drift on a branch
that never moved. Inert becomes actively wrong.

So the repair is scope, not a better filter, and the result is tri-state:

    RESOLVED    compared, and here is the answer
    NO_EPOCH    nothing was ever decided for this PR
    UNRESOLVED  something was, but its scope cannot be established

`drift_detected` is only ever reported alongside `comparison_performed`,
because the pair is what the old field pretended to be on its own.

Independence from the edge spool is unchanged: this reads GitHub.
"""
import datetime

import epochs as ep


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reconcile(request, repo, pr_number, store):
    started_at = utcnow()
    status, pull = request("GET", f"/repos/{repo}/pulls/{pr_number}", None)
    if status != 200:
        return {"state": "UNREADABLE", "repo": repo, "pr_number": pr_number,
                "at": started_at, "comparison_performed": False,
                "cause": f"cannot read PR: {status}"}
    github_head = pull["head"]["sha"]

    known = store.last_known_head(repo, pr_number)
    result = {
        "repo": repo, "pr_number": pr_number, "at": started_at,
        "github_head": github_head,
        "scope_state": known["state"],
        "source": "GitHub read; the edge delivery spool was not consulted",
    }

    if known["state"] == ep.UNRESOLVED:
        # The state that did not exist before. Fail closed: an unscoped
        # history cannot be said to agree with anything.
        result.update({"comparison_performed": False,
                       "drift_detected": None,
                       "stored_head": None,
                       "cause": known.get("cause"),
                       "required_action": "resolve scope before trusting any "
                                          "drift answer for this PR"})
        return result
    if known["state"] == ep.NO_EPOCH:
        result.update({"comparison_performed": False,
                       "drift_detected": None, "stored_head": None,
                       "cause": "no decision was ever recorded for this PR",
                       "current_head_is_unreviewed": True})
        return result

    stored = known["head_sha"]
    result.update({
        "comparison_performed": True,
        "stored_head": stored,
        "stored_repo": known["repo"],
        "stored_pr_number": known["pr_number"],
        "stored_epoch_id": known["epoch_id"],
        "drift_detected": stored != github_head,
        "scope_proven_by": "the epoch row carries repo and pr_number as part "
                           "of its identity, and both were matched",
    })

    runs_status, runs = request(
        "GET", f"/repos/{repo}/commits/{github_head}/check-runs?per_page=100",
        None)
    governor_runs = [
        {"id": r["id"], "conclusion": r.get("conclusion")}
        for r in ((runs or {}).get("check_runs") or [])
        if (r.get("app") or {}).get("id") == 4669438
        and r.get("name") == "ai/final-review"
        and r.get("head_sha") == github_head
    ] if runs_status == 200 else []
    result["governor_runs_on_current_head"] = governor_runs
    result["current_head_is_unreviewed"] = not governor_runs
    result["finished_at"] = utcnow()
    return result
