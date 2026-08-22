"""The A3a policy: what may count as positive evidence, and what may not.

Two rules shape everything here:

  * a provider's own words are *advisory*. `CODEX_ADVISORY_POSITIVE` and
    `CODERABBIT_ADVISORY_POSITIVE` describe what the Governor observed a
    provider say, never a certificate the provider issued. Neither state is
    ever named CLEAN.
  * absence of findings is not evidence. A round with no terminal positive
    artifact fails qualification even if nothing negative was seen.

The policy consumes immutable snapshots, not live mutable carriers.
"""
import hashlib
import json
import re

DECISION_RULE_REVISION = "a3a.1"
BUNDLE_VERSION = "PositiveEvidenceBundle-v1"

CODEX_ACTOR_ID = 199175422
CODERABBIT_ACTOR_ID = 136622811
GOVERNOR_APP_SLUG = "physshell-review-governor"
EXPECTED_USER_ID = 45852143

# Governor verdicts — never provider verdicts
SUCCESS_CANDIDATE = "SUCCESS_CANDIDATE"
NOT_ESTABLISHED = "NOT_ESTABLISHED"
INVALIDATED = "INVALIDATED"
STALE = "STALE"

CODEX_REVIEWED_COMMIT = re.compile(r"reviewed\s+commit:?[\s*`]*([0-9a-f]{7,40})",
                                   re.IGNORECASE)
CODEX_POSITIVE = ("didn't find any major issues", "did not find any major issues",
                  "no major issues")
CODEX_NO_START = ("to use codex here",)
CODERABBIT_POSITIVE = ("no actionable comments",)
CODERABBIT_RATE_LIMIT = ("rate limit", "rate-limit", "quota")
CODERABBIT_RANGE = re.compile(
    r"between\s+([0-9a-f]{7,40})\s+and\s+([0-9a-f]{7,40})", re.IGNORECASE)


def body_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


def carrier_of(comment: dict) -> str:
    """Which of the three carriers authored this comment (A1b/A1b-R)."""
    user = comment.get("user") or {}
    via = comment.get("performed_via_github_app")
    slug = via.get("slug") if isinstance(via, dict) else via
    if user.get("type") == "User" and user.get("id") == EXPECTED_USER_ID:
        return "app_mediated_user" if slug == GOVERNOR_APP_SLUG else "plain_user"
    if user.get("type") == "Bot" and slug == GOVERNOR_APP_SLUG:
        return "app_installation_bot"
    return "other"


def resolves_uniquely(prefix: str, head_sha: str, other_shas) -> bool:
    """The Codex attestation is a short prefix in free text; it counts only
    if it prefixes the current head and nothing else in the PR."""
    if not prefix or not head_sha.lower().startswith(prefix.lower()):
        return False
    return not any(sha.lower().startswith(prefix.lower())
                   for sha in other_shas if sha.lower() != head_sha.lower())


