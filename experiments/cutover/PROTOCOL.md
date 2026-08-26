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
the frozen HEAD — the snapshot is not repaired silently. The consequence is
**not** "that is fine, it fails closed": step 3b exists precisely to catch
that divergence and stop the stage before activation.

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

**Bootstrap must be complete against the frozen inventory** — that is the
referent, stated explicitly, because "complete" without one is unfalsifiable.
Completeness against *reality* is a different claim and is step 3b's job.

A partial bootstrap followed by an active ruleset produces PRs blocked by a
bare absence rather than by a readable Governor verdict, and an operator
cannot tell those apart: A5a established that GitHub returns the identical
`"…is expected."` for a missing check and for base drift. So an incomplete
bootstrap stops the stage here as well.

Bootstrap deliberately precedes the ruleset for the same reason: every open
PR should meet the gate already carrying an explicit `NOT_ESTABLISHED`,
not an empty space.

## Step 3b — pre-activation closure

Step 3 establishes that the **frozen** inventory was bootstrapped. That is
not the same claim as "GitHub is closed right now", and the gap between them
is the whole of steps 2 to 4: in that window a PR can open, close, change
base, or move its head. A bootstrap can therefore be complete against the
snapshot and incomplete against reality, and `BOOTSTRAP_COMPLETE` without a
named referent is exactly the kind of value this programme keeps catching.

So, immediately before activation, a fresh **read-only** enumeration:

```text
for every PR currently open against main:
    its CURRENT full HEAD must carry
        name        ai/final-review
        conclusion  failure
        verdict     NOT_ESTABLISHED
        app.id      4669438
    confirmed by independent readback of that exact run
```

Any delta at all — a new PR, a closed one, a changed base, a moved head —

```text
STOP. Do not activate. Do not bootstrap the delta on the spot.
```

Silently bootstrapping the delta would repair a snapshot mid-flight and
leave nobody able to say afterwards what was frozen and what was patched.
The correct response is to record the delta, freeze a new inventory as an
amendment, and repeat steps 3 and 3b from there.

Two separate acceptance facts follow, and the ruleset is created only when
both hold:

```text
FROZEN_INVENTORY_BOOTSTRAPPED            the snapshot was covered
PREACTIVATION_CURRENT_INVENTORY_CLOSED   reality is covered, now
```

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
                   AND blocked for the reason under test
```

### Why the outcome alone proves nothing

This protocol already records that under `strict`, base drift and a missing
required check return the **identical** `"…is expected."`. A probe whose
base moved before the attempt would be blocked by drift, and writing that
down as evidence for the required-check path would be a guess wearing the
shape of a proof — in a document that warns about exactly this defect three
sections earlier.

GitHub cannot be asked which cause applied. The alternative therefore has to
be **excluded by construction, before the attempt**, and the predicate has
to be frozen here rather than decided while looking at a result.

### Fixture validity predicate, evaluated immediately before the attempt

```text
main_sha_before      recorded
probe base freshness merge-base(probe, main) == main_sha_before
                     i.e. the probe is current with main and cannot be BEHIND
check absence        no ai/final-review run of any conclusion on the probe HEAD
ruleset              enforcement active
                     POLICY_HASH and ACTIVE_RULESET_HASH still match
```

Only with all four true is the merge attempted, at the exact probe HEAD.
Afterwards `main_sha_after` is recorded too, so a base that moved *during*
the attempt is visible rather than assumed away.

```text
if main moved, or any predicate fails:
    SMOKE_FIXTURE_STALE
    the attempt is NOT counted, in either direction
    recreate or rebase the disposable probe and re-evaluate
```

This is a validity predicate, not a retry loop. It is frozen before the
first attempt, it can only invalidate a fixture, and it can never turn a
failed test into a passing one — a genuine block on a fresh, checkless
probe counts the first time.

As a secondary, corroborating observation only: `mergeStateStatus` is
expected to read `BLOCKED` rather than `BEHIND`, which would distinguish the
two causes directly. It is recorded but is **not** load-bearing, because
that field's behaviour in this exact configuration has not been established
by this programme, and the freshness predicate does not depend on it.

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
FRESH_INVENTORY_FREEZE                   PASS / FAIL
FROZEN_INVENTORY_BOOTSTRAPPED            PASS / FAIL
BOOTSTRAP_READBACK_CONFIRMED             PASS / FAIL
PREACTIVATION_CURRENT_INVENTORY_CLOSED   PASS / FAIL
NO_PROVIDER_ROUND_STARTED                PASS / FAIL
RULESET_CREATED_DISABLED                 PASS / FAIL
DISABLED_READBACK_HASHES_MATCH           PASS / FAIL
ACTIVATED                                PASS / FAIL
ACTIVE_READBACK_HASHES_MATCH             PASS / FAIL
POLICY_HASH_UNCHANGED_ACROSS_FLIP        PASS / FAIL
SMOKE_PROBE_BASE_FRESH                   PASS / FAIL
SMOKE_PROBE_CHECK_ABSENT                 PASS / FAIL
NEGATIVE_SMOKE_TEST_BLOCKED              PASS / FAIL
PROBE_CLOSED_UNMERGED                    PASS / FAIL
NOTHING_MERGED                           PASS / FAIL

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

## Amendment A5b-r1 — two defects found in review of the frozen protocol

Recorded before either was fixed, so the record of what was wrong does not
depend on reading a diff.

### r1-1 — the protocol contradicted itself about snapshot divergence

Step 2 said a head that moved after the freeze was fine and failed closed.
Step 3 said the bootstrap must be complete before activation *because* a
bare absence is indistinguishable from base drift. Both sentences were in
the same frozen document, and they cannot both govern.

Worse, the divergence was never only about `synchronize`: between steps 2
and 4 a PR can open, close, or change base. A bootstrap could therefore be
complete against the snapshot and incomplete against GitHub, leaving
`BOOTSTRAP_COMPLETE_FAIL_CLOSED` with no answer to "complete relative to
what".

Fixed by Step 3b: a fresh read-only enumeration immediately before
activation, `STOP` on any delta rather than an on-the-spot bootstrap, and
two separately named acceptance facts —
`FROZEN_INVENTORY_BOOTSTRAPPED` and
`PREACTIVATION_CURRENT_INVENTORY_CLOSED`.

### r1-2 — the smoke test could have passed for the wrong reason

Step 5 required only that a merge attempt be BLOCKED. Under `strict`, a
probe whose base had moved would be blocked by drift and produce the
identical message — which this same document warns about three sections
earlier. The test would have passed while proving nothing about the
required-check path.

Fixed by a fixture validity predicate frozen in advance: `main_sha_before`
recorded, `merge-base(probe, main) == main_sha_before` so the probe cannot
be BEHIND, no `ai/final-review` of any conclusion on the probe HEAD, and the
ruleset active with matching hashes. `main_sha_after` is recorded as well.
A failed predicate yields `SMOKE_FIXTURE_STALE` and the attempt is not
counted in either direction. It can only invalidate a fixture; it can never
convert a failed test into a passing one.

### What this says about the stage

Both defects sat in the class this programme has now found five times: a
value that would have been reported as evidence while actually resting on a
coincidence of timing. Finding them in preregistration rather than in a
report is the entire reason the preregistration step exists.
