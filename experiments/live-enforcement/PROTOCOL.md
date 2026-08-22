# A4-live — Enforcement behaviour on an isolated ref (preregistered)

Status: **PREREGISTERED** — committed before the target ref, the ruleset or
any probe PR existed.
Program: `ai-final-review` · Control plane: `PhysShell/review-governance` ·
Branch: `experiment/live-enforcement`.

## Question

Does a required, expected-source Governor check actually **behave** the way
its configuration claims — and in particular, is the `integration_id`
binding effective rather than "JSON-recorded hope"?

```text
A4_LIVE_ENFORCEMENT: PASS | PARTIAL | FAIL
```

## Frozen prerequisites

```text
A1 PARTIAL · A1b PASS · A1b-R PASS · A1c PASS/HUMAN RECOVERY
A2a PASS · A2b PASS · A3a PASS · A3b PASS/FROZEN
A4-design READY_FOR_ISOLATED_LIVE_PROBE · MODEL O selected
A4a-1 CURRENT_PERMISSION_EXPECTED_SOURCE: PASS · fixture RETIRED
PRODUCTION_ENFORCEMENT: NOT_READY
```

```text
Governor statuses permission: MUST REMAIN ABSENT
STATUS_PERMISSION_DELTA: NOT_REQUIRED_FOR_OBSERVED_REST_RULESET_PATH
```

Control-plane PRs #1–#10 are frozen evidence and are not touched.

## Isolation

```text
target ref : refs/heads/governor/a4-enforcement-target
context    : ai/final-review-enforcement-probe
production : ai/final-review — UNTOUCHED, never created, never required
main       : never targeted, never merged into
```

Ruleset, created by the **owner** (the Governor has no `administration`
permission and must not acquire one):

```text
enforcement        : active
target             : ONLY refs/heads/governor/a4-enforcement-target
bypass_actors      : []
required check     : context ai/final-review-enforcement-probe
                     integration_id 4669438
strict / up-to-date: DISABLED
```

`strict` stays off deliberately: testing expected-source and
"branch must be up to date" at once would produce a block whose cause is
ambiguous between them.

Full readback before **any** merge attempt: active; exact target ref; no
exclusions widening scope; no bypass actors; exact context;
`integration_id == 4669438`; `main` not targeted. The endpoint
`GET /repos/{owner}/{repo}/rules/branch/{branch}` remains **UNAVAILABLE**
on this account (404 for every ref) and is never treated as evidence.

## Test 1 — wrong source, the gate for everything else

Until this passes, the `integration_id` binding is unproven and no other
result means anything.

```text
disposable PR into governor/a4-enforcement-target, frozen HEAD_A
NO Governor check run on HEAD_A
owner token publishes a plain COMMIT STATUS:
    context = ai/final-review-enforcement-probe
    state   = success
owner attempts merge with the exact expected SHA

required outcome: MERGE BLOCKED
```

The commit status is an owner-side test fixture and must not pass through
the Governor runtime — whose write allowlist contains `/check-runs` only
and raises on anything else.

```text
merge blocked  -> EXPECTED_SOURCE_EFFECTIVE: PASS, continue
merge allowed  -> EXPECTED_SOURCE_EFFECTIVE: FAIL
                  A4_LIVE_ENFORCEMENT: FAIL, STOP
                  (integration_id is a decorative field)
```

## Enforcement matrix (after the gate)

```text
1. no matching check                              -> BLOCKED
2. Governor exact-head failure                    -> BLOCKED
3. wrong-source same-context success only         -> BLOCKED   (test 1)
4. Governor success on previous HEAD, push HEAD_B -> BLOCKED on HEAD_B
5. Governor success on current HEAD               -> ALLOWED
6. Governor success -> same-head EVIDENCE_INVALIDATED
   -> failure CONFIRMED -> merge attempt          -> BLOCKED
```

Case 6 is the live second half of **G1**: the Governor observes an
invalidation, projects failure, confirms it, and GitHub then refuses the
merge.

Every merge attempt is performed by the **owner**, never by the Governor,
and always with the exact expected PR head SHA. The Governor stays a
reviewer coordinator; it never merges anything.

## Probe-only evidence, full lifecycle

A3b already proved that a provider bundle yields a legitimate Governor
success. The object under measurement here is the GitHub ruleset, so a
probe-only evidence object is permitted:

```text
EnforcementProbeEvidence-v1
output must state:
    A4 enforcement fixture.
    Not a provider review verdict.
    Not production evidence.
```

It does **not** weaken the check lifecycle. Every success still goes
through: durable decision → projection `PENDING` → PATCH → independent
exact-run GET → projection `CONFIRMED`, and carries the full fixture hash
in its output.

## One real merge is permitted

To establish case 5, exactly one real merge may happen:

```text
probe PR -> governor/a4-enforcement-target      PERMITTED
anything -> main                                FORBIDDEN
```

It is performed by the owner as part of the experiment and changes nothing
about the Governor's authority. Subsequent cases use a fresh disposable PR
from the new target head.

## G2 is composed, not staged

No artificial provider race is required, and none is a PASS criterion.
Chasing the millisecond between a CodeRabbit mutation and a Governor poll
would optimise the experiment for a dramatic recording rather than for
causal knowledge. The conclusion is composed from what is already
observed:

```text
OBSERVED (A3a)   a provider carrier can mutate asynchronously under a
                 frozen decision
OBSERVED (A4-live) GitHub allows merge while the expected-source Governor
                 check on the current head reads success
STRUCTURAL       GitHub has no input representing an unobserved provider
                 mutation
=> UNOBSERVED_INVALIDATION_ATOMICITY: NOT_PROVIDED
```

If such a mutation happens naturally during the round, the window is
measured and reported.

## Result matrix

```text
RULESET_SCOPE_ISOLATED                    PASS/FAIL
EXPECTED_SOURCE_PERSISTED                 PASS/FAIL
EXPECTED_SOURCE_WRONG_SOURCE_BLOCKED      PASS/FAIL
NO_CHECK_BLOCKED                          PASS/FAIL
GOVERNOR_FAILURE_BLOCKED                  PASS/FAIL
OLD_HEAD_SUCCESS_BLOCKED                  PASS/FAIL
CURRENT_HEAD_GOVERNOR_SUCCESS_ALLOWED     PASS/FAIL
SAME_HEAD_REVOKED_SUCCESS_BLOCKED         PASS/FAIL
OWNER_MERGE_EXPECTED_SHA_GUARD            PASS/FAIL
GOVERNOR_NEVER_MERGES                     PASS/FAIL
GOVERNOR_STATUS_API_ABSTINENCE            PASS/FAIL
G1_OBSERVED_STATE_ENFORCEMENT             PASS/FAIL

G2_UNOBSERVED_INVALIDATION_ATOMICITY: NOT_PROVIDED

A4_LIVE_ENFORCEMENT: PASS | PARTIAL | FAIL
PRODUCTION_ENFORCEMENT: NOT_READY
```

## Forbidden

Touching `main`; creating or requiring `ai/final-review`; auto-merge; merge
queue; branch protection; any permission change; any Commit Status write
from the Governor runtime; Governor-performed merges; Codex or CodeRabbit
participation (no provider is involved).

## Stop rule

After the matrix, fixtures, replay tests, secret scan and report: tear the
ruleset and target ref down, keep the check runs as evidence, open a draft
PR, stop. Even on a full PASS, production stays off: an activation gate
(name `ai/final-review`, rollout/rollback, reconciliation SLO, webhook
availability, auth-loss behaviour, bootstrap of existing PRs) is a separate
decision.
