#!/usr/bin/env python3
"""A5b step 4: create the production ruleset disabled, verify, then activate.

Runs under the **owner** token, because ruleset administration requires it
and the Governor App has no `administration` permission and must never be
given one. That asymmetry is deliberate: the runtime that publishes
verdicts cannot alter the rule that consults them.

Three things are enforced rather than intended.

**The mutator's response is never the confirmation.** Both the create and
the flip are believed only after an independent GET of the object, hashed
and compared against values frozen in the protocol.

**Ambiguity is cured by reading, never by writing again.** A create whose
response was lost may or may not have made a ruleset. Posting a second one
would leave two rules on `main` with the same name and no way to say which
one the gate consulted, so the recovery is an enumeration: exactly one
object of this name proceeds, zero or more than one stops the stage.

**A hash mismatch stops.** It is never repaired by editing the canonical
object to match what GitHub returned. That would convert "the policy is
what we reviewed" into "the policy is whatever arrived", which is the
whole substitution the three-hash split exists to expose.
"""
import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

import cutover

CANONICAL_KEYS = ("name", "target", "enforcement", "bypass_actors",
                  "conditions", "rules")


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gh(*args, body=None):
    """Owner token, via the CLI. Returns (ok, parsed_or_error)."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True,
                          input=json.dumps(body) if body is not None else None)
    if proc.returncode != 0:
        return False, {"stderr": proc.stderr.strip()[:400]}
    try:
        return True, json.loads(proc.stdout or "null")
    except ValueError:
        return False, {"unparseable": proc.stdout[:400]}


def normalize(readback: dict) -> dict:
    """Project a GitHub ruleset onto the canonical shape.

    Only the six policy-bearing keys survive. Everything GitHub adds — id,
    node_id, source, timestamps, _links — is metadata about the object, not
    the policy it expresses, and hashing it would make the frozen value
    unmatchable by construction.

    The projection is deliberately an explicit allowlist. A denylist would
    silently absorb any new policy-bearing field GitHub introduces later,
    which is exactly how a hash stops meaning anything.
    """
    return {k: readback[k] for k in CANONICAL_KEYS if k in readback}


def structural_diff(observed: dict, expected: dict):
    """What actually differs, so a STOP is diagnosable rather than a bare
    mismatch. Reported, never acted on."""
    diffs = []
    for key in sorted(set(observed) | set(expected)):
        if observed.get(key) != expected.get(key):
            diffs.append({"key": key, "expected": expected.get(key),
                          "observed": observed.get(key)})
    return diffs


def find_by_name(repo, name):
    """Every repository ruleset carrying this name. Returns None if the list
    itself could not be read — unreadable is not absence."""
    ok, body = gh("api", f"repos/{repo}/rulesets")
    if not ok:
        return None, body
    return [r for r in (body or []) if r.get("name") == name], None


def read_one(repo, ruleset_id):
    ok, body = gh("api", f"repos/{repo}/rulesets/{ruleset_id}")
    return (body if ok else None), (None if ok else body)


def verify(repo, ruleset_id, expected_enforcement):
    """Independent readback, normalized, hashed, compared."""
    readback, error = read_one(repo, ruleset_id)
    if readback is None:
        return {"state": "UNCERTAIN", "cause": "readback failed",
                "error": error}
    observed = normalize(readback)
    expected = cutover.ruleset_with(expected_enforcement)
    digests = cutover.hashes()
    observed_policy = cutover.policy_hash(observed)
    observed_full = cutover.canonical_hash(observed)
    expected_full = digests["DISABLED_RULESET_HASH" if
                            expected_enforcement == "disabled"
                            else "ACTIVE_RULESET_HASH"]
    result = {
        "ruleset_id": ruleset_id,
        "observed_enforcement": readback.get("enforcement"),
        "POLICY_HASH": {"observed": observed_policy,
                        "expected": digests["POLICY_HASH"],
                        "match": observed_policy == digests["POLICY_HASH"]},
        "FULL_HASH": {"observed": observed_full, "expected": expected_full,
                      "which": ("DISABLED_RULESET_HASH"
                                if expected_enforcement == "disabled"
                                else "ACTIVE_RULESET_HASH"),
                      "match": observed_full == expected_full},
    }
    result["state"] = ("VERIFIED" if result["POLICY_HASH"]["match"]
                       and result["FULL_HASH"]["match"] else "MISMATCH")
    if result["state"] == "MISMATCH":
        result["diff"] = structural_diff(observed, expected)
        result["required_action"] = (
            "STOP. Do not edit the canonical object to make the hash match: "
            "that converts 'the policy is what we reviewed' into 'the policy "
            "is whatever arrived'.")
    return result


def create_disabled(repo, name):
    """Create, then establish what exists by reading — not by trusting the
    create response."""
    existing, error = find_by_name(repo, name)
    if existing is None:
        return {"state": "UNCERTAIN",
                "cause": "cannot list rulesets; absence not established",
                "error": error}
    if existing:
        return {"state": "REFUSED",
                "cause": f"a ruleset named {name!r} already exists "
                         f"({[r['id'] for r in existing]})"}

    body = cutover.ruleset_with("disabled")
    ok, response = gh("api", "-X", "POST", f"repos/{repo}/rulesets",
                      "--input", "-", body=body)
    attempted_at = utcnow()

    after, error = find_by_name(repo, name)
    if after is None:
        return {"state": "UNCERTAIN", "attempted_at": attempted_at,
                "cause": "post-create enumeration failed; count unknown",
                "create_ok": ok}
    if len(after) == 1:
        return {"state": "CREATED", "attempted_at": attempted_at,
                "ruleset_id": after[0]["id"], "create_ok": ok,
                # The body is evidence, not debug output. A5b-r2 recreated
                # this object specifically so that
                # do_not_enforce_on_create=false would be an assertion of
                # the reviewed specification rather than a GitHub default
                # that happened to agree. That claim is only checkable if
                # what was asserted is recorded.
                "request_body": body,
                "create_response": (response if not ok else "not trusted")}
    return {"state": "UNCERTAIN", "attempted_at": attempted_at,
            "cause": f"{len(after)} rulesets named {name!r} after one POST; "
                     "NOT posting again — a second create would leave two "
                     "rules on main with no way to say which the gate used",
            "found": [r["id"] for r in after], "create_ok": ok}


def activate(repo, ruleset_id):
    body = cutover.ruleset_with("active")
    ok, response = gh("api", "-X", "PUT", f"repos/{repo}/rulesets/{ruleset_id}",
                      "--input", "-", body=body)
    return {"flip_ok": ok, "flipped_at": utcnow(),
            "flip_response": (response if not ok else "not trusted")}


def activate_existing(repo, ruleset_id):
    """Flip one existing ruleset, and establish the outcome by reading.

    The PUT response is not consulted for the verdict, and a lost or
    garbled one is never cured by a second PUT. Building an entire
    architecture on "write is not fact" and then trusting an HTTP client at
    the one flip that closes production would be almost artistic.
    """
    name = cutover.canonical_ruleset()["name"]
    named, error = find_by_name(repo, name)
    if named is None:
        return {"state": "STOP", "cause": "cannot list rulesets", "error": error}
    if len(named) != 1 or named[0]["id"] != ruleset_id:
        return {"state": "STOP",
                "cause": f"expected exactly one ruleset named {name!r} with id "
                         f"{ruleset_id}; found {[r['id'] for r in named]}"}

    before = verify(repo, ruleset_id, "disabled")
    if before["state"] != "VERIFIED" or before["observed_enforcement"] != "disabled":
        return {"state": "STOP", "cause": "pre-flip state is not a verified "
                                          "disabled ruleset", "before": before}
    pre_flip_policy_hash = before["POLICY_HASH"]["observed"]

    flip = activate(repo, ruleset_id)
    after = verify(repo, ruleset_id, "active")

    result = {"ruleset_id": ruleset_id, "flip": flip,
              "pre_flip_policy_hash": pre_flip_policy_hash,
              "before": before, "after": after,
              "retry_performed": False}

    if after["state"] == "UNCERTAIN":
        result["state"] = "OUTCOME_UNKNOWN"
        result["cause"] = ("readback unavailable; the flip may or may not "
                           "have landed. No second mutation until state is "
                           "established by reads.")
        return result
    if after["observed_enforcement"] == "disabled":
        result["state"] = "DID_NOT_ESTABLISH"
        result["cause"] = "readback still reports disabled; activation did " \
                          "not take effect"
        return result
    if after["state"] != "VERIFIED":
        result["state"] = "OUTCOME_UNKNOWN"
        result["cause"] = "readback is neither the verified active object " \
                          "nor the disabled one"
        return result

    result["policy_hash_unchanged_across_flip"] = (
        pre_flip_policy_hash == after["POLICY_HASH"]["observed"])
    result["state"] = ("CONFIRMED" if result["policy_hash_unchanged_across_flip"]
                       else "STOP")
    if result["state"] == "STOP":
        result["cause"] = ("POLICY_HASH moved across the flip; something "
                           "edited the policy while the enforcement state "
                           "changed")
    return result


def run(args):
    repo = args.repo
    name = cutover.canonical_ruleset()["name"]
    steps = {"step": "A5b step 4 ruleset", "repo": repo, "name": name,
             "started_at": utcnow()}

    created = create_disabled(repo, name)
    steps["create"] = created
    if created["state"] != "CREATED":
        steps["verdict"] = "STOP"
        return steps

    ruleset_id = created["ruleset_id"]
    disabled_check = verify(repo, ruleset_id, "disabled")
    steps["verify_disabled"] = disabled_check
    if disabled_check["state"] != "VERIFIED":
        steps["verdict"] = "STOP"
        steps["note"] = ("the ruleset exists and is DISABLED, so nothing is "
                         "enforced; it is left in place for inspection")
        return steps

    if args.stop_after_disabled:
        steps["verdict"] = "HALTED_BEFORE_ACTIVATION"
        return steps

    steps["activate"] = activate(repo, ruleset_id)
    active_check = verify(repo, ruleset_id, "active")
    steps["verify_active"] = active_check
    steps["policy_hash_unchanged_across_flip"] = (
        disabled_check["POLICY_HASH"]["observed"]
        == active_check.get("POLICY_HASH", {}).get("observed"))
    steps["verdict"] = ("ACTIVE" if active_check["state"] == "VERIFIED"
                        and steps["policy_hash_unchanged_across_flip"]
                        else "STOP")
    steps["finished_at"] = utcnow()
    return steps


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=cutover.REPO)
    ap.add_argument("--stop-after-disabled", action="store_true",
                    help="create and verify, then halt before the flip")
    ap.add_argument("--activate-existing", type=int, default=None,
                    metavar="RULESET_ID",
                    help="flip an existing verified-disabled ruleset. Creates "
                         "nothing.")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if args.activate_existing:
        result = activate_existing(args.repo, args.activate_existing)
        ok_states = ("CONFIRMED",)
    else:
        result = run(args)
        ok_states = ("ACTIVE", "HALTED_BEFORE_ACTIVATION")
    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0 if result.get("verdict", result.get("state")) in ok_states else 1


if __name__ == "__main__":
    sys.exit(main())
