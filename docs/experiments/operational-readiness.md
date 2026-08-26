# A5a — Operational readiness: report

Program `ai-final-review` · Control plane `PhysShell/review-governance` ·
Branch `experiment/operational-readiness` · Date: 2026-08-22 (UTC).
Preregistered protocol: `experiments/operational-readiness/PROTOCOL.md`.

## Result

```text
A5_OPERATIONAL_READINESS: READY_FOR_CUTOVER
PRODUCTION_ENFORCEMENT: NOT_READY
```

The blocker recorded in the first pass — no stable first-party webhook
endpoint — was closed by amendment **A5a-c1**: a dedicated edge VPS now
serves the endpoint and hosts the watchdog in its own failure domain.
Every acceptance row is demonstrated live; the A5a-c1 section below carries
that evidence.

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

## The blocker (as it stood before A5a-c1)

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
STABLE_FIRST_PARTY_WEBHOOK_ENDPOINT   PASS      https://192-248-184-141.sslip.io, TLS on the VPS
WEBHOOK_DURABLE_BEFORE_ACK            PASS      three live 202s, each with a stored row
FAILED_DELIVERY_RECONCILIATION        PASS      dropped delivery, drift found in 2.4 s
MISSED_EVENT_RECOVERY                 PASS      reconciliation never consults the spool
HEALTHY_DETECTION_SLO                 PASS      revocation ~1 s, outage->revoked 35 s,
                                                reconciliation 2.4 s vs a 30 s budget
INDEPENDENT_WATCHDOG                  PASS
INDEPENDENT_FAILURE_DOMAIN_WATCHDOG   PASS      separate host, OS and network (A5a-c1)
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

A5_OPERATIONAL_READINESS: READY_FOR_CUTOVER
```

35 tests pass; secret scan clean.

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


## A5a-c1 — external failure domain, live

The edge VPS (Arch, `192.248.184.141`) runs the pushed code byte-identically
— `edge_service.py`, `edge_watchdog.py`, `edge_store.py` all match the
repository by sha256 — under systemd with `ProtectSystem=strict` and
`NoNewPrivileges`, behind Caddy terminating Let's Encrypt TLS and proxying
to `127.0.0.1:8931`.

### Key delivery

The App key was streamed from the primary straight into its final path, so
it never existed as a separate file on the VPS and no `shred` was relied on
(which on journalling and copy-on-write filesystems promises more than it
delivers).

```text
app.pem            0600 governor-edge:governor-edge
public fingerprint 45e1536b38d315ca   — identical to the primary's
GET /app           200 physshell-review-governor (4669438)
installation token minted successfully
```

The edge needs this same App's authority because GitHub only lets the App
that *created* a check run update it: a separate minimal watchdog App could
not revoke the Governor's checks at all. The blast radius is therefore
forced by the platform, not chosen for convenience.

### Endpoint and heartbeat

```text
GET  /healthz              200 over HTTPS, valid chain
POST /github/webhook       401 when unsigned — verification before anything else
heartbeat primary -> edge  202, recorded server-side
```

The webhook secret was set **from the VPS itself** via `PATCH
/app/hook/config` under an App JWT, so it never traversed the primary or a
human's clipboard.

### Watchdog across a real failure boundary

```text
standing success on the probe head, mergeStateStatus CLEAN
primary heartbeat process SIGKILLed
edge, entirely on its own: heartbeat age 321.9 s > 45 s -> primary stale
success -> failure, patch 200, independent readback CONFIRMED
GitHub check title: "Governor: EVIDENCE_INVALIDATED (edge watchdog)"
merge attempt -> BLOCKED, 405 "is failing"
primary returns -> revocations 0, success NOT restored
edge incident #1 recorded: stale_age 322 s, affected [98026540146]
```

### Webhook rows

Once the owner re-enabled **Active** — there is no REST API for that toggle,
as with event subscriptions — deliveries flowed immediately:

```text
check_suite.requested   OK 202   stored on the edge
pull_request.opened     OK 202   stored on the edge
pull_request.synchronize OK 202  stored, then marked DROPPED on purpose
```

Every 202 has a durably stored row with a body hash; the ordering itself is
structural in `handle_webhook` and asserted by test.

### Missed delivery

```text
delivery 79370be0… marked DROPPED, never processed
reconciliation: stored head da4138ae… vs GitHub head 771896fe…
drift detected in 2.372 s (budget 30 s), current head reported unreviewed
source: GitHub read; the edge delivery spool was not consulted
```

The independence matters more than the latency: had reconciliation asked
the spool, a delivery that never arrived would have been invisible to the
very mechanism meant to catch it.

### Two harness defects the live run exposed

- `edge_watchdog` had no `--context` flag, so it could only police the
  production context — a probe context could not be qualified without
  repointing production at it. The first watch ran a full window and
  revoked nothing for exactly this reason.
- `cmd_watch` returned a hardcoded `primary_stale: false` when its window
  expired, hiding whatever it had actually observed.

Both fixed and redeployed with hashes re-verified.

### Teardown

Probe PRs #27 and #28 closed without merge; both isolated refs and the
probe ruleset deleted (repository ruleset inventory back to 0); check runs
preserved as commit-bound evidence; `main` still `047ff1a641e3…`,
unprotected, untouched; the open PRs on `main` remain #8 and #12.

## A5a-c2 — three review defects closed, and one the rerun found

Date: 2026-08-26 (UTC). Preregistered in `PROTOCOL.md` before any fix, so the
record of what was wrong does not depend on remembering it afterwards.

### c2-1 — the external watchdog SLO, measured after the `--context` fix

Fresh heartbeat loop, fresh probe success `CONFIRMED` on an isolated ref,
live `systemd` watchdog scoped to the probe branch and the probe context,
then `SIGKILL` on the primary's heartbeat.

```text
last primary heartbeat        2026-08-26T02:03:26Z
primary killed                2026-08-26T02:03:28Z
watchdog detected (age 49.0s) 2026-08-26T02:04:16Z
revoked, readback CONFIRMED   2026-08-26T02:04:17Z
failure seen by an outside    2026-08-26T02:04:18Z
  observer polling every 2 s

