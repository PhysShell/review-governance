# A1c — User authorization lifecycle, rotation and revocation (preregistered)

Status: **PREREGISTERED** — committed before any refresh, revocation or
re-authorization was attempted, and before the current credential pair was
touched in any way.
Program: `ai-final-review` · Control plane: `PhysShell/review-governance` ·
Branch: `experiment/user-authorization-lifecycle`.

## Primary question

Can the App-mediated user credential chain be **operationally viable for an
unattended Governor**?

Measured independently:

```text
REFRESH_ROTATION
OLD_ACCESS_INVALIDATION
OLD_REFRESH_INVALIDATION
CARRIER_PRESERVATION_AFTER_REFRESH
USER_REVOCATION_ACCESS
USER_REVOCATION_REFRESH
INSTALLATION_SURVIVES_USER_REVOCATION
AUTH_LOSS_DETECTION
AUTH_LOSS_WEBHOOK_DETECTION
HUMAN_REAUTH_RECOVERY
CARRIER_PRESERVATION_AFTER_REAUTH
CONCURRENT_REFRESH_SAFETY
AMBIGUOUS_OUTCOME_FAIL_CLOSED
```

Final verdicts are not binary:

```text
USER_AUTH_LIFECYCLE:
  OPERATIONALLY_VIABLE | VIABLE_WITH_HUMAN_RECOVERY | NOT_VIABLE | INCONCLUSIVE

UNATTENDED_TRIGGER_AUTHORITY:
  SUSTAINABLE | HUMAN_DEPENDENT | NOT_SUSTAINABLE
```

The result is not to be fitted to a desired production architecture.

## Frozen prerequisites

Verified against the API immediately before this preregistration:

| Item | Frozen value |
|---|---|
| A1 PR | `review-governance` #1, draft, `d4bf2918ae495ab1dbc651560a05be0a791aead7` |
| A1b PR | `review-governance` #2, draft, `7b6c6c9ee14b09968938cb5e2ba00dc4bd78876e` |
| A1b-R PR | `review-governance` #3, draft, `1d6b5ca2cc00a9de285e7240f0a51b89bf8e8403` |
| Governor App | id `4669438`, slug `physshell-review-governor` |
| Installation | `155393018`, `repository_selection: selected`, only `PhysShell/evm-from-scratch` |
| Governor bot | `physshell-review-governor[bot]` = `319376779` |
| GitHub user | `PhysShell` = `45852143`, type `User` |

Frozen evidence from A1 / A1b / A1b-R is never rewritten. Established
architecture under test here:

```text
Governor installation identity      -> coordinator / future webhooks / state / Check Runs
Governor App-mediated user identity -> @codex review, @coderabbitai full review
```

Codex and CodeRabbit take **no part** in A1c: no provider commands are
issued. (Provider auto-activity on PR creation is outside the experiment's
control; it is recorded in the baseline and never acted upon.)

## Credential generation naming

The program already uses `A1`, `A1b`, `A1c` for experiments, so credential
generations are named `G0`, `G1`, `G2` to avoid collision:

```text
G0 = the pair in use before A1c   (access A0 / refresh R0 in the task wording)
G1 = the pair produced by the single deliberate refresh
G2 = the pair produced by human re-authorization after revocation
```

Only safe metadata is recorded: `SHA-256(token)` truncated to 16 hex
characters as a generation fingerprint, prefix **class** (`ghu_` / `ghr_`),
`expires_in`, `refresh_token_expires_in`, issue timestamps. Never a token
value, never a prefix+tail, never any reversible representation.

Recorded G0 baseline (captured before any A1c action):

```text
access  fingerprint dc87549ea0062906  class ghu_  expires_in 28800
refresh fingerprint bd9ca2a8dd6bfd5b  class ghr_  refresh_token_expires_in 15897600
obtained 2026-08-22T03:31:38Z via github_app_device_flow
```

## Lifecycle probe

A new disposable **draft** PR in `PhysShell/evm-from-scratch`
(`probe/user-authorization-lifecycle`), one documentation-only file, HEAD
frozen for the whole experiment, never merged, no provider commands. It
exists only to carry benign attribution comments.

## Phase R — refresh rotation

1. **Baseline**: `G0.access → GET /user` must be 200/`PhysShell`; a benign
   comment posted with `G0.access` and read back defines carrier **C0**
   (`user = PhysShell/45852143/User`,
   `performed_via_github_app = physshell-review-governor/4669438`);
   the installation token must independently reach the probe PR (200).
