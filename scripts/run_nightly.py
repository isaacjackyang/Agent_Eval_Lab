from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evolution.mutator import (
    EVOLUTION_MODE_OPTIONS,
    build_heat_map_candidates,
    build_mutation_candidates,
    resolve_heat_map_plan,
    parameter_snapshot,
    persist_candidate_config,
    sampling_provider_for_config,
    selected_sampling_parameters_for_config,
)
from rollback.baseline_manager import assess_candidate, load_baseline, write_baseline
from run_single import execute_single_run
from scoring.aggregation import compute_fitness, compute_stability_score, compute_suite_score
from storage.history_writer import append_history_entry, ensure_report_files, seed_static_histories, write_json
from storage.live_writer import LiveWriter


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _summarize_results(results: list[dict]) -> dict:
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


def _evaluate_candidate_config(
    config_path: Path,
    config: dict,
    suite_id: str,
    seed_offset: int,
    candidate_runs_per_config: int,
    regression_suite: dict,
    progress_offset: int,
    progress_target: int,
) -> dict:
    candidate_results = []
    for index in range(candidate_runs_per_config):
        candidate_results.append(
            execute_single_run(
                config_path=config_path,
                seed=seed_offset + index,
                append_score_history=True,
                append_baseline_history=False,
                append_rollback_history=False,
                manage_baseline=False,
                reset_stream=False,
                run_kind="candidate",
                suite_id=suite_id,
                case_id=f"{config['config_id']}__candidate_{index + 1:02d}",
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
                append_score_history=False,
                append_baseline_history=False,
                append_rollback_history=False,
                manage_baseline=False,
                reset_stream=False,
                run_kind="regression",
                suite_id=suite_id,
                case_id=f"{config['config_id']}__{case['case_id']}",
                progress_current=progress_index,
                progress_target=progress_target,
            )
        )

    candidate_summary = _summarize_results(candidate_results)
    regression_summary = _summarize_results(regression_results)
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
        "parameter_snapshot": config.get("parameter_snapshot", parameter_snapshot(config)),
        "fitness": fitness,
        "candidate_summary": candidate_summary,
        "regression_summary": regression_summary,
        "suite_score_a": suite_score_a,
        "candidate_payload": payload,
        "candidate_runs": candidate_results,
        "regression_runs": regression_results,
    }


def _round_summary_entry(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "config_id": result["config"]["config_id"],
        "mutation_profile": result["mutation_profile"],
        "parameter_name": result.get("parameter_name"),
        "parameter_before": result.get("parameter_before"),
        "parameter_after": result.get("parameter_after"),
        "heat_map_coordinates": result["config"].get("heat_map_coordinates"),
        "fitness": result["fitness"],
        "suite_score_c": result["candidate_summary"]["suite_score"],
        "suite_score_b": result["regression_summary"]["suite_score"],
        "regression_pass_rate": result["regression_summary"]["pass_rate"],
        "honesty_score": result["candidate_summary"]["honesty_score"],
        "config_path": result["config_path"],
        "parameter_snapshot": result.get("parameter_snapshot", {}),
    }


def _improvement_decision(reference_result: dict[str, Any] | None, candidate_result: dict[str, Any], min_pass_rate: float) -> dict[str, Any]:
    candidate_score = candidate_result["candidate_summary"]["suite_score"]
    candidate_fitness = candidate_result["fitness"]
    candidate_pass_rate = candidate_result["regression_summary"]["pass_rate"]
    reasons: list[str] = []

    if candidate_pass_rate < min_pass_rate:
        reasons.append(f"regression_pass_rate_below_gate:{candidate_pass_rate:.4f}<{min_pass_rate:.4f}")
        return {"improved": False, "reasons": reasons}

    if reference_result is None:
        reasons.append("first_reference_candidate")
        return {"improved": True, "reasons": reasons}

    reference_score = reference_result["candidate_summary"]["suite_score"]
    reference_fitness = reference_result["fitness"]

    if candidate_score > reference_score + 1e-9:
        reasons.append(f"suite_score_c_improved:{candidate_score:.4f}>{reference_score:.4f}")
        return {"improved": True, "reasons": reasons}

    if abs(candidate_score - reference_score) <= 1e-9 and candidate_fitness > reference_fitness + 1e-9:
        reasons.append(f"fitness_tiebreak_improved:{candidate_fitness:.4f}>{reference_fitness:.4f}")
        return {"improved": True, "reasons": reasons}

    reasons.append(f"no_score_gain:{candidate_score:.4f}<={reference_score:.4f}")
    return {"improved": False, "reasons": reasons}


