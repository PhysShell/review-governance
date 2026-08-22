# A5a — Operational readiness (preregistered)

Status: **PREREGISTERED** — committed before any watchdog, ruleset or probe
existed.
Program: `ai-final-review` · Control plane: `PhysShell/review-governance` ·
Branch: `experiment/operational-readiness`.

## Question

Is the Governor operationally safe enough to be made a required check —
specifically, does a published `success` stop being authoritative when the
Governor itself dies, and when the *base* it was reviewed against moves?

```text
A5_OPERATIONAL_READINESS: READY_FOR_CUTOVER | BLOCKED
```

## Frozen prerequisites

```text
A1 PARTIAL · A1b PASS · A1b-R PASS · A1c PASS/HUMAN RECOVERY
A2a PASS · A2b PASS · A3a PASS · A3b PASS/FROZEN
A4-design READY · A4a-1 PASS/FROZEN · A4-live PASS/FROZEN
G1 PASS · G2 NOT_PROVIDED
PRODUCTION_ENFORCEMENT: NOT_READY
```

Control-plane PRs #1–#11 are frozen evidence. In `evm-from-scratch` the
open PRs targeting `main` are **#8** (not draft, head `6d81a4d62e14…`) and
**#12** (draft — the frozen pilot baseline, head `e29621f54a63…`). Neither
is touched in this stage.

## Two new invariants this stage exists for

```text
I1  BASE FRESHNESS
    "exact current HEAD" does not mean "same merge context". With loose
    required checks the base may move under a reviewed head, so production
    runs strict: the branch must be up to date, updating it produces a new
    HEAD, and the already-proven staleness machinery invalidates the old
    review.

I2  LIVENESS
    A previously published success must not remain authoritative
    indefinitely merely because the primary Governor runtime disappeared.
    A Check Run is not a lease; GitHub keeps it for a long time and will
    not expire it on our behalf.
```

## Watchdog architecture

```text
Primary Governor            Independent watchdog
  provider logic              liveness only
  durable state               no provider triggers
  reconciliation              no success publication
  check projection            failure-only revocation
```

Separate runtime and failure domain. It may use the same App's installation
identity but needs **no** user OAuth credentials.

```text
allowed   : GET PR/check state
            PATCH an existing Governor Check Run to a non-passing state
forbidden : provider triggers · success publication · commit statuses
            merges · ruleset administration · user OAuth
```

On a stale heartbeat: enumerate confirmed successes, record a durable
incident, revoke each to `failure` with cause `GOVERNOR_UNAVAILABLE`,
confirm by independent readback of that exact run.

```text
Governor comes back != restore success
```

After a watchdog trip a **fresh provider qualification** is required: during
the outage nobody was watching the mutable carriers, so restoring the old
green would quietly undo four stages of work.

## Preregistered SLOs (targets, not guarantees)

```text
webhook ACK                              <= 2 s   (GitHub hard limit 10 s)
webhook event -> durable observation     P99 <= 10 s
reconciliation interval                  <= 30 s
observed invalidation -> failure CONFIRMED P99 <= 5 s  (A3b baseline ~1 s)
external mutation -> failure CONFIRMED   <= 60 s target
primary heartbeat                        every <= 15 s
watchdog declares primary unavailable    after <= 45 s of silence
primary outage -> all successes failed   <= 60 s target
```

These bound the exposure window; they do not close G2.

## Live probes (isolated refs, probe contexts only)

```text
P1  strict base drift
    ruleset with strict=true on an isolated ref
    Governor success on HEAD_A; base moves; merge attempt MUST BLOCK as
    not up to date; updating the branch yields HEAD_B and the old success
    MUST NOT satisfy it

P2  watchdog outage
    standing confirmed success; primary heartbeat stops; watchdog declares
    the primary unavailable; success -> failure with cause
    GOVERNOR_UNAVAILABLE; confirmed by readback; merge attempt blocked

P3  break-glass drill
    snapshot ruleset JSON + hash; disable; restore; verify the readback
    hashes identically
```

`ai/final-review` is **never** created in this stage, and nothing is done
to `main`.

