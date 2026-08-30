"""The live reading. Four GETs, no callbacks, no semantic parameters.

A6f-c3 made the acceptance writer re-derive the gate from a durable row
instead of taking a caller's result. It did not make the row a reading:
`observe()` performed one `GET /pulls/{n}` and then wrote down whatever
`ruleset_verified_fn()` and `carrier_fn()` returned. An immutable lie is
still a lie; it is just indexed.

    A durable observation is not an observation merely because somebody
    wrote it into the observations table.

So this module performs every read the gate depends on, and stores what
came back rather than a conclusion about it. Nothing here takes a boolean.

Four readings:

    pull        state, draft, head, base ref, and the base branch's
                current SHA — read, not assumed from the ref name
    ancestry    `compare/{base_sha}...{head}`; the candidate contains the
                current base only if the merge base *is* that base commit
    ruleset     the exact ruleset object, projected onto the policy-bearing
                keys and hashed
    carrier     the check runs on that exact head, filtered to this App and
                context, and required to be exactly one

**What this identity cannot see.** `bypass_actors` is absent from the
Governor App's view of a ruleset and present as `[]` under the owner token
— confirmed against `21640654` on 2026-08-30. So the pinned
`ACTIVE_RULESET_HASH`, computed from the owner's view, is unreproducible
here by construction. Rather than quietly hash a different set of keys and
call it a match, the projection this identity *can* see is pinned
separately, and the absence of a bypass observation is recorded as
`UNOBSERVABLE_BY_RUNTIME_IDENTITY`. An empty bypass list is a real
precondition and it is not established by a reader that cannot see the
field; the sentinel checks it with the owner credential instead.
"""
import datetime
import hashlib
import json

PRODUCTION_CONTEXT = "ai/final-review"
GOVERNOR_APP_ID = 4669438
PRODUCTION_RULESET_ID = 21640654
PRODUCTION_BASE = "main"

#: Policy-bearing keys the Governor App is able to read back.
APP_VISIBLE_KEYS = ("name", "target", "enforcement", "conditions", "rules")

#: Everything the runtime can see, pinned. Derived from the canonical
#: object in `cutover.py` minus `bypass_actors`, which this identity is not
#: shown. Recomputed and asserted against the canonical source in the
#: tests, so it cannot drift away from the reviewed policy.
APP_VISIBLE_RULESET_HASH = (
    "d7862d47542833ead6cd93481e0bc7fd7b849e513d330c34ad5645e4bfef2d7c")

BYPASS_UNOBSERVABLE = "UNOBSERVABLE_BY_RUNTIME_IDENTITY"

RESOLVED = "RESOLVED"
UNREADABLE = "UNREADABLE"


class ObservationRefused(Exception):
    """Raised where a reading would be recorded without having been made."""


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_visible_ruleset(enforcement="active"):
    """The canonical policy, projected onto what this identity can read."""
    import sys
    from pathlib import Path
    p = str(Path(__file__).resolve().parents[2] / "operational-readiness" / "harness")
    if p not in sys.path:
        sys.path.insert(0, p)
    import cutover
    canonical = cutover.ruleset_with(enforcement)
    return {k: canonical[k] for k in APP_VISIBLE_KEYS if k in canonical}


def visible_hash(projection):
    return hashlib.sha256(
        json.dumps(projection, sort_keys=True).encode()).hexdigest()


def project_ruleset(readback):
    """Allowlist, never a denylist.

    A denylist would silently absorb any policy-bearing field GitHub adds
    later, which is how a hash stops meaning anything.
    """
    return {k: readback[k] for k in APP_VISIBLE_KEYS if k in readback}


