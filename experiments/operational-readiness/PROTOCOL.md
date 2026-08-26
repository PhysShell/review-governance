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

## Amendment A5a-c1 — external failure domain (preregistered)

Owner decisions taken after the A5a report, before any of the code below
was written:

```text
DECISION 1  WEBHOOK TRANSPORT = stable HTTPS endpoint on a small dedicated VPS
            not Quick Tunnel, not Funnel, not polling-only
            polling REMAINS mandatory as reconciliation/fallback, but is no
            longer the primary healthy-path detector

DECISION 2  WATCHDOG runs on that same edge VPS
            separate OS, network and failure domain from the primary
            no user OAuth on the edge; failure-only capability
```

The reasoning is not primarily about webhooks: a second failure domain is
needed for the watchdog regardless, and once that host exists a fixed HTTPS
endpoint costs almost nothing on top.

### Inverted heartbeat

```text
primary  --POST signed heartbeat every 15 s-->  edge
edge stores last_primary_heartbeat (server time)
heartbeat_age > 45 s  ->  the edge acts alone
```

The primary needs only outbound access, which it already has, and the
watchdog never has to ask the primary whether it is dead — a question with
a predictable answer rate.

### GitHub as a cleanup surface, never as policy truth

The watchdog does **not** read the primary's authoritative decision store.
When the primary is stale it enumerates, from GitHub: open PRs on governed
branches, their current heads, and check runs named `ai/final-review` owned
by app `4669438`, and extinguishes any that are passing.

```text
FORBIDDEN  GitHub says success            => watchdog concludes policy SUCCESS
PERMITTED  GitHub shows a passing Governor run AND the primary is
           unavailable                    => watchdog destroys that authorization
```

The asymmetry is the whole licence: a false-positive revoke is safe, a
false-positive success is not. Every watchdog operation is monotone in the
safe direction.

### Recovery after total loss of primary state

```text
new primary starts, durable policy state unavailable
  -> DO NOT reconstruct SUCCESS from GitHub
  -> every current head is NOT_ESTABLISHED
  -> fresh provider qualification required
```

This keeps SQLite viable for now instead of turning A5 into an unplanned
control-plane storage migration.

### Edge storage (SQLite WAL is sufficient)

```text
webhook_deliveries   delivery_guid PK · event · action · received_at
                     body_hash · processing_state
primary_heartbeat    last_seen_at · primary_instance_id
watchdog_incidents   incident_id · detected_at · stale_age
                     affected_check_run_ids · revocation/readback results
```

Losing the edge database cannot manufacture a success, because no
authoritative success is ever born there.

### Webhook is a signal, not a second source of truth

```text
receive raw body -> verify HMAC -> durable commit of GUID + metadata -> 2xx
webhook says      "something relevant changed"
primary then       re-reads GitHub and derives the observation itself
```

### Degradation modes (webhook outage never opens the gate)

```text
WEBHOOK_HEALTHY   normal; reconciliation <= 30 s
WEBHOOK_DOWN      polling-only degradation; gate stays active; detection SLO
                  degrades to the poll interval; visible in health state
PRIMARY_DOWN      watchdog revokes standing successes
BOTH_DOWN         watchdog still revokes standing successes
```

Polling-only is an official degradation mode, not the production contract.
A webhook outage requires no break-glass; it makes detection slower, not
permissive.

### Credential blast radius, stated plainly

To patch its own App's check runs the edge needs App installation
authority. On a compromised edge host, `WatchdogCapability` is a program
boundary, not a cryptographic sandbox. This is recorded as a real risk and
accepted for now because the alternative — user OAuth or `administration`
on that host — is strictly worse. The edge holds: the webhook secret, App
installation capability, and a heartbeat authentication secret. Nothing
else.

### A5a-c1 live qualification (runs once the VPS exists)

```text
1  edge healthy, stable public endpoint verified
2  real signed GitHub delivery: HMAC PASS, durable-before-ACK PASS
3  primary heartbeat healthy
4  standing confirmed probe success
5  kill primary HOST/process connectivity — not merely one Python loop
6  edge watchdog independently detects > 45 s
7  watchdog: GET exact run -> success->failure -> independent GET -> CONFIRMED
8  merge attempt -> BLOCKED
9  primary restored -> old success NOT restored
10 intentionally drop one webhook delivery from processing
11 reconciliation discovers the corresponding GitHub state within
   <= 30 s + processing budget
```

Closing rows on success:

```text
STABLE_FIRST_PARTY_WEBHOOK_ENDPOINT   PASS
WEBHOOK_DURABLE_BEFORE_ACK            PASS
FAILED_DELIVERY_RECONCILIATION        PASS
INDEPENDENT_FAILURE_DOMAIN_WATCHDOG   PASS
=> A5_OPERATIONAL_READINESS: READY_FOR_CUTOVER
```

Until then A5a stays `BLOCKED`, and the architectural decision alone does
not promote it.

## Amendment A5a-c2 — three defects found in review of the c1 evidence

Preregistered before the fixes. A5a returns to `HOLD` until all three close;
the core mechanisms stay `PASS`.

### c2-1 — the external watchdog SLO was never observed *after* the fix

The c1 capture shows detection at a heartbeat age of **321.9 s** because the
success had been standing since before `--context` was added; the first
watch ran a full window and revoked nothing precisely because of that
defect. The 35 s figure belongs to the earlier **in-host** prototype, not to
the external domain.

