from __future__ import annotations

from statistics import mean, pstdev


def compute_suite_score(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(mean(values), 4)


def compute_stability_score(values: list[float]) -> float:
    if len(values) < 2:
        return 1.0
    volatility = pstdev(values)
    return round(max(0.0, 1 - min(volatility / 0.35, 1.0)), 4)


def compute_rollback_safety(
    config_restore_success: float,
    workspace_restore_success: float,
    regression_preservation: float,
) -> float:
    score = (
        0.5 * config_restore_success
        + 0.3 * workspace_restore_success
        + 0.2 * regression_preservation
    )
    return round(score, 4)


def compute_fitness(
    suite_score_c: float,
    suite_score_b: float,
    suite_score_a: float,
    stability_score: float,
    rollback_safety_score: float,
) -> float:
    return round(
        0.55 * suite_score_c
        + 0.20 * suite_score_b
        + 0.10 * suite_score_a
        + 0.10 * stability_score
        + 0.05 * rollback_safety_score,
        4,
    )