EXTERNAL_WATCHDOG_DETECTION_SLO = 52 s   (heartbeat -> failure observed)
                                  50 s   (kill -> failure observed)
```

That is what the configuration produces: `stale-after 45 s` plus a `10 s`
poll interval plus one API round trip. It is the recorded SLO, not a target
the seconds were argued into. While the primary was alive the watchdog ran
and revoked nothing, and when the primary came back neither revoked run was
restored.

### c2-1a — the rerun found a worse defect than the one it was measuring

`cmd_watch` was written as a bounded fixture — *poll until something is
revoked, then stop* — and then deployed as the supervisor. After the
incident the unit exited `0`; `Restart=on-failure` correctly did nothing;
the journal's last line was `Deactivated successfully`. The independent
failure domain disabled itself the first time it was needed, and the
service history looked like a clean run.

```text
--window 0             run until stopped   <- what the unit now uses
--stop-after-incident  explicit fixture flag
Restart=always
```

Requalified live: with the primary still dead, a second probe success was
published and revoked by the *same* process — `is-active: active`,
`MainPID` unchanged, `NRestarts: 0`, incidents 2 and 3 both recorded.

### c2-2 — the fast path from edge to primary

The primary pulls; the edge never pushes and never reaches into the primary.
A signal says *something changed* and carries no payload, so the primary
re-reads GitHub for the fact.

```text
pushed                  2026-08-26T01:59:38Z
edge received delivery  2026-08-26T01:59:41Z
primary observed        2026-08-26T01:59:44Z

DELIVERY -> OBSERVATION = 3.0 s          (target < 10 s)
```

The observation stores the whole open-PR snapshot the primary re-read. An
earlier draft of `signal_client.py` recorded `pulls[0]` as *the* head for a
signal that names no PR — a guess wearing the shape of evidence, and exactly
the kind of thing that reads as a fact six months later. The cursor advances
only after the observation is durable, and a failed GitHub read stops the
drain rather than skipping the signal.

Then the independence half. Deliveries 15 and 16 were marked `DROPPED` on
the edge and the primary's cursor was left at 14, so the fast path was blind
to the new head by construction:

```text
primary cursor before reconciliation   14
stored head    fef17796f4b23a86b9be3b610498b76f30f33a1c
github head    99157ac6d5042507161edffbcf1a4b2d4d52a857
drift_detected true
current_head_is_unreviewed true
primary cursor after reconciliation    14   (never read, never advanced)
```

Reconciliation neither imported the cursor nor consulted the spool; a test
asserts that structurally, because a future refactor that "optimises" it
into consulting the spool would silently delete this property.

### c2-3 — three hashes instead of one

```text
POLICY_HASH            d6a4fa262d31c1f9fb95c5be631a52b7884febd65cec36194c5a9e303fedf5a7
DISABLED_RULESET_HASH  b6ea30b64ae311ce348dcef0be6cf0de69872d74aa18496213fe7eb8e8fa474b
ACTIVE_RULESET_HASH    fd77f989384bc400967710aa5fa795418946b0b2a9022c9202e9d63a4506e813
```

`ACTIVE_RULESET_HASH` is unchanged, so nothing reviewed earlier moved.
`POLICY_HASH` covers the object without `enforcement`, so it is identical on
both sides of the enable flip — the state transition stops masquerading as a
policy change — while still changing if the context, `integration_id`,
`strict` policy, target or `bypass_actors` change. Both properties are
tested, the second being the one that matters: a hash that ignored too much
would be worse than the defect it replaced.

### Teardown

Probe PR #29 closed without merge; `probe/a5ac2` and `governor/a5ac2-target`
deleted; the fixture `systemd` drop-in removed and the watchdog unit back to
its production scope (`--branches main`, context `ai/final-review`,
`--window 0`, `Restart=always`) and left `inactive`/`disabled`; the edge
receiver stays `active`; revoked check runs preserved as commit-bound
evidence. `ai/final-review` is still unused and `main` still carries no
ruleset.

### Status after c2

```text
EXTERNAL_WATCHDOG_DETECTION_SLO_AFTER_FIX  52 s (observed)
WATCHDOG_SURVIVES_ITS_OWN_INCIDENT         PASS (requalified live)
WEBHOOK_FAST_PATH                          3.0 s (target < 10 s)
RECONCILIATION_INDEPENDENT_OF_CURSOR       PASS (live + structural test)
RULESET_HASH_SPLIT                         PASS
```
