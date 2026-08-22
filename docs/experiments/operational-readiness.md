# A5a — Operational readiness: report

Program `ai-final-review` · Control plane `PhysShell/review-governance` ·
Branch `experiment/operational-readiness` · Date: 2026-08-22 (UTC).
Preregistered protocol: `experiments/operational-readiness/PROTOCOL.md`.

## Result

```text
A5_OPERATIONAL_READINESS: BLOCKED
PRODUCTION_ENFORCEMENT: NOT_READY
```

Blocked on exactly one thing: **there is no stable first-party webhook
endpoint**, and this environment cannot provide one. Everything else in the
matrix was demonstrated, including the two invariants this stage existed
for. The blocker is infrastructure, not design.

## The two headline invariants

### I2 — a success does not outlive the runtime that must revoke it

The most important new evidence in this stage.

```text
10:23:43  primary heartbeat, success published on HEAD_B, projection CONFIRMED
          GitHub mergeStateStatus: CLEAN
10:24:00  primary stops heartbeating
10:24:30  watchdog: heartbeat age 47.6 s > 45 s threshold -> primary stale
10:24:31  incident GOVERNOR_UNAVAILABLE; all 3 confirmed successes revoked
          to failure, each CONFIRMED by independent readback
          elapsed from outage: 35 s   (target <= 60 s)
          merge attempt -> BLOCKED, "is failing", mergeStateStatus BLOCKED
          primary returns -> standing successes: 0, revocations: 0
          check output title: "Governor: EVIDENCE_INVALIDATED (watchdog)"
```

The returning primary restored nothing, which is the rule that makes the
watchdog worth having: during the outage nobody was watching the providers'
mutable carriers, so the old green is exactly as trustworthy as an
unattended shop.

The watchdog's capability is enforced in code, not promised in prose: it
may `GET` state and `PATCH` an existing Governor check run to a
**non-passing** conclusion. Creating a run, publishing any passing
conclusion, writing a commit status, merging, or touching a ruleset each
raise `WatchdogCapability`. It reads no user credentials at all — its only
token source is the App installation — so a revoked or expired user
authorization cannot disarm it.

### I1 — base freshness

With `strict_required_status_checks_policy: true` on an isolated ref:

```text
success CONFIRMED on HEAD_A (92e68d2d…), mergeStateStatus CLEAN
base advanced 047ff1a6… -> 209749e2… (by merging a second PR)
PR head unchanged, its success still confirmed
merge attempt -> 405 BLOCKED
branch updated -> new head e113e8a1… with ZERO check runs
merge attempt -> 405 BLOCKED
```

So a review does not travel across a base change, and updating the branch
produces a head that must be reviewed afresh — which is exactly the
already-proven staleness machinery doing the work, at the cost of extra
review rounds.

## Two operational findings worth knowing before cutover

- **A required-status-check ruleset blocks direct pushes to the protected
  ref**, not only merges. Advancing the base during this experiment had to
  be done through a legitimate PR. In production that is desirable; it will
  still surprise anyone reaching for a hotfix push.
- **Under `strict`, base drift and a missing check produce the same
  message**: `Required status check "…" is expected.` GitHub does not
  distinguish "never reviewed" from "reviewed, branch behind". The
  Governor's own surface has to explain the difference, because the API
  error will not.

## Break-glass drill (P3)

```text
incident recorded -> snapshot (canonical hash f4a1f83b…)
enforcement active -> disabled -> restored
readback canonical hash: identical
exceptional operation performed: none (drill)
closure recorded
```

Restoration is verified against a computed canonical object, not against
someone's memory of a settings page.

## Cutover artefacts (generated, not applied)

Canonical production ruleset, hash `fd77f989384bc400967710aa5fa795418946b0b2a9022c9202e9d63a4506e813`:

```text
target        refs/heads/main
required      context ai/final-review, integration_id 4669438
strict        true
bypass_actors []
enforcement   active
allowed       success failure cancelled action_required timed_out
forbidden     neutral skipped
statuses permission: remains ABSENT
```

Bootstrap dry run over the real inventory — PR **#8** (not draft,
`6d81a4d62e14…`) and PR **#12** (draft, the frozen pilot baseline,
`e29621f54a63…`) — plans `ai/final-review = failure / NOT_ESTABLISHED` for
each head with the output "Activation bootstrap. No final-review evidence
established for this head. Fresh qualification required.", and starts **no**
provider round for either: drafts consume no quota, and non-drafts wait for
an explicit `ACCEPT-CANDIDATE` transition rather than launching two AI
reviews per open PR on activation day.

Verified after the dry run: `ai/final-review` check runs found on those
heads — **0**; rulesets targeting `main` — **0**.

## The blocker

```text
STABLE_FIRST_PARTY_WEBHOOK_ENDPOINT: BLOCKED
```

A2a's receiver ran behind a Cloudflare Quick Tunnel — ephemeral by
construction, third-party in the path, and explicitly recorded there as
*not* a production candidate. This host is WSL2 behind NAT with egress
only, so it cannot serve a stable HTTPS endpoint. Two matrix rows depend on
having one:

```text
WEBHOOK_DURABLE_BEFORE_ACK      BLOCKED  (contract specified, receiver written
                                          in A2a, but nothing to run it on)
FAILED_DELIVERY_RECONCILIATION  BLOCKED  (a failed delivery needs a receiver
                                          that can fail)
```

