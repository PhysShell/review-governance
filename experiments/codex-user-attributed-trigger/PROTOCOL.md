# A1b — Codex user-attributed trigger authority (preregistered protocol)

Status: **PREREGISTERED** — committed before Device Flow was enabled and
before any user-to-server authentication attempt.
Program: `ai-final-review` · Control plane: `PhysShell/review-governance` ·
Branch: `experiment/codex-user-attributed-trigger`.

## Question

If the Governor GitHub App acts **on behalf of** `PhysShell` through a
GitHub App **user access token**, can a user-attributed `@codex review`
actually start a Codex review?

```text
A1:  installation token -> author physshell-review-governor[bot]
     -> Codex routes command -> review authorization FAIL

A1b: user access token   -> expected author PhysShell
     -> App mediation observable if GitHub exposes it
     -> Codex review authority = ?
```

This is **not** "can PhysShell trigger Codex" — that is already known from
the pilot. It is: *can a central Governor retain GitHub-App-mediated
authorization while triggering Codex under the user's GitHub identity?*

Terminal verdict grammar:

```text
CODEX_USER_ATTRIBUTED_TRIGGER_AUTHORITY: PASS | FAIL | INCONCLUSIVE
```

## Frozen prerequisites

Re-verified against the API immediately before this preregistration; all
matched with zero drift. Any later mismatch stops A1b as prerequisite drift.

| Item | Frozen value |
|---|---|
| A1 experiment PR | `PhysShell/review-governance` **#1**, draft, head `d4bf2918ae495ab1dbc651560a05be0a791aead7` |
| Governor App id | `4669438` (slug `physshell-review-governor`) |
| Governor bot actor | `physshell-review-governor[bot]` = `319376779`, type `Bot` |
| GitHub user | `PhysShell` = `45852143`, type `User` |
| Codex provider actor | `chatgpt-codex-connector[bot]` = `199175422`, type `Bot` |
| Shadow pilot PR | evm-from-scratch **#12**, OPEN draft, head `e29621f54a63b50db4afb77b608d6c3a4d533812` |
| Probe PRs | evm-from-scratch **#11** CLOSED unmerged, **#13** CLOSED unmerged (head `3b022724…`) |

A1 results are frozen evidence and are not rewritten by A1b:

```text
APP_TRIGGER_AUTHORITY: PARTIAL
CODEX_APP_COMMAND_ROUTING:       PASS
CODEX_APP_REVIEW_AUTHORITY:      FAIL
CODERABBIT_APP_COMMAND_ROUTING:  FAIL
Governor App coordinator: VIABLE · direct trigger ID: NOT VIABLE
Production enforcement: NOT READY
```

CodeRabbit does not participate in A1b. A2 is not started.

## Authentication model

The primary request **must** use a user-to-server token of the same App
(`physshell-review-governor`), obtained via **Device Flow** (GitHub's
documented headless/CLI path). A user access token acts on behalf of the
authorizing user and is bounded by `App permissions ∩ user permissions` —
a different trust model from the installation (server-to-server) token.

Forbidden as the primary credential: `gh auth token`, any existing OAuth
token, PATs, installation tokens, Actions tokens. Token expiration is not
disabled for convenience; no refresh lifecycle is built in A1b (refresh
tokens, if issued, are not used). Token material is stored only in
`~/.config/review-governor/` (0600), never committed, printed, or placed in
fixtures/PRs/logs. The `ghu_` prefix is noted but is **not** treated as
sufficient evidence of anything by itself.

Manual boundary (acceptable): if Device Flow is disabled on the App, exact
UI instructions are prepared and the experiment pauses there. Enabling
Device Flow is the *only* permitted App change — no permission changes, no
Statuses, no webhook, no change to installation repository selection.

## Method

1. **Preregistration** (this file) committed first.
2. **Device Flow**: `POST https://github.com/login/device/code` with the
   App's `client_id` → `user_code` + `verification_uri` → `PhysShell`
   authorizes in a browser → poll `POST /login/oauth/access_token` with
   `grant_type=urn:ietf:params:oauth:grant-type:device_code` → user access
   token stored 0600. Repository scoping via `repository_id` is attempted
   if the endpoint accepts it; if not available, that is recorded as an
   observed limitation, not worked around.
3. **Identity proof, necessary but not sufficient**: `GET /user` must
   return `login == PhysShell`, `id == 45852143`, `type == User`. Then a
   **benign identity-probe comment** (no `@codex`) on the probe PR, read
   back through the API, capturing `user.login` / `user.id` / `user.type` /
   `performed_via_github_app` / `created_at` / `id` in full.
   `performed_via_github_app` is a *hypothesis* until observed: if GitHub
   does not return it on this carrier, record
   `APP_MEDIATION_OBSERVABILITY: NOT_AVAILABLE_ON_OBSERVED_CARRIER` and
   fall back to token-generation provenance + user identity as the evidence
   chain. No substitute field is invented.
4. **Probe PR**: a new disposable draft PR in `PhysShell/evm-from-scratch`
   (branch `probe/codex-user-attributed-trigger`), one harmless document,
   never merged. #11/#12/#13 and E1/E5 untouched. No repository or
   environment configuration is changed. HEAD frozen for the duration.
