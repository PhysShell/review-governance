# A1c — User authorization lifecycle, rotation and revocation: final report

Program `ai-final-review` · Control plane `PhysShell/review-governance` ·
Branch `experiment/user-authorization-lifecycle` · Date: 2026-08-22 (all
times UTC). Preregistered protocol:
`experiments/user-authorization-lifecycle/PROTOCOL.md` (amendment A1c-c1
committed mid-experiment, before any classification it governs).

## Question

Can the App-mediated user credential chain — the trigger identity proven in
A1b and A1b-R — be **operationally viable for an unattended Governor**?

## Frozen prerequisites

Verified before preregistration, zero drift: A1 PR #1 draft `d4bf2918…`;
A1b PR #2 draft `7b6c6c9e…`; A1b-R PR #3 draft `1d6b5ca2…`; App `4669438`;
installation `155393018` (selected, only `PhysShell/evm-from-scratch`);
governor bot `319376779`; user `PhysShell` `45852143`. No provider commands
were issued in A1c; Codex and CodeRabbit took no part.

## Credential generations

Named G0/G1/G2 to avoid collision with experiment names. Only SHA-256[:16]
fingerprints, prefix classes and lifetimes were recorded — never a token
value, never a partial one.

| gen | origin | access fp | refresh fp | access class | refresh class | issued |
|---|---|---|---|---|---|---|
| G0 | device flow (A1b) | `dc87549ea0062906` | `bd9ca2a8dd6bfd5b` | `ghu_` | `ghr_` | 03:31:38 |
| G1 | refresh grant | `ae87624c33db188e` | `ecc99eb370c91982` | `ghu_` | `ghr_` | 04:15:36 |
| G2 | device flow re-auth | `3ab6f5265858095a` | `86f172b15a231c60` | `ghu_` | `ghr_` | 04:22:20 |

All three generations are distinct in both tokens: a refresh rotates the
whole pair, not just the access token.

## Refresh experiment

`04:14:36Z` G0 access → 200 `PhysShell`; carrier **C0** captured
(`5377815336`); installation identity independently usable (probe PR #16 at
frozen head `3879664eb01699a9046463ec014896005fff04e4`).

`04:15:36Z` — exactly one refresh with `grant_type=refresh_token`:

```text
HTTP 200 · granted · token_type bearer
expires_in 28800            (8 h)
refresh_token_expires_in 15897600  (6 months)
```

GitHub's documented lifetimes were treated as claims and matched the actual
response exactly. The new pair was committed to the durable store under an
exclusive lock with a compare-and-swap against the previous generation.

## Rotation evidence

```text
G1 access  -> GET /user   200, PhysShell/45852143/User
G0 access  -> GET /user   401 "Bad credentials"
G0 refresh -> refresh     rejected (see A1c-c1)
```

→ `REFRESH_ROTATION: PASS`, `OLD_ACCESS_INVALIDATION: PASS`,
`OLD_REFRESH_INVALIDATION: PASS`.

**Amendment A1c-c1 — the rejection surface is generic, misleading, and
arrives as HTTP 200.** Re-using the consumed refresh token produced:

```text
HTTP 200 · error "incorrect_client_credentials"
"The client_id and/or client_secret passed are incorrect."
```

The preregistered expectation `bad_refresh_token` was never observed. A
control with a **never-issued** refresh token (same client id, consuming
nothing) returned a byte-identical error, proving the message is a generic
rejection and not a statement about client credentials. Two operational
consequences:

1. **Failure is reported with HTTP 200.** Status-code-based handling reads
   a rejected refresh as success. Only the presence of `access_token` in
   the body distinguishes them.
2. **The error accuses the wrong component.** In the race scenario the
   losing worker's only evidence points at App credentials — away from the
   true cause, "another worker already rotated". The durable-generation
   re-read is therefore not a nicety but the sole correct disambiguator.

## Carrier preservation

The carrier — what GitHub shows on an artifact created by the credential —
is identical across all three generations:

```text
C0 (G0, 04:14:47)  user PhysShell/45852143/User   via physshell-review-governor/4669438
C1 (G1, 04:16:45)  user PhysShell/45852143/User   via physshell-review-governor/4669438
C2 (G2, 04:22:36)  user PhysShell/45852143/User   via physshell-review-governor/4669438
```

→ `CARRIER_PRESERVATION_AFTER_REFRESH: PASS`,
`CARRIER_PRESERVATION_AFTER_REAUTH: PASS`. Rotation and even a full
revoke/re-authorize cycle do not change how the Governor appears to
providers — which is what makes A1b/A1b-R's trigger authority survivable
across credential changes.

## Concurrency hazard

