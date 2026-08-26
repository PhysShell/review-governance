# A5b — production cutover: preregistration

Program `ai-final-review` · Control plane `PhysShell/review-governance` ·
Branch `experiment/a5b-cutover`, based on the frozen A5b-preflight evidence
at `5827e04ebf008503ae1fb63dc5781128d0f424e7`.

Written and frozen **before any GitHub enforcement mutation**. Nothing in
this stage may begin until this document is committed and reviewed.

PR #12 (A5a) and PR #15 (A5b-preflight) are frozen evidence and are not
modified.

## Question

Does the production gate hold a real merge closed?

Everything before this stage proved the Governor *could* — it observed,
published into a probe context, and revoked its own fixtures. This stage is
the first in which it becomes a condition on somebody else's merge. The cost
of a mistake stops being educational here.

## What changes, and what that means

```text
BEFORE   ai/final-review has never existed on a real PR
         evm-from-scratch: 0 rulesets, main unprotected at 047ff1a641e3…
AFTER    every open PR -> main carries an explicit Governor verdict
         main is protected by a required check from integration 4669438
         merges into main are conditional on the Governor
```

Two prohibitions from earlier stages are **lifted by this protocol and only
by this protocol**, so that nobody later reads the old text as still
binding:

```text
A5a / preflight said   NO bootstrap of PR #8 / #12
                       NO main ruleset
A5b says               bootstrap of every open PR is step 3
                       the main ruleset is step 4
```

Everything else on the forbidden list stays forbidden.

## Inherited contract, restated in its final form

Carried from A5b-preflight, in the owner's phrasing, because this is now the
canonical wording:

```text
refresh response
  -> both new secrets received:
        durable quarantine
        validated=false
  -> full structural validation
  -> PASS only:
        validated=true
        AUTHORIZED
```

Durability protects recoverable material; `validated=false` stops it
becoming an authorization.

Also inherited and unchanged: the authoritative auth state lives in
`auth.sqlite3`; `auth-state.json` is a one-way mirror for alerting and is
never policy authority; `PERMITS_TRIGGERS` is an allowlist of exactly
`{AUTHORIZED}`; no observation at all is not permission.

## Step 1 — this document

Frozen before any mutator runs. If the procedure below turns out to be
wrong, it is amended *and the amendment is committed* before the corrected
step executes, as in every prior stage.

## Step 2 — fresh atomic inventory freeze

The first live action, and it only reads.

```text
freeze {
  repo, base, PR, full HEAD, draft, observed_at
}
```

Re-queried from GitHub at that moment. The values in any earlier report —
`#8 8aeafa9c28b9…`, `#12 e29621f54a63…` — are historical observations and
are **not** inputs. Using them would be the `pulls[0]` defect wearing a
roadmap.

If a HEAD moves between the freeze and step 3, the bootstrap still targets
the frozen HEAD and the new head simply carries no check, which fails
closed. That is recorded, not repaired silently.

## Step 3 — bootstrap, fail closed

For each open PR whose base is `main`, on the **exact frozen HEAD**:

```text
name        ai/final-review
conclusion  failure
verdict     NOT_ESTABLISHED
```

Then an independent readback of that exact check run — the PATCH/POST
response is never the confirmation (A3b-c4).

```text
draft PR      no provider round
non-draft PR  no provider round either
              a round starts only on an explicit ACCEPT-CANDIDATE
```

This is the first moment the production context exists anywhere.

**Bootstrap must be complete before step 4.** A partial bootstrap followed
by an active ruleset produces PRs blocked by a bare absence rather than by a
readable Governor verdict, and an operator cannot tell those apart — A5a
already established that GitHub returns the identical `"…is expected."` for
a missing check and for base drift. Incomplete bootstrap → stop, do not
activate.

Bootstrap deliberately precedes the ruleset for the same reason: every open
PR should meet the gate already carrying an explicit `NOT_ESTABLISHED`,
not an empty space.

## Step 4 — ruleset, disabled first

Create exactly this object, with `enforcement: disabled`:

```text
name           ai-final-review-enforcement
target         branch
conditions     ref_name include [refs/heads/main], exclude []
bypass_actors  []
rules          required_status_checks
                 context         ai/final-review
                 integration_id  4669438
                 strict_required_status_checks_policy  true
```

Independent readback, then compare:

