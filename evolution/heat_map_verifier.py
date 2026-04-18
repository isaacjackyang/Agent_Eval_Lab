from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from evolution.nightly_evaluator import evaluate_candidate_config, load_json
from evolution.relayer_plan import resolve_relayer_scan_runtime_mode
from storage.history_writer import append_history_entry, write_json

ROOT = Path(__file__).resolve().parents[1]


def _slugify(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value).strip("_") or "candidate"


def _persist_verification_config(config_dir: Path, name: str, config: dict[str, Any]) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / f"{_slugify(name)}.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


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


def resolve_relayer_scan_verification_settings(
    base_config: dict[str, Any],
    candidate_count: int,
    *,
    seed_start: int,
) -> dict[str, Any]:
    relayer_cfg = base_config.get("relayer", {})
    if not isinstance(relayer_cfg, dict):
        relayer_cfg = {}
    scan_cfg = relayer_cfg.get("scan", {})
    if not isinstance(scan_cfg, dict):
        scan_cfg = {}
    verify_cfg = scan_cfg.get("verify", {})
    if not isinstance(verify_cfg, dict):
        verify_cfg = {}

    try:
        scan_runtime_mode = resolve_relayer_scan_runtime_mode(base_config)
    except Exception:
        scan_runtime_mode = str(scan_cfg.get("runtime_mode") or relayer_cfg.get("mode") or "metadata_only").strip().lower()
    verification_capable = scan_runtime_mode in {"runtime_patch", "mock_layer_stack"}
    enabled = bool(verify_cfg.get("enabled", verification_capable))
    candidate_runs = int(
        verify_cfg.get(
            "candidate_runs_per_config",
            max(int(base_config.get("nightly", {}).get("candidate_runs_per_config", 4)), 4),
        )
    )
    bounded_candidate_count = max(0, int(candidate_count or 0))
    top_k_default = min(5, bounded_candidate_count) if bounded_candidate_count else 0
    top_k = int(verify_cfg.get("top_k", top_k_default or 1))

    note = (
        f"Relayer top-k verification will run candidate configs with relayer mode={scan_runtime_mode}."
        if verification_capable
        else (
            f"Relayer top-k verification is disabled because runtime mode resolves to {scan_runtime_mode}; "
            "no runtime relayer effect would be applied."
        )
    )

    return {
        "enabled": enabled and verification_capable and bounded_candidate_count > 0,
        "verification_capable": verification_capable,
        "candidate_runs_per_config": max(1, candidate_runs),
        "top_k": max(1, min(top_k, bounded_candidate_count or 1)),
        "seed_start": int(verify_cfg.get("seed_start", seed_start + 20000)),
        "scan_runtime_mode": scan_runtime_mode or "metadata_only",
        "note": note,
    }


def estimate_relayer_scan_total_evals(
    base_config: dict[str, Any],
    candidate_count: int,
    *,
    seed_start: int,
) -> int:
    bounded_candidate_count = max(0, int(candidate_count or 0))
    if bounded_candidate_count == 0:
        return 0

    total = bounded_candidate_count
    settings = resolve_relayer_scan_verification_settings(base_config, bounded_candidate_count, seed_start=seed_start)
    if not settings["enabled"]:
        return total

    regression_suite = load_json((ROOT / base_config["regression_suite"]["path"]).resolve())
    per_target_evals = settings["candidate_runs_per_config"] + len(regression_suite.get("cases", []))
    verify_target_count = min(settings["top_k"], bounded_candidate_count) + 1
    return total + (verify_target_count * per_target_evals)