def qualify_codex(request_snapshot: dict, inventory: dict, head_sha: str,
                  other_shas) -> dict:
    """CODEX_ADVISORY_POSITIVE requires every clause to hold at once."""
    reasons = []
    request = request_snapshot
    if carrier_of(request) != "app_mediated_user":
        reasons.append("request not on the app-mediated user carrier")

    after = [c for c in inventory["issue_comments"]
             if (c["user"].get("id") == CODEX_ACTOR_ID
                 and c["created_at"] > request["created_at"])]
    terminal = None
    for comment in after:
        body = (comment.get("body") or "").lower()
        if any(marker in body for marker in CODEX_NO_START):
            reasons.append("Codex returned a no-start/refusal response")
        if any(marker in body for marker in CODEX_POSITIVE):
            terminal = comment
    if not after:
        reasons.append("no Codex response after this request")
    if terminal is None and not reasons:
        reasons.append("no terminal positive Codex artifact for this round")

    attested_prefix = resolved = None
    if terminal:
        match = CODEX_REVIEWED_COMMIT.search(terminal.get("body") or "")
        attested_prefix = match.group(1).lower() if match else None
        if not attested_prefix:
            reasons.append("Codex terminal artifact attests no commit")
        elif not resolves_uniquely(attested_prefix, head_sha, other_shas):
            reasons.append("Codex attested prefix does not uniquely resolve "
                           "to the current head")
        else:
            resolved = head_sha

    findings = [r for r in inventory["reviews"]
                if r["user"].get("id") == CODEX_ACTOR_ID
                and r.get("submitted_at", "") > request["created_at"]]
    inline = [c for c in inventory["review_comments"]
              if c["user"].get("id") == CODEX_ACTOR_ID
              and c["created_at"] > request["created_at"]]
    if findings:
        reasons.append(f"{len(findings)} Codex review(s) present for this round")
    if inline:
        reasons.append(f"{len(inline)} Codex inline finding(s) present")

    return {
        "provider": "codex",
        "state": "CODEX_ADVISORY_POSITIVE" if not reasons else "NOT_QUALIFIED",
        "qualified": not reasons,
        "reasons": reasons,
        "request_comment_id": request["id"],
        "request_carrier": carrier_of(request),
        "terminal_comment": None if not terminal else {
            "id": terminal["id"], "created_at": terminal["created_at"],
            "updated_at": terminal.get("updated_at"),
            "actor_id": terminal["user"].get("id"),
            "body_hash": body_hash(terminal.get("body")),
            "attested_prefix": attested_prefix,
            "resolved_full_sha": resolved,
            "carrier_kind": "mutable_advisory_carrier",
        },
        "findings_seen": {"reviews": len(findings), "inline": len(inline)},
    }


def qualify_coderabbit(request_snapshot: dict, inventory: dict, head_sha: str,
                       base_sha: str) -> dict:
    """CODERABBIT_ADVISORY_POSITIVE. A rate limit is not positive, and a
    check-run `status: success` is never accepted as cleanliness."""
    reasons = []
    request = request_snapshot
    if carrier_of(request) != "app_mediated_user":
        reasons.append("request not on the app-mediated user carrier")

    after = [c for c in inventory["issue_comments"]
             if c["user"].get("id") == CODERABBIT_ACTOR_ID
             and c["created_at"] > request["created_at"]]
    acknowledged = bool(after)
    if not acknowledged:
        reasons.append("no CodeRabbit activity after this request")
    for comment in after:
        if any(m in (comment.get("body") or "").lower()
               for m in CODERABBIT_RATE_LIMIT):
            reasons.append("CodeRabbit rate-limited this request generation")

    # the sticky/summary surface, whenever it was last written
    sticky = None
    for comment in inventory["issue_comments"]:
        if comment["user"].get("id") != CODERABBIT_ACTOR_ID:
            continue
        body = (comment.get("body") or "").lower()
        if any(marker in body for marker in CODERABBIT_POSITIVE):
            sticky = comment
    if sticky is None:
        reasons.append("no terminal positive CodeRabbit surface for this round")

    range_from = range_to = None
    if sticky:
        match = CODERABBIT_RANGE.search(sticky.get("body") or "")
        if match:
            range_from, range_to = match.group(1).lower(), match.group(2).lower()
            if not head_sha.lower().startswith(range_to):
                reasons.append("CodeRabbit review range does not terminate at "
                               "the current head")
        else:
            reasons.append("CodeRabbit surface states no review range")
        if sticky.get("updated_at", "") < request["created_at"]:
            reasons.append("CodeRabbit surface predates this request generation")

    findings = [r for r in inventory["reviews"]
                if r["user"].get("id") == CODERABBIT_ACTOR_ID
                and (r.get("body") or "").strip()
                and r.get("state") not in ("APPROVED",)]
    inline = [c for c in inventory["review_comments"]
              if c["user"].get("id") == CODERABBIT_ACTOR_ID]
    if inline:
        reasons.append(f"{len(inline)} CodeRabbit inline finding(s) present")

    return {
        "provider": "coderabbit",
        "state": ("CODERABBIT_ADVISORY_POSITIVE" if not reasons
                  else "NOT_QUALIFIED"),
        "qualified": not reasons,
        "reasons": reasons,
        "request_comment_id": request["id"],
        "request_carrier": carrier_of(request),
        "acknowledged": acknowledged,
        "mutable_advisory_carrier": None if not sticky else {
            "id": sticky["id"], "created_at": sticky["created_at"],
            "updated_at": sticky.get("updated_at"),
            "actor_id": sticky["user"].get("id"),
            "body_hash": body_hash(sticky.get("body")),
            "review_range": {"from": range_from, "to": range_to},
        },
        "findings_seen": {"reviews": len(findings), "inline": len(inline)},
        "note": "check-run status is never used as cleanliness evidence",
    }


