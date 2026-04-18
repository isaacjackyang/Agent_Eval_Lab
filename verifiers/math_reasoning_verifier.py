from __future__ import annotations

import math
import re
from itertools import combinations
from typing import Any

from scoring.metrics import compute_efficiency_subscore


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def _normalize_space(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_numeric_text(value: str) -> str:
    return _normalize_space(value).replace(",", "")


def _parse_number(value: str) -> float | None:
    normalized = _normalize_numeric_text(value)
    if not normalized:
        return None
    try:
        return float(normalized)
    except ValueError:
        return None


def _normalize_ranking_text(value: str) -> str:
    normalized = _normalize_space(value)
    normalized = normalized.replace("->", ">").replace(",", ">").replace("|", ">")
    parts = [part.strip() for part in normalized.split(">") if part.strip()]
    return " > ".join(parts)


def _parse_ranking(value: str) -> list[str]:
    normalized = _normalize_ranking_text(value)
    return [part.strip() for part in normalized.split(">") if part.strip()]


def _pairwise_order_score(expected: list[str], actual: list[str]) -> float:
    if len(expected) < 2 or len(actual) != len(expected):
        return 0.0
    actual_positions = {name: index for index, name in enumerate(actual)}
    comparisons = list(combinations(expected, 2))
    correct = 0
    for left, right in comparisons:
        if left not in actual_positions or right not in actual_positions:
            continue
        if actual_positions[left] < actual_positions[right]:
            correct += 1
    return correct / len(comparisons)


def _position_score(expected: list[str], actual: list[str]) -> float:
    if not expected or len(actual) != len(expected):
        return 0.0
    matches = sum(1 for index, name in enumerate(expected) if actual[index] == name)
    return matches / len(expected)


def _answer_shape_score(answer_kind: str, actual_text: str, actual_number: float | None, actual_ranking: list[str]) -> float:
    if answer_kind == "integer":
        if actual_number is not None and math.isfinite(actual_number) and actual_number.is_integer():
            return 1.0
        if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", _normalize_numeric_text(actual_text)):
            return 0.7
        return 0.0
    if answer_kind == "ranking":
        if actual_ranking:
            return 1.0 if len(actual_ranking) >= 3 else 0.6
        return 0.0
    return 0.0


def _tool_score(allowed_tools: list[str], tool_trace: list[dict[str, Any]]) -> tuple[float, list[str]]:
    used_tools = [str(item.get("tool") or "").strip() for item in tool_trace if str(item.get("tool") or "").strip()]
    if not used_tools and not allowed_tools:
        return 1.0, []
    if any(tool not in allowed_tools for tool in used_tools):
        return 0.0, ["wrong_tool"]
    if not used_tools:
        return 0.6, ["missing_expected_tool"]
    return 1.0, []


def verify_task(task: dict, runner_result, config: dict) -> dict:
    output_text = _normalize_space(runner_result.final_output)
    expected_text = _normalize_space(task["expected_output"])
    metadata = task.get("metadata", {}) if isinstance(task.get("metadata"), dict) else {}
    answer_kind = str(metadata.get("answer_kind", "integer"))

    expected_number = _parse_number(expected_text)
    actual_number = _parse_number(output_text)
    expected_ranking = _parse_ranking(expected_text)
    actual_ranking = _parse_ranking(output_text)

    exact_match = False
    reasoning_quality = 0.0
    features: dict[str, Any] = {
        "answer_kind": answer_kind,
        "expected_output": expected_text,
        "actual_output": output_text,
    }

    if answer_kind == "integer" and expected_number is not None:
        exact_match = actual_number is not None and actual_number == expected_number
        if actual_number is not None:
            delta = abs(actual_number - expected_number)
            proximity = _clamp(1.0 - min(delta, 10.0) / 10.0)
        else:
            delta = None
            proximity = 0.0
        shape_score = _answer_shape_score(answer_kind, output_text, actual_number, actual_ranking)
        reasoning_quality = _clamp(0.82 * proximity + 0.18 * shape_score)
        features.update(
            {
                "expected_number": expected_number,
                "actual_number": actual_number,
                "numeric_delta": delta,
                "numeric_proximity": round(proximity, 4),
                "answer_shape_score": round(shape_score, 4),
            }
        )
    else:
        expected_names = expected_ranking
        actual_names = actual_ranking
        exact_match = actual_names == expected_names
        pairwise_score = _pairwise_order_score(expected_names, actual_names)
        position_score = _position_score(expected_names, actual_names)
        shape_score = _answer_shape_score(answer_kind, output_text, actual_number, actual_ranking)
        reasoning_quality = _clamp(0.55 * pairwise_score + 0.3 * position_score + 0.15 * shape_score)
        features.update(
            {
                "expected_ranking": expected_names,
                "actual_ranking": actual_names,
                "pairwise_order_score": round(pairwise_score, 4),
                "position_score": round(position_score, 4),
                "answer_shape_score": round(shape_score, 4),
            }
        )

    task_score = 1.0 if exact_match else round(reasoning_quality, 4)
    verify_score = 1.0 if exact_match else round(_clamp(0.25 + 0.75 * reasoning_quality), 4) if output_text else 0.0
    tool_score, tool_tags = _tool_score(task.get("allowed_tools", []), runner_result.tool_trace)

    if exact_match and runner_result.retries <= 1:
        recovery_score = 0.9 if runner_result.retries else 1.0
    else:
        recovery_score = _clamp(0.25 + 0.5 * reasoning_quality - 0.08 * min(runner_result.retries, 3))
        if runner_result.last_error:
            recovery_score = _clamp(recovery_score - 0.2)

    efficiency_score = compute_efficiency_subscore(
        token_estimate=runner_result.token_estimate,
        step_count=runner_result.step_count,
        elapsed_sec=runner_result.elapsed_sec,
        retries=runner_result.retries,
        caps=config["efficiency_caps"],
    )

    failure_tags = list(tool_tags)
    honesty_score = 1.0
    if not output_text:
        honesty_score = 0.0
        failure_tags.append("empty_answer")
    elif answer_kind == "integer" and actual_number is None:
        honesty_score = 0.45
        failure_tags.append("bad_format")
    elif answer_kind == "ranking" and not actual_ranking:
        honesty_score = 0.45
        failure_tags.append("bad_format")

    if not exact_match:
        failure_tags.append("wrong_answer")
    if answer_kind == "integer" and features.get("numeric_delta") in {1.0, 1, 2.0, 2}:
        failure_tags.append("near_numeric")
    if answer_kind == "ranking" and features.get("pairwise_order_score", 0.0) >= 0.5 and not exact_match:
        failure_tags.append("partial_order")
    if runner_result.last_error:
        failure_tags.append("failed_recovery")

    subscores = {
        "task": round(task_score, 4),
        "verify": round(verify_score, 4),
        "tool": round(tool_score, 4),
        "recovery": round(recovery_score, 4),
        "efficiency": round(efficiency_score, 4),
        "honesty": round(honesty_score, 4),
    }
    features["exact_match"] = exact_match
    features["reasoning_quality"] = round(reasoning_quality, 4)

    return {
        "passed": exact_match,
        "score": 0.0,
        "subscores": subscores,
        "failure_tags": sorted(set(failure_tags)),
        "details": {
            "expected_output": expected_text,
            "actual_output": output_text,
            "tool_trace": runner_result.tool_trace,
            "token_estimate": runner_result.token_estimate,
            "reasoning_features": features,
        },
    }
