from __future__ import annotations

import csv
import json
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from evolution.relayer_plan import (
    RelayerConfig,
    RelayerPlan,
    apply_relayer_config,
    build_relayer_plan,
    build_relayer_scan_candidates,
    resolve_relayer_num_layers,
    resolve_relayer_scan_settings,
)
from runners.relayer_runner import RecordingLayerBackend, RelayerRunner
from storage.heat_map_artifacts import build_heat_map_artifacts
from storage.history_writer import append_history_entry, write_json


DEFAULT_MOCK_PROBE_NAME = "mock_relayer_probe_v1"

ProgressCallback = Callable[[dict[str, Any]], None]
EventCallback = Callable[[dict[str, Any]], None]


def baseline_execution_plan(num_layers: int) -> RelayerPlan:
    return RelayerPlan(execution_order=list(range(num_layers)))


def _mock_probe_config(config: dict[str, Any], num_layers: int) -> dict[str, Any]:
    relayer_cfg = config.get("relayer", {})
    if not isinstance(relayer_cfg, dict):
        relayer_cfg = {}
    mock_probe = relayer_cfg.get("mock_probe", {})
    if not isinstance(mock_probe, dict):
        mock_probe = {}

    preferred_start = int(mock_probe.get("preferred_start_layer", max(0, (num_layers // 2) - 2)))
    preferred_end = int(mock_probe.get("preferred_end_layer", min(num_layers - 1, preferred_start + 3)))
    preferred_repeat_count = int(mock_probe.get("preferred_repeat_count", 1))
    return {
        "probe_name": str(mock_probe.get("probe_name", DEFAULT_MOCK_PROBE_NAME)),
        "preferred_start_layer": preferred_start,
        "preferred_end_layer": preferred_end,
        "preferred_repeat_count": preferred_repeat_count,
    }


def evaluate_mock_relayer_candidate(
    *,
    config: dict[str, Any],
    relayer_config: RelayerConfig | None,
) -> dict[str, Any]:
    num_layers = resolve_relayer_num_layers(config)
    if num_layers is None:
        raise ValueError("relayer.num_layers is required for mock relayer scan evaluation.")

    if relayer_config is None:
        plan = baseline_execution_plan(num_layers)
    else:
        plan = build_relayer_plan(num_layers, relayer_config)

    backend = RecordingLayerBackend()
    runner = RelayerRunner(backend)
    run_result = runner.execute(plan=plan, initial_state=[])
    probe_cfg = _mock_probe_config(config, num_layers)

    execution_ok = 1.0 if run_result.layer_trace == plan.execution_order else 0.0
    if relayer_config is None:
        position_score = 0.5
        block_len_score = 0.5
        repeat_score = 1.0
        start_layer = None
        end_layer = None
        repeat_count = 0
        block_len = 0
    else:
        start_layer = relayer_config.start_layer
        end_layer = relayer_config.end_layer
        repeat_count = relayer_config.repeat_count
        block_len = relayer_config.block_len
        start_distance = abs(start_layer - probe_cfg["preferred_start_layer"])
        end_distance = abs(end_layer - probe_cfg["preferred_end_layer"])
        max_span = max(1, num_layers - 1)
        position_score = max(0.0, 1.0 - ((start_distance + end_distance) / (2 * max_span)))
        preferred_block_len = probe_cfg["preferred_end_layer"] - probe_cfg["preferred_start_layer"] + 1
        block_len_score = max(0.0, 1.0 - (abs(block_len - preferred_block_len) / max(1, num_layers)))
        repeat_score = max(
            0.0,
            1.0 - (abs(repeat_count - probe_cfg["preferred_repeat_count"]) / max(1, probe_cfg["preferred_repeat_count"])),
        )

    raw_score = round(
        (0.55 * execution_ok) + (0.30 * position_score) + (0.10 * block_len_score) + (0.05 * repeat_score),
        6,
    )

    return {
        "config_id": config["config_id"],
        "probe_name": probe_cfg["probe_name"],
        "start_layer": start_layer,
        "end_layer": end_layer,
        "repeat_count": repeat_count,
        "block_len": block_len,
        "raw_score": raw_score,
        "answered": 1,
        "failed": 0 if execution_ok >= 1.0 else 1,
        "executed_layers": run_result.executed_layers,
        "execution_order": plan.execution_order,
        "layer_trace": run_result.layer_trace,
        "execution_ok": execution_ok >= 1.0,
        "metrics": {
            "execution_score": execution_ok,
            "position_score": round(position_score, 6),
            "block_len_score": round(block_len_score, 6),
            "repeat_score": round(repeat_score, 6),
        },
        "latency_ms": run_result.executed_layers,
    }


def build_relayer_scan_summary(
    *,
    base_config: dict[str, Any],
    baseline_result: dict[str, Any],
    candidate_results: list[dict[str, Any]],
    top_k: int | None = None,
) -> dict[str, Any]:
    settings = resolve_relayer_scan_settings(base_config)
    top_k_value = max(1, min(int(top_k or 5), len(candidate_results) or 1))

    baseline_cell = {
        "config_id": baseline_result["config_id"],
        "x_axis": "relayer.start_layer",
        "x_label": "Start Layer",
        "x_value": None,
        "y_axis": "relayer.end_layer",
        "y_label": "End Layer",
        "y_value": None,
        "suite_score_c": baseline_result["raw_score"],
        "fitness": baseline_result["raw_score"],
        "regression_pass_rate": 1.0,
        "honesty_score": 1.0,
        "delta_suite_score": 0.0,
        "delta_fitness": 0.0,
        "improved_vs_baseline": False,
    }

    cell_lookup: dict[tuple[int, int], dict[str, Any]] = {}
    ranked_candidates: list[dict[str, Any]] = []
    baseline_score = baseline_result["raw_score"]
    for item in candidate_results:
        cell = {
            "config_id": item["config_id"],
            "x_axis": "relayer.start_layer",
            "x_label": "Start Layer",
            "x_value": item["start_layer"],
            "y_axis": "relayer.end_layer",
            "y_label": "End Layer",
            "y_value": item["end_layer"],
            "suite_score_c": item["raw_score"],
            "fitness": item["raw_score"],
            "regression_pass_rate": 1.0,
            "honesty_score": 1.0,
            "delta_suite_score": round(item["raw_score"] - baseline_score, 6),
            "delta_fitness": round(item["raw_score"] - baseline_score, 6),
            "improved_vs_baseline": item["raw_score"] > baseline_score + 1e-9,
            "repeat_count": item["repeat_count"],
            "block_len": item["block_len"],
            "executed_layers": item["executed_layers"],
            "execution_ok": item["execution_ok"],
            "latency_ms": item["latency_ms"],
        }
        ranked_candidates.append(cell)
        cell_lookup[(int(item["start_layer"]), int(item["end_layer"]))] = cell

    matrix_rows: list[dict[str, Any]] = []
    x_values = list(range(settings.start_layer_min, settings.end_layer_max + 1))
    y_values = list(range(settings.start_layer_min, settings.end_layer_max + 1))
    for y_value in y_values:
        row_cells: list[dict[str, Any]] = []
        for x_value in x_values:
            if x_value > y_value:
                row_cells.append({"x_value": x_value, "y_value": y_value, "status": "not_run"})
                continue
            cell = cell_lookup.get((x_value, y_value))
            if cell is None:
                row_cells.append({"x_value": x_value, "y_value": y_value, "status": "not_run"})
            else:
                row_cells.append(cell)
        matrix_rows.append({"y_value": y_value, "cells": row_cells})

    ranked_candidates.sort(
        key=lambda item: (
            item["suite_score_c"],
            item["delta_suite_score"],
            item["block_len"],
        ),
        reverse=True,
    )
    best_cell = ranked_candidates[0] if ranked_candidates else baseline_cell

    return {
        "x_axis": "relayer.start_layer",
        "x_label": "Start Layer",
        "x_values": x_values,
        "y_axis": "relayer.end_layer",
        "y_label": "End Layer",
        "y_values": y_values,
        "cell_count": len(candidate_results),
        "baseline": baseline_cell,
        "best_cell": best_cell,
        "improved_cell_count": sum(1 for item in ranked_candidates if item["improved_vs_baseline"]),
        "top_candidates": ranked_candidates[:top_k_value],
        "matrix": matrix_rows,
    }


def write_relayer_aggregated_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "run_id",
        "config_id",
        "start_layer",
        "end_layer",
        "repeat_count",
        "probe_name",
        "raw_score",
        "baseline_score",
        "delta",
        "answered",
        "failed",
        "executed_layers",
        "latency_ms",
        "execution_ok",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def run_relayer_scan(
    *,
    base_config: dict[str, Any],
    output_dir: Path,
    reports_dir: Path,
    max_candidates: int | None = None,
    run_id: str | None = None,
    progress_callback: ProgressCallback | None = None,
    event_callback: EventCallback | None = None,
) -> dict[str, Any]:
    timestamp = datetime.now().isoformat(timespec="seconds")
    run_id = run_id or datetime.now().strftime("relayer_scan_%Y%m%d_%H%M%S")
    started = time.perf_counter()

    candidates = build_relayer_scan_candidates(base_config)
    if max_candidates is not None:
        candidates = candidates[: max(1, int(max_candidates))]
    candidate_target = len(candidates)

    if event_callback is not None:
        event_callback(
            {
                "type": "system",
                "name": "relayer_scan",
                "text": (
                    f"Starting relayer scan {run_id} with synthetic mock_layer_stack backend "
                    f"across {candidate_target} candidates."
                ),
            }
        )

    if progress_callback is not None:
        progress_callback(
            {
                "status": "starting_relayer_scan",
                "progress_current": 0,
                "progress_target": candidate_target,
                "progress_text": f"0/{candidate_target}" if candidate_target else "0/0",
                "candidate_count": candidate_target,
                "elapsed_sec": round(time.perf_counter() - started, 3),
            }
        )

    baseline_result = evaluate_mock_relayer_candidate(config=base_config, relayer_config=None)
    if event_callback is not None:
        event_callback(
            {
                "type": "system",
                "name": "relayer_scan",
                "text": f"Baseline mock relayer score={baseline_result['raw_score']}.",
            }
        )
    if progress_callback is not None:
        progress_callback(
            {
                "status": "baseline_ready",
                "progress_current": 0,
                "progress_target": candidate_target,
                "progress_text": f"0/{candidate_target}" if candidate_target else "0/0",
                "baseline_score": baseline_result["raw_score"],
                "elapsed_sec": round(time.perf_counter() - started, 3),
            }
        )

    candidate_results = []
    csv_rows = []
    best_result: dict[str, Any] | None = None
    for candidate in candidates:
        relayer_cfg = candidate.get("relayer", {})
        if event_callback is not None:
            event_callback(
                {
                    "type": "system",
                    "name": "relayer_scan",
                    "text": f"Evaluating candidate {candidate.get('config_id')}.",
                }
            )
        result = evaluate_mock_relayer_candidate(
            config=candidate,
            relayer_config=RelayerConfig(
                start_layer=int(relayer_cfg["start_layer"]),
                end_layer=int(relayer_cfg["end_layer"]),
                repeat_count=int(relayer_cfg.get("repeat_count", 1)),
            ),
        )
        candidate_results.append(result)
        csv_rows.append(
            {
                "run_id": run_id,
                "config_id": result["config_id"],
                "start_layer": result["start_layer"],
                "end_layer": result["end_layer"],
                "repeat_count": result["repeat_count"],
                "probe_name": result["probe_name"],
                "raw_score": result["raw_score"],
                "baseline_score": baseline_result["raw_score"],
                "delta": round(result["raw_score"] - baseline_result["raw_score"], 6),
                "answered": result["answered"],
                "failed": result["failed"],
                "executed_layers": result["executed_layers"],
                "latency_ms": result["latency_ms"],
                "execution_ok": result["execution_ok"],
            }
        )
        if best_result is None or result["raw_score"] > best_result["raw_score"]:
            best_result = result
        if progress_callback is not None:
            current = len(candidate_results)
            progress_callback(
                {
                    "status": "running_relayer_scan",
                    "progress_current": current,
                    "progress_target": candidate_target,
                    "progress_text": f"{current}/{candidate_target}" if candidate_target else "0/0",
                    "current_candidate": result["config_id"],
                    "best_config_id": best_result["config_id"] if best_result else None,
                    "baseline_score": baseline_result["raw_score"],
                    "elapsed_sec": round(time.perf_counter() - started, 3),
                }
            )

    top_k = base_config.get("nightly", {}).get("heat_map", {}).get("top_k", 5)
    summary = build_relayer_scan_summary(
        base_config=base_config,
        baseline_result=baseline_result,
        candidate_results=candidate_results,
        top_k=top_k,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    aggregated_csv_path = output_dir / "aggregated.csv"
    write_relayer_aggregated_csv(aggregated_csv_path, csv_rows)
    write_json(output_dir / "baseline.json", baseline_result)
    write_json(output_dir / "candidate_results.json", {"results": candidate_results})

    artifacts = build_heat_map_artifacts(
        output_dir=output_dir,
        suite_id=run_id,
        heat_map_summary=summary,
        created_at=timestamp,
        base_config_id=str(base_config.get("config_id")),
        selected_config_id=str(summary.get("best_cell", {}).get("config_id", base_config.get("config_id"))),
        status="completed",
    )
    manifest = {
        "run_id": run_id,
        "created_at": timestamp,
        "config_id": base_config.get("config_id"),
        "probe_name": baseline_result["probe_name"],
        "baseline_score": baseline_result["raw_score"],
        "candidate_count": len(candidate_results),
        "output_dir": str(output_dir.resolve()),
        "aggregated_csv_path": str(aggregated_csv_path.resolve()),
        "artifacts": artifacts,
        "notes": "Synthetic relayer infrastructure scan using mock_layer_stack backend. Not a real model benchmark.",
    }
    write_json(output_dir / "manifest.json", manifest)
    write_json(output_dir / "summary.json", summary)
    write_json(output_dir / f"{run_id}.json", {"manifest": manifest, "summary": summary, "results": candidate_results})

    append_history_entry(
        reports_dir / "relayer_scan_history.json",
        {
            "ts": timestamp,
            "run_id": run_id,
            "config_id": base_config.get("config_id"),
            "probe_name": baseline_result["probe_name"],
            "candidate_count": len(candidate_results),
            "baseline_score": baseline_result["raw_score"],
            "best_config_id": summary.get("best_cell", {}).get("config_id"),
            "best_delta_suite_score": summary.get("best_cell", {}).get("delta_suite_score"),
            "output_dir": str(output_dir.resolve()),
            "notes": manifest["notes"],
        },
    )
    if progress_callback is not None:
        progress_callback(
            {
                "status": "completed",
                "progress_current": candidate_target,
                "progress_target": candidate_target,
                "progress_text": f"{candidate_target}/{candidate_target}" if candidate_target else "0/0",
                "best_config_id": summary.get("best_cell", {}).get("config_id"),
                "baseline_score": baseline_result["raw_score"],
                "elapsed_sec": round(time.perf_counter() - started, 3),
            }
        )
    if event_callback is not None:
        event_callback(
            {
                "type": "system",
                "name": "relayer_scan",
                "text": (
                    f"Completed relayer scan {run_id}. "
                    f"Best candidate={summary.get('best_cell', {}).get('config_id') or '-'}."
                ),
            }
        )

    return {
        "manifest": manifest,
        "summary": summary,
        "baseline": baseline_result,
        "candidate_results": candidate_results,
    }
