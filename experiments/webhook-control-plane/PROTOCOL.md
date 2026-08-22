# A2a — Webhook control-plane contract (preregistered)

Status: **PREREGISTERED** — committed before any receiver was exposed, any
webhook configured, or any live delivery observed.
Program: `ai-final-review` · Control plane: `PhysShell/review-governance` ·
Branch: `experiment/webhook-control-plane`.

## Question

Can the Governor establish a **trustworthy webhook control plane** — and
nothing more? Exactly this chain, end to end:

```text
public first-party receiver
    -> valid GitHub App webhook delivery
    -> HMAC verification
    -> X-GitHub-Delivery idempotency
    -> pull_request.synchronize
    -> current ReviewEpoch -> STALE

+ github_app_authorization.revoked
    -> AUTH_LOST
```

**No Check Run.** Publishing a Governor-owned check run is A2b, gated by
this experiment. The split is deliberate: webhook transport is currently
zero (`GET /app/hook/config` → 404 as of A1c), and if receiver, epoch
reducer and Checks API arrive together, a failure has three layers each
able to blame its neighbour.

Verdict:

```text
WEBHOOK_CONTROL_PLANE_CONTRACT: PASS | PARTIAL | FAIL
```

with these results reported independently:

```text
RECEIVER_REACHABLE
SIGNATURE_VERIFICATION
INVALID_SIGNATURE_REJECTED_BEFORE_DELIVERY_CONSUMPTION
DELIVERY_IDEMPOTENCY
SYNCHRONIZE_EPOCH_STALENESS
REVOCATION_EVENT_AUTH_LOST          (live this time — A1c had no receiver)
TRIGGERS_FORBIDDEN_WHEN_NOT_AUTHORIZED
NO_CLEAN_MANUFACTURE
```

## Frozen prerequisites

| Item | Value |
|---|---|
| A1 PR | `review-governance` #1, draft, `d4bf2918…` |
| A1b PR | #2, draft, `7b6c6c9e…` |
| A1b-R PR | #3, draft, `1d6b5ca2…` |
| A1c PR | #4, draft (frozen after amendments A1c-c1, A1c-c2) |
| Governor App | id `4669438`, installation `155393018`, only `PhysShell/evm-from-scratch` |
| Credential state | generation G2 (device-flow re-auth `04:22:20Z`) |

Frozen program state carried in as constraints:

```text
A1     App direct triggers            PARTIAL
A1b    Codex App-mediated user        PASS
A1b-R  CodeRabbit App-mediated user   PASS
A1c    lifecycle                      VIABLE_WITH_HUMAN_RECOVERY
       natural-expiry recovery        NOT_TESTED
```

## Invariants (fixed before implementation)

```text
invalid HMAC
    -> discard before the delivery id is consumed or recorded

duplicate X-GitHub-Delivery
    -> exactly-once state effect

pull_request.synchronize(new_sha)
    -> every epoch of that PR whose head differs becomes STALE

github_app_authorization.revoked
    -> AUTH_LOST immediately

AUTH_LOST                    -> provider triggers forbidden
REFRESH_OUTCOME_UNKNOWN      -> provider triggers forbidden
missing / malformed webhook / reconciliation uncertainty
    -> never manufacture CLEAN
```

The last one is the reason the whole program exists: absent evidence must
never read as a passing gate. In A2a the reducer has **no code path to
`CLEAN` at all**, and tests assert that no sequence of inputs produces one.

## Method

1. **Offline first.** Receiver, signature verification, idempotency store
   and epoch/gate reducer are implemented and adversarially tested against
   synthetic deliveries signed with a locally generated secret. This part
   depends on nothing external and is committed before any exposure.
2. **Transport decision (owner's call).** The receiver must be reachable by
   GitHub. This host is WSL2 behind NAT (private `10.x` addresses, egress
   only), so a tunnel or an owned host is required. The options differ in
   *who can see the payload in transit*, which is a trust decision and is
   therefore recorded, not assumed:

   ```text
   Cloudflare Quick Tunnel  no account; TLS terminates at Cloudflare,
                            which can see payload plaintext in transit
   Tailscale Funnel         account required; TLS terminates on this host,
                            relay forwards ciphertext
   owner-operated host      no third party in path; needs a public host
   ```

   Whichever is chosen, the receiver stays **first-party**: no third-party
   collector stores, displays or replays payloads.
3. **Webhook configuration** (manual, owner): set the App's webhook URL and
   a freshly generated secret. The secret goes to `~/.config/review-governor/`
   at 0600 and never into the repo, chat, or evidence. Subscribed events
   are limited to `pull_request` and `github_app_authorization`.
4. **Live captures**: a benign synchronize on a disposable draft probe PR
   (a documentation-only commit that changes the head SHA), and a real
   `github_app_authorization` revocation — the event A1c could not test
   because no receiver existed. Deliveries are recorded sanitized:
   headers reduced to delivery id / event / signature *presence*, payload
   reduced to identity and SHA fields. **No signature value, no secret.**
5. **Recovery**: after the live revocation the user re-authorizes (Device
   Flow), producing a new credential generation, so the program is left in
   a working state.

## Classification

`PASS` requires every independently-reported result above to pass, with the
live revocation event observed and verified. `PARTIAL` if the control-plane
mechanics pass but a live event could not be observed. `FAIL` if any
invariant is violated in the live path.

Silence is never interpreted as success: an unobserved delivery is recorded
as `NO_OBSERVED_DELIVERY`, and the gate state stays `NOT_ESTABLISHED`.

## Forbidden in A2a

Creating a Check Run; making any check required; ruleset or
branch-protection changes; triggering Codex or CodeRabbit; multi-repo
rollout; a production token daemon; merging PRs #1–#4 or the probe PR;
rewriting frozen experiments; routing payloads through a third-party
collector.

## Evidence and report

Sanitized fixtures in `experiments/webhook-control-plane/fixtures/`;
adversarial tests over the reducer and the verifier; report at
`docs/experiments/webhook-control-plane.md` with sections Question /
Frozen prerequisites / Transport and trust / Receiver contract / Signature
verification / Idempotency / Epoch staleness / Revocation event /
Fail-closed semantics / Raw evidence mapping / Negative controls / Result /
What this DOES prove / What this DOES NOT prove / Production consequence /
Next gate. Probe PR closed without merge; A2a opens its own draft PR; stop
after the verdict. A2b (Governor-owned shadow Check Run) stays gated.