def _append_parameter_history(reports_dir: Path, entry: dict[str, Any]) -> None:
    append_history_entry(reports_dir / "parameter_history.json", entry)


def _resolve_evolution_mode(base_config: dict[str, Any], raw_mode: str | None) -> str:
    mode = str(raw_mode or base_config.get("nightly", {}).get("evolution_mode", "model_params")).strip().lower()
    valid_modes = {item["value"] for item in EVOLUTION_MODE_OPTIONS}
    if mode not in valid_modes:
        raise RuntimeError(f"Unsupported evolution mode: {mode}")
    return mode


def _sequential_strategy_for_mode(evolution_mode: str) -> str:
    if evolution_mode == "architecture_program":
        return "architecture_program_hill_climb"
    if evolution_mode == "heat_map":
        return "heat_map_scan"
    return "model_params_hill_climb"


def _append_mutation_history(reports_dir: Path, entry: dict[str, Any], evolution_mode: str) -> None:
    if evolution_mode == "architecture_program":
        improved_text = "improved" if entry.get("improved") else "kept baseline"
        append_history_entry(
            reports_dir / "architecture_history.json",
            {
                **entry,
                "change": entry.get("mutation_profile"),
                "summary": (
                    f"Nightly round {entry.get('round_index')}: {entry.get('config_id')} "
                    f"variant={entry.get('mutation_profile')} fitness={entry.get('fitness', 0.0):.4f} "
                    f"status={improved_text}"
                ),
            },
        )
        return
    if evolution_mode == "heat_map":
        return
    _append_parameter_history(reports_dir, entry)


def _heat_map_cell_entry(
    result: dict[str, Any],
    *,
    baseline_result: dict[str, Any],
    coordinates: dict[str, Any],
) -> dict[str, Any]:
    suite_score = result["candidate_summary"]["suite_score"]
    baseline_suite_score = baseline_result["candidate_summary"]["suite_score"]
    fitness = result["fitness"]
    baseline_fitness = baseline_result["fitness"]
    return {
        "config_id": result["config"]["config_id"],
        "mutation_profile": result["mutation_profile"],
        "round_index": result.get("round_index"),
        "x_axis": coordinates.get("x_axis"),
        "x_label": coordinates.get("x_label"),
        "x_value": coordinates.get("x_value"),
        "y_axis": coordinates.get("y_axis"),
        "y_label": coordinates.get("y_label"),
        "y_value": coordinates.get("y_value"),
        "suite_score_c": suite_score,
        "fitness": fitness,
        "regression_pass_rate": result["regression_summary"]["pass_rate"],
        "honesty_score": result["candidate_summary"]["honesty_score"],
        "delta_suite_score": round(suite_score - baseline_suite_score, 6),
        "delta_fitness": round(fitness - baseline_fitness, 6),
        "improved_vs_baseline": suite_score > baseline_suite_score + 1e-9,
    }