```text
POLICY_HASH            d6a4fa262d31c1f9fb95c5be631a52b7884febd65cec36194c5a9e303fedf5a7
DISABLED_RULESET_HASH  b6ea30b64ae311ce348dcef0be6cf0de69872d74aa18496213fe7eb8e8fa474b
```

Only then flip `disabled -> active`, and read back again:

```text
POLICY_HASH            unchanged — the flip is a state transition,
                       not a policy change
ACTIVE_RULESET_HASH    fd77f989384bc400967710aa5fa795418946b0b2a9022c9202e9d63a4506e813
```

A mismatch at any readback stops the stage. `POLICY_HASH` changing across
the flip would mean something edited the policy while the enforcement state
moved, which is precisely the substitution the three-hash split exists to
make visible.

Ruleset administration requires the **owner** token. The Governor has no
`administration` permission and must never be given one.

## Step 5 — negative production smoke test

One disposable PR into the **real** `main`, with no successful
`ai/final-review`.

```text
REQUIRED OUTCOME   merge attempt -> BLOCKED
```

Deliberately not done, and each for a reason:

```text
do NOT publish a green check on this probe
     the positive path is not what this step is for, and manufacturing a
     success to admire it is how a smoke test becomes a rehearsal
do NOT merge it
     the first merge through the production gate should be a real change
     somebody wanted, not a probe
```

Close it unmerged afterwards. The check runs stay as commit-bound evidence.

## Operational consequences to expect immediately

Stated here so they are not discovered as surprises:

```text
direct pushes to main are blocked too, not only merges (A5a finding);
moving main now requires a PR that satisfies the rule

under `strict`, base drift and a missing check return the IDENTICAL
message "…is expected." — GitHub cannot distinguish them for an operator

PRs #8 and #12 become unmergeable until they get a real review round;
that is the intended state, not an incident
```

## Rollback posture

Unchanged from the A5a runbook, and the distinction stays load-bearing:

```text
SERVICE ROLLBACK        deploy a previous Governor build; ruleset stays
                        ACTIVE; gate stays fail-closed; no special approval
ENFORCEMENT BREAK-GLASS the owner explicitly disables or deletes the
                        ruleset; the merge gate OPENS; incident record
                        required; NEVER automatic
```

A provider outage, a webhook outage, an authorization loss or a rate limit
is **not** a reason to disable enforcement.

## Forbidden in this stage

```text
NO merge of anything through the new gate
NO provider triggers, and no ACCEPT-CANDIDATE transition
NO green ai/final-review on any PR
NO bypass actors
NO auto-merge
NO App permission changes, and never `statuses` or `administration`
NO commit statuses from the Governor runtime
NO modification of PR #12 or PR #15
```

## Acceptance matrix

```text
PROTOCOL_FROZEN_BEFORE_MUTATION      PASS / FAIL
FRESH_INVENTORY_FREEZE               PASS / FAIL
BOOTSTRAP_COMPLETE_FAIL_CLOSED       PASS / FAIL
BOOTSTRAP_READBACK_CONFIRMED         PASS / FAIL
NO_PROVIDER_ROUND_STARTED            PASS / FAIL
RULESET_CREATED_DISABLED             PASS / FAIL
DISABLED_READBACK_HASHES_MATCH       PASS / FAIL
ACTIVATED                            PASS / FAIL
ACTIVE_READBACK_HASHES_MATCH         PASS / FAIL
POLICY_HASH_UNCHANGED_ACROSS_FLIP    PASS / FAIL
NEGATIVE_SMOKE_TEST_BLOCKED          PASS / FAIL
PROBE_CLOSED_UNMERGED                PASS / FAIL
NOTHING_MERGED                       PASS / FAIL

A5b_PRODUCTION_CUTOVER: PASS / FAIL
PRODUCTION_ENFORCEMENT: ACTIVE / NOT_ACTIVE
```

## Stop rule

Report, draft PR, stop.

The first real governed review round is a **separate decision**, and the PR
it runs against is chosen at that moment from its actual state. Naming a
guinea pig in advance — `#8`, say — would be `pulls[0]` in a roadmap
costume, which is the one defect shape this programme has now found four
times.

Not in this stage, recorded so the boundary is legible: the first review
round, the production soak and its statistics, multi-repo rollout, provider
abstraction, an SCM adapter for Azure DevOps Server, and the Pullfrog
runtime spike in issue #13. Each is a post-cutover question, and none of
them is a reason to hurry this one.