2. **Exactly one real refresh** with `grant_type=refresh_token`,
   `refresh_token = G0.refresh`, producing **G1**. Documented GitHub values
   (access 28800 s, refresh 15897600 s) are treated as claims to compare
   against the actual response, not as observations.
3. **Rotation semantics**, in this order, capturing actual HTTP status and
   structured body:

   ```text
   G1.access  -> GET /user            expected 200
   G0.access  -> GET /user            expected authentication failure
   G0.refresh -> refresh endpoint     expected bad_refresh_token
   ```

   → `REFRESH_ROTATION`, `OLD_ACCESS_INVALIDATION`,
   `OLD_REFRESH_INVALIDATION`.
4. **Carrier preservation**: a benign comment with `G1.access` defines
   **C1**; `CARRIER_PRESERVATION_AFTER_REFRESH` passes only if C1 matches
   C0 on user login, user id, user type and app slug/id.

## Phase H — concurrency and ambiguity hazards (model + tests, not live)

The one-use rotation observed in Phase R implies two hazards that matter
more for an unattended gate than the 8-hour number:

```text
race:      W1 refresh(Rn) -> Gn+1 ;  W2 refresh(Rn) -> bad_refresh_token
ambiguity: GitHub accepts Rn, invalidates Gn, returns Gn+1,
           response lost before durable commit
```

A lifecycle reducer is implemented and adversarially tested over states:

```text
AUTHORIZED · REFRESH_DUE · REFRESHING · AUTHORIZED_NEW_GENERATION
AUTH_LOST · REAUTH_REQUIRED · REFRESH_OUTCOME_UNKNOWN
```

Invariants fixed in advance:

- `bad_refresh_token` **alone must never** mean "authorization revoked".
  The worker must first re-read the durable credential generation; if a
  newer generation exists, another worker rotated and the newer generation
  is adopted; only if none exists does `REAUTH_REQUIRED` become a candidate.
- Refresh is **single-writer / CAS-serialized** against the durable store.
- A lost/ambiguous refresh response yields `REFRESH_OUTCOME_UNKNOWN`, never
  a blind retry; recovery is human re-authorization when no newer durable
  generation exists.
- **No state other than a known-current `AUTHORIZED` may issue provider
  triggers.**

The ambiguous-outcome hazard is marked **INFERRED FROM OBSERVED ROTATION**,
not OBSERVED: no destructive live packet-loss test is run, because there is
no safe recovery path from a genuinely lost generation.

## Phase V — user revocation

Pre-revocation state must be confirmed: `G1.access` 200, `G1.refresh`
unconsumed, installation identity 200. Then a **manual** revocation by the
user (revoke the *authorization* of `physshell-review-governor`, **not**
uninstall the App). Exact UI steps are prepared and the experiment pauses
there.

Detection: `G1.access → GET /user` is polled; the transition to 401 is the
observed authorization-loss signal (`AUTH_LOSS_DETECTION`). After the
transition:

```text
G1.access  -> GET /user           expected 401 Bad Credentials
G1.refresh -> refresh endpoint    expected failure
installation token -> probe PR    expected still 200
```

→ `USER_REVOCATION_ACCESS`, `USER_REVOCATION_REFRESH`,
`INSTALLATION_SURVIVES_USER_REVOCATION`.

**Webhook capture** (`github_app_authorization`, `action: revoked`) is
attempted only if a receiver can be exposed without routing unsanitized
GitHub payloads through a third-party collector, and only as a capture-only
receiver: HMAC validation, sanitized envelope, timestamp. It implements no
Governor engine, no `pull_request` events, no Check Runs. If that is not
possible in this environment, the result is recorded as
`AUTH_LOSS_WEBHOOK_DETECTION: NOT_TESTED` with the reason, and detection
rests on the observed 401 path. No substitute is invented.

## Phase Recovery — human re-authorization

Device Flow is run again, producing **G2** — treated as a *new
authorization generation*, not a continuation of G1. Checks: `G2.access →
GET /user` = `PhysShell`; a benign comment defines carrier **C2**, which
must match C0/C1 → `HUMAN_REAUTH_RECOVERY`,
`CARRIER_PRESERVATION_AFTER_REAUTH`. Installation identity must have
remained reachable throughout. No provider triggers afterwards.

## The operational questions the report must answer

```text
Can the Governor refresh without human involvement?
Can it survive ordinary access-token expiry?
Can it recover from user revocation without a human?
Can it survive a lost successful refresh response?
Does installation-side coordination survive loss of user authorization?
```

Anticipated but not predetermined: refresh happy path potentially
unattended; explicit revocation requiring human recovery; lost successful
refresh likely requiring human recovery. Only the experiment decides.

## Security invariant

