# A4-live — Enforcement behaviour on an isolated ref: final report

Program `ai-final-review` · Control plane `PhysShell/review-governance` ·
Branch `experiment/live-enforcement` · Date: 2026-08-22 (UTC).
Preregistered protocol: `experiments/live-enforcement/PROTOCOL.md`.

## Question and result

Does a required, expected-source Governor check actually **behave** as its
configuration claims — in particular, is the `integration_id` binding
effective rather than JSON-recorded hope?

```text
A4_LIVE_ENFORCEMENT: PASS
G2_UNOBSERVED_INVALIDATION_ATOMICITY: NOT_PROVIDED
PRODUCTION_ENFORCEMENT: NOT_READY
```

## Setup

```text
target ref : refs/heads/governor/a4-enforcement-target   (created, later deleted)
context    : ai/final-review-enforcement-probe
ruleset    : 21192252, enforcement active, bypass_actors []
             required_status_checks: [{context, integration_id: 4669438}]
             strict_required_status_checks_policy: false
production : ai/final-review — never created, never required
main       : never targeted, never merged into, still 047ff1a6…, unprotected
```

`strict` was deliberately off so that a block could not be ambiguous
between expected-source and branch-freshness. The ruleset was created by
the **owner**; the Governor has no `administration` permission.

## The gate: wrong source

The load-bearing test, run first, because until it passes nothing else
means anything. On a PR into the isolated ref with **no Governor check at
all**, the owner published a plain **commit status** with the required
context and `state: success` — a genuinely passing signal from the wrong
source, produced entirely outside the Governor runtime.

```text
combined status surface : success  (ai/final-review-enforcement-probe)
Governor check runs     : 0
owner merge, exact sha  : HTTP 405

  Repository rule violations found
  Required status check "ai/final-review-enforcement-probe"
  was not set by the expected GitHub app.
```

```text
EXPECTED_SOURCE_EFFECTIVE: PASS
```

`integration_id` is not decorative. A same-context success from another
source does not satisfy the rule, and GitHub says why in those words.

## Enforcement matrix, as observed

| case | state | merge attempt | GitHub |
|---|---|---|---|
| 1 | no check, no status | 405 | `…is expected.` |
| 2 | Governor `failure` on exact head | 405 | `…is failing.` |
| 3 | wrong-source `success` only | 405 | `…was not set by the expected GitHub app.` |
| 4a | stale expected `sha` passed | 409 | `Head branch was modified.` |
| 4b | Governor `success` on the **previous** head | 405 | `…is expected.` |
| 5 | Governor `success` on the **current** head | **merged** | merge commit `83bf2b79…` |
| 6 | `success` → `EVIDENCE_INVALIDATED` → `failure` CONFIRMED | 405 | `…is failing.` |

Case 4a is worth noting separately: the expected-`sha` guard fires *before*
rule evaluation, so head drift and rule violation are distinguishable by
status code alone (409 versus 405).

## G1 observed end to end

Case 6 is the live second half of `G1 OBSERVED_STATE_ENFORCEMENT`. On one
unchanged head, in one logical check run:

```text
Governor success CONFIRMED   -> GitHub mergeStateStatus: CLEAN   (no merge attempted)
EVIDENCE_INVALIDATED         -> failure, projection CONFIRMED
owner merge attempt          -> BLOCKED, "is failing"
```

The Governor observed an invalidation, projected it, confirmed the
projection by independent readback, and GitHub then refused the merge. The
`CLEAN → blocked` transition on the same head shows the revocation reaches
GitHub's merge decision, not merely its display.

```text
G1_OBSERVED_STATE_ENFORCEMENT: PASS
```

## G2, composed rather than staged

No artificial provider race was run, and none was a pass criterion.

```text
OBSERVED  (A3a)      a provider carrier mutates asynchronously under a
                     frozen decision — including into a comment that reads
                     positive for a head it never reviewed
OBSERVED  (A4-live)  GitHub allows the merge exactly while the expected-source
                     Governor check on the current head reads success
STRUCTURAL           GitHub has no input representing a provider mutation
                     the Governor has not yet observed
=>                   UNOBSERVED_INVALIDATION_ATOMICITY: NOT_PROVIDED
```

