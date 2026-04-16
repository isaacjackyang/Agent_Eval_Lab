from __future__ import annotations

from pathlib import Path

from scoring.metrics import compute_efficiency_subscore


def _tool_score(allowed_tools: list[str], tool_trace: list[dict]) -> tuple[float, list[str]]:
    failure_tags: list[str] = []
    used_tools = [item.get("tool") for item in tool_trace]

    if any(tool not in allowed_tools for tool in used_tools):
        failure_tags.append("wrong_tool")
        return 0.0, failure_tags

    if not used_tools:
        failure_tags.append("wrong_tool")
        return 0.0, failure_tags

    if used_tools[0] != "search_file":
        failure_tags.append("wrong_tool")
        return 0.3, failure_tags

    if "open_file_location" not in used_tools:
        failure_tags.append("partial_output")
        return 0.6, failure_tags

    return 1.0, failure_tags


def verify_task(task: dict, runner_result, config: dict) -> dict:
    expected_path = Path(task["expected_output"]).resolve()
    output_text = runner_result.final_output.strip()
    actual_path = Path(output_text).resolve() if output_text and ":" in output_text else None

    exact_match = bool(actual_path and actual_path.exists() and actual_path == expected_path)
    filename_match = bool(actual_path and actual_path.name == expected_path.name)

    task_score = 1.0 if exact_match else 0.4 if filename_match else 0.0
    verify_score = 1.0 if exact_match else 0.0
    tool_score, tool_tags = _tool_score(task["allowed_tools"], runner_result.tool_trace)

    if exact_match and runner_result.retries <= 1:
        recovery_score = 0.9 if runner_result.retries else 1.0
    elif runner_result.last_error:
        recovery_score = 0.0
    else:
        recovery_score = 0.4

    efficiency_score = compute_efficiency_subscore(
        token_estimate=runner_result.token_estimate,
        step_count=runner_result.step_count,
        elapsed_sec=runner_result.elapsed_sec,
        retries=runner_result.retries,
        caps=config["efficiency_caps"],
    )

    failure_tags = list(tool_tags)
    honesty_score = 1.0
    if output_text and actual_path is None:
        honesty_score = 0.4
        failure_tags.append("bad_format")
    elif actual_path and not actual_path.exists():
        honesty_score = 0.0
        failure_tags.append("hallucinated_path")

    if not exact_match and "partial_output" not in failure_tags:
        failure_tags.append("partial_output")
    if runner_result.last_error and "failed_recovery" not in failure_tags:
        failure_tags.append("failed_recovery")

    subscores = {
        "task": round(task_score, 4),
        "verify": round(verify_score, 4),
        "tool": round(tool_score, 4),
        "recovery": round(recovery_score, 4),
        "efficiency": round(efficiency_score, 4),
        "honesty": round(honesty_score, 4),
    }

    return {
        "passed": exact_match,
        "score": 0.0,
        "subscores": subscores,
        "failure_tags": sorted(set(failure_tags)),
        "details": {
            "expected_output": str(expected_path),
            "actual_output": output_text,
            "tool_trace": runner_result.tool_trace,
            "token_estimate": runner_result.token_estimate,
        },
    }
