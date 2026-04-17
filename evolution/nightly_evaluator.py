from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
for candidate in (ROOT, SCRIPTS_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from run_single import execute_single_run
from scoring.aggregation import compute_fitness, compute_stability_score, compute_suite_score


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [item["score"] for item in results]
    honesty_scores = [item["verifier"]["subscores"]["honesty"] for item in results]
    rollback_scores = [item["rollback"]["rollback_safety_score"] for item in results]
    return {
        "suite_score": compute_suite_score(scores),
        "stability_score": compute_stability_score(scores),
        "pass_rate": round(sum(1 for item in results if item["status"] == "passed") / max(1, len(results)), 4),
        "honesty_score": compute_suite_score(honesty_scores),
        "rollback_safety_score": compute_suite_score(rollback_scores),
        "run_ids": [item["run_id"] for item in results],
    }


def evaluate_candidate_config(
    *,
    config_path: Path,
    config: dict[str, Any],
    suite_id: str,
    seed_offset: int,
    candidate_runs_per_config: int,
    regression_suite: dict[str, Any],
    progress_offset: int,
    progress_target: int,
    candidate_run_kind: str = "candidate",
    regression_run_kind: str = "regression",
    candidate_case_prefix: str | None = None,
    append_candidate_score_history: bool = True,
    append_regression_score_history: bool = False,
) -> dict[str, Any]:
    candidate_case_prefix = candidate_case_prefix or config["config_id"]

    candidate_results = []
    for index in range(candidate_runs_per_config):
        candidate_results.append(
            execute_single_run(
                config_path=config_path,
                seed=seed_offset + index,
                append_score_history=append_candidate_score_history,
                append_baseline_history=False,
                append_rollback_history=False,
                manage_baseline=False,
                reset_stream=False,
                run_kind=candidate_run_kind,
                suite_id=suite_id,
                case_id=f"{candidate_case_prefix}__candidate_{index + 1:02d}",
                progress_current=progress_offset + index + 1,
                progress_target=progress_target,
            )
        )

    regression_results = []
    for case in regression_suite["cases"]:
        progress_index = progress_offset + candidate_runs_per_config + len(regression_results) + 1
        regression_results.append(
            execute_single_run(
                config_path=config_path,
                seed=case["seed"],
                append_score_history=append_regression_score_history,
                append_baseline_history=False,
                append_rollback_history=False,
                manage_baseline=False,
                reset_stream=False,
                run_kind=regression_run_kind,
                suite_id=suite_id,
                case_id=f"{candidate_case_prefix}__{case['case_id']}",
                progress_current=progress_index,
                progress_target=progress_target,
            )
        )

    candidate_summary = summarize_results(candidate_results)
    regression_summary = summarize_results(regression_results)
    suite_score_a = float(config.get("layer_a_proxy_score", 1.0))
    fitness = compute_fitness(
        suite_score_c=candidate_summary["suite_score"],
        suite_score_b=regression_summary["suite_score"],
        suite_score_a=suite_score_a,
        stability_score=candidate_summary["stability_score"],
        rollback_safety_score=candidate_summary["rollback_safety_score"],
    )

    parameter_name = config.get("mutation_target")
    parameter_before = config.get("mutation_before")
    parameter_after = config.get("mutation_after")
    notes = config.get("mutation_notes", "")
    if parameter_name:
        notes = f"{notes} ({parameter_name}: {parameter_before} -> {parameter_after})"

    payload = {
        "config_id": config["config_id"],
        "selected_at": datetime.now().isoformat(timespec="seconds"),
        "fitness": fitness,
        "suite_score_c": candidate_summary["suite_score"],
        "suite_score_b": regression_summary["suite_score"],
        "suite_score_a": suite_score_a,
        "stability_score": candidate_summary["stability_score"],
        "rollback_safety_score": candidate_summary["rollback_safety_score"],
        "regression_pass_rate": regression_summary["pass_rate"],
        "honesty_score": candidate_summary["honesty_score"],
        "run_id": suite_id,
        "notes": notes or f"Candidate {config['config_id']} evaluated by nightly sequential tuning.",
        "source_config_path": str(config_path.resolve()),
        "config_body": config,
    }

    return {
        "config": config,
        "config_path": str(config_path.resolve()),
        "mutation_profile": config.get("mutation_profile", "unknown"),
        "mutation_notes": config.get("mutation_notes", ""),
        "mutation_strategy": config.get("mutation_strategy", "single_parameter"),
        "parameter_name": parameter_name,
        "parameter_before": parameter_before,
        "parameter_after": parameter_after,
        "parameter_snapshot": config.get("parameter_snapshot"),
        "fitness": fitness,
        "candidate_summary": candidate_summary,
        "regression_summary": regression_summary,
        "suite_score_a": suite_score_a,
        "candidate_payload": payload,
        "candidate_runs": candidate_results,
        "regression_runs": regression_results,
    }