Single-use rotation implies a race: two workers refreshing generation *n*
produce one winner and one worker holding a rejected refresh token while
authorization is perfectly healthy. The durable store makes the resolution
mechanical rather than heuristic: refresh is serialized by an exclusive
lock, and the commit is a compare-and-swap, so the loser cannot overwrite
the winner (`GenerationRaceLost`, tested). The reducer's rule — *re-read
the durable generation before concluding anything* — turns the loser's
misleading `incorrect_client_credentials` into "adopt generation n+1".
Adversarial tests cover the loser's path, the no-newer-generation path, and
the requirement that no error string is ever a control-flow key.
→ `CONCURRENT_REFRESH_SAFETY: PASS` (model + tests).

## Ambiguous refresh outcome

```text
GitHub accepts Rn -> invalidates Gn -> returns Gn+1 -> response lost
                                                        before durable commit
```

The client then holds no valid pair and never persisted the new one, and
GitHub offers no way to re-read an issued refresh token. This is marked
**INFERRED FROM OBSERVED ROTATION, not OBSERVED**: no destructive
packet-loss test was run, precisely because there is no safe recovery from
a genuinely lost generation. The reducer maps it to
`REFRESH_OUTCOME_UNKNOWN`: never a blind retry, no provider triggers, and
resolution only if the durable store turns out to have advanced; otherwise
human re-authorization. → `AMBIGUOUS_OUTCOME_FAIL_CLOSED: PASS` (model).

## Revocation experiment

The user revoked the *authorization* of `physshell-review-governor` (not an
uninstall) between `04:18:17Z` and `04:20:20Z`. Observed immediately after:

```text
G1 access  -> GET /user            401 "Bad credentials"
G1 refresh -> refresh grant        rejected (same generic error)
installation token -> probe PR     200, head 3879664e…, still usable
```

→ `USER_REVOCATION_ACCESS: PASS`, `USER_REVOCATION_REFRESH: PASS`,
`INSTALLATION_SURVIVES_USER_REVOCATION: PASS`.

Revocation kills the entire user chain — both tokens, not just the access
token — so it is unrecoverable without a human.

## Authorization-loss detection

Detected by **use**: a poller on `GET /user` saw the 401 transition at
`04:20:20Z`, on the 7th poll of a 20-second cycle that began at
`04:18:17Z`. Detection latency is therefore bounded by the polling/usage
interval, not by any push signal. → `AUTH_LOSS_DETECTION: PASS`.

`AUTH_LOSS_WEBHOOK_DETECTION: NOT_TESTED`. The App has **no webhook
configured** (`GET /app/hook/config` → 404, `events: []`), so
`github_app_authorization` cannot be delivered anywhere, and the protocol
forbids routing GitHub payloads through a third-party collector — which is
the only way to expose a receiver from this WSL2 environment. No substitute
signal was invented; the observed 401 path is the fail-closed detector that
must work regardless.

## Installation independence

The installation identity was probed before the refresh, before the
revocation, immediately after the revocation, and after re-authorization —
usable every time (token minted, probe PR read 200). The Governor's control
plane is unaffected by the loss of the user authorization.

## Human recovery

Device Flow was run again at `04:22:20Z`, producing G2 — a **new
authorization generation**, not a continuation of the revoked chain, and
committed through the same CAS store. G2 access → 200 `PhysShell`; carrier
C2 identical to C0/C1. → `HUMAN_REAUTH_RECOVERY: PASS`.

## Security review

- Credential material lives only in `~/.config/review-governor/` at 0600;
  the store writes via atomic replace and never returns token values to
  callers.
- Evidence contains only SHA-256[:16] fingerprints, prefix classes and
  lifetimes.
- Secret scan over the **working tree and every commit on this branch** for
  token shapes, JWTs, and Authorization headers: clean. The only matches
  are the code that *constructs* a header and deliberate synthetic strings
  in the scanner's own positive-control test.
- The scanner's first version flagged the word "authorization" in prose
  (`github_app_device_flow_reauthorization`). It was tightened to
  credential *shapes* and given a positive control, so the tightening
  cannot silently disable it.
- 31 tests pass (adversarial reducer, CAS store, live replay).

## Result

```text
Refresh rotates pair                     PASS
Proactive refresh before expiry          PASS
Old access invalidated (by rotation)     PASS
Old refresh invalidated                  PASS
Recovery after NATURAL access expiry     NOT_TESTED
Carrier preserved after refresh          PASS
Revoked access rejected                  PASS
Revoked refresh rejected                 PASS
Revocation webhook                       NOT_TESTED (no webhook configured;
                                                     no receiver without a
                                                     third-party collector)
Installation survives user revoke        PASS
Human reauthorization restores carrier   PASS
Concurrent refresh safety model          PASS (model + adversarial tests)
Ambiguous outcome fail-closed            PASS (model; hazard INFERRED)
Authorization-loss detection             PASS (401 on use, ≤ poll interval)

USER_AUTH_LIFECYCLE:          VIABLE_WITH_HUMAN_RECOVERY
UNATTENDED_TRIGGER_AUTHORITY: HUMAN_DEPENDENT
```