## Bootstrap algorithm (implemented and dry-run only)

```text
T0  enumerate every open PR targeting main
    freeze {pr_number, head_sha, draft, observed_at}
for each current head at cutover:
    ai/final-review = failure / NOT_ESTABLISHED, App 4669438,
    independent readback CONFIRMED
    output states: activation bootstrap, no evidence established,
                   fresh qualification required
```

Draft PRs stay `failure` and consume no provider quota. Non-draft PRs are
not auto-reviewed either: a provider round starts only on an explicit
`ACCEPT-CANDIDATE` transition, or a rollout would trigger two AI reviews
per open PR and a race for the next CodeRabbit rate limit.

## Bypass and break-glass

```text
CONFIGURED_BYPASS_ACTORS = NONE
Repository owner retains administrative authority to disable or delete the
ruleset — this is stated, not pretended away.
```

Emergency bypass is a procedure, never a Merge button: record the incident,
snapshot the ruleset JSON with its hash, explicitly disable or delete,
perform the exceptional operation, restore the exact ruleset, verify by
readback, record closure. No provider outage, webhook outage, OAuth loss or
rate limit may automatically disable enforcement.

## Rollback, two different things

```text
SERVICE ROLLBACK       previous Governor build; ruleset stays ACTIVE;
                       gate stays fail-closed
ENFORCEMENT BREAK-GLASS owner explicitly disables the ruleset; the gate
                       opens; incident required; never automatic
```

## Canonical production ruleset (generated, not created)

```text
target        refs/heads/main
required      context ai/final-review, integration_id 4669438
strict        true
bypass_actors []
enforcement   active
conclusions   allowed: success failure cancelled action_required timed_out
              forbidden: neutral skipped
statuses permission: remains ABSENT
```

## Acceptance matrix

```text
STABLE_FIRST_PARTY_WEBHOOK_ENDPOINT   PASS/FAIL
WEBHOOK_DURABLE_BEFORE_ACK            PASS/FAIL
FAILED_DELIVERY_RECONCILIATION        PASS/FAIL
MISSED_EVENT_RECOVERY                 PASS/FAIL
HEALTHY_DETECTION_SLO                 PASS/FAIL
INDEPENDENT_WATCHDOG                  PASS/FAIL
PRIMARY_OUTAGE_REVOKES_SUCCESS        PASS/FAIL
WATCHDOG_REVOCATION_CONFIRMED         PASS/FAIL
NO_AUTOMATIC_SUCCESS_RESTORE          PASS/FAIL
STRICT_BASE_DRIFT_BLOCKED             PASS/FAIL
NEW_HEAD_REQUIRES_NEW_REVIEW          PASS/FAIL
USER_AUTH_LOSS_REVOKES                PASS/FAIL
HUMAN_REAUTH_REQUIRED                 PASS/FAIL
CONFIGURED_BYPASS_ACTORS_EMPTY        PASS/FAIL
BREAK_GLASS_RUNBOOK                   PASS/FAIL
SERVICE_ROLLBACK_FAIL_CLOSED          PASS/FAIL
EXISTING_PR_BOOTSTRAP                 PASS/FAIL
DRAFT_DOES_NOT_TRIGGER_PROVIDERS      PASS/FAIL
PRODUCTION_RULESET_CANONICALIZED      PASS/FAIL
PRODUCTION_CONTEXT_STILL_UNUSED       PASS/FAIL
```

A row is only `PASS` if it was demonstrated here; anything requiring
infrastructure this environment does not have is reported as `BLOCKED`
with the reason, and blocks `READY_FOR_CUTOVER` rather than being waved
through.

## Forbidden

Creating or requiring `ai/final-review`; any ruleset on `main`; touching
PRs #8 or #12; auto-merge; permission changes; Governor-performed merges;
commit statuses from the Governor runtime; provider triggers on existing
PRs.

## Stop rule

Fixtures, tests, runbooks, report, teardown of every probe fixture, draft
PR, stop. A5b cutover is a separate approval and may not begin from this
stage.
