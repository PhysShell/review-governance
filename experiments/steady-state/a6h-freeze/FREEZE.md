# A6h candidate freeze

Published **before** the disposable branch or pull request exists, and
before any credential is spent. Nothing in A6h may be constructed until
this document is readable on the remote.

## Why this exists

`SECOND-ROUND.md` registered the HEAD_B mutation "at step 0, before any
provider sees the candidate". That was not achievable in the order it
described. Provider exposure does not begin with our request: it begins at
`pull_request.opened`. A6g watched CodeRabbit post carrier 5462558501 on
`#32` six seconds after the PR opened, unprompted and before the Governor
had asked anything.

So a mutation registered after the PR exists is registered after the
candidate has been seen, and "we chose it in advance" becomes a claim about
commit timestamps rather than a fact. The freeze is what makes it a fact.

## Frozen base

```text
base_sha   047ff1a641e33e0bb8c6b9eea26bb80eea021e08   (main)
```

If `main` has moved when A6h begins, this freeze is void and a new one is
written. HEAD_A is not rebased onto a different base to keep a freeze
alive.

## HEAD_A specimen

Two files, added in one commit from `base_sha`. Their exact bytes are in
`specimen/`, which is the artifact — not a description of it.

```text
governor/pilot/a6h_probe.py        1479 bytes
  sha256 ede5ccdf0a22919e4e9c78c4d7073f3c9f223ff4e7b9ecac0c903ae25b28c598
governor/pilot/test_a6h_probe.py    947 bytes
  sha256 eb0202e533e072bd4188d5ff0b711aa267f1090d2be0099462e30eb748969826
```

commit message, exactly:

```text
A6h probe: word-size accounting for EVM memory and copy costs
```

The specimen is a total function with a stated contract and a companion
test file discoverable by pytest's default naming. It was written to be
correct, not to be liked: it is not adjusted in response to anything a
provider says, and a finding against it is a `VALID_NEGATIVE` outcome
rather than a prompt to edit.

`experiments/steady-state/tests/test_a6h_freeze.py` runs the frozen tests
against the frozen module, so the specimen cannot rot into something that
does not pass its own suite.

## HEAD_B mutation

One file, added in one commit on top of the confirmed HEAD_A.

```text
governor/pilot/a6h-head-move.txt    367 bytes
  sha256 fe26505d216ddee8631424b1b843eb78de76ae6a185b8efc70deeedb7dc4b630
```

commit message, exactly:

```text
A6h invalidation probe: move the disposable head after HEAD_A success
```

operation: add this one file. No edit to the specimen, no rebase, no
amend, no force push. One commit, one push.

**Condition.** HEAD_B may be applied if and only if a SUCCESS on HEAD_A has
been independently read back from the check run — not merely published, and
not inferred from the absence of a refusal. Any other A6h outcome ends the
round with HEAD_B unapplied.

## Branch naming

```text
probe/a6h-<suffix>
```

created from `base_sha`, never reused, deleted at cleanup under the owner
credential — the Governor App has no `contents: write` and returns 403 on
ref deletion, which A6g established by attempting it.

## Verification

`verify.py` compares a candidate working tree against these bytes and
reports; it never repairs. A candidate carrying anything the freeze does
not list is `UNFROZEN_EXTRA` and fails.

```sh
python3 verify.py --manifest
python3 verify.py --specimen <worktree>
python3 verify.py --mutation <worktree>
```
