# A6a — production steady-state review runtime: preregistration

Program `ai-final-review` · Control plane `PhysShell/review-governance` ·
Branch `experiment/a5b-cutover`, continuing from the frozen A5b cutover at
`d5622846052d2e30513b614bd5b5630a6dc63dea`.

Written and frozen before implementation.

## Question

The gate is proven. Can the software above it carry a review round from a
new head to a published verdict?

A5b proved GitHub refuses a merge. The first mundane head update then
proved the rest is a set of well-tested experimental pieces rather than a
loop: nothing creates a carrier for a new head, nothing can publish a
production success, no provider trigger exists, and the decision history
cannot tell one PR from another.

A system must not begin a review it is structurally unable to finish.

## The four absences this stage closes

```text
steady_state_carrier_producer   bootstrap.py posts the production context
                                but only from a frozen inventory
production_success_projection   governor.py can publish success, but is
                                hardcoded to the probe context
provider_trigger_lineage        no code can invoke a provider
pr_scoped_decision_history      `decisions` carries neither repo nor
                                pr_number
```

## Load-bearing requirements

### 1 — historical decisions are not rewritten

The two bootstrap decisions are historical facts. They are not
back-filled with `repo`/`pr_number`.

A new scoped relation is created instead, and populated by a migration
whose mapping is **provable**: each old decision carries a full
`head_sha`, and the commit-bound A5b inventory
(`93dd8e5b…`) maps a full head to exactly one PR. An old row whose head
matches exactly one inventory entry is mapped; anything else is
`UNMAPPED` and stays that way.

```text
FORBIDDEN   enumerate(rows) paired with enumerate(PRs)
            prefix matching on abbreviated SHAs
            any mapping whose justification is order
```

### 2 — scope is identity, not a convenient parameter

An epoch's logical identity is the tuple, not a string that happens to
contain part of it:

```text
repo · pr_number · full head_sha · generation
```

`last_known_head(repo, pr)` must be able to **prove** a record belongs to
that pair. Where it cannot, the answer is not `None` and it is not "no
drift":

```text
RESOLVED    a scoped record exists for this (repo, PR)
NO_EPOCH    no record; nothing was ever decided here
UNRESOLVED  records exist but scope cannot be established -> fail closed
```

The current defect is precisely that `None` reads as reassurance. A
comparison that never ran must never be reported as a comparison that
found nothing.

### 3 — the carrier producer is a lifecycle, not a perpetual bootstrap

```text
exact current HEAD
  ├── carriers unreadable        -> OUTCOME_UNKNOWN / STOP
  ├── >1 applicable carriers     -> AMBIGUOUS / STOP
  ├── exactly 1 valid scoped     -> adopt by reading, NO POST
  └── 0 carriers
        -> durable NOT_ESTABLISHED decision
        -> PENDING projection
        -> exactly ONE POST
        -> independent readback
              exactly 1 matching -> CONFIRMED
              otherwise          -> OUTCOME_UNKNOWN
        -> never a blind retry
```

Everything A5b's bootstrap did right is kept. What goes is the one-shot
binding to a frozen activation inventory.

### 4 — production projection is separate from probe machinery

Renaming `CONTEXT` in `governor.py` and declaring the runtime ready is not
permitted. `ReadinessProbeEvidence-v1`, the `a5a-` epochs and production
provider evidence have different semantics, and merging them would be the
"it almost fits already" from which this programme has drawn most of its
defects.

### 5 — ACCEPT-CANDIDATE is implemented, not executed

Its preconditions bind, at minimum:

```text
repo · PR · exact current HEAD
non-draft
current with the intended base
ruleset verified active
exactly one CONFIRMED failure carrier on that same HEAD
authorized trigger state
no incompatible generation already open
```

A head move between acceptance and trigger **invalidates** the acceptance.
It is never re-pointed at the new head: an acceptance is a statement about
a commit, and the commit is gone.

### 6 — provider lineage is implemented and tested, never fired

The machinery must be able to record:

```text
accepted candidate · request generation · provider
request carrier id · requested_for_head · requested_at
terminal carrier(s) · carrier head/range attestation · qualification
```

A6a ends before the first real `@codex` review or CodeRabbit command.

### 7 — the positive path must exist without being run

Proven on adversarial fixtures:

```text
qualified exact-head bundle
  -> durable SUCCESS decision
  -> pre-publication current-head guard
  -> production ai/final-review projection
  -> independent readback
  -> CONFIRMED success

incomplete · stale · ambiguous · unauthorized
  -> cannot reach success
```

## The single permitted production write

After offline acceptance closes, and only on `#8` at the exact unchanged
head:

```text
pre-read      #8 HEAD unchanged at 2d8348703924c7470ba82f525cafc9afe720aee2
              main unchanged at 047ff1a641e33e0bb8c6b9eea26bb80eea021e08
              ruleset 21640654 verified active
              ai/final-review on that HEAD == []

produce       exactly one FAILURE / NOT_ESTABLISHED via the new runtime
              independent exact-run readback
              app.id 4669438 · exact HEAD match
              durable scoped epoch identifies PR #8

reconcile     stored repo   PhysShell/evm-from-scratch
              stored PR     8
              stored HEAD   2d834870…
              GitHub HEAD   2d834870…
              drift_detected false BECAUSE values were compared

control       #12's durable state can never be returned as #8's

STOP
```

If either head moves before that write, the fixture is not adapted. STOP
and a new decision.

## Forbidden

```text
ACCEPT-CANDIDATE on #8          any provider request comment
triggering Codex                ai/final-review success on any real HEAD
triggering CodeRabbit           merging #8 · auto-merge · bypass
ruleset mutation                App permission mutation
modification of #12
```

## Acceptance matrix

```text
PR_SCOPED_DECISION_HISTORY             PASS / FAIL
CROSS_PR_CONFUSION_CONTROL             PASS / FAIL
STEADY_STATE_CARRIER_PRODUCER          PASS / FAIL
EXACTLY_ONE_FAILURE_CARRIER            PASS / FAIL
INDEPENDENT_FAILURE_READBACK           PASS / FAIL
PR_SCOPED_RECONCILIATION               PASS / FAIL
HEAD_MOVE_INVALIDATION                 PASS / FAIL

ACCEPT_CANDIDATE_TRANSITION            IMPLEMENTED / QUALIFIED_OFFLINE
PROVIDER_TRIGGER_LINEAGE               IMPLEMENTED / QUALIFIED_OFFLINE
PRODUCTION_SUCCESS_PROJECTION          IMPLEMENTED / QUALIFIED_OFFLINE
SUCCESS_GUARDS                         PASS_OFFLINE

LIVE_PROVIDER_ROUND                    NOT_STARTED
LIVE_SUCCESS                           NOT_PUBLISHED
```

## Stop rule

Report, stop. The first real `ACCEPT-CANDIDATE` and the first provider
round are a separate decision, and this stage does not prepare a candidate
for them beyond the one already established.
