# A1b-R — CodeRabbit user-attributed trigger authority: final report

Program `ai-final-review` · Control plane `PhysShell/review-governance` ·
Branch `experiment/coderabbit-user-attributed-trigger` · Date: 2026-08-22
(all times UTC). Preregistered protocol:
`experiments/coderabbit-user-attributed-trigger/PROTOCOL.md`.

## Question

Does CodeRabbit process `@coderabbitai full review` when the comment is

```text
user                     = PhysShell
performed_via_github_app = physshell-review-governor
```

— the **App-mediated user** carrier that A1b established and that A1 never
tested against CodeRabbit? A1 had measured only the installation-bot
carrier (61 min of silence) and the plain-human carrier (acknowledged in
5 s). The hypothesis under test: CodeRabbit may reject on
`user.type == "Bot"`, in which case an App-mediated user passes.

## Frozen prerequisites

Verified before preregistration, zero drift: A1 PR #1 draft at
`d4bf2918…`; A1b PR #2 draft at `7b6c6c9e…`; App `4669438`; governor bot
`319376779`; user `PhysShell` `45852143`; CodeRabbit actor
`coderabbitai[bot]` `136622811`; probe repository
`PhysShell/evm-from-scratch` id `1335599563`; PRs #11/#13/#14 closed
unmerged, #12 OPEN draft at `e29621f5…`. A1 and A1b verdicts were not
rewritten. Codex did not participate; no lifecycle work; A2 not started.

## Authentication model

The primary request used the **GitHub App user access token** from A1b
(Device Flow, `ghu_`, 8 h expiry, issued `03:31:38Z`, refresh token issued
and deliberately unused). Validity re-checked at `03:51:13Z` before the
experiment. No `gh` token, OAuth app token, PAT, installation token or
Actions token was used for the primary request. Secrets stayed in
`~/.config/review-governor/` at 0600.

## Probe PR

`PhysShell/evm-from-scratch` PR **#15**, draft, branch
`probe/coderabbit-user-attributed-trigger`, one harmless document, frozen
head `44ed22c487ae59528e0840e03ff983c6fea3bfcb` — unchanged and still draft
throughout; closed without merge afterwards. PRs #11/#12/#13/#14 untouched;
no repository configuration changed.

## Attribution observation

Benign probe comment `5377728754` at `03:55:54Z`, posted with the user
token and read back:

```text
user:                     PhysShell / 45852143 / User
performed_via_github_app: physshell-review-governor (id 4669438)
```

`APP_MEDIATION_OBSERVABILITY: PASS`. Identical carrier shape to A1b, on a
different PR — the mediation field is reproducible, not a one-off.

## CodeRabbit observation

- `03:55:48Z` — CodeRabbit auto-summary on PR open (baseline, pre-trigger).
- `03:55:54Z` → `04:00:19Z` — settle window, 5 polls: the benign
  App-mediated comment drew **no** CodeRabbit activity. No contamination.
- `04:00:31Z` — **primary trigger**: `@coderabbitai full review`, comment
  `5377746719`, author `PhysShell`, mediated by
  `physshell-review-governor`.
- `04:00:39Z` — **8 seconds later** — `coderabbitai[bot]` (`136622811`)
  replied with a parsed command invocation:

  > `CodeRabbit review command invocation: 13b96ad7-d232-4b98-86aa-72a67ed593b7`
  > ⚠️ Action not completed — "Review rate limited. Your included review
  > limit is currently reached under our Fair Usage Limits Policy… Your
  > next included review will be available in 49 minutes."

This is explicit provider handling of *this* command: the command was
parsed, given an invocation id, evaluated, and declined for quota. Per the
preregistered classification, `RATE_LIMITED` is a PASS for trigger
authority — the estimand is authority, not review completion. No matched
control was required (the protocol calls for one only if the mediated
command draws nothing). Quota recovery was not awaited; no review object
was emitted (`reviews: 0`, `review_comments: 0` at final reconciliation).

## Raw evidence mapping

Probe PR #15 inventory at final reconciliation:

| comment id | author (id) | time | role |
|---|---|---|---|
| 5377728347 | `coderabbitai[bot]` (136622811) | 03:55:48 | auto-summary on PR open (baseline) |
| 5377728754 | `PhysShell` (45852143) via `physshell-review-governor` | 03:55:54 | benign identity probe (no mention) |
| 5377746719 | `PhysShell` (45852143) via `physshell-review-governor` | 04:00:31 | **primary user-attributed trigger** |
| 5377747235 | `coderabbitai[bot]` (136622811) | 04:00:39 | **handling evidence**: invocation id + rate-limit notice |

Cross-experiment contrast, all same provider, same repository, same command
text:

