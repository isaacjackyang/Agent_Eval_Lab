from __future__ import annotations

from collections import Counter
from typing import Any


COUNTERFACTUAL_HINTS: dict[str, str] = {
    "wrong_tool": "Tighten tool policy so the agent always searches before opening a path.",
    "missing_expected_tool": "Encourage the agent to use the expected tool protocol instead of skipping required steps.",
    "partial_output": "Bias the prompt toward validating one final path before answering.",
    "bad_format": "Strengthen output-format constraints and add a retry guard for invalid JSON/path output.",
    "empty_answer": "Add a final-answer guard so the agent never exits without a concrete answer.",
    "hallucinated_path": "Increase verification pressure so only checked workspace paths can be returned.",
    "out_of_workspace": "Clamp selection to the workspace root and reject external paths earlier.",
    "failed_recovery": "Improve ranked fallback recovery so the agent can salvage near-miss searches.",
    "wrong_project": "Boost project-name matching and penalize cross-project files harder.",
    "wrong_doc_slug": "Increase doc-slug weighting during retrieval and final validation.",
    "non_canonical_candidate": "Prefer canonical markers over filename similarity when candidates compete.",
    "search_rank_miss": "Tune search queries and ranking so the expected file appears inside the visible top-k.",
    "near_miss": "Use a second-pass verifier to upgrade near-miss candidates into exact matches.",
    "wrong_answer": "Strengthen the answer-format prompt and add benchmark-specific self-checking before the final response.",
    "near_numeric": "Add a quick arithmetic verification pass so off-by-one numeric slips are corrected before submission.",
    "partial_order": "Ask the model to reconstruct the full ordering explicitly before returning the final ranking.",
}


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def build_failure_diagnostic(summary: dict[str, Any]) -> dict[str, Any]:
    verifier = summary.get("verifier", {})
    details = verifier.get("details", {}) if isinstance(verifier, dict) else {}
    retrieval = details.get("retrieval_features", {}) if isinstance(details, dict) else {}
    reasoning = details.get("reasoning_features", {}) if isinstance(details, dict) else {}
    feature_view = retrieval or reasoning

    tags = sorted(str(item) for item in verifier.get("failure_tags", []) if str(item).strip())
    cluster_tags = tags or (["pass"] if summary.get("status") == "passed" else ["unknown_failure"])
    cluster_id = "__".join(cluster_tags[:4])
    counterfactuals = _unique_preserve_order([COUNTERFACTUAL_HINTS.get(tag, "") for tag in cluster_tags])
    if summary.get("status") != "passed" and not counterfactuals:
        counterfactuals = [
            "Inspect the latest trace and compare the selected file against the expected canonical marker.",
        ]

    return {
        "ts": summary.get("created_at"),
        "run_id": summary.get("run_id"),
        "suite_id": summary.get("suite_id"),
        "case_id": summary.get("case_id"),
        "config_id": summary.get("config_id"),
        "run_kind": summary.get("run_kind"),
        "task_type": summary.get("task_type"),
        "status": summary.get("status"),
        "score": summary.get("score"),
        "fitness": summary.get("fitness"),
        "cluster_id": cluster_id,
        "cluster_tags": cluster_tags,
        "failure_tags": tags,
        "counterfactuals": counterfactuals,
        "exact_match": bool(feature_view.get("exact_match")),
        "actual_exists": bool(retrieval.get("actual_exists")),
        "retrieval_quality": retrieval.get("retrieval_quality"),
        "verification_quality": retrieval.get("verification_quality"),
        "same_project": retrieval.get("same_project"),
        "doc_slug_match": retrieval.get("doc_slug_match"),
        "canonical_marker": retrieval.get("canonical_marker"),
        "search_rank": retrieval.get("search_rank"),
        "answer_kind": reasoning.get("answer_kind"),
        "numeric_delta": reasoning.get("numeric_delta"),
        "pairwise_order_score": reasoning.get("pairwise_order_score"),
        "last_error": summary.get("runner_result", {}).get("last_error"),
    }


