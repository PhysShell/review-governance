# A1b — Codex user-attributed trigger authority: final report

Program `ai-final-review` · Control plane `PhysShell/review-governance` ·
Branch `experiment/codex-user-attributed-trigger` · Date: 2026-08-22 (all
times UTC). Preregistered protocol:
`experiments/codex-user-attributed-trigger/PROTOCOL.md` (amendments A1b-c1,
A1b-c2 committed before the classification they govern).

## Question

If the Governor GitHub App acts **on behalf of** `PhysShell` through a
GitHub App **user access token**, can a user-attributed `@codex review`
actually start a Codex review — the boundary A1 showed the App
*installation* identity cannot cross?

## Frozen prerequisites

Re-verified against the API before preregistration, zero drift: A1 PR #1
draft at `d4bf2918ae495ab1dbc651560a05be0a791aead7`; App `4669438`; bot
`physshell-review-governor[bot]` = `319376779`; user `PhysShell` =
`45852143`; Codex actor `chatgpt-codex-connector[bot]` = `199175422`;
evm-from-scratch PR #12 OPEN draft at `e29621f5…`, PR #11 and PR #13 closed
unmerged. A1's verdicts are frozen evidence and were not rewritten.
CodeRabbit did not participate; A2 was not started.

## Authentication model

GitHub App **user-to-server** authentication of the same App
(`physshell-review-governor`) via **Device Flow** — a different trust model
from A1's installation (server-to-server) token: requests act on behalf of
the authorizing user, bounded by App permissions ∩ user permissions.
No `gh` token, OAuth app token, PAT, installation token or Actions token
was used for the primary request.

Device Flow was disabled on the App at the start (`device_flow_disabled`,
recorded before anything was changed); enabling it was the single manual UI
step, and the only App setting changed in A1b. Permissions, Statuses,
webhook and installation repository selection were untouched.

## User authorization flow

- Authorization completed `03:31:38Z`. Token: prefix `ghu_`, type `bearer`,
  `expires_in` 28800 (8 h) — expiration left enabled per protocol.
  A refresh token was issued and deliberately **not used** in A1b.
- Secrets (access token, refresh token, device code, App private key) live
  only in `~/.config/review-governor/` at 0600; they never entered the
  repository, fixtures, PRs, logs, or chat.
- Repository scoping via a `repository_id` parameter was attempted on the
  device-code request. The endpoint documents no such parameter and the
  effective scope was instead verified afterwards from the token's own
  side (below) — not assumed from silent acceptance.

## GitHub attribution observation

`GET /user` with the user access token → `PhysShell` / `45852143` /
`User`. From the token's own side, `GET /user/installations` shows exactly
one installation — the Governor App (`app_id 4669438`, installation
`155393018`, `repository_selection: selected`, permissions `checks: write`,
`issues: write`, `metadata: read`, `pull_requests: write`) — reaching
exactly `PhysShell/evm-from-scratch`.

Comments created with this token carry **both** identities:

```text
user:                     PhysShell / 45852143 / User
performed_via_github_app: physshell-review-governor (id 4669438)
```

`APP_MEDIATION_OBSERVABILITY: PASS` — and the field genuinely discriminates
the three carriers, as the frozen reference fixtures show:

| carrier | `user` | `performed_via_github_app` |
|---|---|---|
| A1 installation token | `physshell-review-governor[bot]` (Bot) | `physshell-review-governor` |
| ordinary `gh`/OAuth | `PhysShell` (User) | `null` |
| **A1b user access token** | `PhysShell` (User) | `physshell-review-governor` |

## Probe PR

`PhysShell/evm-from-scratch` PR **#14**, draft, branch
`probe/codex-user-attributed-trigger`, one harmless document, frozen head
`a4e756b0324e1bebd76a2476a684dfa753abca54` — unchanged for the whole
experiment, closed without merge afterwards. PRs #11/#12/#13 untouched; no
repository or Codex configuration was changed by this experiment.

## Codex observation

Primary sequence, all on the frozen HEAD:

- `03:32:07Z` — benign user-attributed comment (no `@codex`), comment
  `5377631148`. Settle window `03:32:30Z`→`03:36:43Z`, 5 polls: **no Codex
  activity** — no contamination.