def _build_relayer_verification_targets(
    *,
    base_config_path: Path,
    base_config: dict[str, Any],
    relayer_scan_summary: dict[str, Any],
    candidate_lookup: dict[str, dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    targets = [
        {
            "kind": "baseline",
            "config_id": base_config["config_id"],
            "config": base_config,
            "config_path": str(base_config_path.resolve()),
            "synthetic_rank": 0,
            "synthetic_delta_suite_score": 0.0,
        }
    ]
    for index, cell in enumerate(relayer_scan_summary.get("top_candidates", [])[:top_k], start=1):
        config_id = cell.get("config_id")
        candidate_config = candidate_lookup.get(str(config_id))
        if not config_id or candidate_config is None:
            continue
        targets.append(
            {
                "kind": "candidate",
                "config_id": str(config_id),
                "config": candidate_config,
                "config_path": None,
                "synthetic_rank": index,
                "synthetic_delta_suite_score": cell.get("delta_suite_score"),
                "heat_map_coordinates": candidate_config.get("heat_map_coordinates"),
            }
        )
    return targets


def run_relayer_scan_verification(
    *,
    base_config_path: Path,
    base_config: dict[str, Any],
    relayer_scan_summary: dict[str, Any],
    candidate_lookup: dict[str, dict[str, Any]],
    suite_id: str,
    reports_dir: Path,
    artifact_dir: Path,
    seed_start: int,
    progress_offset: int = 0,
    progress_target: int | None = None,
) -> dict[str, Any] | None:
    settings = resolve_relayer_scan_verification_settings(
        base_config,
        int(relayer_scan_summary.get("cell_count", len(candidate_lookup)) or 0),
        seed_start=seed_start,
    )
    if not settings["enabled"]:
        return None

    regression_suite = load_json((ROOT / base_config["regression_suite"]["path"]).resolve())
    verify_suite_id = f"{suite_id}__verify"
    targets = _build_relayer_verification_targets(
        base_config_path=base_config_path,
        base_config=base_config,
        relayer_scan_summary=relayer_scan_summary,
        candidate_lookup=candidate_lookup,
        top_k=settings["top_k"],
    )
    if len(targets) <= 1:
        return None

    config_dir = artifact_dir / "verification_configs"
    per_target_evals = settings["candidate_runs_per_config"] + len(regression_suite.get("cases", []))
    total_evals = progress_target or (progress_offset + (len(targets) * per_target_evals))

    baseline_result: dict[str, Any] | None = None
    verified_candidates: list[dict[str, Any]] = []

    for index, target in enumerate(targets):
        if target["kind"] == "baseline":
            config_path = Path(target["config_path"])
        else:
            config_path = _persist_verification_config(
                config_dir,
                f"{index:02d}_{target['config_id']}",
                target["config"],
            )

        result = evaluate_candidate_config(
            config_path=config_path,
            config=target["config"],
            suite_id=verify_suite_id,
            seed_offset=settings["seed_start"] + (index * 1000),
            candidate_runs_per_config=settings["candidate_runs_per_config"],
            regression_suite=regression_suite,
            progress_offset=progress_offset + (index * per_target_evals),
            progress_target=total_evals,
            candidate_run_kind="relayer_verification_candidate",
            regression_run_kind="relayer_verification_regression",
            candidate_case_prefix=f"{target['config_id']}__relayer_verify",
            append_candidate_score_history=False,
            append_regression_score_history=False,
        )
        result["verification_target_kind"] = target["kind"]
        result["synthetic_rank"] = target.get("synthetic_rank")
        result["synthetic_delta_suite_score"] = target.get("synthetic_delta_suite_score")
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
        relayer_cfg = result["config"].get("relayer", {})
        suite_score = result["candidate_summary"]["suite_score"]
        fitness = result["fitness"]
        row = {
            "config_id": result["config"]["config_id"],
            "config_path": result["config_path"],
            "mutation_profile": result.get("mutation_profile"),
            "synthetic_rank": result.get("synthetic_rank"),
            "synthetic_delta_suite_score": result.get("synthetic_delta_suite_score"),
            "start_layer": relayer_cfg.get("start_layer"),
            "end_layer": relayer_cfg.get("end_layer"),
            "repeat_count": relayer_cfg.get("repeat_count"),
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
    winner_result = None
    if winner is not None:
        winner_result = next(
            (item for item in verified_candidates if item["config"]["config_id"] == winner["config_id"]),
            None,
        )

    created_at = datetime.now().isoformat(timespec="seconds")
    report = {
        "suite_id": suite_id,
        "verification_suite_id": verify_suite_id,
        "created_at": created_at,
        "candidate_runs_per_config": settings["candidate_runs_per_config"],
        "top_k": settings["top_k"],
        "verification_backend": settings["scan_runtime_mode"],
        "verification_note": settings["note"],
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
        reports_dir / "relayer_scan_verification_history.json",
        {
            "ts": created_at,
            "suite_id": suite_id,
            "verification_suite_id": verify_suite_id,
            "verification_backend": settings["scan_runtime_mode"],
            "top_k": settings["top_k"],
            "candidate_runs_per_config": settings["candidate_runs_per_config"],
            "survivor_count": report["survivor_count"],
            "winner": winner,
            "report_path": str(report_path.resolve()),
        },
    )
    return {
        **report,
        "report_path": str(report_path.resolve()),
        "_baseline_result": baseline_result,
        "_verified_candidate_results": verified_candidates,
        "_winner_result": winner_result,
    }
