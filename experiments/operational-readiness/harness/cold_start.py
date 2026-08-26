"""Primary cold start after losing durable policy state.

The one rule, and the reason it is its own module rather than a comment:

    a Governor that has lost its decision history may NOT reconstruct
    SUCCESS from GitHub

GitHub can show a green check that this process has no record of deciding.
Adopting it would mean inferring policy from the very surface the program
spent five stages refusing to trust. Every current head is therefore
`NOT_ESTABLISHED` until a fresh provider qualification says otherwise.
"""
NOT_ESTABLISHED = "NOT_ESTABLISHED"


def plan_cold_start(observed_heads, durable_state_available: bool) -> dict:
    """`observed_heads` is what GitHub currently shows, including any
    conclusions. It is used to enumerate work, never to adopt verdicts."""
    if durable_state_available:
        return {"cold_start": False,
                "note": "durable state present; normal reconciliation applies"}
    plan = []
    for head in observed_heads:
        plan.append({
            "pr_number": head.get("pr_number"),
            "head_sha": head.get("head_sha"),
            "observed_conclusion": head.get("conclusion"),
            "adopted_verdict": NOT_ESTABLISHED,
            "adopted_from_github": False,
            "requires_fresh_qualification": True,
        })
    return {
        "cold_start": True,
        "durable_state_available": False,
        "plan": plan,
        "successes_reconstructed": 0,
        "rule": "never reconstruct SUCCESS from GitHub; enumerate work only",
    }


def adopted_verdicts(plan: dict) -> set:
    return {item["adopted_verdict"] for item in plan.get("plan", [])}