def _build_heat_map_summary(
    *,
    base_config: dict[str, Any],
    baseline_result: dict[str, Any],
    evaluated_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    plan = resolve_heat_map_plan(base_config)
    baseline_coordinates = dict(plan["baseline_coordinate"])
    baseline_cell = _heat_map_cell_entry(
        baseline_result,
        baseline_result=baseline_result,
        coordinates={
            "x_axis": baseline_coordinates["x_axis"],
            "x_label": baseline_coordinates["x_label"],
            "x_value": baseline_coordinates["x_value"],
            "y_axis": baseline_coordinates["y_axis"],
            "y_label": baseline_coordinates["y_label"],
            "y_value": baseline_coordinates["y_value"],
        },
    )

    cell_lookup: dict[tuple[str, str], dict[str, Any]] = {
        (str(baseline_cell["x_value"]), str(baseline_cell["y_value"])): baseline_cell
    }
    ranked_cells: list[dict[str, Any]] = [baseline_cell]
    for result in evaluated_candidates:
        coordinates = result["config"].get("heat_map_coordinates")
        if not isinstance(coordinates, dict):
            continue
        cell = _heat_map_cell_entry(result, baseline_result=baseline_result, coordinates=coordinates)
        cell_lookup[(str(cell["x_value"]), str(cell["y_value"]))] = cell
        ranked_cells.append(cell)

    matrix_rows: list[dict[str, Any]] = []
    for y_value in plan["y_values"]:
        row_cells: list[dict[str, Any]] = []
        for x_value in plan["x_values"]:
            cell = cell_lookup.get((str(x_value), str(y_value)))
            if cell is None:
                row_cells.append(
                    {
                        "x_value": x_value,
                        "y_value": y_value,
                        "status": "not_run",
                    }
                )
            else:
                row_cells.append(cell)
        matrix_rows.append({"y_value": y_value, "cells": row_cells})

    ranked_non_baseline = sorted(
        [
            cell
            for cell in ranked_cells
            if not (
                cell["x_value"] == baseline_coordinates["x_value"]
                and cell["y_value"] == baseline_coordinates["y_value"]
            )
        ],
        key=lambda item: (
            item["suite_score_c"],
            item["fitness"],
            item["regression_pass_rate"],
            item["honesty_score"],
        ),
        reverse=True,
    )

    best_cell = ranked_non_baseline[0] if ranked_non_baseline else baseline_cell
    return {
        "x_axis": plan["x_axis"],
        "x_label": plan["x_label"],
        "x_values": plan["x_values"],
        "y_axis": plan["y_axis"],
        "y_label": plan["y_label"],
        "y_values": plan["y_values"],
        "cell_count": plan["cell_count"],
        "baseline": baseline_cell,
        "best_cell": best_cell,
        "improved_cell_count": sum(1 for cell in ranked_non_baseline if cell["improved_vs_baseline"]),
        "top_candidates": ranked_non_baseline[: plan["top_k"]],
        "matrix": matrix_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local nightly evaluation and sequential evolution loop.")
    parser.add_argument("--config", default=str(ROOT / "configs" / "experiments" / "default_mvp.json"))
    parser.add_argument("--seed-start", type=int, default=500)
    parser.add_argument("--evolution-mode", default=None)
    parser.add_argument("--sampling-parameters", default=None)
    args = parser.parse_args()

    base_config_path = Path(args.config)
    base_config = _load_json(base_config_path)
    evolution_mode = _resolve_evolution_mode(base_config, args.evolution_mode)
    if evolution_mode in {"architecture_program", "heat_map"} and base_config.get("runner") != "llama_cpp_agent":
        raise RuntimeError(f"{evolution_mode} evolution is currently supported only for llama_cpp_agent configs.")
    if evolution_mode == "model_params" and sampling_provider_for_config(base_config) is None:
        raise RuntimeError("Model-parameter evolution currently requires a llama_cpp_agent config with provider=ollama or provider=llama-cpp.")
    base_config.setdefault("nightly", {})
    base_config["nightly"]["evolution_mode"] = evolution_mode
    if args.sampling_parameters is not None:
        selected_sampling = [item.strip() for item in str(args.sampling_parameters).split(",") if item.strip()]
        base_config["nightly"]["sampling_parameters"] = selected_sampling
    selected_sampling = selected_sampling_parameters_for_config(base_config)
    if evolution_mode == "model_params" and not selected_sampling:
        raise RuntimeError("Select at least one sampling parameter for model-parameter evolution.")
    regression_suite = _load_json(ROOT / base_config["regression_suite"]["path"])

    now = datetime.now()
    timestamp = now.isoformat(timespec="seconds")
    suite_id = now.strftime("nightly_%Y%m%d_%H%M%S")

    runs_dir = ROOT / base_config["paths"]["runs_dir"]
    reports_dir = ROOT / base_config["paths"]["reports_dir"]
    baseline_path = ROOT / base_config["paths"].get("baseline_path", "configs/baselines/best_stable_config.json")

    ensure_report_files(reports_dir)
    seed_static_histories(reports_dir, timestamp)

    candidate_runs_per_config = int(
        base_config["nightly"].get("candidate_runs_per_config", base_config["nightly"].get("candidate_runs", 4))
    )
    include_base_config = bool(base_config["nightly"].get("include_base_config", True))
    min_regression_pass_rate = float(base_config["regression_suite"].get("min_pass_rate", 0.0))

    heat_map_plan: dict[str, Any] | None = None
    heat_map_candidates: list[dict[str, Any]] = []
    if evolution_mode == "heat_map":
        heat_map_plan = resolve_heat_map_plan(base_config)
        heat_map_candidates = build_heat_map_candidates(base_config)
        planned_rounds = len(heat_map_candidates) + 1
    else:
        configured_rounds = int(base_config["nightly"].get("candidate_pool_size", 1))
        planned_rounds = max(configured_rounds, 1)

    per_round_evals = candidate_runs_per_config + len(regression_suite["cases"])
    total_evals = planned_rounds * per_round_evals

    writer = LiveWriter(runs_dir)
    writer.reset_stream()
    started = time.perf_counter()
    writer.write_status(
        {
            "run_id": suite_id,
            "status": "nightly_preparing",
            "task_id": "nightly_evolution_loop",
            "config_id": base_config["config_id"],
            "runner": base_config["runner"],
            "current_tool": None,
            "last_error": None,
            "step_count": 0,
            "max_steps": total_evals,
            "elapsed_sec": 0,
            "updated_at": timestamp,
            "fitness_mode": base_config["fitness_mode"],
            "run_kind": "nightly",
            "suite_id": suite_id,
            "progress_current": 0,
            "progress_target": total_evals,
            "progress_text": f"0/{total_evals}",
            "evolution_mode": evolution_mode,
            "sampling_parameters": selected_sampling,
        }
    )
    start_message = (
        f"Nightly {_sequential_strategy_for_mode(evolution_mode)} started with {planned_rounds} rounds, "
        f"{candidate_runs_per_config} candidate runs per round, and {len(regression_suite['cases'])} regression cases. "
        f"Sampling params={','.join(selected_sampling) if selected_sampling else 'n/a'}"
    )
    if evolution_mode == "heat_map" and heat_map_plan is not None:
        start_message = (
            f"Nightly heat_map_scan started with baseline + {len(heat_map_candidates)} scan cells. "
            f"Axes={heat_map_plan['y_axis']} x {heat_map_plan['x_axis']}."
        )
    writer.append_event(
        {
            "type": "system",
            "name": "nightly",
            "text": start_message,
        }
    )

    evaluated_candidates: list[dict[str, Any]] = []
    improvement_history: list[dict[str, Any]] = []
    current_base_config = copy.deepcopy(base_config)
    current_base_config["mutation_profile"] = "baseline"
    current_base_config["mutation_notes"] = "Current accepted baseline before nightly tuning."
    current_base_config["mutation_strategy"] = "reference"
    current_base_config["mutation_target"] = None
    current_base_config["mutation_before"] = None
    current_base_config["mutation_after"] = None
    current_base_config["parameter_snapshot"] = parameter_snapshot(current_base_config)
    current_base_config.setdefault("nightly", {})
    current_base_config["nightly"]["evolution_mode"] = evolution_mode

    reference_result: dict[str, Any] | None = None
    mutation_cursor = 0
    completed_evals = 0

    for round_index in range(1, planned_rounds + 1):
        round_timestamp = datetime.now().isoformat(timespec="seconds")
        if round_index == 1 and (include_base_config or evolution_mode == "heat_map"):
            candidate_config = copy.deepcopy(current_base_config)
            config_path = base_config_path.resolve()
            event_label = "baseline_reference"
        elif evolution_mode == "heat_map":
            candidate_config = heat_map_candidates[round_index - 2]
            config_path = persist_candidate_config(ROOT, suite_id, round_index, candidate_config)
            event_label = "heat_map_cell"
        else:
            mutation_candidates = build_mutation_candidates(current_base_config, evolution_mode)
            if not mutation_candidates:
                writer.append_event(
                    {
                        "type": "system",
                        "name": "mutator",
                        "text": (
                            f"No more {evolution_mode} mutations are available from the current baseline. "
                            "Stopping early."
                        ),
                    }
                )
                break
            candidate_config = mutation_candidates[mutation_cursor % len(mutation_candidates)]
            mutation_cursor += 1
            config_path = persist_candidate_config(ROOT, suite_id, round_index, candidate_config)
            event_label = "architecture_program_mutation" if evolution_mode == "architecture_program" else "single_parameter_mutation"

        append_history_entry(
            reports_dir / "config_history.json",
            {
                "ts": round_timestamp,
                "suite_id": suite_id,
                "round_index": round_index,
                "config_id": candidate_config["config_id"],
                "event": "candidate_generated",
                "event_label": event_label,
                "mutation_profile": candidate_config.get("mutation_profile", "baseline"),
                "mutation_notes": candidate_config.get("mutation_notes", ""),
                "mutation_target": candidate_config.get("mutation_target"),
                "mutation_before": candidate_config.get("mutation_before"),
                "mutation_after": candidate_config.get("mutation_after"),
                "reference_config_id": current_base_config["config_id"],
                "config_path": str(config_path),
                "parameter_snapshot": candidate_config.get("parameter_snapshot"),
                "evolution_mode": evolution_mode,
            },
        )

        if evolution_mode == "heat_map" and isinstance(candidate_config.get("heat_map_coordinates"), dict):
            coordinates = candidate_config["heat_map_coordinates"]
            round_text = (
                f"Round {round_index}/{planned_rounds}: scanning heat-map cell "
                f"{coordinates.get('y_label')}={coordinates.get('y_value')}, "
                f"{coordinates.get('x_label')}={coordinates.get('x_value')}."
            )
        elif candidate_config.get("mutation_target"):
            round_text = (
                f"Round {round_index}/{planned_rounds}: evaluating {candidate_config['config_id']} by changing "
                f"{candidate_config['mutation_target']} from {candidate_config.get('mutation_before')} "
                f"to {candidate_config.get('mutation_after')}."
            )
        else:
            round_text = f"Round {round_index}/{planned_rounds}: measuring the current baseline {candidate_config['config_id']}."
        writer.append_event({"type": "system", "name": "mutator", "text": round_text})

        candidate_result = _evaluate_candidate_config(
            config_path=Path(config_path),
            config=candidate_config,
            suite_id=suite_id,
            seed_offset=args.seed_start + ((round_index - 1) * 100),
            candidate_runs_per_config=candidate_runs_per_config,
            regression_suite=regression_suite,
            progress_offset=completed_evals,
            progress_target=total_evals,
        )
        completed_evals += per_round_evals
        candidate_result["round_index"] = round_index
        candidate_result["reference_config_id_before"] = current_base_config["config_id"]
        evaluated_candidates.append(candidate_result)

        round_entry = {
            "ts": round_timestamp,
            "suite_id": suite_id,
            "round_index": round_index,
            "config_id": candidate_result["config"]["config_id"],
            "reference_config_id_before": current_base_config["config_id"],
            "parameter_name": candidate_result.get("parameter_name"),
            "parameter_before": candidate_result.get("parameter_before"),
            "parameter_after": candidate_result.get("parameter_after"),
            "heat_map_coordinates": candidate_result["config"].get("heat_map_coordinates"),
            "suite_score_c": candidate_result["candidate_summary"]["suite_score"],
            "suite_score_b": candidate_result["regression_summary"]["suite_score"],
            "fitness": candidate_result["fitness"],
            "regression_pass_rate": candidate_result["regression_summary"]["pass_rate"],
            "honesty_score": candidate_result["candidate_summary"]["honesty_score"],
            "parameter_snapshot": candidate_result.get("parameter_snapshot", {}),
            "config_path": candidate_result["config_path"],
        }

        if reference_result is None:
            reference_result = candidate_result
            current_base_config = copy.deepcopy(candidate_result["config"])
            round_entry["event"] = "reference_measured"
            round_entry["improved"] = True
            round_entry["reference_suite_score_c_before"] = None
            round_entry["reference_fitness_before"] = None
            round_entry["decision_reasons"] = ["baseline_reference_established"]
            _append_mutation_history(reports_dir, round_entry, evolution_mode)
            append_history_entry(
                reports_dir / "config_history.json",
                {
                    **round_entry,
                    "event": "reference_measured",
                    "mutation_profile": candidate_result["mutation_profile"],
                    "mutation_notes": candidate_result["mutation_notes"],
                    "evolution_mode": evolution_mode,
                },
            )
            writer.append_event(
                {
                    "type": "system",
                    "name": "nightly",
                    "text": (
                        f"Reference baseline established at score={candidate_result['candidate_summary']['suite_score']:.4f} "
                        f"fitness={candidate_result['fitness']:.4f}."
                    ),
                }
            )
            continue

        decision = _improvement_decision(reference_result, candidate_result, min_regression_pass_rate)
        round_entry["reference_suite_score_c_before"] = reference_result["candidate_summary"]["suite_score"]
        round_entry["reference_fitness_before"] = reference_result["fitness"]
        round_entry["decision_reasons"] = decision["reasons"]
        round_entry["improved"] = decision["improved"]
        _append_mutation_history(reports_dir, round_entry, evolution_mode)

        if decision["improved"]:
            improvement_history.append(round_entry)
            if evolution_mode == "heat_map":
                append_history_entry(
                    reports_dir / "config_history.json",
                    {
                        **round_entry,
                        "event": "candidate_above_baseline",
                        "mutation_profile": candidate_result["mutation_profile"],
                        "mutation_notes": candidate_result["mutation_notes"],
                        "evolution_mode": evolution_mode,
                    },
                )
                writer.append_event(
                    {
                        "type": "system",
                        "name": "nightly",
                        "text": (
                            f"Round {round_index} beat the fixed baseline: "
                            f"score {round_entry['reference_suite_score_c_before']:.4f} -> {round_entry['suite_score_c']:.4f}, "
                            f"fitness {round_entry['reference_fitness_before']:.4f} -> {round_entry['fitness']:.4f}."
                        ),
                    }
                )
            else:
                reference_result = candidate_result
                current_base_config = copy.deepcopy(candidate_result["config"])
                append_history_entry(
                    reports_dir / "config_history.json",
                    {
                        **round_entry,
                        "event": "candidate_improved",
                        "mutation_profile": candidate_result["mutation_profile"],
                        "mutation_notes": candidate_result["mutation_notes"],
                        "evolution_mode": evolution_mode,
                    },
                )
                writer.append_event(
                    {
                        "type": "system",
                        "name": "nightly",
                        "text": (
                            f"Round {round_index} improved the reference baseline: "
                            f"score {round_entry['reference_suite_score_c_before']:.4f} -> {round_entry['suite_score_c']:.4f}, "
                            f"fitness {round_entry['reference_fitness_before']:.4f} -> {round_entry['fitness']:.4f}. "
                            f"Using this config as the new baseline for the next mutation."
                        ),
                    }
                )
        else:
            append_history_entry(
                reports_dir / "config_history.json",
                {
                    **round_entry,
                    "event": "candidate_below_baseline" if evolution_mode == "heat_map" else "candidate_rejected",
                    "mutation_profile": candidate_result["mutation_profile"],
                    "mutation_notes": candidate_result["mutation_notes"],
                    "evolution_mode": evolution_mode,
                },
            )
            writer.append_event(
                {
                    "type": "system",
                    "name": "nightly",
                    "text": (
                        f"Round {round_index} did not improve the reference baseline. "
                        f"Keeping {reference_result['config']['config_id']} as the baseline."
                    ),
                }
            )

    if reference_result is None:
        raise RuntimeError("Nightly tuning produced no evaluated rounds.")

    ranked_candidates = sorted(
        evaluated_candidates,
        key=lambda item: (
            item["candidate_summary"]["suite_score"],
            item["fitness"],
            item["regression_summary"]["pass_rate"],
            item["candidate_summary"]["honesty_score"],
        ),
        reverse=True,
    )
    selected_candidate = ranked_candidates[0] if evolution_mode == "heat_map" else reference_result

    baseline_before = load_baseline(baseline_path)
    final_timestamp = datetime.now().isoformat(timespec="seconds")
    decision = assess_candidate(
        current=baseline_before,
        candidate=selected_candidate["candidate_payload"],
        gate=selected_candidate["config"].get("baseline_gate", base_config["baseline_gate"]),
    )
    promoted_baseline = None
    if decision["promoted"]:
        promoted_baseline = write_baseline(baseline_path, selected_candidate["candidate_payload"])

    gate_reason = ", ".join(decision["reasons"]) if decision["reasons"] else "nightly_candidate_accepted"
    rollback_reason = "nightly promotion"
    if not decision["promoted"]:
        rollback_reason = f"nightly gate rejected: {gate_reason}"
        if decision["rollback_required"]:
            rollback_reason = f"nightly rollback gate: {gate_reason}"

    regression_status = (
        f"{len([item for item in selected_candidate['regression_runs'] if item['status'] == 'passed'])}/"
        f"{len(selected_candidate['regression_runs'])} passed"
    )
    rollback_event = {
        "ts": final_timestamp,
        "reason": rollback_reason,
        "before_config_id": decision["before_config_id"],
        "after_config_id": decision["after_config_id"],
        "regression_status": regression_status,
        "baseline_restored": not decision["promoted"],
        "success": True,
        "run_id": suite_id,
        "run_kind": "nightly",
        "suite_id": suite_id,
        "gate_reasons": decision["reasons"],
    }
    append_history_entry(reports_dir / "rollback_events.json", rollback_event)

    baseline_event = {
        "ts": final_timestamp,
        "run_id": suite_id,
        "config_id": selected_candidate["config"]["config_id"],
        "fitness": selected_candidate["fitness"],
        "suite_score_c": selected_candidate["candidate_summary"]["suite_score"],
        "suite_score_b": selected_candidate["regression_summary"]["suite_score"],
        "status": "promoted" if decision["promoted"] else "rejected",
        "run_kind": "nightly",
        "suite_id": suite_id,
        "gate_reasons": decision["reasons"],
        "mutation_profile": selected_candidate["mutation_profile"],
    }
    append_history_entry(reports_dir / "baseline_history.json", baseline_event)

    heat_map_summary = None
    if evolution_mode == "heat_map":
        heat_map_summary = _build_heat_map_summary(
            base_config=base_config,
            baseline_result=reference_result,
            evaluated_candidates=evaluated_candidates,
        )
        append_history_entry(
            reports_dir / "heat_map_history.json",
            {
                "ts": final_timestamp,
                "suite_id": suite_id,
                "status": "promoted" if decision["promoted"] else "rejected",
                "selected_config_id": selected_candidate["config"]["config_id"],
                "best_delta_suite_score": heat_map_summary["best_cell"]["delta_suite_score"],
                "best_delta_fitness": heat_map_summary["best_cell"]["delta_fitness"],
                **heat_map_summary,
            },
        )

    nightly_entry = {
        "ts": final_timestamp,
        "suite_id": suite_id,
        "config_id": selected_candidate["config"]["config_id"],
        "fitness": selected_candidate["fitness"],
        "suite_score_c": selected_candidate["candidate_summary"]["suite_score"],
        "suite_score_b": selected_candidate["regression_summary"]["suite_score"],
        "suite_score_a": selected_candidate["suite_score_a"],
        "stability_score": selected_candidate["candidate_summary"]["stability_score"],
        "rollback_safety_score": selected_candidate["candidate_summary"]["rollback_safety_score"],
        "regression_pass_rate": selected_candidate["regression_summary"]["pass_rate"],
        "honesty_score": selected_candidate["candidate_summary"]["honesty_score"],
        "status": "promoted" if decision["promoted"] else "rejected",
        "gate_reasons": decision["reasons"],
        "mutation_profile": selected_candidate["mutation_profile"],
        "planned_rounds": planned_rounds,
        "completed_rounds": len(evaluated_candidates),
        "sequential_strategy": _sequential_strategy_for_mode(evolution_mode),
        "evolution_mode": evolution_mode,
        "sampling_parameters": selected_sampling,
        "heat_map": heat_map_summary,
        "improvement_history": improvement_history,
        "ranking": [
            {
                **_round_summary_entry(item),
                "round_index": item.get("round_index"),
            }
            for item in ranked_candidates[:5]
        ],
    }
    append_history_entry(reports_dir / "nightly_history.json", nightly_entry)

    append_history_entry(
        reports_dir / "config_history.json",
        {
            "ts": final_timestamp,
            "suite_id": suite_id,
            "round_index": len(evaluated_candidates),
            "config_id": selected_candidate["config"]["config_id"],
            "event": "candidate_selected" if decision["promoted"] else "candidate_rejected",
            "mutation_profile": selected_candidate["mutation_profile"],
            "mutation_notes": selected_candidate["mutation_notes"],
            "mutation_target": selected_candidate.get("parameter_name"),
            "mutation_before": selected_candidate.get("parameter_before"),
            "mutation_after": selected_candidate.get("parameter_after"),
            "fitness": selected_candidate["fitness"],
            "suite_score_c": selected_candidate["candidate_summary"]["suite_score"],
            "suite_score_b": selected_candidate["regression_summary"]["suite_score"],
            "config_path": selected_candidate["config_path"],
            "archive_path": promoted_baseline["config_archive_path"] if promoted_baseline else None,
            "gate_reasons": decision["reasons"],
            "parameter_snapshot": selected_candidate.get("parameter_snapshot", {}),
            "evolution_mode": evolution_mode,
        },
    )

    append_history_entry(
        reports_dir / "score_history.json",
        {
            "ts": final_timestamp,
            "run_id": suite_id,
            "label": suite_id[-6:],
            "config_id": selected_candidate["config"]["config_id"],
            "overall": selected_candidate["candidate_summary"]["suite_score"],
            "fitness": selected_candidate["fitness"],
            "fitness_mode": base_config["fitness_mode"],
            "suite_score_c": selected_candidate["candidate_summary"]["suite_score"],
            "suite_score_b": selected_candidate["regression_summary"]["suite_score"],
            "passed": decision["promoted"],
            "run_kind": "nightly_summary",
            "suite_id": suite_id,
            "honesty_score": selected_candidate["candidate_summary"]["honesty_score"],
        },
    )

    summary = {
        "suite_id": suite_id,
        "created_at": timestamp,
        "base_config_id": base_config["config_id"],
        "selected_config_id": selected_candidate["config"]["config_id"],
        "fitness_mode": base_config["fitness_mode"],
        "status": "promoted" if decision["promoted"] else "rejected",
        "fitness": selected_candidate["fitness"],
        "suite_score_c": selected_candidate["candidate_summary"]["suite_score"],
        "suite_score_b": selected_candidate["regression_summary"]["suite_score"],
        "suite_score_a": selected_candidate["suite_score_a"],
        "stability_score": selected_candidate["candidate_summary"]["stability_score"],
        "rollback_safety_score": selected_candidate["candidate_summary"]["rollback_safety_score"],
        "regression_pass_rate": selected_candidate["regression_summary"]["pass_rate"],
        "honesty_score": selected_candidate["candidate_summary"]["honesty_score"],
        "baseline_decision": decision,
        "planned_rounds": planned_rounds,
        "completed_rounds": len(evaluated_candidates),
        "sequential_strategy": _sequential_strategy_for_mode(evolution_mode),
        "evolution_mode": evolution_mode,
        "sampling_parameters": selected_sampling,
        "heat_map": heat_map_summary,
        "selected_round_index": selected_candidate.get("round_index"),
        "improvement_history": improvement_history,
        "candidate_rankings": [
            {
                **_round_summary_entry(item),
                "round_index": item.get("round_index"),
            }
            for item in ranked_candidates
        ],
        "evaluated_candidates": evaluated_candidates,
    }
    artifact_path = runs_dir / "artifacts" / f"{suite_id}.json"
    write_json(artifact_path, summary)

    final_progress_target = completed_evals or total_evals or 1
    writer.write_status(
        {
            "run_id": suite_id,
            "status": summary["status"],
            "task_id": "nightly_evolution_loop",
            "config_id": selected_candidate["config"]["config_id"],
            "runner": selected_candidate["config"]["runner"],
            "current_tool": None,
            "last_error": None,
            "step_count": completed_evals,
            "max_steps": final_progress_target,
            "elapsed_sec": round(time.perf_counter() - started, 4),
            "updated_at": final_timestamp,
            "score": selected_candidate["candidate_summary"]["suite_score"],
            "fitness": selected_candidate["fitness"],
            "fitness_mode": base_config["fitness_mode"],
            "rollback_time": rollback_event["ts"],
            "rollback_reason": rollback_event["reason"],
            "rollback_before_config_id": rollback_event["before_config_id"],
            "rollback_after_config_id": rollback_event["after_config_id"],
            "regression_status": rollback_event["regression_status"],
            "baseline_restored": rollback_event["baseline_restored"],
            "run_kind": "nightly",
            "suite_id": suite_id,
            "progress_current": final_progress_target,
            "progress_target": final_progress_target,
            "progress_text": f"{final_progress_target}/{final_progress_target}",
            "honesty_score": selected_candidate["candidate_summary"]["honesty_score"],
            "rollback_safety_score": selected_candidate["candidate_summary"]["rollback_safety_score"],
            "evolution_mode": evolution_mode,
            "sampling_parameters": selected_sampling,
        }
    )
    writer.append_event(
        {
            "type": "system",
            "name": "nightly",
            "text": (
                f"Nightly ({evolution_mode}) selected {selected_candidate['config']['config_id']} with score="
                f"{selected_candidate['candidate_summary']['suite_score']:.4f}, fitness="
                f"{selected_candidate['fitness']:.4f}, and status={summary['status']}."
            ),
        }
    )

    print(json.dumps(nightly_entry, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