- `03:37:37Z` — **primary trigger** `@codex review`, comment `5377651611`,
  posted with the user access token; author `PhysShell`, mediated by
  `physshell-review-governor`.
- `03:37:46Z` — **9 seconds later**, `chatgpt-codex-connector[bot]`
  (`199175422`) added an `eyes` reaction to the exact request comment —
  acknowledgement/start, something the App-authored request in A1 never
  received.
- `03:38:56Z` — **79 seconds after the trigger** — Codex posted its result:

  > "Codex Review: Didn't find any major issues. Bravo.
  > **Reviewed commit:** `a4e756b032`"

  The reaction was withdrawn once the result was posted. No
  `pull_request_review` object and no inline comments were emitted
  (verified at `03:40:37Z` and at final reconciliation).

**Matched identity control** (amendment A1b-c1), same PR, same frozen HEAD,
same repository state, 4 minutes later:

- `03:41:57Z` — `@codex review` posted with the **App installation token**;
  author `physshell-review-governor[bot]` (`319376779`).
- `03:42:02Z` — **5 seconds later**, Codex replied:

  > "To use Codex here, [create a Codex account and connect to github]…"

  — verbatim the A1 refusal.

## Raw evidence mapping

Probe PR #14 inventory at final reconciliation:

| comment id | author (id) | time | role |
|---|---|---|---|
| 5373574224 | `coderabbitai[bot]` (136622811) | 2026-08-21 18:13:02 | auto-summary on PR open (CodeRabbit not part of A1b) |
| 5373574639 | `chatgpt-codex-connector[bot]` (199175422) | 2026-08-21 18:13:04 | auto-comment on PR open: "create an environment for this repo" — repo-state evidence for A1b-c1 |
| 5377631148 | `PhysShell` (45852143) via `physshell-review-governor` | 2026-08-22 03:32:07 | benign identity probe (no `@codex`) |
| 5377651611 | `PhysShell` (45852143) via `physshell-review-governor` | 2026-08-22 03:37:37 | **primary user-attributed trigger** |
| — reaction — | `chatgpt-codex-connector[bot]` | 2026-08-22 03:37:46 | `eyes` on comment 5377651611 (acknowledgement) |
| 5377656674 | `chatgpt-codex-connector[bot]` (199175422) | 2026-08-22 03:38:56 | **terminal review result**, attests `a4e756b032` |
| 5377668179 | `physshell-review-governor[bot]` (319376779) | 2026-08-22 03:41:57 | matched identity control |
| 5377668534 | `chatgpt-codex-connector[bot]` (199175422) | 2026-08-22 03:42:02 | control refusal: "create a Codex account…" |

`reviews: 0`, `review_comments: 0` throughout.

Sanitized fixtures: `experiments/codex-user-attributed-trigger/fixtures/`
(identity readback, benign probe, primary request, acknowledgement
reaction, terminal response, pre-trigger repo-state comment, matched
control request + response, final inventory, plus read-only reference
inputs copied from frozen A1/pilot evidence). No token, refresh token,
device code or private key appears in any of them. 20 replay tests pass.

## Negative controls

- **Contamination control:** the benign App-mediated comment produced no
  Codex activity in a 5-poll settle window — the response at `03:38:56Z`
  belongs to the trigger, not to the carrier.
- **Carrier discrimination:** replay tests prove the A1 installation
  request classifies as `app_installation_bot`, an ordinary `gh`/OAuth
  request as `plain_user`, and only the A1b request as
  `app_mediated_user`; wrong user id, foreign app slug, or command text
  alone all fail closed.