5. **Negative settle**: full inventory (issue comments, reviews, inline
   comments, reactions, HEAD) plus a short settle window before the
   trigger. If the benign comment itself provokes Codex activity, stop and
   investigate contamination.
6. **Primary trigger**: `@codex review` posted **only** with the user
   access token; the created comment is read back immediately and the
   request envelope frozen (`repository_id`, `pr_number`, `head_sha`,
   `comment_id`, `created_at`, `user.*`, `performed_via_github_app` if
   present, `auth_model = github_app_user_access_token`). The token itself
   is never recorded.
7. **Observation**: 30 min primary window, one extension to 60 min total if
   the provider produced nothing; then `NO_OBSERVED_START`.

## Classification (fixed before data)

Separate results, reported individually — the aggregate never absorbs them:

```text
GITHUB_USER_ATTRIBUTION:       PASS/FAIL
APP_MEDIATION_OBSERVABILITY:   PASS/UNAVAILABLE
CODEX_COMMAND_ROUTING:         PASS/FAIL/INCONCLUSIVE
CODEX_REVIEW_AUTHORITY:        PASS/FAIL/INCONCLUSIVE
TERMINAL_HEAD_BINDING:         PASS/FAIL/NOT_REACHED
```

**Critical causal distinction.** "The comment shows `PhysShell`" proves
`GITHUB_USER_ATTRIBUTION: PASS` and nothing more. It is never sufficient
for the aggregate verdict.

`CODEX_USER_ATTRIBUTED_TRIGGER_AUTHORITY: PASS` requires **all** of:

1. the request comment demonstrably belongs to the primary A1b request;
2. the request was made with the GitHub App user access token;
3. GitHub attributes the request to `PhysShell`;
4. Codex starts/executes a review rather than returning an onboarding or
   other no-start response;
5. terminal provider evidence is observed;
6. that terminal evidence binds/attests the frozen probe HEAD.

Terminal `CLEAN` and `FINDINGS` both prove review authority.

`FAIL`: Codex demonstrably recognizes the primary command but returns a
provider-authored no-start/refusal attributable to this request — including
a repeat of "create a Codex account and connect to github" while
`comment.user == PhysShell`, which would be evidence that GitHub user
attribution alone is insufficient. Causes are not explained beyond the
observation.

`INCONCLUSIVE`: silence → `NO_OBSERVED_START` after the preregistered
window. **Only then**, a matched human control on the same PR, same HEAD,
same draft state, via the ordinary human/OAuth path:

```text
user-access-token silent + ordinary user handled
    -> evidence against the GitHub-App-user-token trigger path
both silent
    -> INCONCLUSIVE
```

A control is never promoted to the primary result.

## Evidence plan

Sanitized fixtures in `experiments/codex-user-attributed-trigger/fixtures/`
(credential material removed; `performed_via_github_app` preserved verbatim
when GitHub actually returns it): `user_token_identity_readback.json`,
`benign_user_attributed_comment.json`, `codex_user_attributed_request.json`,
`codex_response.json`, `codex_terminal_review.json` (if it exists),
`codex_inline_comments.json` (if applicable), `final_inventory.json`,
`matched_control_*.json` (only if required).

Regression tests must prove: the A1 installation-token App-bot request is
not user-attributed; an ordinary human/OAuth request is not automatically
equivalent to a GitHub-App-user-token request where
`performed_via_github_app` distinguishes them; the user-token request
carries the expected GitHub user identity; an observed Codex refusal can
never classify as `CLEAN`; an observed terminal Codex review must bind the
frozen HEAD; spoofed user/app attribution fails closed.

Report: `docs/experiments/codex-user-attributed-trigger.md` with sections
Question / Frozen prerequisites / Authentication model / User authorization
flow / GitHub attribution observation / Probe PR / Codex observation / Raw
evidence mapping / Negative controls / Result / What this DOES prove / What
this DOES NOT prove / Architectural consequence / Next gate, and the final
matrix.

## Architectural outcomes (decided in advance)

- **PASS** → admissible conclusion only: a *split-authority* architecture is
  possible — installation identity for coordinator/webhooks/checks/state,
  user access token for Codex triggers on behalf of an authorized user.
  Explicitly **not** production-ready: the next question becomes how to
  safely maintain/refresh user authorization and whether this trust model is
  acceptable for unattended automation. A2 still does not start
  automatically.
- **FAIL** → no second OAuth workaround is attempted inside A1b. Record that
  GitHub App user-to-server identity does not solve Codex trigger authority;
  the next architectural fork compares explicit human trigger vs dedicated
  machine-user authority vs dropping Codex from the mandatory automated gate.
- **INCONCLUSIVE** → no production changes; design a stronger
  identification/control experiment first.

## Forbidden in A1b

CodeRabbit participation; webhook server; Check Run creation; ruleset or
branch-protection changes; required checks; refresh-token daemon;
scheduler; creating a dedicated GitHub user; PATs; rewriting A1; merging
PR #1; merging the probe PR; starting A2.

## Git discipline

Real chronology preserved as separate commits: preregistration → auth
harness → live identity evidence → Codex capture → contract corrections (if
any) → fixtures/tests → report. No artificial commits, no squashing of real
evidence chronology. Probe PR closed without merge; A1b opens its own draft
PR. Stop after the verdict.
