from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from evolution.nightly_evaluator import evaluate_candidate_config, load_json
from storage.history_writer import append_history_entry, write_json

ROOT = Path(__file__).resolve().parents[1]


def resolve_heat_map_verification_settings(
    base_config: dict[str, Any],
    heat_map_summary: dict[str, Any],
    *,
    seed_start: int,
) -> dict[str, Any]:
    heat_map_cfg = base_config.get("nightly", {}).get("heat_map", {})
    if not isinstance(heat_map_cfg, dict):
        heat_map_cfg = {}
    verify_cfg = heat_map_cfg.get("verify", {})
    if not isinstance(verify_cfg, dict):
        verify_cfg = {}

    candidate_runs = int(
        verify_cfg.get(
            "candidate_runs_per_config",
            max(
                int(base_config.get("nightly", {}).get("candidate_runs_per_config", 4)),
                6,
            ),
        )
    )
    top_k = int(verify_cfg.get("top_k", len(heat_map_summary.get("top_candidates", [])) or heat_map_summary.get("cell_count", 1)))
    return {
        "enabled": bool(verify_cfg.get("enabled", True)),
        "candidate_runs_per_config": max(1, candidate_runs),
        "top_k": max(1, top_k),
        "seed_start": int(verify_cfg.get("seed_start", seed_start + 10000)),
    }


def _build_verification_targets(
    *,
    base_config_path: Path,
    base_config: dict[str, Any],
    heat_map_summary: dict[str, Any],
    evaluated_candidates: list[dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    lookup = {
        item["config"]["config_id"]: {
            "config": item["config"],
            "config_path": item["config_path"],
            "mutation_profile": item.get("mutation_profile"),
            "heat_map_coordinates": item["config"].get("heat_map_coordinates"),
        }
        for item in evaluated_candidates
        if isinstance(item, dict) and isinstance(item.get("config"), dict)
    }

    targets = [
        {
            "kind": "baseline",
            "config_id": base_config["config_id"],
            "config": base_config,
            "config_path": str(base_config_path.resolve()),
            "mutation_profile": "baseline",
            "heat_map_coordinates": heat_map_summary.get("baseline"),
        }
    ]
    for cell in heat_map_summary.get("top_candidates", [])[:top_k]:
        config_id = cell.get("config_id")
        if not config_id or config_id not in lookup:
            continue
        targets.append({"kind": "candidate", "config_id": config_id, **lookup[config_id]})
    return targets


def run_heat_map_verification(
    *,
    base_config_path: Path,
    base_config: dict[str, Any],
    heat_map_summary: dict[str, Any],
    evaluated_candidates: list[dict[str, Any]],
    suite_id: str,
    reports_dir: Path,
    artifact_dir: Path,
    seed_start: int,
) -> dict[str, Any] | None:
    settings = resolve_heat_map_verification_settings(base_config, heat_map_summary, seed_start=seed_start)
    if not settings["enabled"]:
        return None

    regression_suite = load_json((ROOT / base_config["regression_suite"]["path"]).resolve())
    verify_suite_id = f"{suite_id}__verify"
    targets = _build_verification_targets(
        base_config_path=base_config_path,
        base_config=base_config,
        heat_map_summary=heat_map_summary,
        evaluated_candidates=evaluated_candidates,
        top_k=settings["top_k"],
    )
    if len(targets) <= 1:
        return None

    per_target_evals = settings["candidate_runs_per_config"] + len(regression_suite.get("cases", []))
    total_evals = len(targets) * per_target_evals
    baseline_result: dict[str, Any] | None = None
    verified_candidates: list[dict[str, Any]] = []

    for index, target in enumerate(targets):
        result = evaluate_candidate_config(
            config_path=Path(target["config_path"]),
            config=target["config"],
            suite_id=verify_suite_id,
            seed_offset=settings["seed_start"] + (index * 1000),
            candidate_runs_per_config=settings["candidate_runs_per_config"],
            regression_suite=regression_suite,
            progress_offset=index * per_target_evals,
            progress_target=total_evals,
            candidate_run_kind="verification_candidate",
            regression_run_kind="verification_regression",
            candidate_case_prefix=f"{target['config_id']}__verify",
            append_candidate_score_history=False,
            append_regression_score_history=False,
        )
        result["verification_target_kind"] = target["kind"]
        result["heat_map_coordinates"] = target.get("heat_map_coordinates")
        if target["kind"] == "baseline":
            baseline_result = result
        else:
            verified_candidates.append(result)

    if baseline_result is None:
        return None

    min_pass_rate = float(base_config.get("regression_suite", {}).get("min_pass_rate", 0.0))
    baseline_suite_score = baseline_result["candidate_summary"]["suite_score"]
    baseline_fitness = baseline_result["fitness"]
    verified_rows: list[dict[str, Any]] = []
    for result in verified_candidates:
        suite_score = result["candidate_summary"]["suite_score"]
        fitness = result["fitness"]
        row = {
            "config_id": result["config"]["config_id"],
            "config_path": result["config_path"],
            "mutation_profile": result.get("mutation_profile"),
            "heat_map_coordinates": result.get("heat_map_coordinates"),
            "suite_score_c": suite_score,
            "fitness": fitness,
            "regression_pass_rate": result["regression_summary"]["pass_rate"],
            "honesty_score": result["candidate_summary"]["honesty_score"],
            "delta_suite_score": round(suite_score - baseline_suite_score, 6),
            "delta_fitness": round(fitness - baseline_fitness, 6),
            "maintained_improvement": (
                suite_score > baseline_suite_score + 1e-9
                and result["regression_summary"]["pass_rate"] >= min_pass_rate
            ),
        }
        verified_rows.append(row)

    ranked = sorted(
        verified_rows,
        key=lambda item: (
            item["maintained_improvement"],
            item["suite_score_c"],
            item["fitness"],
            item["regression_pass_rate"],
            item["honesty_score"],
        ),
        reverse=True,
    )
    winner = ranked[0] if ranked else None
    created_at = datetime.now().isoformat(timespec="seconds")
    report = {
        "suite_id": suite_id,
        "verification_suite_id": verify_suite_id,
        "created_at": created_at,
        "candidate_runs_per_config": settings["candidate_runs_per_config"],
        "top_k": settings["top_k"],
        "baseline": {
            "config_id": baseline_result["config"]["config_id"],
            "config_path": baseline_result["config_path"],
            "suite_score_c": baseline_suite_score,
            "fitness": baseline_fitness,
            "regression_pass_rate": baseline_result["regression_summary"]["pass_rate"],
            "honesty_score": baseline_result["candidate_summary"]["honesty_score"],
        },
        "survivor_count": sum(1 for item in ranked if item["maintained_improvement"]),
        "winner": winner,
        "ranked_candidates": ranked,
    }

    artifact_dir.mkdir(parents=True, exist_ok=True)
    report_path = artifact_dir / "verification.json"
    write_json(report_path, report)
    append_history_entry(
        reports_dir / "heat_map_verification_history.json",
        {
            "ts": created_at,
            "suite_id": suite_id,
            "verification_suite_id": verify_suite_id,
            "top_k": settings["top_k"],
            "candidate_runs_per_config": settings["candidate_runs_per_config"],
            "survivor_count": report["survivor_count"],
            "winner": winner,
            "report_path": str(report_path.resolve()),
        },
    )
    report["report_path"] = str(report_path.resolve())
    return report
