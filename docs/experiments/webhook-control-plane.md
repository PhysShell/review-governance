# A2a — Webhook control-plane contract: final report

Program `ai-final-review` · Control plane `PhysShell/review-governance` ·
Branch `experiment/webhook-control-plane` · Date: 2026-08-22 (all times
UTC). Preregistered protocol:
`experiments/webhook-control-plane/PROTOCOL.md`.

## Question

Can the Governor establish a trustworthy webhook control plane — receiver,
HMAC, delivery idempotency, `pull_request.synchronize` → STALE epochs, and
`github_app_authorization.revoked` → `AUTH_LOST` — and nothing more? No
Check Run: that is A2b.

## Frozen prerequisites

A1 PR #1, A1b PR #2, A1b-R PR #3, A1c PR #4 — all draft, unmerged,
untouched. Governor App `4669438`, installation `155393018`, scoped to
`PhysShell/evm-from-scratch`. Program state carried in: A1 `PARTIAL`,
A1b `PASS`, A1b-R `PASS`, A1c `VIABLE_WITH_HUMAN_RECOVERY` with
natural-expiry recovery `NOT_TESTED`.

## Transport and trust

```text
TRANSPORT: Cloudflare Quick Tunnel  (owner's decision)
TRUST STATUS:
  - receiver: first-party
  - transport: third-party
  - payload confidentiality from Cloudflare: NOT PROVIDED
  - payload authenticity: enforced locally by GitHub HMAC
  - payload persistence by Cloudflare: not relied upon / not part of evidence
```

This host is WSL2 behind NAT (private `10.x`, egress only), so GitHub
cannot reach it directly. The tunnel is transport to our own process, not
a collector that receives and stores payloads as the terminal system.
Cloudflare could read plaintext after TLS termination; it could not
fabricate a delivery, because the HMAC secret never left this machine.
Cloudflare Quick Tunnel is **not** a production candidate.

Live URL: `https://flux-loves-communities-removing.trycloudflare.com`
(ephemeral by design; retired at teardown).

## Receiver contract

The order of operations *is* the contract:

```text
read raw body -> verify HMAC over raw bytes -> ONLY THEN consume the
X-GitHub-Delivery id -> reduce -> append a sanitized envelope
```

A rejected delivery leaves no trace in the idempotency store, so a forged
request cannot burn a delivery id that a genuine redelivery would need.
The HTTP layer is a thin shell over the same `Receiver.handle` the tests
exercise; the receiver creates no Check Runs, posts nothing to GitHub, and
triggers no providers.

The webhook secret was **never handled by a human**: it was generated
locally (0600) and written into the App via `PATCH /app/hook/config` with
an App JWT. The owner created the webhook with an empty secret from a
phone; the API supplied the rest. GitHub returned 200 and subsequent
deliveries verified.

## Signature verification — live

| time | event | outcome |
|---|---|---|
| 04:50:51 | `ping` (secret not yet set, unsigned) | **401, delivery id not consumed** |
| 04:51:23 | `installation.new_permissions_accepted` (signed) | 202, verified, consumed once |

The first row is the negative control the offline suite could only
simulate: an unsigned delivery was rejected outright, and the receiver's
idempotency store stayed empty for it.

## Idempotency — live, against GitHub's real behaviour

`POST /app/hook/deliveries/{id}/attempts` was used to make GitHub redeliver
the `pull_request.synchronize` delivery. **GitHub reused the same GUID**
`944ad5c0-9de5-11f1-91e9-2ef4aa12fd77`:

```text
04:54:40  guid 944ad5c0…  EPOCH_OPENED head=0f8223447c stale_marked=1
04:55:24  guid 944ad5c0…  DUPLICATE_IGNORED
```

Exactly-once state effect is therefore verified against GitHub's actual
redelivery semantics, not against a model of them.

## Epoch staleness — live

Probe PR **#17** (draft, never merged):

```text
04:53:25  synchronize head=e4e07459…  EPOCH_OPENED  stale_marked=0
04:54:40  synchronize head=0f822344…  EPOCH_OPENED  stale_marked=1
```

The first synchronize marked nothing stale — correctly: the PR was created
before the webhook existed, so the receiver had never seen an earlier epoch
for it. The second synchronize is the observation that matters: the
previous epoch became `STALE` and the new head became `CURRENT`. Replay of
the live log reproduces exactly that end state.

## Revocation event — live

The gap A1c had to record as `NOT_TESTED` is now closed, because a
first-party receiver existed:

```text
04:56:45  github_app_authorization  action=revoked
          sender PhysShell/45852143/User   signature verified
          effect AUTH_LOST
```

Recovery: Device Flow re-authorization at `04:58:57Z` produced credential
generation G3, leaving the program in a working state.

## Fail-closed semantics

- After `AUTH_LOST`, replaying the live log leaves provider triggers
  forbidden for every head, current or stale.
- A stale head can never carry a gate verdict forward: any gate state tied
  to a superseded head is dropped when the epoch goes `STALE`.
- `REFRESH_OUTCOME_UNKNOWN` (from A1c) also forbids triggers.
- **No gate state was ever established, let alone `CLEAN`.** The reducer
  contains no `CLEAN` state; a test asserts the source contains none, and
  another asserts no sequence of live or synthetic inputs produces one.
  Absent, malformed or uncertain evidence leaves `NOT_ESTABLISHED`, which
  is a failed gate.

## Raw evidence mapping

Ten verified deliveries were captured (`fixtures/delivery_log.json`,
`fixtures/live_deliveries.jsonl`):