def build_bundle(epoch: dict, head_sha: str, auth_generation: int,
                 requests: dict, observations: dict, inventory_cutoff: str) -> dict:
    payload = {
        "bundle_version": BUNDLE_VERSION,
        "epoch_id": epoch["epoch_id"],
        "epoch_generation": epoch["generation"],
        "head_sha": head_sha,
        "auth_generation": auth_generation,
        "decision_rule_revision": DECISION_RULE_REVISION,
        "requests": requests,
        "observations": observations,
        "inventory_cutoff": inventory_cutoff,
    }
    payload["evidence_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return payload


def evaluate(bundle: dict, current_head: str, auth_state: str) -> dict:
    """The frozen decision. There is no path to publication here — the
    strongest possible outcome is an internal SUCCESS_CANDIDATE."""
    reasons = []
    if auth_state != "AUTHORIZED":
        return {"verdict": INVALIDATED, "reasons": [f"authorization {auth_state}"],
                "publishable": False}
    if current_head != bundle["head_sha"]:
        return {"verdict": STALE,
                "reasons": ["current head differs from the bundle head"],
                "publishable": False}

    codex = bundle["observations"]["codex"]
    rabbit = bundle["observations"]["coderabbit"]
    for observation in (codex, rabbit):
        if not observation["qualified"]:
            reasons.extend(f"{observation['provider']}: {r}"
                           for r in observation["reasons"])
    if codex["request_carrier"] != "app_mediated_user" or \
            rabbit["request_carrier"] != "app_mediated_user":
        reasons.append("request lineage is not on the app-mediated user carrier")
    if bundle["requests"]["codex"]["epoch_id"] != \
            bundle["requests"]["coderabbit"]["epoch_id"]:
        reasons.append("requests belong to different review epochs")
    for observation in (codex, rabbit):
        if any(observation["findings_seen"].values()):
            reasons.append(f"{observation['provider']} findings present")

    if reasons:
        return {"verdict": NOT_ESTABLISHED, "reasons": reasons,
                "publishable": False}
    return {
        "verdict": SUCCESS_CANDIDATE,
        "reasons": [],
        "publishable": False,          # A3a never publishes success
        "note": "internal candidate only; publication is A3b",
    }


def detect_mutation(bundle: dict, fresh_observations: dict) -> dict:
    """Non-monotonicity: any change to a referenced artifact invalidates."""
    changed = []
    for provider, key in (("codex", "terminal_comment"),
                          ("coderabbit", "mutable_advisory_carrier")):
        before = bundle["observations"][provider].get(key)
        after = fresh_observations[provider].get(key)
        if before is None or after is None:
            if before != after:
                changed.append(f"{provider}: carrier appeared or vanished")
            continue
        if before["body_hash"] != after["body_hash"]:
            changed.append(f"{provider}: carrier body changed")
        if before.get("updated_at") != after.get("updated_at"):
            changed.append(f"{provider}: carrier updated_at changed")
        if before["id"] != after["id"]:
            changed.append(f"{provider}: carrier replaced by a different comment")
    for provider in ("codex", "coderabbit"):
        before = bundle["observations"][provider]["findings_seen"]
        after = fresh_observations[provider]["findings_seen"]
        if after != before:
            changed.append(f"{provider}: finding inventory changed "
                           f"{before} -> {after}")
    return {"stable": not changed, "changes": changed,
            "verdict": INVALIDATED if changed else None}
