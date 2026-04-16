from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evolution.mutator import build_candidate_pool, persist_candidate_pool
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
            )
        )

    regression_results = []
    for case in regression_suite["cases"]:
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
        "notes": f"Candidate {config['config_id']} evaluated by nightly pool.",
        "source_config_path": str(config_path.resolve()),
        "config_body": config,
    }

    return {
        "config": config,
        "config_path": str(config_path.resolve()),
        "mutation_profile": config.get("mutation_profile", "unknown"),
        "mutation_notes": config.get("mutation_notes", ""),
        "fitness": fitness,
        "candidate_summary": candidate_summary,
        "regression_summary": regression_summary,
        "suite_score_a": suite_score_a,
        "candidate_payload": payload,
        "candidate_runs": candidate_results,
        "regression_runs": regression_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local nightly evaluation and selection loop.")
    parser.add_argument("--config", default=str(ROOT / "configs" / "experiments" / "default_mvp.json"))
    parser.add_argument("--seed-start", type=int, default=500)
    args = parser.parse_args()

    base_config_path = Path(args.config)
    base_config = _load_json(base_config_path)
    regression_suite = _load_json(ROOT / base_config["regression_suite"]["path"])

    now = datetime.now()
    timestamp = now.isoformat(timespec="seconds")
    suite_id = now.strftime("nightly_%Y%m%d_%H%M%S")

    runs_dir = ROOT / base_config["paths"]["runs_dir"]
    reports_dir = ROOT / base_config["paths"]["reports_dir"]
    baseline_path = ROOT / base_config["paths"].get("baseline_path", "configs/baselines/best_stable_config.json")

    ensure_report_files(reports_dir)
    seed_static_histories(reports_dir, timestamp)

    pool_size = int(base_config["nightly"].get("candidate_pool_size", 1))
    candidate_runs_per_config = int(
        base_config["nightly"].get("candidate_runs_per_config", base_config["nightly"].get("candidate_runs", 4))
    )
    include_base_config = bool(base_config["nightly"].get("include_base_config", True))
    candidate_configs = build_candidate_pool(
        base_config=base_config,
        pool_size=pool_size,
        include_base=include_base_config,
    )
    candidate_paths = persist_candidate_pool(ROOT, suite_id, candidate_configs)

    writer = LiveWriter(runs_dir)
    writer.reset_stream()
    total_evals = len(candidate_paths) * (candidate_runs_per_config + len(regression_suite["cases"]))
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
        }
    )
    writer.append_event(
        {
            "type": "system",
            "name": "nightly",
            "text": f"Nightly evaluation started with {len(candidate_paths)} candidate configs, {candidate_runs_per_config} candidate runs per config, and {len(regression_suite['cases'])} regression cases.",
        }
    )

    evaluated_candidates = []
    for index, config_path in enumerate(candidate_paths):
        candidate_config = _load_json(config_path)
        append_history_entry(
            reports_dir / "config_history.json",
            {
                "ts": timestamp,
                "suite_id": suite_id,
                "config_id": candidate_config["config_id"],
                "event": "candidate_generated",
                "mutation_profile": candidate_config.get("mutation_profile", "baseline"),
                "mutation_notes": candidate_config.get("mutation_notes", ""),
                "config_path": str(config_path.resolve()),
            },
        )
        writer.append_event(
            {
                "type": "system",
                "name": "mutator",
                "text": f"Evaluating candidate {index + 1}/{len(candidate_paths)}: {candidate_config['config_id']} ({candidate_config.get('mutation_profile', 'baseline')}).",
            }
        )
        evaluated_candidates.append(
            _evaluate_candidate_config(
                config_path=config_path,
                config=candidate_config,
                suite_id=suite_id,
                seed_offset=args.seed_start + (index * 100),
                candidate_runs_per_config=candidate_runs_per_config,
                regression_suite=regression_suite,
            )
        )

    ranked_candidates = sorted(
        evaluated_candidates,
        key=lambda item: (
            item["fitness"],
            item["regression_summary"]["pass_rate"],
            item["candidate_summary"]["stability_score"],
            item["candidate_summary"]["honesty_score"],
        ),
        reverse=True,
    )
    best_candidate = ranked_candidates[0]

    baseline_before = load_baseline(baseline_path)
    decision = assess_candidate(
        current=baseline_before,
        candidate=best_candidate["candidate_payload"],
        gate=best_candidate["config"].get("baseline_gate", base_config["baseline_gate"]),
    )
    promoted_baseline = None
    if decision["promoted"]:
        promoted_baseline = write_baseline(baseline_path, best_candidate["candidate_payload"])

    gate_reason = ", ".join(decision["reasons"]) if decision["reasons"] else "nightly_candidate_accepted"
    rollback_reason = "nightly promotion"
    if not decision["promoted"]:
        rollback_reason = f"nightly gate rejected: {gate_reason}"
        if decision["rollback_required"]:
            rollback_reason = f"nightly rollback gate: {gate_reason}"

    regression_status = (
        f"{len([item for item in best_candidate['regression_runs'] if item['status'] == 'passed'])}/{len(best_candidate['regression_runs'])} passed"
    )
    rollback_event = {
        "ts": timestamp,
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
        "ts": timestamp,
        "run_id": suite_id,
        "config_id": best_candidate["config"]["config_id"],
        "fitness": best_candidate["fitness"],
        "suite_score_c": best_candidate["candidate_summary"]["suite_score"],
        "suite_score_b": best_candidate["regression_summary"]["suite_score"],
        "status": "promoted" if decision["promoted"] else "rejected",
        "run_kind": "nightly",
        "suite_id": suite_id,
        "gate_reasons": decision["reasons"],
        "mutation_profile": best_candidate["mutation_profile"],
    }
    append_history_entry(reports_dir / "baseline_history.json", baseline_event)

    nightly_entry = {
        "ts": timestamp,
        "suite_id": suite_id,
        "config_id": best_candidate["config"]["config_id"],
        "fitness": best_candidate["fitness"],
        "suite_score_c": best_candidate["candidate_summary"]["suite_score"],
        "suite_score_b": best_candidate["regression_summary"]["suite_score"],
        "suite_score_a": best_candidate["suite_score_a"],
        "stability_score": best_candidate["candidate_summary"]["stability_score"],
        "rollback_safety_score": best_candidate["candidate_summary"]["rollback_safety_score"],
        "regression_pass_rate": best_candidate["regression_summary"]["pass_rate"],
        "honesty_score": best_candidate["candidate_summary"]["honesty_score"],
        "status": "promoted" if decision["promoted"] else "rejected",
        "gate_reasons": decision["reasons"],
        "mutation_profile": best_candidate["mutation_profile"],
        "candidate_pool_size": len(candidate_paths),
        "ranking": [
            {
                "config_id": item["config"]["config_id"],
                "mutation_profile": item["mutation_profile"],
                "fitness": item["fitness"],
            }
            for item in ranked_candidates[:5]
        ],
    }
    append_history_entry(reports_dir / "nightly_history.json", nightly_entry)

    append_history_entry(
        reports_dir / "config_history.json",
        {
            "ts": timestamp,
            "suite_id": suite_id,
            "config_id": best_candidate["config"]["config_id"],
            "event": "candidate_selected" if decision["promoted"] else "candidate_rejected",
            "mutation_profile": best_candidate["mutation_profile"],
            "fitness": best_candidate["fitness"],
            "config_path": best_candidate["config_path"],
            "archive_path": promoted_baseline["config_archive_path"] if promoted_baseline else None,
            "gate_reasons": decision["reasons"],
        },
    )

    append_history_entry(
        reports_dir / "score_history.json",
        {
            "ts": timestamp,
            "run_id": suite_id,
            "label": suite_id[-6:],
            "config_id": best_candidate["config"]["config_id"],
            "overall": best_candidate["candidate_summary"]["suite_score"],
            "fitness": best_candidate["fitness"],
            "fitness_mode": base_config["fitness_mode"],
            "suite_score_c": best_candidate["candidate_summary"]["suite_score"],
            "suite_score_b": best_candidate["regression_summary"]["suite_score"],
            "passed": decision["promoted"],
            "run_kind": "nightly_summary",
            "suite_id": suite_id,
            "honesty_score": best_candidate["candidate_summary"]["honesty_score"],
        },
    )

    summary = {
        "suite_id": suite_id,
        "created_at": timestamp,
        "base_config_id": base_config["config_id"],
        "selected_config_id": best_candidate["config"]["config_id"],
        "fitness_mode": base_config["fitness_mode"],
        "status": "promoted" if decision["promoted"] else "rejected",
        "fitness": best_candidate["fitness"],
        "suite_score_c": best_candidate["candidate_summary"]["suite_score"],
        "suite_score_b": best_candidate["regression_summary"]["suite_score"],
        "suite_score_a": best_candidate["suite_score_a"],
        "stability_score": best_candidate["candidate_summary"]["stability_score"],
        "rollback_safety_score": best_candidate["candidate_summary"]["rollback_safety_score"],
        "regression_pass_rate": best_candidate["regression_summary"]["pass_rate"],
        "honesty_score": best_candidate["candidate_summary"]["honesty_score"],
        "baseline_decision": decision,
        "candidate_rankings": [
            {
                "config_id": item["config"]["config_id"],
                "mutation_profile": item["mutation_profile"],
                "fitness": item["fitness"],
                "suite_score_c": item["candidate_summary"]["suite_score"],
                "suite_score_b": item["regression_summary"]["suite_score"],
                "regression_pass_rate": item["regression_summary"]["pass_rate"],
            }
            for item in ranked_candidates
        ],
        "evaluated_candidates": evaluated_candidates,
    }
    artifact_path = runs_dir / "artifacts" / f"{suite_id}.json"
    write_json(artifact_path, summary)

    writer.write_status(
        {
            "run_id": suite_id,
            "status": summary["status"],
            "task_id": "nightly_evolution_loop",
            "config_id": best_candidate["config"]["config_id"],
            "runner": best_candidate["config"]["runner"],
            "current_tool": None,
            "last_error": None,
            "step_count": total_evals,
            "max_steps": total_evals,
            "elapsed_sec": round(
                sum(item["elapsed_sec"] for candidate in ranked_candidates for item in candidate["candidate_runs"] + candidate["regression_runs"]),
                4,
            ),
            "updated_at": timestamp,
            "score": best_candidate["candidate_summary"]["suite_score"],
            "fitness": best_candidate["fitness"],
            "fitness_mode": base_config["fitness_mode"],
            "rollback_time": rollback_event["ts"],
            "rollback_reason": rollback_event["reason"],
            "rollback_before_config_id": rollback_event["before_config_id"],
            "rollback_after_config_id": rollback_event["after_config_id"],
            "regression_status": rollback_event["regression_status"],
            "baseline_restored": rollback_event["baseline_restored"],
            "run_kind": "nightly",
            "suite_id": suite_id,
            "honesty_score": best_candidate["candidate_summary"]["honesty_score"],
            "rollback_safety_score": best_candidate["candidate_summary"]["rollback_safety_score"],
        }
    )
    writer.append_event(
        {
            "type": "system",
            "name": "nightly",
            "text": f"Nightly selected {best_candidate['config']['config_id']} with fitness={best_candidate['fitness']} and status={summary['status']}.",
        }
    )

    print(json.dumps(nightly_entry, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