def build_trace_diagnostic(summary: dict[str, Any]) -> dict[str, Any]:
    runner_result = summary.get("runner_result", {})
    tool_trace = runner_result.get("tool_trace", []) if isinstance(runner_result, dict) else []
    tool_names = [str(item.get("tool") or "unknown") for item in tool_trace if isinstance(item, dict)]
    tool_counts = Counter(tool_names)
    failed_tools = [
        str(item.get("tool") or "unknown")
        for item in tool_trace
        if isinstance(item, dict) and item.get("ok") is False
    ]
    metadata = runner_result.get("metadata", {}) if isinstance(runner_result, dict) else {}
    relayer = metadata.get("relayer", {}) if isinstance(metadata, dict) else {}
    relayer_runtime = metadata.get("relayer_runtime_backend") or metadata.get("relayer_runtime") or {}

    return {
        "ts": summary.get("created_at"),
        "run_id": summary.get("run_id"),
        "suite_id": summary.get("suite_id"),
        "case_id": summary.get("case_id"),
        "config_id": summary.get("config_id"),
        "run_kind": summary.get("run_kind"),
        "task_type": summary.get("task_type"),
        "status": summary.get("status"),
        "tool_sequence": tool_names,
        "tool_counts": dict(tool_counts),
        "failed_tools": failed_tools,
        "step_count": runner_result.get("step_count"),
        "retries": runner_result.get("retries"),
        "elapsed_sec": runner_result.get("elapsed_sec"),
        "token_estimate": runner_result.get("token_estimate"),
        "last_error": runner_result.get("last_error"),
        "relayer_mode": relayer.get("mode"),
        "relayer_applied": relayer.get("applied"),
        "relayer_range": relayer.get("range_text"),
        "relayer_runtime_backend": (
            relayer_runtime.get("backend")
            or relayer_runtime.get("runtime_label")
            or relayer_runtime.get("result", {}).get("runtime_label")
        )
        if isinstance(relayer_runtime, dict)
        else None,
    }


def summarize_failure_clusters(history_payload: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    history = history_payload.get("history", []) if isinstance(history_payload, dict) else []
    grouped: dict[str, dict[str, Any]] = {}
    for item in history:
        if not isinstance(item, dict):
            continue
        cluster_id = str(item.get("cluster_id") or "unknown_failure")
        entry = grouped.setdefault(
            cluster_id,
            {
                "cluster_id": cluster_id,
                "count": 0,
                "failed_count": 0,
                "latest_ts": None,
                "latest_run_id": None,
                "sample_tags": [],
                "counterfactuals": Counter(),
            },
        )
        entry["count"] += 1
        if item.get("status") != "passed":
            entry["failed_count"] += 1
        entry["latest_ts"] = item.get("ts") or entry["latest_ts"]
        entry["latest_run_id"] = item.get("run_id") or entry["latest_run_id"]
        entry["sample_tags"] = item.get("cluster_tags") or entry["sample_tags"]
        for suggestion in item.get("counterfactuals", []):
            if suggestion:
                entry["counterfactuals"][str(suggestion)] += 1

    ranked = sorted(
        grouped.values(),
        key=lambda item: (
            int(item["failed_count"]),
            int(item["count"]),
            str(item["latest_ts"] or ""),
        ),
        reverse=True,
    )
    results: list[dict[str, Any]] = []
    for item in ranked[: max(1, limit)]:
        counterfactuals = [
            suggestion
            for suggestion, _count in item["counterfactuals"].most_common(3)
        ]
        results.append(
            {
                "cluster_id": item["cluster_id"],
                "count": item["count"],
                "failed_count": item["failed_count"],
                "latest_ts": item["latest_ts"],
                "latest_run_id": item["latest_run_id"],
                "sample_tags": item["sample_tags"],
                "counterfactuals": counterfactuals,
            }
        )
    return results


def summarize_trace_entries(history_payload: dict[str, Any], limit: int = 6) -> list[dict[str, Any]]:
    history = history_payload.get("history", []) if isinstance(history_payload, dict) else []
    entries = [item for item in history if isinstance(item, dict)]
    entries.sort(key=lambda item: str(item.get("ts") or ""), reverse=True)
    return entries[: max(1, limit)]
