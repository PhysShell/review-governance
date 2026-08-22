#!/usr/bin/env python3
"""Phase R instrument: perform exactly one refresh and prove rotation.

    --refresh     one CAS-serialized refresh of the current generation
    --probe       probe a given generation's access token (GET /user)
    --probe-refresh   attempt the refresh grant with a given generation's
                      refresh token (used to prove single-use semantics and
                      post-revocation behaviour)
    --installation    prove the installation identity independently

Refresh is single-writer: the new pair is committed under an exclusive lock
with a compare-and-swap against the durable generation, so a worker that
lost a race cannot overwrite the winner. Token values never reach evidence.
"""
import argparse
import json
import sys
from pathlib import Path

import creds
import gh_probe


def write(out_dir: str, name: str, payload: dict) -> str:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return str(path)


def do_refresh(client_id: str, out_dir: str, label: str) -> dict:
    from_generation = creds.current_generation()
    refresh_token = creds.generation(from_generation)["refresh_token"]
    sanitized, payload = gh_probe.refresh_exchange(client_id, refresh_token)
    result = {"from_generation": from_generation, "response": sanitized}
    if sanitized["granted"]:
        try:
            committed = creds.commit_new_generation(
                payload, from_generation, label=label,
                obtained_via="github_app_refresh_grant",
                obtained_at=sanitized["at"])
            result["committed_generation"] = committed
            result["outcome"] = "ROTATED"
        except creds.GenerationRaceLost as e:
            # Another writer already rotated: the pair we just received is
            # unreachable, which is exactly the ambiguous-outcome hazard.
            result["outcome"] = "RACE_LOST_AFTER_GRANT"
            result["durable_generation"] = e.on_disk
    else:
        result["outcome"] = "REJECTED"
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default=".captures/a1c")
    ap.add_argument("--name", required=True, help="evidence file name")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--label", default=None, help="label for the new generation")
    ap.add_argument("--probe", type=int, default=None,
                    help="generation whose ACCESS token to probe")
    ap.add_argument("--probe-refresh", type=int, default=None,
                    help="generation whose REFRESH token to attempt")
    ap.add_argument("--installation", action="store_true")
    ap.add_argument("--repo", default="PhysShell/evm-from-scratch")
    ap.add_argument("--pr", type=int, default=None)
    args = ap.parse_args()

    public = json.loads((creds.CONFIG_DIR / "app-public.json").read_text())

    if args.refresh:
        result = do_refresh(public["client_id"], args.out_dir,
                            args.label or "G?")
    elif args.probe is not None:
        record = creds.generation(args.probe)
        result = {"probed_generation": args.probe,
                  "access_fingerprint": creds.fingerprint(record["access_token"]),
                  "result": gh_probe.probe_access(record["access_token"])}
    elif args.probe_refresh is not None:
        record = creds.generation(args.probe_refresh)
        sanitized, _ = gh_probe.refresh_exchange(public["client_id"],
                                                 record["refresh_token"])
        result = {"probed_generation": args.probe_refresh,
                  "refresh_fingerprint": creds.fingerprint(record["refresh_token"]),
                  "result": sanitized}
    elif args.installation:
        import installation
        result = installation.probe(args.repo, args.pr)
    else:
        ap.error("choose one of --refresh / --probe / --probe-refresh / "
                 "--installation")

    path = write(args.out_dir, args.name, result)
    print(json.dumps({"written": path, **result}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