Chasing the millisecond between a CodeRabbit edit and a Governor poll would
have produced a dramatic recording and no new causal knowledge. The residual
exposure is the detection lag, measured in A3b at ~1 s for revocation once
detection happens; detection itself is bounded by webhook delivery plus
reconciliation interval, and is the dominant term.

## Probe-only evidence, full lifecycle

The object under measurement was the ruleset, so the evidence object was
`EnforcementProbeEvidence-v1`, whose output states plainly:

```text
A4 enforcement fixture.
Not a provider review verdict.
Not production evidence.
```

The check lifecycle was **not** relaxed for it. Every publication went
through: durable decision → projection `PENDING` → PATCH → independent GET
of that exact run → `CONFIRMED`, with the fixture hash in the output. All
five decisions and all three projections are `CONFIRMED` in the chain.

## Boundaries that survived

- **The Governor never merged anything.** Its harness contains no merge
  path at all (asserted by test); every merge attempt was an owner-side
  call with the exact expected SHA.
- **The Governor never wrote a commit status**, even though A4a-1 showed
  GitHub would now accept the binding without that permission and the
  wrong-source fixture needed one. The fixture was produced by the owner;
  the Governor's write allowlist is `/check-runs` and raises otherwise.
- `neutral` and `skipped` remain unwritable — a production invariant, since
  GitHub counts them as passing.
- `statuses` permission remains **absent** from the App.

## Result matrix

```text
RULESET_SCOPE_ISOLATED                    PASS
EXPECTED_SOURCE_PERSISTED                 PASS
EXPECTED_SOURCE_WRONG_SOURCE_BLOCKED      PASS
NO_CHECK_BLOCKED                          PASS
GOVERNOR_FAILURE_BLOCKED                  PASS
OLD_HEAD_SUCCESS_BLOCKED                  PASS
CURRENT_HEAD_GOVERNOR_SUCCESS_ALLOWED     PASS
SAME_HEAD_REVOKED_SUCCESS_BLOCKED         PASS
OWNER_MERGE_EXPECTED_SHA_GUARD            PASS  (409 on stale sha)
GOVERNOR_NEVER_MERGES                     PASS
GOVERNOR_STATUS_API_ABSTINENCE            PASS
G1_OBSERVED_STATE_ENFORCEMENT             PASS

G2_UNOBSERVED_INVALIDATION_ATOMICITY: NOT_PROVIDED

A4_LIVE_ENFORCEMENT: PASS
PRODUCTION_ENFORCEMENT: NOT_READY
```

18 replay tests pass; secret scan clean.

## Admissible claim

> GitHub prevents merge whenever the latest-head Governor check, from the
> expected Governor App, is not in a passing state. The Governor fails
> closed for every invalidation it has observed.

Still not provable, and still not claimed:

> A PR can never be merged after its provider evidence becomes invalid.

## Teardown

```text
probe PRs #22, #23   closed without merge
probe PR #24         merged into the isolated ref only (83bf2b79…)
ruleset 21192252     deleted; repository ruleset inventory back to 0
target ref           deleted; readback 404
check runs           97014507572, 97014645339, 97014701608 PRESERVED
main                 047ff1a641e3…, unprotected, untouched throughout
```

## Next gate — activation, not research

The GitHub semantics question is answered. What remains is operational:

```text
A5-activation (gated, separate decision):
  - production context name ai/final-review, still unused
  - rollout and rollback procedure, and who can bypass
  - reconciliation SLO and the measured detection lag it implies
  - webhook availability: A2a's receiver was a tunnel; production needs a
    stable first-party endpoint
  - authorization-loss operational behaviour (A1c: human recovery required)
  - bootstrap of existing open PRs that have no Governor check at all
```

Carried forward unchanged: a Check Run publishes the Governor's own verdict
and never upgrades a provider carrier into authoritative provenance
(A1b-c3); only a known-current `AUTHORIZED` state may trigger providers
(A1c); authorization loss renders the gate failed, never passed (A1c/A2a);
absence of findings is not positive evidence (A3a); validity predicates are
evaluated against the frozen bundle, never live state (A3b-c1); a write is
not a fact until read back (A3b-c4); a standing success is extinguished and
confirmed before the Governor causes its invalidation (A3b-c3); the
Governor holds no `statuses` permission and writes only Check Runs
(A4a-1).