Answers to the five operational questions:

| question | answer | basis |
|---|---|---|
| Refresh without human involvement? | **Yes** | observed rotation, carrier preserved |
| Avoid ordinary access-token expiry without a human? | **Yes** | observed via *proactive* refresh (G0 rotated 44 min after issue, well inside its 8 h life) |
| Recover *after* the access token has already expired naturally, while the refresh token is still valid? | **NOT_TESTED** | no natural expiry occurred in this experiment |
| Recover from user revocation without a human? | **No** | observed: both tokens dead |
| Survive a lost successful refresh response? | **No**, absent a newer durable generation | inferred from observed rotation |
| Does installation-side coordination survive? | **Yes** | observed at every phase |

## What this DOES prove

- The refresh grant rotates the **entire pair**; the old access token dies
  immediately (401) and the consumed refresh token is rejected. Documented
  lifetimes (8 h / 6 months) matched the live response.
- The Governor's observable carrier is stable across rotation and across a
  complete revoke/re-authorize cycle, so trigger authority proven in A1b /
  A1b-R is not invalidated by credential turnover.
- Revocation is total for the user chain and **partial for the system**:
  the installation identity keeps working. Degradation is exactly the
  useful shape — control plane up, trigger path down.
- Loss is detectable without a webhook, by use, with latency bounded by the
  polling interval.
- The refresh rejection surface is generic (`incorrect_client_credentials`,
  HTTP 200) and identical for consumed and never-issued tokens, so error
  strings and status codes are unusable as control-flow keys.

## What this DOES NOT prove

- Not that the webhook path works: `github_app_authorization` was never
  delivered or validated here.
- Not that the ambiguous-outcome hazard behaves as modelled — it was
  reasoned from the observed single-use rotation, never provoked.
- **Not that recovery from a naturally expired access token works.** No
  natural expiry occurred: G0 was issued `03:31:38Z` with `expires_in`
  28800 and was rotated at `04:15:36Z`, about 44 minutes later. What was
  observed is *proactive* refresh well inside the token's life, plus
  invalidation of the old access token **by rotation** — not by the clock.
  Refreshing after the access token has already lapsed is `NOT_TESTED`
  (amendment A1c-c2).
- Not anything about long-horizon behaviour: the 6-month refresh lifetime,
  refresh-token expiry, or repeated rotation over weeks were not observed.
- Not that a production token daemon is safe; none was built, and the CAS
  store here is a single-host file lock, not a distributed one.
- Not production enforcement readiness. `PRODUCTION_ENFORCEMENT` remains
  `NOT_READY_FOR_ENFORCEMENT`.

## Production consequence

```text
Governor control plane        UP        (installation identity unaffected)
External-review trigger path  AUTH_LOST (both user tokens dead)
Merge authorization           FAIL CLOSED
Recovery                      HUMAN REAUTHORIZATION REQUIRED
```

An unattended Governor can carry itself across ordinary token expiry, and
can survive concurrent workers provided refresh is CAS-serialized against a
durable generation. It cannot carry itself across revocation or a lost
refresh response. Therefore the trigger path must be designed to **fail
closed into a human-recoverable state**, and the gate must never interpret
missing credentials as a passing check — the failure mode this whole
program exists to prevent.

A second consequence for whoever builds the daemon: a multi-host Governor
needs a genuinely distributed CAS for the credential generation. The
hazard is not "the token expired" but "two hosts rotated and one of them
believes the App credentials are broken".

## Next gate

```text
A2 (still gated, unblocked only by a separate decision after this review):
    installation webhook -> signed delivery -> pull_request.synchronize
    -> STALE epochs -> Governor-owned non-required Check Run -> HEAD binding

Carried into A2 as fixed constraints:
  * a Governor Check Run publishes the Governor's own verdict derived from
    advisory provider evidence; it does not upgrade a provider carrier into
    authoritative provider provenance (A1b-c3)
  * no state other than a known-current AUTHORIZED may issue provider
    triggers (A1c)
  * an authorization-loss state must render the gate failed, never passed

Deferred, optional follow-ups (not scheduled):
  * webhook delivery of github_app_authorization, once a receiver exists
    that needs no third-party collector — naturally folded into A2
  * long-horizon refresh behaviour and refresh-token expiry
```