| carrier | `user` | `performed_via_github_app` | CodeRabbit response |
|---|---|---|---|
| installation bot (A1) | `physshell-review-governor[bot]` (Bot) | `physshell-review-governor` | **nothing in 61 min** (`NO_OBSERVED_START`) |
| plain human (A1) | `PhysShell` (User) | `null` | acknowledged in **5 s** ("Full review triggered") |
| **App-mediated user (A1b-R)** | `PhysShell` (User) | `physshell-review-governor` | handled in **8 s** (invocation id + rate limit) |

Sanitized fixtures:
`experiments/coderabbit-user-attributed-trigger/fixtures/` — identity
readback, benign probe, primary request, rate-limit response, pre-trigger
auto-summary, final inventory, plus read-only reference inputs from frozen
A1 evidence (installation-carrier silence, plain-human request and
acknowledgement, installation request envelope). No credentials in any
fixture. 13 replay tests pass.

## Negative controls

- **Contamination control:** the benign App-mediated comment produced no
  CodeRabbit activity across a 5-poll settle window; the `04:00:39Z`
  response belongs to the trigger, not to the carrier.
- **Carrier discrimination:** tests prove `plain_user` and
  `app_mediated_user` are different carriers — same human identity, same
  command body, separated only by `performed_via_github_app` — and that the
  installation-bot carrier is neither. Wrong user id or foreign app slug
  fail closed.
- **Handling vs cleanliness:** the rate-limit response normalizes to
  handling with gate `ADVISORY_ONLY`; nothing in this experiment yields
  `CLEAN`, and no attempt was made to re-establish a CodeRabbit `CLEAN`
  contract.
- **Silence is not quota:** the A1 installation-carrier silence cannot be
  explained by rate limiting, because rate limiting is exactly what
  CodeRabbit *says out loud* when it happens, as observed here.

## Result

```text
Command carried by App-mediated user   YES
App mediation observable               YES  (performed_via_github_app = governor)
Provider handled the command           YES  (invocation id + rate limit, +8 s)
Review completed                       NO   (quota; not required by the estimand)
Review object emitted                  NO

CODERABBIT_USER_ATTRIBUTED_TRIGGER_AUTHORITY: PASS
```

## What this DOES prove

- CodeRabbit **processes** commands on the App-mediated user carrier:
  parsed, given an invocation id, and answered in 8 seconds.
- The rejection A1 observed was tied to the **installation-bot** carrier,
  not to App involvement as such: the same App, mediating for a human
  identity, is served.
- One GitHub App user authorization carries trigger authority for **both**
  providers — Codex (A1b, review executed) and CodeRabbit (A1b-R, command
  handled).
- CodeRabbit announces quota exhaustion explicitly, which retroactively
  strengthens A1's reading of its 61-minute silence: silence was not a
  rate limit.

## What this DOES NOT prove

- Not that CodeRabbit *completed* a review for this carrier — it did not;
  quota was exhausted, and the experiment deliberately did not wait.
- Not **why** CodeRabbit ignored the installation bot. The matched-pair
  evidence isolates the carrier, not the provider's internal rule; the
  `user.type == "Bot"` filter remains a plausible hypothesis, not an
  observation.
- Not that CodeRabbit produces an authoritative CLEAN carrier — no
  `pull_request_review` object appeared here, and its sticky surface stays
  disqualified (mutable, not append-only, per A1).
- Not that this is a viable unattended production mechanism: the token is
  user-bound, 8-hour, revocable, with no refresh lifecycle built.
- Not production enforcement readiness. `PRODUCTION_ENFORCEMENT` remains
  `NOT_READY_FOR_ENFORCEMENT`.

## Architectural consequence

```text
Governor App installation identity
  -> webhooks / state / Governor-owned check runs        VIABLE
  -> direct provider trigger identity                    NOT VIABLE (A1)

Governor App user access token (on behalf of PhysShell)
  -> @codex review                 review executed       (A1b)
  -> @coderabbitai full review     command handled       (A1b-R)
```

The architecture is **symmetric**: a single user authorization suffices for
both external reviewers, so the Governor does not need per-provider trigger
mechanisms. The remaining dependency is therefore singular and sharp — one
user-bound authorization — which makes its lifecycle the next thing that
must be measured rather than assumed.

## Next gate

```text
A1c: user-authorization lifecycle and revocation semantics
     - 8 h access-token expiry and refresh rotation
     - invalidation of the used refresh token and the old access token
     - carrier identity after refresh (does performed_via_github_app hold?)
     - revocation by the authorizing user, 401 behaviour, and the
       github_app_authorization event
     - authorization-loss detection and recovery requiring human reauth
```

A2 (installation webhook → signed delivery → `synchronize` → STALE epochs →
Governor-owned non-required check run) remains gated. Standing note for
whichever stage owns gate semantics: neither provider offers an
authoritative CLEAN carrier, so a Governor Check Run may publish the
Governor's own verdict derived from advisory provider evidence — it does
not upgrade that evidence into authoritative provider provenance.