- **Repo-state confounder** (A1b-c1): Codex's own auto-comment on PR open
  ("create an environment for this repo") versus its review text ("your
  team has set up Codex to review pull requests in this repo") proves the
  repository's Codex configuration changed between the A1 observation and
  A1b. The matched identity control resolves it: under the **current**
  repo state, the App bot is still refused while the user-attributed
  request is reviewed. Identity, not repository configuration, is the
  operative variable.
- **Outcome normalization:** the refusal never classifies as `CLEAN`; the
  review result never classifies as `CLEAN` either (advisory carrier); a
  tampered SHA prefix fails to bind the frozen HEAD.

## Result

```text
GitHub user attribution        YES
App mediation observable       YES   (performed_via_github_app = physshell-review-governor)
Codex command recognized       YES   (eyes reaction at +9 s)
Codex review authorized        YES   (review executed at +79 s)
Terminal review                YES   (issue comment, "no major issues")
Terminal exact-head binding    YES*  (*textual 10-char prefix a4e756b032 on a
                                      mutable carrier; no pull_request_review object)

GITHUB_USER_ATTRIBUTION:       PASS
APP_MEDIATION_OBSERVABILITY:   PASS
CODEX_COMMAND_ROUTING:         PASS
CODEX_REVIEW_AUTHORITY:        PASS
TERMINAL_HEAD_BINDING:         PASS (advisory carrier)

CODEX_USER_ATTRIBUTED_TRIGGER_AUTHORITY: PASS
```

All six preregistered PASS conditions are satisfied, including the sixth
(terminal evidence attesting the frozen HEAD) — with the carrier caveat
recorded rather than smoothed over.

## What this DOES prove

- A GitHub App user access token obtained via Device Flow lets the Governor
  App act **on behalf of** the user, and GitHub exposes both facts on the
  artifact: `user = PhysShell` **and**
  `performed_via_github_app = physshell-review-governor`. App mediation is
  observable, not merely asserted.
- Codex **accepts and executes** a review for that carrier: acknowledged in
  9 seconds, result in 79 seconds, attesting the frozen HEAD.
- Under identical repository state, PR and HEAD, the App **installation**
  identity is still refused with the A1 onboarding message. The matched
  control isolates requester identity from the repository's Codex
  configuration, which had demonstrably changed since A1.
- A1's `CODEX_APP_REVIEW_AUTHORITY: FAIL` therefore stands, and is not an
  artifact of the older repo state.
- Codex's terminal CLEAN-ish result arrives on a **mutable issue comment**
  with a 10-character SHA prefix in free text — no `pull_request_review`
  object, no `commit_id` field. Machine-authoritative binding is absent
  even when the review genuinely runs.

## What this DOES NOT prove

- Not that this is a viable **unattended** production mechanism. The token
  is bound to a specific user, expires in 8 hours, can be revoked by that
  user at any time, and A1b built no refresh lifecycle by design.
- Not that Codex's authorization rule is "the author's ChatGPT account" in
  general — one repository, one App, one user, two carriers, one session.
- Not that repository Codex configuration is irrelevant; only that under
  the *observed current* state identity decides the outcome.
- Not that the review's *content* is trustworthy or that "no major issues"
  can serve as a gate: the carrier is advisory and mutable.
- Not production enforcement readiness. `PRODUCTION_ENFORCEMENT` stays
  `NOT_READY_FOR_ENFORCEMENT`; no webhook, check run, ruleset or required
  check exists.

## Architectural consequence

```text
Governor App installation identity
  -> coordinator / webhooks / checks / state        VIABLE
  -> direct Codex trigger identity                  NOT VIABLE  (A1, reconfirmed)

Governor App user access token (on behalf of PhysShell)
  -> Codex command routing                          PASS
  -> Codex review authorization                     PASS

=> split-authority architecture is possible:
     installation identity  = machinery and state
     user access token      = provider triggers on behalf of an authorized user
```

Explicitly not production-ready. The open question this creates is
lifecycle, not capability: how to maintain or refresh user authorization
safely, what happens when the authorizing user revokes or leaves, and
whether a user-bound credential is acceptable for a gate that is supposed
to be unattended.

## Next gate

```text
A1c: user-authorization lifecycle and revocation semantics
     (expiry, refresh, revocation, who the gate belongs to when the
      authorizing user is gone) — design first, no implementation
```

A2 (installation webhook → signed delivery → `synchronize` → STALE epochs →
Governor-owned non-required check run → exact HEAD binding) remains gated
and unstarted. A separate open item recorded for whichever stage owns gate
semantics: Codex's terminal result has no authoritative carrier, so a
Governor-owned check run would have to *re-attest* it rather than trust it.
