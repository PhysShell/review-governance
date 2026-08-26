# A5b-preflight — preregistration

Program `ai-final-review` · Control plane `PhysShell/review-governance` ·
Branch `experiment/a5b-preflight`, based on the frozen A5a evidence at
`9ea7055212d5…`. PR #12 is frozen and is not modified by this stage.

Written before any of it was executed, so the record of what was promised
does not depend on remembering it afterwards.

## Question

Can the credentials, runtime and monitoring be brought to their production
configuration **without** touching GitHub enforcement state — and can a
human be shown to actually receive an alert, rather than merely to have
been alerted at?

## What this stage may change

```text
MAY CHANGE   App private keys · systemd units · monitoring · alerting
MAY NOT      any GitHub enforcement state
```

## Forbidden (unchanged from A5a, restated because it is the whole point)

```text
NO ai/final-review Check Runs      NO main ruleset
NO bootstrap of PR #8 / #12        NO branch protection
NO bypass actors                   NO auto-merge
NO provider triggers               NO merge
NO App permission changes          NO statuses permission
NO administration on the Governor
```

The last green preflight test does **not** authorise the first production
check run. A boundary crossed automatically is a boundary that was never
there.

## 1 — App key separation

`K0` is today's shared App private key, used by both primary and edge.

```text
create   K_primary   deployed to the primary only
         K_edge      deployed to the edge only
```

Private keys for a GitHub App can only be generated in the App settings UI;
there is no REST endpoint for it. That step is therefore human, and so is
the deletion of `K0`.

Each new key must be proven **independently**, from the host that will use
it, against a readback rather than an assumption:

```text
JWT accepted by GitHub
GET /app                    -> app.id == 4669438
installation token minted
installation                -> id == 155393018
expected repository accessible
runtime is using this key    -> fingerprint match
```

`K0` is not deleted until both new paths are confirmed. Then, and only then:

```text
delete K0 in the App settings
K0        -> authentication FAILS
K_primary -> PASS
K_edge    -> PASS
```

Evidence keeps **fingerprints only** — never PEM material, never a key ID
that is itself sensitive.

### What this is, and is not

This is **rotation isolation**, not permission isolation. Both keys carry
the authority of the same App. An attacker holding the edge PEM before
revocation holds the App's repository permissions and bypasses
`WatchdogCapability` entirely, because that class is a property of the edge
*code*, not of the credential. The value bought here is precise: the edge
credential can be killed without killing the primary, and a compromise has a
name and a blast radius instead of a shrug.

## 2 — Alerting

Operator for v1 is the repository owner. One human, so no rota.

```text
path 1   off-host availability monitor -> GET /healthz, interval <= 60 s,
         alerting after two consecutive failures
path 2   Governor/edge incident notifications -> one external human channel
```

Both must leave the primary **and** the edge. A notifier that lives inside
the system it watches is decoration.

### Events

```text
CRITICAL   heartbeat_age > 45 s
           watchdog incident created
           watchdog revocation = OUTCOME_UNKNOWN
           watchdog revocation = FAILED
           installation token cannot be minted
           AUTH_LOST
           REFRESH_OUTCOME_UNKNOWN
           reconciliation healthy timestamp age > 60 s

WARNING    heartbeat_age > 30 s
           webhook receiver unavailable
           RECEIVED delivery stuck beyond processing budget
```

### Payload — an allowlist, not a redaction pass

```text
severity · cause · repo · pr_number · check_run_id · incident_id
detected_at · state
```

Anything outside that list is rejected by construction rather than filtered
on the way out, because a filter is a list of the leaks somebody thought of.
No webhook bodies, no OAuth material, no provider evidence, no PEM, no
heartbeat payloads.

### Proof

```text
synthetic incident  -> the human receives an alert
synthetic recovery  -> the human receives a recovery
```

A successful `curl /healthz` proves the endpoint answers, not that anyone
would be told when it stops. Only end-to-end delivery counts, and recovery
delivery counts equally: a red light nobody sees turn green is a red light
that gets muted.

## 3 — Production processes, running

Permitted before ruleset activation, and safe precisely because
`ai/final-review` does not exist yet: the watchdog guards nothing, which is
the ideal subject for a production smoke test.

```text
edge receiver              enabled + active
edge watchdog              enabled + active
                           --branches main --context ai/final-review
                           --window 0, Restart=always
primary heartbeat          enabled + active, 15 s
primary signal fast-path   enabled + active
primary reconciliation     enabled + active, <= 30 s
```

Soak, with actual timestamps recorded:

```text
>= 2 watchdog poll intervals
>= 2 reconciliation intervals
>= 4 heartbeat intervals
```

## Acceptance matrix

```text
PRIMARY_APP_KEY_SEPARATE             PASS / FAIL
EDGE_APP_KEY_SEPARATE                PASS / FAIL
OLD_SHARED_KEY_REVOKED               PASS / FAIL
PRIMARY_AUTH_AFTER_ROTATION          PASS / FAIL
EDGE_AUTH_AFTER_ROTATION             PASS / FAIL

OFFHOST_HEALTH_MONITOR               PASS / FAIL
INCIDENT_ALERT_END_TO_END            PASS / FAIL
RECOVERY_ALERT_END_TO_END            PASS / FAIL

EDGE_RECEIVER_RUNNING                PASS / FAIL
WATCHDOG_RUNNING_CONTINUOUSLY        PASS / FAIL
HEARTBEAT_HEALTHY                    PASS / FAIL
FAST_PATH_HEALTHY                    PASS / FAIL
RECONCILIATION_HEALTHY               PASS / FAIL

PRODUCTION_CONTEXT_STILL_UNUSED      PASS / FAIL
MAIN_RULESET_STILL_ABSENT            PASS / FAIL
```

## Stop rule

Report, draft PR, stop. The A5b cutover sequence is a separate approval and
may not begin from this stage, including — especially — while everything is
green and the next step looks obvious.

## Note for the future cutover, recorded here so it is not lost

The A5a bootstrap dry run is an illustration, not an input. PR #8 has moved
to `8aeafa9c28b9…` and #12 stands at `e29621f54a63…`; A5b must re-enumerate
open PRs and freeze `{PR, full HEAD, draft}` immediately before bootstrap.