| time | event / action | PR | effect |
|---|---|---|---|
| 04:51:23 | `installation.new_permissions_accepted` | — | `EVENT_IGNORED` |
| 04:53:01 | `check_suite.requested` | — | `EVENT_IGNORED` |
| 04:53:02 | `pull_request.synchronize` | 8 | `EPOCH_OPENED head=add0a0975e stale_marked=0` |
| 04:53:24 | `check_suite.requested` | — | `EVENT_IGNORED` |
| 04:53:25 | `pull_request.synchronize` | 17 | `EPOCH_OPENED head=e4e07459ba stale_marked=0` |
| 04:53:30 | `pull_request.edited` | 8 | `PR_ACTION_IGNORED:edited` |
| 04:54:40 | `pull_request.synchronize` | 17 | `EPOCH_OPENED head=0f8223447c stale_marked=1` |
| 04:54:40 | `check_suite.requested` | — | `EVENT_IGNORED` |
| 04:55:24 | `pull_request.synchronize` (redelivery) | 17 | `DUPLICATE_IGNORED` |
| 04:56:45 | `github_app_authorization.revoked` | — | `AUTH_LOST` |

Plus the rejected unsigned `ping` at 04:50:51, which by design left no
entry in the capture log — only a 401 and an untouched idempotency store.

Unrelated repository traffic (PR #8 activity, `check_suite` requests) was
verified, classified and ignored without touching any gate: a control
plane that only reacts to what it understands.

Captures record `signature_present` / `signature_verified` — never a
signature value, never the secret. Payloads are reduced to identity,
action and SHA fields.

## Negative controls

- Unsigned `ping` rejected live (401) without consuming its delivery id.
- Offline adversarial suite: forged signature, body tampering under a
  valid signature, delivery-id burning, duplicate delivery, malformed
  payload, malformed JSON, unknown event, stale-head triggers,
  `AUTH_LOST`, `REFRESH_OUTCOME_UNKNOWN`, and a test that the reducer
  source contains no `CLEAN` state.
- 29 tests pass in total (20 contract + 9 live replay).

## Result

```text
RECEIVER_REACHABLE                                    PASS
SIGNATURE_VERIFICATION                                PASS (live)
INVALID_SIGNATURE_REJECTED_BEFORE_DELIVERY_CONSUMPTION PASS (live unsigned ping
                                                            + offline forgery)
DELIVERY_IDEMPOTENCY                                  PASS (live GitHub redelivery,
                                                            same GUID)
SYNCHRONIZE_EPOCH_STALENESS                           PASS (live)
REVOCATION_EVENT_AUTH_LOST                            PASS (live)
TRIGGERS_FORBIDDEN_WHEN_NOT_AUTHORIZED                PASS
NO_CLEAN_MANUFACTURE                                  PASS

WEBHOOK_CONTROL_PLANE_CONTRACT: PASS
```

## What this DOES prove

- A first-party receiver can verify GitHub's HMAC over the raw body and
  reject unsigned traffic **before** touching delivery-id state.
- GitHub's redelivery reuses the delivery GUID, so a delivery-id
  idempotency key is the right exactly-once mechanism — verified against
  the real service.
- `pull_request.synchronize` gives the Governor everything it needs to
  supersede a review epoch: the new head arrives signed, and the previous
  epoch can be marked `STALE` deterministically.
- `github_app_authorization.revoked` is delivered to an App's webhook and
  identifies the revoking user, so authorization loss is detectable by
  **push**, not only by the 401-on-use path A1c had to rely on.
- The control plane can be built so that no input sequence manufactures a
  passing gate.

## What this DOES NOT prove

- Nothing about Check Runs, required checks, or enforcement — none were
  created; that is A2b and beyond.
- Not that this transport is production-grade: the tunnel is ephemeral,
  third-party, and confidentiality-free by construction.
- Not reconciliation after a genuinely missed delivery: the `UNCERTAIN`
  path is implemented and tested, but no delivery was actually lost.
- Not durability: the receiver's state is in-process. A production
  Governor needs the epoch and idempotency stores persisted, with the same
  ordering guarantee across restarts.
- Not ordering under concurrency: deliveries arrived serially here.
- Not that provider evidence means anything — A2a establishes transport,
  not verdicts.

## Production consequence

```text
webhook delivery  -> HMAC verified locally  -> delivery id consumed once
                  -> epoch superseded on synchronize
                  -> AUTH_LOST on revocation, by push
gate state        -> NOT_ESTABLISHED until something establishes it
                  -> never CLEAN by omission
```

Two constraints for whoever builds the durable version: the idempotency
store must be committed *before* any externally visible effect, or a crash
between effect and commit re-runs the effect on redelivery; and the epoch
store must be keyed by full head SHA, since the whole point is that a new
push invalidates prior review evidence.

## Teardown

- Webhook secret rotated via API to a value nobody holds; the experiment's
  local secret retired to `webhook-secret.retired-a2a` (0600).
- The API cannot blank a webhook URL (`Url cannot be blank`) nor point it
  at a private address, so the App's webhook must be deactivated in the UI
  — the one remaining owner action. Until then GitHub will simply fail to
  deliver, since the tunnel is down.
- Receiver and tunnel stopped; probe PR #17 closed without merge.

## Next gate

```text
A2b (gated by this): Governor-owned ai/final-review-shadow Check Run
     -> exact full HEAD SHA
     -> stale-head invalidation
     -> reconciliation after a missed webhook
     -> Governor verdict provenance

A3 (gated): enforcement / expected source
```

Carried forward as fixed constraints: a Governor Check Run publishes the
Governor's own verdict derived from advisory provider evidence and does not
upgrade a provider carrier into authoritative provider provenance
(A1b-c3); only a known-current `AUTHORIZED` state may issue provider
triggers (A1c); an authorization-loss state must render the gate failed,
never passed.
