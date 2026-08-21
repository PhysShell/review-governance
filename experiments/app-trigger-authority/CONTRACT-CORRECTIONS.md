# A1 contract corrections

Factual corrections to the preregistered assumptions, discovered live.
Each correction is evidence-backed and was applied **before** the trigger
experiment proper, so the estimands are unaffected.

## A1-c1 — PR issue-comments require `pull_requests: write`, not `issues: write`

**Preregistered assumption** (inherited from the stage spec):

```text
Issues: read/write   # PR issue comments
Pull requests: read  # snapshot current HEAD/PR metadata
```

**Observed timeline (UTC, 2026-08-21):**

- `07:43:15Z` — identity readback v1: installation 155393018 granted
  `{checks: write, issues: write, metadata: read, pull_requests: read}`.
- `~07:45Z` — App installation token, probe PR #13:
  `GET /repos/PhysShell/evm-from-scratch/pulls/13` → **200**;
  `POST /repos/PhysShell/evm-from-scratch/issues/13/comments` → **403**:

  ```json
  {"message": "Resource not accessible by integration",
   "documentation_url": "https://docs.github.com/rest/issues/comments#create-an-issue-comment",
   "status": "403"}
  ```

- `≤07:48:4xZ` — owner upgraded the App's Pull requests permission to
  read & write and accepted the update for installation 155393018.
- `07:48:43Z` — identity readback v2: `pull_requests: write`
  (see the committed diff of `app-identity.json`).
- `07:48:46Z` — the **identical** POST succeeded: comment `5366890619`,
  author `physshell-review-governor[bot]` (numeric id `319376779`,
  type `Bot`), PR head at request `3b022724d737feeae0a89e0450e6ea11f949d2e3`.

**Diagnosis.** GitHub routes issue-comment creation by the *target's* type:
on a pull request the endpoint is governed by the **Pull requests**
permission; `issues: write` covers comments on plain issues only. The
before/after pair above is the causal evidence (same endpoint, same token
transport, only the permission changed).

**Design consequence — with epistemic status per claim.**

- `pull_requests: write` **was required** for this operation to succeed in
  the tested configuration — EMPIRICAL (the causal pair above).
- `issues: write` **is insufficient** for PR issue-comments — EMPIRICAL
  (it was granted at the time of the 403).
- `issues: write` **is unnecessary** for PR issue-comments — DOCUMENTED +
  consistent with experiment, but NOT experimentally established: the
  permission was never removed in a subtraction test. GitHub's permission
  reference maps "create issue comment on a PR" to the Pull requests
  permission; the experiment is consistent with that, no more.

`issues: write` stays granted for now (minimization is a possible later
test; permissions are not touched again in A1). `checks: write` remains
reserved for A2. `statuses` remains not granted.
