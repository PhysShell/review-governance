# review-governance

Control plane for the `ai-final-review` governance program: preregistered,
single-question experiments probing what AI review providers (Codex,
CodeRabbit) actually accept and enforce — one boundary at a time, evidence
before machinery.

## Frozen evidence baseline (do not modify)

- `PhysShell/evm-from-scratch` PR **#12** — shadow pilot, frozen at head
  `e29621f54a63b50db4afb77b608d6c3a4d533812`.
  Verdicts: `PROVIDER_CONTRACT_PILOT: PARTIAL`,
  `PRODUCTION_ENFORCEMENT: NOT_READY_FOR_ENFORCEMENT`.
- `PhysShell/evm-from-scratch` PR **#11** — probe, closed without merge; its
  SHA chain is immutable.

PR #12 is an evidence baseline, not a working branch. Experiments here may
read it, never write to it.

## Active experiment

- **A1 — App-authored provider trigger authority** —
  [`experiments/app-trigger-authority/PROTOCOL.md`](experiments/app-trigger-authority/PROTOCOL.md)
  (branch `experiment/app-trigger-authority`).
  Question: do Codex and CodeRabbit accept review triggers when the issue
  comment is authored by a GitHub App installation identity instead of a
  human/OAuth user?

## Environment

Everything runs from the flake dev shell (WSL2 / NixOS):

```sh
nix develop            # python3 (+pytest), openssl, jq, gh, git, curl
```

## Secrets policy

The Governor GitHub App private key and credentials live **only** in
`~/.config/review-governor/` with `0600` permissions. They never enter this
repository, chat transcripts, issues, or PRs. Fixtures are sanitized:
no tokens, no JWTs, no authorization headers — numeric actor IDs, logins,
comment IDs, timestamps and SHAs are retained.