```text
INDEPENDENT_FAILURE_DOMAIN_WATCHDOG        PASS (mechanism)
EXTERNAL_WATCHDOG_DETECTION_SLO_AFTER_FIX  NOT_YET_OBSERVED
```

Required: a fresh run on the fixed, deployed watchdog under systemd — fresh
heartbeat, fresh probe success, kill the primary, and measure
detection → `failure` CONFIRMED. Whatever the number is, it is recorded as
the SLO. If it comes out at 70 s, the SLO is 70 s; seconds are not
negotiable by assertion.

### c2-2 — there is no fast path from the edge to the primary

`edge_service` verifies, stores durably, and ACKs — and stops there. The
primary's `reconcile.py` reads GitHub and deliberately never consults the
spool. Both properties are correct in isolation, and together they mean the
decision recorded in A5a-c1 is not yet implemented:

```text
DECIDED    webhook = primary healthy-path detector, polling = fallback
ACTUAL     webhook -> edge durable spool -> (nothing reads it)
           primary -> polls GitHub anyway
```

A very reliable mailbox nobody opens. The fix keeps the network direction —
the primary stays outbound-only:

```text
primary -> authenticated pull from the edge
           GET /signals?after=<cursor>
           returns metadata only:
               delivery_guid · event · action · repository · body_hash · seq
primary  -> on a signal: re-read GitHub, write a durable observation,
            advance its cursor
reconciliation -> UNCHANGED: reads GitHub directly every <= 30 s and never
            depends on the edge cursor or spool
```

Required live fixture: `delivery received_at → primary observed_at < 10 s`
on the healthy path, followed by another `DROPPED` delivery proving
reconciliation still catches it independently.

### c2-3 — `fd77f989…` cannot be verified against a disabled ruleset

`canonical_ruleset()` contains `enforcement: active` and `canonical_hash()`
hashes the whole object, while the cutover sequence requires creating the
ruleset **disabled**, hashing the readback, and only then flipping to
active. A disabled object necessarily hashes differently. SHA-256 has not
yet learned to infer intent.

```text
POLICY_HASH            canonical object WITHOUT enforcement — proves target,
                       context, integration_id, strict, bypass, rules
DISABLED_RULESET_HASH  exact object with enforcement=disabled
ACTIVE_RULESET_HASH    exact object with enforcement=active (fd77f989… may
                       remain this value)
```

A5b then verifies: create disabled → readback matches `DISABLED_RULESET_HASH`
**and** `POLICY_HASH`; flip active → readback matches `ACTIVE_RULESET_HASH`
**and** `POLICY_HASH` unchanged. The state transition stops masquerading as
a policy change.

### A5b preflight (not A5a acceptance criteria)

```text
KEY SPLIT   generate K_primary and K_edge, deploy and verify each, confirm
            heartbeat/edge/reconciliation healthy, delete the shared K0,
            then prove K0 is rejected while both new keys work.
            Evidence keeps fingerprints only.
            This is rotation/revocation isolation, NOT permission isolation:
            both keys carry the same App permissions, so an attacker with
            the edge PEM bypasses WatchdogCapability entirely. Its value is
            that the edge credential can be killed without killing the
            primary.

ALERTING    two independent signal sources for a single operator: an
            external uptime monitor on /healthz (60 s, alert after two
            consecutive failures), and incident notifications from
            edge/primary for primary_stale > 45 s, any watchdog incident,
            OUTCOME_UNKNOWN/FAILED revocation, inability to mint an
            installation token, AUTH_LOST, and no successful reconciliation
            for > 60 s. Payload carries severity, incident_id, repo,
            pr_number, check_run_id, cause, detected_at, state — never
            webhook bodies, secrets or provider content. Recovery alerts are
            mandatory, or a red light quietly becomes a green one nobody
            looks at.
```

### c2-1a — the deployed watchdog stopped watching after one incident

Found *by* the c2-1 rerun, not preregistered, and recorded here before it was
fixed. `cmd_watch` was written as a bounded fixture instrument — "poll until
something is revoked, then stop" — and was then deployed as the supervisor.
The unit exited `0` after the incident, `Restart=on-failure` correctly did
nothing, and the watchdog was gone while `systemctl` history still showed a
successful run.

```text
OBSERVED   incident at 02:04:16Z -> revoked -> process exits 0
           governor-watchdog.service: Deactivated successfully.
EFFECT     first incident disables the independent failure domain
           silently; the second stale primary is unwatched
```

Fix: `--window 0` runs until stopped and is what the unit uses;
`--stop-after-incident` is now an explicit fixture flag; the unit is
`Restart=always`. Requalification: a second incident handled by the *same*
process, service still `active`, `NRestarts=0`.

### Inventory is frozen at cutover, not reused

PR #8 has already moved from `6d81a4d…` to `8aeafa9c…`. The A5a dry run is
therefore an illustration, not an input: A5b enumerates open PRs and freezes
their exact heads at cutover time, which is why its first step is worded
that way.

## Forbidden

Creating or requiring `ai/final-review`; any ruleset on `main`; touching
PRs #8 or #12; auto-merge; permission changes; Governor-performed merges;
commit statuses from the Governor runtime; provider triggers on existing
PRs.

## Stop rule

Fixtures, tests, runbooks, report, teardown of every probe fixture, draft
PR, stop. A5b cutover is a separate approval and may not begin from this
stage.