The credential store is treated as production-grade secret material.
Forbidden: token values in git, refresh tokens in fixtures, Authorization
headers in captures, device codes in the repo, shell xtrace around
credentials, environment dumps, plaintext debug logging. Before the draft
PR: a secret scan over the **working tree and the full git history** of
this branch for `ghu_`, `ghr_`, `ghs_`, `gho_`, JWT-shaped strings and
Authorization headers.

## Evidence plan

Sanitized fixtures in
`experiments/user-authorization-lifecycle/fixtures/`: generation metadata
(fingerprints only), carrier captures C0/C1/C2, rotation probe results,
revocation probe results, installation-independence checks, final
inventory. Replay tests over those fixtures plus adversarial reducer tests
for the race and the ambiguous outcome.

Report: `docs/experiments/user-authorization-lifecycle.md` with sections
Question / Frozen prerequisites / Credential generations / Refresh
experiment / Rotation evidence / Carrier preservation / Concurrency hazard
/ Ambiguous refresh outcome / Revocation experiment / Authorization-loss
detection / Installation independence / Human recovery / Security review /
Result / What this DOES prove / What this DOES NOT prove / Production
consequence / Next gate.

## Amendments

- **A1c-c1 (2026-08-22, during Phase R, before any classification) — the
  refresh rejection surface is generic, misleading, and returns HTTP 200.**

  Observed on the live refresh endpoint:

  ```text
  04:15:36Z  refresh with G0.refresh (valid, unused)
             -> HTTP 200, access_token present, pair rotated to G1

  04:15:46Z  refresh with G0.refresh again (now consumed)
             -> HTTP 200, no token, error "incorrect_client_credentials",
                "The client_id and/or client_secret passed are incorrect."

  04:16:19Z  control: never-issued refresh token, same client_id
             -> HTTP 200, identical error, identical description
  ```

  Two consequences fixed here, before they can be smoothed over:

  1. **The endpoint reports failure with HTTP 200.** Status-code-based
     handling would read a rejected refresh as success. Only the presence
     of `access_token` in the body distinguishes them; the harness keys on
     exactly that.
  2. **The error name accuses the wrong thing.** A consumed refresh token
     and a never-issued one produce the same `incorrect_client_credentials`
     — the preregistered expectation `bad_refresh_token` was not observed
     at all. An operator or a worker reading that string would investigate
     App client credentials while the actual cause is a rotated or invalid
     refresh token. In the race scenario this is worse than noise: the
     loser worker's evidence *points away* from "another worker rotated".

  The reducer event is therefore named `refresh_rejected` (carrying the
  observed error as data, never as a control-flow key), and the
  preregistered invariant stands unchanged and reinforced: any refresh
  rejection triggers a durable-generation re-read first, and never by
  itself implies revocation.

- **A1c-c2 (2026-08-22, before freeze) — proactive refresh is not natural
  expiry.** The result matrix carried "survive ordinary access-token
  expiry: yes, observed". No natural expiry occurred: G0 was issued
  `03:31:38Z` with `expires_in` 28800 and refreshed at `04:15:36Z`, ~44
  minutes into an 8-hour life. Old-access invalidation was caused by
  **rotation**, not by the clock. The claim is split:

  ```text
  PROACTIVE_REFRESH:                    PASS        (observed)
  OLD_ACCESS_INVALIDATION:              PASS        (observed, by rotation)
  POST_NATURAL_EXPIRY_REFRESH_RECOVERY: NOT_TESTED
  ```

  and the operational question becomes two:

  ```text
  Can the Governor avoid ordinary access-token expiry without a human?
      YES — observed, via proactive refresh.
  Can it recover after the access token has already expired naturally,
  while the refresh token remains valid?
      NOT_TESTED.
  ```

  Epistemic labelling only; no observation, capture or aggregate verdict
  changes. `USER_AUTH_LIFECYCLE: VIABLE_WITH_HUMAN_RECOVERY` and
  `UNATTENDED_TRIGGER_AUTHORITY: HUMAN_DEPENDENT` stand. Production intent
  is to refresh proactively and never reach expiry, so this gap is a label
  to carry forward, not a blocker.

## Forbidden in A1c

Triggering Codex or CodeRabbit; building a production token daemon;
enabling a required check; handling `pull_request.synchronize`; multi-repo
rollout; starting A2; merging PR #1, PR #2, PR #3 or the probe PR;
rewriting frozen experiments.

## Stop rule

After the verdict: fixtures, replay/adversarial tests, report, close the
lifecycle probe PR without merge, open a separate draft PR in
`review-governance`, stop. A2 may be unblocked only by a separate decision
after A1c is reviewed.