What is *not* blocked is the reconciliation path itself: A2b already showed
reconciliation discovering a head change with no delivery at all, and this
stage ran its entire live sequence with **no webhook receiver in
existence**, detecting every state purely by reading GitHub. A
polling-only v1 is therefore viable; its detection lag is the poll interval
rather than delivery latency, which the SLO table must then state honestly.

Options for the owner, in increasing order of durability: a small VPS or
cloud function with a fixed hostname; a named Cloudflare Tunnel bound to an
owned domain; Tailscale Funnel with a stable `ts.net` name. All three are
first-party receivers; they differ in who can see the payload in transit,
which is the same trust question A2a already answered once.

## Acceptance matrix

```text
STABLE_FIRST_PARTY_WEBHOOK_ENDPOINT   BLOCKED   no public endpoint in this environment
WEBHOOK_DURABLE_BEFORE_ACK            BLOCKED   depends on the endpoint
FAILED_DELIVERY_RECONCILIATION        BLOCKED   depends on the endpoint
MISSED_EVENT_RECOVERY                 PASS      whole stage ran with no receiver at all
HEALTHY_DETECTION_SLO                 PARTIAL   revocation ~1 s and outage->revoked 35 s
                                                observed; webhook-path P99 unmeasurable
INDEPENDENT_WATCHDOG                  PASS
PRIMARY_OUTAGE_REVOKES_SUCCESS        PASS      35 s, all three successes
WATCHDOG_REVOCATION_CONFIRMED         PASS      independent readback per run
NO_AUTOMATIC_SUCCESS_RESTORE          PASS      live and offline
STRICT_BASE_DRIFT_BLOCKED             PASS
NEW_HEAD_REQUIRES_NEW_REVIEW          PASS      new head, zero checks
USER_AUTH_LOSS_REVOKES                PASS      A1c live; semantics carried into the runbook
HUMAN_REAUTH_REQUIRED                 PASS      A1c live; no auto-restore path exists
CONFIGURED_BYPASS_ACTORS_EMPTY        PASS      [] in the canonical object and in both probes
BREAK_GLASS_RUNBOOK                   PASS      written and rehearsed, hash-verified
SERVICE_ROLLBACK_FAIL_CLOSED          PASS      durable state survives restart; gate stays failed
EXISTING_PR_BOOTSTRAP                 PASS      dry run over the real inventory
DRAFT_DOES_NOT_TRIGGER_PROVIDERS      PASS      #12 planned failing, no round
PRODUCTION_RULESET_CANONICALIZED      PASS      hash fd77f989…
PRODUCTION_CONTEXT_STILL_UNUSED       PASS      0 check runs, 0 rulesets on main

A5_OPERATIONAL_READINESS: BLOCKED
```

18 tests pass; secret scan clean.

## Teardown

```text
probe PR #25   closed without merge
probe PR #26   merged into the isolated readiness ref only (base-mover)
ruleset 21193128  deleted; repository ruleset inventory 0
readiness ref     deleted; readback 404
main              047ff1a641e3…, unprotected, untouched
open PRs on main  #8 6d81a4d62e14…, #12 e29621f54a63… — unchanged
```

## Owner decisions (taken after this report, amendment A5a-c1)

```text
DECISION 1  stable HTTPS endpoint on a small dedicated VPS
            polling retained as mandatory reconciliation/fallback, but no
            longer the primary healthy-path detector
DECISION 2  the watchdog runs on that same edge host — separate OS, network
            and failure domain, no user OAuth, failure-only capability
```

Polling-only was rejected as a production baseline for a reason that is
about the watchdog rather than webhooks: a second failure domain is needed
regardless, and once that host exists a fixed HTTPS endpoint is nearly free
on top of it. Polling survives as an official **degradation mode** —
`WEBHOOK_DOWN` slows detection to the poll interval, and does not open the
gate or justify break-glass.

The design that follows was implemented in this stage and is ready to
deploy: an inverted heartbeat (the primary POSTs signed liveness outbound
every 15 s, so the watchdog never asks a dead process whether it is dead);
a watchdog that reads GitHub as a **cleanup surface and never as policy
truth**, licensed by the asymmetry that a false-positive revoke is safe
while a false-positive success is not; a cold-start rule that refuses to
reconstruct `SUCCESS` from GitHub after losing durable state; and an edge
schema with nowhere to store a verdict. See
`docs/runbooks/edge-deployment.md`.

## What must happen before A5b

1. Provision the VPS per `docs/runbooks/edge-deployment.md` and run the
   **A5a-c1** qualification, which closes the three blocked rows plus
   `INDEPENDENT_FAILURE_DOMAIN_WATCHDOG`. The architectural decision alone
   does not promote A5a to `READY_FOR_CUTOVER`; the live run does.
2. Then A5b, whose sequence is already fixed: freeze inventory → verify
   primary, watchdog, auth and reconciliation healthy → bootstrap every open
   PR to `failure` → create the ruleset **disabled** → readback and hash →
   flip to active → readback → disposable PR to `main` with no check must be
   **blocked** → close it unmerged → declare activation. No successful probe
   merge into `main`: A4-live already proved the success path, and cutover
   only needs to prove the gate is shut.

Carried forward unchanged: only a known-current `AUTHORIZED` state may
trigger providers (A1c); authorization loss fails the gate closed (A1c/A2a);
absence of findings is not positive evidence (A3a); validity predicates are
evaluated against the frozen bundle (A3b-c1); a write is not a fact until
read back (A3b-c4); a standing success is extinguished and confirmed before
the Governor causes its invalidation (A3b-c3); the Governor holds no
`statuses` permission and writes only Check Runs (A4a-1); `neutral` and
`skipped` are never written (A4-live).