def read_live(read, *, repo, pr_number, epoch_id,
              ruleset_id=PRODUCTION_RULESET_ID, base=PRODUCTION_BASE):
    """Perform the four readings. Returns facts, or an UNREADABLE report.

    `read(method, path) -> (status, parsed)` is the transport. It is the
    only thing injected; every semantic value below is computed here from
    what came back.
    """
    reads = []

    def get(path):
        status, body = read("GET", path)
        reads.append({"path": path, "status": status})
        return status, body

    status, pull = get(f"/repos/{repo}/pulls/{pr_number}")
    if status != 200 or not pull:
        return {"state": UNREADABLE, "cause": f"cannot read the PR ({status})",
                "reads": reads}
    head = pull["head"]["sha"]
    base_ref = pull["base"]["ref"]
    # The base branch's current commit, read rather than inferred from the
    # ref name. `base_ref == "main"` says which branch the PR targets; it
    # says nothing about whether the candidate contains what is on it.
    status, base_commit = get(f"/repos/{repo}/commits/{base_ref}")
    if status != 200 or not base_commit:
        return {"state": UNREADABLE,
                "cause": f"cannot read the base branch head ({status})",
                "reads": reads}
    base_sha = base_commit["sha"]

    status, comparison = get(f"/repos/{repo}/compare/{base_sha}...{head}")
    if status != 200 or not comparison:
        return {"state": UNREADABLE,
                "cause": f"cannot compare the candidate with its base ({status})",
                "reads": reads}
    merge_base = (comparison.get("merge_base_commit") or {}).get("sha")

    status, ruleset = get(f"/repos/{repo}/rulesets/{ruleset_id}")
    if status != 200 or not ruleset:
        return {"state": UNREADABLE,
                "cause": f"cannot read ruleset {ruleset_id} ({status})",
                "reads": reads}
    projection = project_ruleset(ruleset)

    status, runs = get(f"/repos/{repo}/commits/{head}/check-runs?per_page=100")
    if status != 200 or runs is None:
        return {"state": UNREADABLE,
                "cause": f"cannot read the check runs on {head[:12]} ({status})",
                "reads": reads}
    ours = [r for r in (runs.get("check_runs") or [])
            if (r.get("app") or {}).get("id") == GOVERNOR_APP_ID
            and r.get("name") == PRODUCTION_CONTEXT
            and r.get("head_sha") == head]
    carrier = ours[0] if len(ours) == 1 else None

    return {
        "state": RESOLVED,
        "observed_at": utcnow(),
        "repo": repo, "pr_number": int(pr_number), "epoch_id": epoch_id,
        # -- pull ---------------------------------------------------------
        "head_sha": head,
        "draft": bool(pull["draft"]),
        "pr_state": pull["state"],
        "base_ref": base_ref,
        "base_sha": base_sha,
        # -- ancestry -----------------------------------------------------
        "compare_status": comparison.get("status"),
        "merge_base_sha": merge_base,
        "behind_by": comparison.get("behind_by"),
        # The candidate contains the current base exactly when the merge
        # base is the current base commit. `status == "ahead"` says the
        # same thing; both are stored so a reader can check the agreement.
        "contains_current_base": merge_base == base_sha,
        # -- ruleset ------------------------------------------------------
        "ruleset_id": ruleset_id,
        "ruleset_enforcement": ruleset.get("enforcement"),
        "ruleset_visible_hash": visible_hash(projection),
        "ruleset_projection": projection,
        "ruleset_bypass": (ruleset["bypass_actors"]
                           if "bypass_actors" in ruleset
                           else BYPASS_UNOBSERVABLE),
        # -- carrier ------------------------------------------------------
        "carrier_count": len(ours),
        "carrier_run_id": (carrier or {}).get("id"),
        "carrier_status": (carrier or {}).get("status"),
        "carrier_conclusion": (carrier or {}).get("conclusion"),
        "carrier_external_id": (carrier or {}).get("external_id"),
        "carrier_head_sha": (carrier or {}).get("head_sha"),
        "reads": reads,
    }


# --- what the gate derives from those facts ---------------------------------

def ruleset_findings(facts, *, expected_hash=None, ruleset_id=PRODUCTION_RULESET_ID):
    """Why the production rule is, or is not, the reviewed one.

    Derived here from stored readbacks rather than stored as a boolean: a
    load-bearing `ruleset_verified` column is a conclusion nobody can
    re-check, and this programme has now retired three of those.
    """
    expected_hash = expected_hash or APP_VISIBLE_RULESET_HASH
    problems = []
    if int(facts.get("ruleset_id") or 0) != int(ruleset_id):
        problems.append(f"observation is about ruleset {facts.get('ruleset_id')}")
    if facts.get("ruleset_enforcement") != "active":
        problems.append(
            f"ruleset enforcement is {facts.get('ruleset_enforcement')!r}")
    if facts.get("ruleset_visible_hash") != expected_hash:
        problems.append(
            "the ruleset does not hash to the reviewed policy "
            f"({facts.get('ruleset_visible_hash')})")
    bypass = facts.get("ruleset_bypass")
    if bypass == BYPASS_UNOBSERVABLE:
        # Not treated as a failure, and not treated as proof either. The
        # empty-bypass precondition is established by the owner-credentialed
        # sentinel check, and this row records that the runtime identity
        # could not see it.
        pass
    elif bypass:
        problems.append(f"the ruleset has {len(bypass)} bypass actor(s)")
    return problems


def carrier_findings(facts, *, epoch_id=None):
    """Why the carrier on this head is, or is not, the one to transition."""
    problems = []
    count = facts.get("carrier_count")
    if count != 1:
        problems.append(
            f"{count} ai/final-review runs from app {GOVERNOR_APP_ID} on this "
            "head; exactly one applicable carrier is required")
        return problems
    if facts.get("carrier_head_sha") != facts.get("head_sha"):
        problems.append("the carrier is bound to a different head")
    if facts.get("carrier_status") != "completed":
        problems.append(f"carrier status is {facts.get('carrier_status')!r}")
    if facts.get("carrier_conclusion") != "failure":
        problems.append(
            f"carrier conclusion is {facts.get('carrier_conclusion')!r}; a "
            "success is the transition of an existing failure")
    expected_epoch = epoch_id if epoch_id is not None else facts.get("epoch_id")
    if expected_epoch is not None and \
            facts.get("carrier_external_id") != expected_epoch:
        problems.append(
            f"carrier external_id is {facts.get('carrier_external_id')!r}, "
            f"not the scoped epoch {expected_epoch!r}")
    return problems


def base_findings(facts):
    """`base_ref == "main"` was never the predicate it was used as.

    It says which branch the PR targets. The condition the gate needs is
    that the candidate contains what is currently on that branch, which
    requires the base commit and an ancestry read.
    """
    problems = []
    if facts.get("base_ref") != PRODUCTION_BASE:
        problems.append(
            f"PR targets {facts.get('base_ref')!r}, not {PRODUCTION_BASE!r}")
    if not facts.get("base_sha"):
        problems.append("the base branch head was not read")
    elif facts.get("merge_base_sha") != facts.get("base_sha"):
        problems.append(
            f"the candidate does not contain the current base: merge base is "
            f"{(facts.get('merge_base_sha') or '')[:12]}, base is "
            f"{(facts.get('base_sha') or '')[:12]} "
            f"(behind by {facts.get('behind_by')})")
    return problems
