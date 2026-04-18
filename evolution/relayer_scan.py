from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import json
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from evolution.heat_map_verifier import (
    estimate_relayer_scan_total_evals,
    resolve_relayer_scan_verification_settings,
    run_relayer_scan_verification,
)
from evolution.relayer_plan import (
    RelayerConfig,
    RelayerPlan,
    build_relayer_plan,
    build_relayer_scan_candidates,
    resolve_relayer_num_layers,
    resolve_relayer_scan_settings,
)
from rollback.baseline_manager import assess_candidate, load_baseline, write_baseline
from runners.relayer_runner import RecordingLayerBackend, RelayerRunner
from storage.heat_map_artifacts import build_heat_map_artifacts
from storage.history_writer import append_history_entry, write_json


DEFAULT_MOCK_PROBE_NAME = "mock_relayer_probe_v1"

ProgressCallback = Callable[[dict[str, Any]], None]
EventCallback = Callable[[dict[str, Any]], None]


def _format_metric(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _append_bilingual(lines: list[str], english: str, chinese: str, *, bullet: bool = False) -> None:
    if bullet:
        lines.append(f"- {english}")
        lines.append(f"  {chinese}")
        return
    lines.append(english)
    lines.append(chinese)


def _write_relayer_report_readme(
    *,
    path: Path,
    run_id: str,
    summary: dict[str, Any],
    manifest: dict[str, Any],
) -> None:
    best_cell = summary.get("best_cell", {}) if isinstance(summary.get("best_cell"), dict) else {}
    verification = manifest.get("verification", {}) if isinstance(manifest.get("verification"), dict) else {}
    baseline_decision = manifest.get("baseline_decision", {}) if isinstance(manifest.get("baseline_decision"), dict) else {}
    lines = [f"# Relayer Scan Report / Relayer 掃描報告: {run_id}", "", "## TL;DR / 快速摘要", ""]
    _append_bilingual(
        lines,
        "Open `artifacts/heatmap.png` first to see the synthetic ranking over scanned layer windows.",
        "先看 `artifacts/heatmap.png`，它會顯示掃描過的 layer windows 的 synthetic ranking。",
        bullet=True,
    )
    _append_bilingual(
        lines,
        f"Best cell is `{best_cell.get('config_id', '-')}` with delta `{_format_metric(best_cell.get('delta_suite_score'))}`.",
        f"最佳 cell 是 `{best_cell.get('config_id', '-')}`，delta 為 `{_format_metric(best_cell.get('delta_suite_score'))}`。",
        bullet=True,
    )
    _append_bilingual(
        lines,
        f"Verification winner is `{verification.get('winner', {}).get('config_id', '-') if verification else '-'}` and baseline decision is `{baseline_decision.get('status', '-')}`.",
        f"Verification winner 是 `{verification.get('winner', {}).get('config_id', '-') if verification else '-'}`，baseline decision 是 `{baseline_decision.get('status', '-')}`。",
        bullet=True,
    )
    lines.extend(["", "## Quick Start / 先看哪裡", ""])
    _append_bilingual(lines, "`artifacts/heatmap.png`: synthetic ranking heat map.", "`artifacts/heatmap.png`：synthetic ranking 熱圖。", bullet=True)
    _append_bilingual(lines, "`summary.json`: summary with best cell, top candidates, and matrix.", "`summary.json`：包含 best cell、top candidates 與矩陣的摘要。", bullet=True)
    _append_bilingual(lines, "`aggregated.csv`: flat table of all candidate results.", "`aggregated.csv`：所有 candidate 結果的平面表格。", bullet=True)
    _append_bilingual(lines, "`manifest.json`: report index plus verification and baseline-gate outputs.", "`manifest.json`：報告索引，外加 verification 與 baseline gate 結果。", bullet=True)
    _append_bilingual(lines, "`candidate_results.json`: resumable candidate result snapshot.", "`candidate_results.json`：可用於 resume 的 candidate 結果快照。", bullet=True)
    lines.extend(["", "## Scan Summary / 掃描摘要", ""])
    _append_bilingual(lines, f"Best cell: `{best_cell.get('config_id', '-')}`", f"最佳 cell：`{best_cell.get('config_id', '-')}`", bullet=True)
    _append_bilingual(
        lines,
        f"Best layers: `start={best_cell.get('x_value', best_cell.get('start_layer', '-'))}, end={best_cell.get('y_value', best_cell.get('end_layer', '-'))}`",
        f"最佳 layers：`start={best_cell.get('x_value', best_cell.get('start_layer', '-'))}, end={best_cell.get('y_value', best_cell.get('end_layer', '-'))}`",
        bullet=True,
    )
    _append_bilingual(lines, f"Best delta: `{_format_metric(best_cell.get('delta_suite_score'))}`", f"最佳 delta：`{_format_metric(best_cell.get('delta_suite_score'))}`", bullet=True)
    _append_bilingual(lines, f"Candidate count: `{manifest.get('candidate_count', '-')}`", f"Candidate 數量：`{manifest.get('candidate_count', '-')}`", bullet=True)
    _append_bilingual(
        lines,
        f"Verification winner: `{verification.get('winner', {}).get('config_id', '-') if verification else '-'}`",
        f"Verification winner：`{verification.get('winner', {}).get('config_id', '-') if verification else '-'}`",
        bullet=True,
    )
    _append_bilingual(lines, f"Baseline decision: `{baseline_decision.get('status', '-')}`", f"Baseline decision：`{baseline_decision.get('status', '-')}`", bullet=True)
    lines.extend(["", "## Interpretation / 怎麼解讀", ""])
    _append_bilingual(
        lines,
        "Relayer scan first builds a synthetic ranking over candidate layer windows.",
        "Relayer scan 會先對 candidate layer windows 建立 synthetic ranking。",
        bullet=True,
    )
    _append_bilingual(
        lines,
        "The `artifacts/` folder shows the ranking heat map and related synthetic score outputs.",
        "`artifacts/` 資料夾會放 ranking heat map 與相關 synthetic score 輸出。",
        bullet=True,
    )
    _append_bilingual(
        lines,
        "Use `verification` and `baseline_decision` to confirm whether the synthetic winner also survives real evaluation.",
        "請用 `verification` 與 `baseline_decision` 來確認 synthetic winner 是否也通過真實驗證。",
        bullet=True,
    )
    _append_bilingual(
        lines,
        "A verification winner can still be rejected if the baseline gate says the gain is not safe enough.",
        "就算 verification 有 winner，只要 baseline gate 判定不夠安全，最後仍可能被拒絕。",
        bullet=True,
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _cells_dir(output_dir: Path) -> Path:
    return output_dir / "cells"


def _cell_path(output_dir: Path, config_id: str) -> Path:
    safe_name = "".join(char if char.isalnum() or char in "._-" else "_" for char in str(config_id)).strip("_") or "candidate"
    return _cells_dir(output_dir) / f"{safe_name}.json"


def _candidate_results_payload(
    *,
    run_id: str,
    baseline_result: dict[str, Any],
    ordered_results: list[dict[str, Any]],
    candidate_ids: list[str],
    reused_ids: list[str],
    pending_ids: list[str],
    max_workers: int,
    resume: bool,
    skip_completed: bool,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "baseline_score": baseline_result["raw_score"],
        "result_count": len(ordered_results),
        "results": ordered_results,
        "candidate_ids": candidate_ids,
        "reused_ids": reused_ids,
        "pending_ids": pending_ids,
        "max_workers": max_workers,
        "resume": resume,
        "skip_completed": skip_completed,
    }


def _write_candidate_results_snapshot(
    *,
    output_dir: Path,
    run_id: str,
    baseline_result: dict[str, Any],
    ordered_results: list[dict[str, Any]],
    candidate_ids: list[str],
    reused_ids: list[str],
    pending_ids: list[str],
    max_workers: int,
    resume: bool,
    skip_completed: bool,
) -> None:
    write_json(
        output_dir / "candidate_results.json",
        _candidate_results_payload(
            run_id=run_id,
            baseline_result=baseline_result,
            ordered_results=ordered_results,
            candidate_ids=candidate_ids,
            reused_ids=reused_ids,
            pending_ids=pending_ids,
            max_workers=max_workers,
            resume=resume,
            skip_completed=skip_completed,
        ),
    )


def _write_resume_state(
    *,
    output_dir: Path,
    run_id: str,
    phase: str,
    candidate_target: int,
    completed_candidates: int,
    pending_ids: list[str],
    reused_ids: list[str],
    max_workers: int,
    progress_current: int,
    progress_target: int,
    verification_enabled: bool,
    verification_report_path: str | None = None,
    baseline_decision: dict[str, Any] | None = None,
) -> None:
    write_json(
        output_dir / "resume_state.json",
        {
            "run_id": run_id,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "phase": phase,
            "candidate_target": candidate_target,
            "completed_candidates": completed_candidates,
            "pending_candidates": len(pending_ids),
            "pending_ids": pending_ids,
            "reused_ids": reused_ids,
            "max_workers": max_workers,
            "progress_current": progress_current,
            "progress_target": progress_target,
            "progress_text": f"{progress_current}/{progress_target}" if progress_target else "0/0",
            "cells_dir": str(_cells_dir(output_dir).resolve()),
            "verification_enabled": verification_enabled,
            "verification_report_path": verification_report_path,
            "baseline_decision": baseline_decision,
        },
    )


def _write_cell_result(
    *,
    output_dir: Path,
    run_id: str,
    candidate: dict[str, Any],
    result: dict[str, Any],
    ordinal: int,
    baseline_score: float,
    reused: bool,
) -> None:
    path = _cell_path(output_dir, str(result["config_id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        path,
        {
            "run_id": run_id,
            "config_id": result["config_id"],
            "ordinal": ordinal,
            "completed_at": datetime.now().isoformat(timespec="seconds"),
            "reused": reused,
            "delta": round(result["raw_score"] - baseline_score, 6),
            "relayer": candidate.get("relayer", {}),
            "heat_map_coordinates": candidate.get("heat_map_coordinates"),
            "mutation_profile": candidate.get("mutation_profile"),
            "result": result,
        },
    )


def _load_completed_cells(output_dir: Path) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    cells_dir = _cells_dir(output_dir)
    if not cells_dir.exists():
        return results
    for path in sorted(cells_dir.glob("*.json")):
        payload = _load_json_object(path)
        if not payload:
            continue
        result = payload.get("result")
        if not isinstance(result, dict):
            result = payload if isinstance(payload.get("config_id"), str) else None
        if not isinstance(result, dict):
            continue
        config_id = str(result.get("config_id") or payload.get("config_id") or "").strip()
        if not config_id:
            continue
        results[config_id] = result
    return results


def _ordered_results_for_candidates(
    candidates: list[dict[str, Any]],
    result_lookup: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    for candidate in candidates:
        config_id = str(candidate.get("config_id"))
        result = result_lookup.get(config_id)
        if result is not None:
            ordered.append(result)
    return ordered


def _load_existing_completed_run(
    *,
    output_dir: Path,
    candidate_target: int,
    overall_progress_target: int,
) -> dict[str, Any] | None:
    manifest = _load_json_object(output_dir / "manifest.json")
    summary = _load_json_object(output_dir / "summary.json")
    baseline = _load_json_object(output_dir / "baseline.json")
    candidate_results_payload = _load_json_object(output_dir / "candidate_results.json")
    if not manifest or not summary or not baseline or not candidate_results_payload:
        return None
    results = candidate_results_payload.get("results")
    if not isinstance(results, list) or len(results) < candidate_target:
        return None
    baseline_decision = manifest.get("baseline_decision") if isinstance(manifest.get("baseline_decision"), dict) else {}
    verification = manifest.get("verification")
    progress_current = overall_progress_target if verification else candidate_target
    return {
        "manifest": manifest,
        "summary": summary,
        "baseline": baseline,
        "candidate_results": results[:candidate_target],
        "verification": verification if isinstance(verification, dict) else None,
        "baseline_decision": baseline_decision,
        "progress_target": overall_progress_target,
        "progress_current": progress_current,
    }


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
    base_config_path: Path | None,
    base_config: dict[str, Any],
    output_dir: Path,
    reports_dir: Path,
    baseline_path: Path | None = None,
    max_candidates: int | None = None,
    run_id: str | None = None,
    seed_start: int = 500,
    resume: bool = False,
    skip_completed: bool = False,
    max_workers: int = 1,
    progress_callback: ProgressCallback | None = None,
    event_callback: EventCallback | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    _cells_dir(output_dir).mkdir(parents=True, exist_ok=True)
    existing_state = _load_json_object(output_dir / "manifest.json") or _load_json_object(output_dir / "resume_state.json")
    if (resume or skip_completed) and existing_state and existing_state.get("run_id"):
        run_id = str(existing_state["run_id"])
    timestamp = datetime.now().isoformat(timespec="seconds")
    run_id = run_id or datetime.now().strftime("relayer_scan_%Y%m%d_%H%M%S")
    started = time.perf_counter()

    candidates = build_relayer_scan_candidates(base_config)
    if max_candidates is not None:
        candidates = candidates[: max(1, int(max_candidates))]
    max_workers = max(1, int(max_workers or 1))
    candidate_target = len(candidates)
    candidate_lookup = {str(candidate["config_id"]): candidate for candidate in candidates}
    verification_settings = resolve_relayer_scan_verification_settings(
        base_config,
        candidate_target,
        seed_start=seed_start,
    )
    overall_progress_target = estimate_relayer_scan_total_evals(
        base_config,
        candidate_target,
        seed_start=seed_start,
    ) or candidate_target
    candidate_ids = [str(candidate["config_id"]) for candidate in candidates]

    existing_completed = _load_completed_cells(output_dir) if (resume or skip_completed) else {}
    reused_ids = [config_id for config_id in candidate_ids if config_id in existing_completed]
    pending_ids = [config_id for config_id in candidate_ids if config_id not in existing_completed]

    if (resume or skip_completed) and not pending_ids:
        existing_run = _load_existing_completed_run(
            output_dir=output_dir,
            candidate_target=candidate_target,
            overall_progress_target=overall_progress_target,
        )
        if existing_run is not None:
            if event_callback is not None:
                event_callback(
                    {
                        "type": "system",
                        "name": "relayer_scan",
                        "text": (
                            f"Loaded completed relayer scan {run_id} from {output_dir.resolve()} "
                            f"with {candidate_target} cached cells."
                        ),
                    }
                )
            if progress_callback is not None:
                progress_callback(
                    {
                        "status": "completed",
                        "progress_current": existing_run["progress_current"],
                        "progress_target": existing_run["progress_target"],
                        "progress_text": f"{existing_run['progress_current']}/{existing_run['progress_target']}"
                        if existing_run["progress_target"]
                        else "0/0",
                        "elapsed_sec": round(time.perf_counter() - started, 3),
                        "best_config_id": (
                            existing_run["baseline_decision"].get("winner_config_id")
                            or existing_run["summary"].get("best_cell", {}).get("config_id")
                        ),
                        "baseline_status": existing_run["baseline_decision"].get("status"),
                    }
                )
            return existing_run

    if event_callback is not None:
        event_callback(
            {
                "type": "system",
                "name": "relayer_scan",
                "text": (
                    f"Starting relayer scan {run_id} with synthetic mock_layer_stack backend "
                    f"across {candidate_target} candidates (max_workers={max_workers})."
                ),
            }
        )
        if reused_ids:
            event_callback(
                {
                    "type": "system",
                    "name": "relayer_scan",
                    "text": (
                        f"Reusing {len(reused_ids)} completed relayer scan cells from {output_dir.resolve()}."
                    ),
                }
            )

    if progress_callback is not None:
        progress_callback(
            {
                "status": "starting_relayer_scan",
                "progress_current": len(reused_ids),
                "progress_target": overall_progress_target,
                "progress_text": (
                    f"{len(reused_ids)}/{overall_progress_target}" if overall_progress_target else "0/0"
                ),
                "candidate_count": candidate_target,
                "elapsed_sec": round(time.perf_counter() - started, 3),
            }
        )

    baseline_result = evaluate_mock_relayer_candidate(config=base_config, relayer_config=None)
    write_json(output_dir / "baseline.json", baseline_result)
    write_json(
        output_dir / "scan_plan.json",
        {
            "run_id": run_id,
            "created_at": timestamp,
            "candidate_count": candidate_target,
            "candidate_ids": candidate_ids,
            "reused_ids": reused_ids,
            "pending_ids": pending_ids,
            "max_workers": max_workers,
            "resume": resume,
            "skip_completed": skip_completed,
            "verification": verification_settings,
        },
    )
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
                "progress_target": overall_progress_target,
                "progress_text": f"0/{overall_progress_target}" if overall_progress_target else "0/0",
                "baseline_score": baseline_result["raw_score"],
                "elapsed_sec": round(time.perf_counter() - started, 3),
            }
        )

    candidate_results_lookup: dict[str, dict[str, Any]] = {config_id: dict(result) for config_id, result in existing_completed.items()}
    best_result: dict[str, Any] | None = None
    ordered_existing_results = _ordered_results_for_candidates(candidates, candidate_results_lookup)
    if ordered_existing_results:
        best_result = max(ordered_existing_results, key=lambda item: item["raw_score"])

    def evaluate_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
        relayer_cfg = candidate.get("relayer", {})
        return evaluate_mock_relayer_candidate(
            config=candidate,
            relayer_config=RelayerConfig(
                start_layer=int(relayer_cfg["start_layer"]),
                end_layer=int(relayer_cfg["end_layer"]),
                repeat_count=int(relayer_cfg.get("repeat_count", 1)),
            ),
        )

    def persist_progress(config_id: str | None = None) -> None:
        ordered_results = _ordered_results_for_candidates(candidates, candidate_results_lookup)
        remaining_ids = [item for item in candidate_ids if item not in candidate_results_lookup]
        _write_candidate_results_snapshot(
            output_dir=output_dir,
            run_id=run_id,
            baseline_result=baseline_result,
            ordered_results=ordered_results,
            candidate_ids=candidate_ids,
            reused_ids=reused_ids,
            pending_ids=remaining_ids,
            max_workers=max_workers,
            resume=resume,
            skip_completed=skip_completed,
        )
        _write_resume_state(
            output_dir=output_dir,
            run_id=run_id,
            phase="scan_running" if remaining_ids else "scan_complete",
            candidate_target=candidate_target,
            completed_candidates=len(ordered_results),
            pending_ids=remaining_ids,
            reused_ids=reused_ids,
            max_workers=max_workers,
            progress_current=len(ordered_results),
            progress_target=overall_progress_target,
            verification_enabled=verification_settings["enabled"],
        )
        if progress_callback is not None:
            progress_callback(
                {
                    "status": "running_relayer_scan",
                    "progress_current": len(ordered_results),
                    "progress_target": overall_progress_target,
                    "progress_text": (
                        f"{len(ordered_results)}/{overall_progress_target}" if overall_progress_target else "0/0"
                    ),
                    "current_candidate": config_id,
                    "best_config_id": best_result["config_id"] if best_result else None,
                    "baseline_score": baseline_result["raw_score"],
                    "elapsed_sec": round(time.perf_counter() - started, 3),
                }
            )

    persist_progress()

    pending_candidates = [candidate for candidate in candidates if str(candidate["config_id"]) not in existing_completed]
    if max_workers == 1:
        for ordinal, candidate in enumerate(pending_candidates, start=1):
            if event_callback is not None:
                event_callback(
                    {
                        "type": "system",
                        "name": "relayer_scan",
                        "text": f"Evaluating candidate {candidate.get('config_id')}.",
                    }
                )
            result = evaluate_candidate(candidate)
            candidate_results_lookup[str(result["config_id"])] = result
            _write_cell_result(
                output_dir=output_dir,
                run_id=run_id,
                candidate=candidate,
                result=result,
                ordinal=len(candidate_results_lookup),
                baseline_score=baseline_result["raw_score"],
                reused=False,
            )
            if best_result is None or result["raw_score"] > best_result["raw_score"]:
                best_result = result
            persist_progress(str(result["config_id"]))
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {}
            for candidate in pending_candidates:
                if event_callback is not None:
                    event_callback(
                        {
                            "type": "system",
                            "name": "relayer_scan",
                            "text": f"Evaluating candidate {candidate.get('config_id')}.",
                        }
                    )
                future_map[executor.submit(evaluate_candidate, candidate)] = candidate
            for future in as_completed(future_map):
                candidate = future_map[future]
                result = future.result()
                candidate_results_lookup[str(result["config_id"])] = result
                _write_cell_result(
                    output_dir=output_dir,
                    run_id=run_id,
                    candidate=candidate,
                    result=result,
                    ordinal=len(candidate_results_lookup),
                    baseline_score=baseline_result["raw_score"],
                    reused=False,
                )
                if best_result is None or result["raw_score"] > best_result["raw_score"]:
                    best_result = result
                persist_progress(str(result["config_id"]))

    candidate_results = _ordered_results_for_candidates(candidates, candidate_results_lookup)
    csv_rows = []
    for result in candidate_results:
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

    top_k = base_config.get("nightly", {}).get("heat_map", {}).get("top_k", 5)
    summary = build_relayer_scan_summary(
        base_config=base_config,
        baseline_result=baseline_result,
        candidate_results=candidate_results,
        top_k=top_k,
    )

    aggregated_csv_path = output_dir / "aggregated.csv"
    write_relayer_aggregated_csv(aggregated_csv_path, csv_rows)
    _write_candidate_results_snapshot(
        output_dir=output_dir,
        run_id=run_id,
        baseline_result=baseline_result,
        ordered_results=candidate_results,
        candidate_ids=candidate_ids,
        reused_ids=reused_ids,
        pending_ids=[],
        max_workers=max_workers,
        resume=resume,
        skip_completed=skip_completed,
    )
    write_json(output_dir / "summary.json", summary)
    _write_resume_state(
        output_dir=output_dir,
        run_id=run_id,
        phase="verification_pending" if verification_settings["enabled"] else "completed",
        candidate_target=candidate_target,
        completed_candidates=len(candidate_results),
        pending_ids=[],
        reused_ids=reused_ids,
        max_workers=max_workers,
        progress_current=len(candidate_results),
        progress_target=overall_progress_target,
        verification_enabled=verification_settings["enabled"],
    )

    artifacts_dir = output_dir / "artifacts"
    artifacts = build_heat_map_artifacts(
        output_dir=artifacts_dir,
        suite_id=run_id,
        heat_map_summary=summary,
        created_at=timestamp,
        base_config_id=str(base_config.get("config_id")),
        selected_config_id=str(summary.get("best_cell", {}).get("config_id", base_config.get("config_id"))),
        status="completed",
    )

    verification_report = None
    if verification_settings["enabled"] and base_config_path is not None:
        if event_callback is not None:
            event_callback(
                {
                    "type": "system",
                    "name": "relayer_scan",
                    "text": (
                        f"Running relayer top-k verification on {min(verification_settings['top_k'], candidate_target)} "
                        f"candidates with mode={verification_settings['scan_runtime_mode']}."
                    ),
                }
            )
        verification_report = run_relayer_scan_verification(
            base_config_path=base_config_path,
            base_config=base_config,
            relayer_scan_summary=summary,
            candidate_lookup=candidate_lookup,
            suite_id=run_id,
            reports_dir=reports_dir,
            artifact_dir=output_dir,
            seed_start=seed_start,
            progress_offset=candidate_target,
            progress_target=overall_progress_target,
        )

    verification_public = None
    if verification_report is not None:
        verification_public = {key: value for key, value in verification_report.items() if not key.startswith("_")}

    winner_result = verification_report.get("_winner_result") if verification_report else None
    baseline_decision = {
        "status": "verification_skipped" if not verification_settings["enabled"] else "no_verified_improvement",
        "promoted": False,
        "before_config_id": None,
        "after_config_id": None,
        "gate_reasons": [],
        "winner_config_id": None,
        "report_path": verification_public.get("report_path") if verification_public else None,
        "note": verification_settings["note"],
    }

    if winner_result is not None:
        baseline_before = load_baseline(baseline_path) if baseline_path is not None else {"config_id": None, "fitness": 0.0}
        gate = winner_result["config"].get("baseline_gate", base_config.get("baseline_gate", {}))
        decision = assess_candidate(
            current=baseline_before,
            candidate=winner_result["candidate_payload"],
            gate=gate,
        )
        promoted_baseline = None
        if decision["promoted"] and baseline_path is not None:
            promoted_baseline = write_baseline(baseline_path, winner_result["candidate_payload"])

        gate_reason = ", ".join(decision["reasons"]) if decision["reasons"] else "relayer_candidate_accepted"
        baseline_status = "promoted" if decision["promoted"] else "rejected"
        if decision["promoted"] and baseline_path is None:
            baseline_status = "accepted_unpersisted"

        regression_status = (
            f"{len([item for item in winner_result['regression_runs'] if item['status'] == 'passed'])}/"
            f"{len(winner_result['regression_runs'])} passed"
        )
        rollback_reason = "relayer scan promotion"
        if not decision["promoted"]:
            rollback_reason = f"relayer scan gate rejected: {gate_reason}"
            if decision["rollback_required"]:
                rollback_reason = f"relayer scan rollback gate: {gate_reason}"

        rollback_event = {
            "ts": timestamp,
            "reason": rollback_reason,
            "before_config_id": decision["before_config_id"],
            "after_config_id": decision["after_config_id"],
            "regression_status": regression_status,
            "baseline_restored": not decision["promoted"],
            "success": True,
            "run_id": run_id,
            "run_kind": "relayer_scan",
            "suite_id": run_id,
            "gate_reasons": decision["reasons"],
        }
        append_history_entry(reports_dir / "rollback_events.json", rollback_event)
        append_history_entry(
            reports_dir / "baseline_history.json",
            {
                "ts": timestamp,
                "run_id": run_id,
                "config_id": winner_result["config"]["config_id"],
                "fitness": winner_result["fitness"],
                "suite_score_c": winner_result["candidate_summary"]["suite_score"],
                "suite_score_b": winner_result["regression_summary"]["suite_score"],
                "status": baseline_status,
                "run_kind": "relayer_scan",
                "suite_id": run_id,
                "gate_reasons": decision["reasons"],
                "mutation_profile": winner_result["mutation_profile"],
            },
        )
        append_history_entry(
            reports_dir / "config_history.json",
            {
                "ts": timestamp,
                "suite_id": run_id,
                "round_index": 1,
                "config_id": winner_result["config"]["config_id"],
                "event": "relayer_scan_verified_winner",
                "event_label": "relayer_scan_verified_winner",
                "mutation_profile": winner_result["mutation_profile"],
                "mutation_notes": winner_result["mutation_notes"],
                "mutation_target": winner_result.get("parameter_name"),
                "mutation_before": winner_result.get("parameter_before"),
                "mutation_after": winner_result.get("parameter_after"),
                "reference_config_id": base_config.get("config_id"),
                "config_path": winner_result["config_path"],
                "parameter_snapshot": winner_result.get("parameter_snapshot"),
                "evolution_mode": "relayer_scan",
                "fitness": winner_result["fitness"],
                "suite_score_c": winner_result["candidate_summary"]["suite_score"],
                "suite_score_b": winner_result["regression_summary"]["suite_score"],
                "gate_reasons": decision["reasons"],
                "baseline_status": baseline_status,
            },
        )
        baseline_decision = {
            "status": baseline_status,
            "promoted": decision["promoted"],
            "before_config_id": decision["before_config_id"],
            "after_config_id": decision["after_config_id"],
            "gate_reasons": decision["reasons"],
            "winner_config_id": winner_result["config"]["config_id"],
            "winner_fitness": winner_result["fitness"],
            "winner_suite_score_c": winner_result["candidate_summary"]["suite_score"],
            "winner_suite_score_b": winner_result["regression_summary"]["suite_score"],
            "survivor_count": verification_public.get("survivor_count") if verification_public else 0,
            "report_path": verification_public.get("report_path") if verification_public else None,
            "promoted_baseline_config_id": None if promoted_baseline is None else promoted_baseline.get("config_id"),
            "note": verification_settings["note"],
        }
        if event_callback is not None:
            event_callback(
                {
                    "type": "system",
                    "name": "relayer_scan",
                    "text": (
                        f"Relayer verification winner={winner_result['config']['config_id']} "
                        f"status={baseline_status}."
                    ),
                }
            )
    elif verification_public is not None:
        append_history_entry(
            reports_dir / "config_history.json",
            {
                "ts": timestamp,
                "suite_id": run_id,
                "round_index": 1,
                "config_id": summary.get("best_cell", {}).get("config_id"),
                "event": "relayer_scan_no_verified_improvement",
                "event_label": "relayer_scan_no_verified_improvement",
                "reference_config_id": base_config.get("config_id"),
                "evolution_mode": "relayer_scan",
                "baseline_status": "no_verified_improvement",
                "report_path": verification_public.get("report_path"),
            },
        )
        if event_callback is not None:
            event_callback(
                {
                    "type": "system",
                    "name": "relayer_scan",
                    "text": "Relayer verification completed with no candidate above the baseline gate.",
                }
            )

    manifest = {
        "run_id": run_id,
        "created_at": timestamp,
        "config_id": base_config.get("config_id"),
        "probe_name": baseline_result["probe_name"],
        "baseline_score": baseline_result["raw_score"],
        "candidate_count": len(candidate_results),
        "output_dir": str(output_dir.resolve()),
        "cells_dir": str(_cells_dir(output_dir).resolve()),
        "aggregated_csv_path": str(aggregated_csv_path.resolve()),
        "artifacts": artifacts,
        "verification": verification_public,
        "baseline_decision": baseline_decision,
        "resume_state_path": str((output_dir / "resume_state.json").resolve()),
        "max_workers": max_workers,
        "resume": resume,
        "skip_completed": skip_completed,
        "reused_cells": len(reused_ids),
        "notes": (
            "Synthetic relayer scan uses mock_layer_stack for ranking; top-k verification replays selected "
            "candidates through the real Layer C evaluator when a runtime relayer backend is available."
        ),
    }
    _write_relayer_report_readme(
        path=output_dir / "README.md",
        run_id=run_id,
        summary=summary,
        manifest=manifest,
    )
    manifest["readme_path"] = str((output_dir / "README.md").resolve())
    write_json(output_dir / "manifest.json", manifest)
    write_json(
        output_dir / f"{run_id}.json",
        {
            "manifest": manifest,
            "summary": summary,
            "results": candidate_results,
            "verification": verification_public,
            "baseline_decision": baseline_decision,
        },
    )
    _write_resume_state(
        output_dir=output_dir,
        run_id=run_id,
        phase="completed",
        candidate_target=candidate_target,
        completed_candidates=len(candidate_results),
        pending_ids=[],
        reused_ids=reused_ids,
        max_workers=max_workers,
        progress_current=overall_progress_target if verification_public is not None else candidate_target,
        progress_target=overall_progress_target,
        verification_enabled=verification_settings["enabled"],
        verification_report_path=baseline_decision.get("report_path"),
        baseline_decision=baseline_decision,
    )

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
            "verification_enabled": verification_settings["enabled"],
            "verification_backend": verification_settings["scan_runtime_mode"],
            "verified_survivor_count": 0 if verification_public is None else verification_public.get("survivor_count", 0),
            "verified_winner_config_id": None
            if verification_public is None
            else (verification_public.get("winner") or {}).get("config_id"),
            "baseline_status": baseline_decision["status"],
            "gate_reasons": baseline_decision.get("gate_reasons", []),
            "verification_report_path": baseline_decision.get("report_path"),
            "output_dir": str(output_dir.resolve()),
            "cells_dir": str(_cells_dir(output_dir).resolve()),
            "max_workers": max_workers,
            "resume": resume,
            "skip_completed": skip_completed,
            "reused_cells": len(reused_ids),
            "notes": manifest["notes"],
        },
    )
    final_progress_current = candidate_target
    if verification_public is not None:
        final_progress_current = overall_progress_target
    if progress_callback is not None:
        progress_callback(
            {
                "status": "completed",
                "progress_current": final_progress_current,
                "progress_target": overall_progress_target,
                "progress_text": f"{final_progress_current}/{overall_progress_target}" if overall_progress_target else "0/0",
                "best_config_id": baseline_decision.get("winner_config_id") or summary.get("best_cell", {}).get("config_id"),
                "baseline_score": baseline_result["raw_score"],
                "elapsed_sec": round(time.perf_counter() - started, 3),
                "baseline_status": baseline_decision["status"],
            }
        )
    if event_callback is not None:
        event_callback(
            {
                "type": "system",
                "name": "relayer_scan",
                "text": (
                    f"Completed relayer scan {run_id}. "
                    f"Best candidate={baseline_decision.get('winner_config_id') or summary.get('best_cell', {}).get('config_id') or '-'}."
                ),
            }
        )

    return {
        "manifest": manifest,
        "summary": summary,
        "baseline": baseline_result,
        "candidate_results": candidate_results,
        "verification": verification_public,
        "baseline_decision": baseline_decision,
        "progress_target": overall_progress_target,
        "progress_current": final_progress_current,
    }
