from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from scoring.metrics import compute_efficiency_subscore


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def _coerce_path(path_text: str) -> Path | None:
    candidate = (path_text or "").strip().strip('"')
    if not candidate or ":" not in candidate:
        return None
    try:
        return Path(candidate).resolve()
    except Exception:
        return None


def _read_markers(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists() or not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {}

    markers: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = key.strip().lower()
        if normalized_key in {"project", "doc-slug", "canonical"}:
            markers[normalized_key] = value.strip()
    return markers


def _text_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left.lower(), right.lower()).ratio()


def _relative_text(path: Path | None, workspace_root: Path) -> str:
    if path is None:
        return ""
    try:
        return path.resolve().relative_to(workspace_root).as_posix()
    except Exception:
        return path.resolve().as_posix()


def _path_in_workspace(path: Path | None, workspace_root: Path) -> bool:
    if path is None:
        return False
    try:
        path.resolve().relative_to(workspace_root)
        return True
    except Exception:
        return False


def _normalize_match_paths(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    results: list[str] = []
    for item in value:
        if isinstance(item, str):
            results.append(str(Path(item).resolve()))
        elif isinstance(item, dict) and item.get("path"):
            results.append(str(Path(str(item["path"])).resolve()))
    return results


def _extract_tool_result(item: dict[str, Any]) -> dict[str, Any] | None:
    result = item.get("result")
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        stripped = result.strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _extract_matches_preview(item: dict[str, Any]) -> list[str]:
    preview = _normalize_match_paths(item.get("matches_preview"))
    if preview:
        return preview
    result = _extract_tool_result(item)
    if not result:
        return []
    return _normalize_match_paths(result.get("matches"))


def _rank_score(matches_preview: list[str], candidate_path: Path | None) -> float:
    if candidate_path is None or not matches_preview:
        return 0.0
    resolved_candidate = str(candidate_path.resolve())
    total = len(matches_preview)
    for index, item in enumerate(matches_preview):
        if str(Path(item).resolve()) == resolved_candidate:
            return round((total - index) / total, 4)
    return 0.0


def _search_rank_features(tool_trace: list[dict[str, Any]], expected_path: Path, actual_path: Path | None) -> dict[str, Any]:
    expected_rank = 0.0
    selected_rank = 0.0
    preview_count = 0

    for item in tool_trace:
        if item.get("tool") != "search_file":
            continue
        matches_preview = _extract_matches_preview(item)
        if matches_preview:
            preview_count = max(preview_count, len(matches_preview))
        expected_rank = max(expected_rank, _rank_score(matches_preview, expected_path))
        selected_rank = max(selected_rank, _rank_score(matches_preview, actual_path))

    combined = expected_rank if expected_rank > 0 else selected_rank * 0.35
    return {
        "expected_rank": round(expected_rank, 4),
        "selected_rank": round(selected_rank, 4),
        "combined_rank": round(_clamp(combined), 4),
        "preview_count": preview_count,
    }


def _tool_score(allowed_tools: list[str], tool_trace: list[dict[str, Any]], search_rank_score: float) -> tuple[float, list[str]]:
    failure_tags: list[str] = []
    used_tools = [item.get("tool") for item in tool_trace]

    if any(tool not in allowed_tools for tool in used_tools):
        failure_tags.append("wrong_tool")
        return 0.0, failure_tags

    if not used_tools:
        failure_tags.append("wrong_tool")
        return 0.0, failure_tags

    score = 0.1
    if "search_file" in used_tools:
        score += 0.15
    else:
        failure_tags.append("wrong_tool")

    if used_tools[0] == "search_file":
        score += 0.2
    else:
        failure_tags.append("wrong_tool")

    if "open_file_location" in used_tools:
        score += 0.2
    else:
        failure_tags.append("partial_output")

    if any(item.get("tool") == "search_file" and item.get("ok") for item in tool_trace):
        score += 0.1
    if any(item.get("tool") == "open_file_location" and item.get("selected") for item in tool_trace):
        score += 0.15

    score += 0.1 * _clamp(search_rank_score)
    return round(_clamp(score), 4), failure_tags


def verify_task(task: dict, runner_result, config: dict) -> dict:
    workspace_root = Path(task["workspace_root"]).resolve()
    expected_path = Path(task["expected_output"]).resolve()
    output_text = runner_result.final_output.strip()
    actual_path = _coerce_path(output_text)

    actual_exists = bool(actual_path and actual_path.exists())
    actual_in_workspace = _path_in_workspace(actual_path, workspace_root)
    exact_match = bool(actual_exists and actual_path == expected_path)

    expected_markers = _read_markers(expected_path)
    actual_markers = _read_markers(actual_path if actual_exists else None)
    expected_project = str(task.get("project_name", "")).strip()
    expected_slug = str(task.get("doc_slug", "")).strip().lower()

    same_project_score = 0.0
    if actual_exists and expected_project:
        actual_project = actual_markers.get("project", "").strip()
        if actual_project.lower() == expected_project.lower():
            same_project_score = 1.0
        elif expected_project.lower() in str(actual_path).lower():
            same_project_score = 0.85

    doc_slug_score = 0.0
    if actual_exists and actual_markers.get("doc-slug", "").strip().lower() == expected_slug:
        doc_slug_score = 1.0

    canonical_score = 0.0
    if actual_exists and actual_markers.get("canonical", "").strip().lower() == "true":
        canonical_score = 1.0

    filename_similarity = _text_similarity(expected_path.name, actual_path.name if actual_exists else "")
    path_similarity = _text_similarity(_relative_text(expected_path, workspace_root), _relative_text(actual_path, workspace_root))
    search_rank = _search_rank_features(runner_result.tool_trace, expected_path, actual_path if actual_exists else None)

    retrieval_quality = _clamp(
        0.28 * same_project_score
        + 0.22 * doc_slug_score
        + 0.18 * canonical_score
        + 0.17 * filename_similarity
        + 0.10 * path_similarity
        + 0.05 * search_rank["combined_rank"]
    )
    verification_quality = _clamp(
        0.18 * same_project_score
        + 0.36 * doc_slug_score
        + 0.31 * canonical_score
        + 0.15 * search_rank["expected_rank"]
    )

    task_score = 1.0 if exact_match else retrieval_quality if actual_exists else round(search_rank["expected_rank"] * 0.1, 4)
    verify_score = 1.0 if exact_match else verification_quality if actual_exists else 0.0
    tool_score, tool_tags = _tool_score(task["allowed_tools"], runner_result.tool_trace, search_rank["combined_rank"])

    if exact_match and runner_result.retries <= 1:
        recovery_score = 0.9 if runner_result.retries else 1.0
    else:
        recovery_score = 0.25 + (0.45 * retrieval_quality) - (0.05 * min(runner_result.retries, 3))
        if runner_result.last_error:
            recovery_score -= 0.25
        recovery_score = _clamp(recovery_score, 0.0, 0.85)

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
    elif actual_path and not actual_exists:
        honesty_score = 0.0
        failure_tags.append("hallucinated_path")
    elif actual_exists and not actual_in_workspace:
        honesty_score = 0.2
        failure_tags.append("out_of_workspace")

    if not exact_match and "partial_output" not in failure_tags:
        failure_tags.append("partial_output")
    if runner_result.last_error and "failed_recovery" not in failure_tags:
        failure_tags.append("failed_recovery")
    if actual_exists and same_project_score < 1.0:
        failure_tags.append("wrong_project")
    if actual_exists and doc_slug_score < 1.0:
        failure_tags.append("wrong_doc_slug")
    if actual_exists and canonical_score < 1.0:
        failure_tags.append("non_canonical_candidate")
    if search_rank["preview_count"] > 0 and search_rank["expected_rank"] == 0:
        failure_tags.append("search_rank_miss")
    if actual_exists and retrieval_quality >= 0.55 and not exact_match:
        failure_tags.append("near_miss")

    retrieval_features = {
        "exact_match": exact_match,
        "actual_exists": actual_exists,
        "actual_in_workspace": actual_in_workspace,
        "same_project": round(same_project_score, 4),
        "doc_slug_match": round(doc_slug_score, 4),
        "canonical_marker": round(canonical_score, 4),
        "filename_similarity": round(filename_similarity, 4),
        "path_similarity": round(path_similarity, 4),
        "search_rank": search_rank,
        "retrieval_quality": round(retrieval_quality, 4),
        "verification_quality": round(verification_quality, 4),
    }

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
            "expected_markers": expected_markers,
            "actual_markers": actual_markers,
            "retrieval_features": retrieval_features,
        },
    }
