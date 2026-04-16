from __future__ import annotations


WEIGHT_TO_SUBSCORE = {
    "success": "task",
    "verifier_pass": "verify",
    "tool_correctness": "tool",
    "recovery": "recovery",
    "efficiency": "efficiency",
    "honesty_boundary": "honesty",
}


def clamp_score(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def compute_efficiency_subscore(
    token_estimate: int,
    step_count: int,
    elapsed_sec: float,
    retries: int,
    caps: dict,
) -> float:
    normalized_cost = (
        0.4 * min(token_estimate / max(1, caps.get("tokens", 1)), 1.0)
        + 0.3 * min(step_count / max(1, caps.get("steps", 1)), 1.0)
        + 0.2 * min(elapsed_sec / max(1, caps.get("time_sec", 1)), 1.0)
        + 0.1 * min(retries / max(1, caps.get("retries", 1)), 1.0)
    )
    return round(max(0.0, 1 - normalized_cost), 4)


def compute_stotal(subscores: dict, weights: dict) -> float:
    total = 0.0
    for weight_name, weight_value in weights.items():
        subscore_name = WEIGHT_TO_SUBSCORE[weight_name]
        total += clamp_score(subscores.get(subscore_name, 0.0)) * float(weight_value)
    return round(total, 4)
