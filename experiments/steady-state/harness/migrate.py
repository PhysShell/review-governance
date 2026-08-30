#!/usr/bin/env python3
"""Map legacy decisions into scoped epochs, provably or not at all.

The legacy `decisions` table is not edited. Its two bootstrap rows are
historical facts and back-filling them with `repo`/`pr_number` would
manufacture provenance for values nobody recorded.

Instead each legacy row is matched by its **full** `head_sha` against the
commit-bound A5b inventory artifact, which maps a full head to exactly one
PR. That is a justification a reader can check without trusting this
program.

What is forbidden, and why it is worth naming: pairing rows with PRs by
position. `enumerate(rows)` against `enumerate(prs)` would have produced
the correct answer here — two rows, two PRs, same order — and would have
been wrong the first time anything was inserted out of order. A mapping
whose justification is "they lined up" is the defect this programme has
found most often.

A row that matches zero or several inventory entries is `UNMAPPED`, and
stays that way. `UNMAPPED` is what makes `last_known_head` answer
`UNRESOLVED` instead of pretending a PR has no history.
"""
import argparse
import datetime
import json
import sqlite3
import sys
from pathlib import Path

import epochs as ep


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def legacy_rows(path):
    """Read-only. This module never writes to the legacy store."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT decision_id, epoch_id, head_sha, verdict, decided_at "
            "FROM decisions ORDER BY decision_id")]
    finally:
        conn.close()


def resolve(head_sha, inventory):
    """Exactly one inventory entry with this full head, or nothing."""
    if len(head_sha) != 40:
        return None, "legacy head is not a full SHA; refusing prefix matching"
    matches = [i for i in inventory if i["head_sha"] == head_sha]
    if len(matches) == 1:
        return matches[0], (f"full head {head_sha} matches exactly one "
                            f"inventory entry, PR #{matches[0]['pr_number']}")
    if not matches:
        return None, f"no inventory entry has head {head_sha}"
    return None, (f"{len(matches)} inventory entries share head {head_sha}; "
                  "scope is not determined")


def run(args):
    artifact = json.loads(Path(args.inventory).read_text())
    inventory = artifact["inventory"]
    store = ep.EpochStore(args.db)
    results = []
    try:
        for row in legacy_rows(args.legacy):
            match, justification = resolve(row["head_sha"], inventory)
            at = utcnow()
            if match is None:
                store.record_migration(
                    legacy_epoch=row["epoch_id"], legacy_head=row["head_sha"],
                    mapped_to=None, justification=justification,
                    source_artifact=artifact["inventory_hash"], at=at)
                results.append({"legacy_epoch": row["epoch_id"],
                                "state": "UNMAPPED",
                                "justification": justification})
                continue
            epoch = store.open_epoch(
                repo=match["repo"], pr_number=match["pr_number"],
                head_sha=row["head_sha"], opened_at=row["decided_at"],
                source="migrated-from-a5b-bootstrap")
            store.record_decision(
                epoch_id=epoch["epoch_id"], verdict=row["verdict"],
                decided_at=row["decided_at"],
                cause=f"migrated from legacy decision {row['decision_id']}")
            store.record_migration(
                legacy_epoch=row["epoch_id"], legacy_head=row["head_sha"],
                mapped_to=epoch["epoch_id"], justification=justification,
                source_artifact=artifact["inventory_hash"], at=at)
            results.append({"legacy_epoch": row["epoch_id"],
                            "state": "MAPPED", "epoch_id": epoch["epoch_id"],
                            "repo": match["repo"],
                            "pr_number": match["pr_number"],
                            "head_sha": row["head_sha"],
                            "justification": justification})
        unmapped = [r for r in results if r["state"] == "UNMAPPED"]
        return {"step": "A6a legacy migration",
                "source_artifact": artifact["inventory_hash"],
                "source_file": str(args.inventory),
                "legacy_store": str(args.legacy),
                "legacy_store_modified": False,
                "results": results,
                "mapped": len(results) - len(unmapped),
                "unmapped": len(unmapped),
                "note": "mapping is by full head SHA against a commit-bound "
                        "inventory; positional pairing is not used and would "
                        "not be accepted as justification"}
    finally:
        store.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--legacy", required=True)
    ap.add_argument("--inventory", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    result = run(args)
    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
