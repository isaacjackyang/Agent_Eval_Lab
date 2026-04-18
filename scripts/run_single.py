from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from diagnostics.run_diagnostics import build_failure_diagnostic, build_trace_diagnostic
from generators.task_dispatch import TASK_TYPE_CHOICES, build_task_and_benchmark
from rollback.baseline_manager import load_baseline, maybe_promote_baseline
from rollback.workspace_restore import restore_workspace
from runners.factory import build_runner
from sandbox.snapshots.workspace_snapshot import create_snapshot
from scoring.aggregation import compute_rollback_safety
from scoring.metrics import compute_stotal
from storage.db import init_db, insert_run_summary
from storage.history_writer import append_history_entry, ensure_report_files, seed_static_histories, write_json
from storage.live_writer import LiveWriter
from verifiers.task_dispatch import verify_task


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_status_payload(
    *,
    run_id: str,
    config_id: str,
    runner: str,
    timestamp: str,
    fitness_mode: str,
    run_kind: str,
    task_type: str | None,
    suite_id: str | None,
    case_id: str | None,
    suite_index: int | None,
    suite_runs: int | None,
    progress_current: int | None = None,
    progress_target: int | None = None,
    **extra,
) -> dict:
    payload = {
        "run_id": run_id,
        "config_id": config_id,
        "runner": runner,
        "updated_at": timestamp,
        "fitness_mode": fitness_mode,
        "run_kind": run_kind,
        "suite_id": suite_id,
        "case_id": case_id,
    }
    if task_type:
        payload["task_type"] = task_type
    if suite_index is not None:
        payload["suite_progress_current"] = suite_index
    if suite_runs is not None:
        payload["suite_progress_target"] = suite_runs
    if suite_index is not None and suite_runs is not None:
        payload["suite_progress_text"] = f"{suite_index}/{suite_runs}"
    if progress_current is not None:
        payload["progress_current"] = progress_current
    if progress_target is not None:
        payload["progress_target"] = progress_target
    if progress_current is not None and progress_target is not None:
        payload["progress_text"] = f"{progress_current}/{progress_target}"
    payload.update(extra)
    return payload


def _build_candidate_payload(config: dict, run_id: str, timestamp: str, score: float, verifier_result: dict, rollback_safety: float) -> dict:
    passed = bool(verifier_result["passed"])
    return {
        "config_id": config["config_id"],
        "selected_at": timestamp,
        "fitness": score,
        "suite_score_c": score,
        "suite_score_b": score,
        "suite_score_a": float(config.get("layer_a_proxy_score", 1.0)),
        "stability_score": 1.0,
        "rollback_safety_score": rollback_safety,
        "regression_pass_rate": 1.0 if passed else 0.0,
        "honesty_score": verifier_result["subscores"]["honesty"],
        "passed": passed,
        "run_id": run_id,
        "notes": "Promoted by local single-run gate.",
        "config_body": config,
    }


def execute_single_run(
    config_path: Path | None = None,
    seed: int | None = None,
    task_type: str | None = None,
    append_score_history: bool = True,
    append_baseline_history: bool = True,
    append_rollback_history: bool = True,
    manage_baseline: bool = True,
    reset_stream: bool = True,
    run_kind: str = "single",
    suite_id: str | None = None,
    case_id: str | None = None,
    suite_index: int | None = None,
    suite_runs: int | None = None,
    progress_current: int | None = None,
    progress_target: int | None = None,
) -> dict:
    config_path = config_path or ROOT / "configs" / "experiments" / "default_mvp.json"
    config = _load_json(config_path)
    now = datetime.now()
    timestamp = now.isoformat(timespec="seconds")
    run_id = now.strftime("run_%Y%m%d_%H%M%S_%f")

    runs_dir = ROOT / config["paths"]["runs_dir"]
    reports_dir = ROOT / config["paths"]["reports_dir"]
    artifacts_dir = runs_dir / "artifacts"
    workspace_root = ROOT / "sandbox" / "workspaces" / run_id
    snapshot_dir = ROOT / "sandbox" / "snapshots" / run_id
    baseline_path = ROOT / config["paths"].get("baseline_path", "configs/baselines/best_stable_config.json")
    db_path = ROOT / config["paths"]["db_path"]
    restore_config = config.get("restore", {})

    writer = LiveWriter(runs_dir)
    if reset_stream:
        writer.reset_stream()
    init_db(db_path)
    ensure_report_files(reports_dir)
    seed_static_histories(reports_dir, timestamp)

    task, benchmark = build_task_and_benchmark(
        run_id=run_id,
        workspace_root=workspace_root,
        seed=seed,
        task_type=task_type,
    )
    selected_task_type = task["task_type"]
    writer.write_status(
        _build_status_payload(
            run_id=run_id,
            config_id=config["config_id"],
            runner=config["runner"],
            timestamp=timestamp,
            fitness_mode=config["fitness_mode"],
            run_kind=run_kind,
            task_type=selected_task_type,
            suite_id=suite_id,
            case_id=case_id,
            suite_index=suite_index,
            suite_runs=suite_runs,
            progress_current=progress_current,
            progress_target=progress_target,
            status="preparing",
            task_id=benchmark["id"],
            current_tool=None,
            last_error=None,
            step_count=0,
            max_steps=config["max_steps"],
            elapsed_sec=0,
        )
    )

    snapshot = create_snapshot(workspace_root, snapshot_dir)
    writer.append_event(
        {
            "type": "system",
            "text": f"Sandbox workspace snapshot captured with {snapshot['file_count']} files.",
            "name": "sandbox",
            "run_kind": run_kind,
        }
    )

    runner = build_runner(config)
    runner_result = runner.run(
        task=task,
        live_writer=writer,
        context={
            "root": str(ROOT),
            "run_id": run_id,
            "config_id": config["config_id"],
            "started_at": timestamp,
            "fitness_mode": config["fitness_mode"],
            "run_kind": run_kind,
            "suite_id": suite_id,
            "case_id": case_id,
            "task_type": task.get("task_type"),
            "suite_progress_current": suite_index,
            "suite_progress_target": suite_runs,
            "progress_current": progress_current,
            "progress_target": progress_target,
        },
    )

    verifier_result = verify_task(task=task, runner_result=runner_result, config=config)
    verifier_result["score"] = compute_stotal(verifier_result["subscores"], config["weights"])

    writer.append_event(
        {
            "type": "verifier",
            "name": benchmark.get("verifier", {}).get("entry", "task_verifier"),
            "text": f"passed={verifier_result['passed']} score={verifier_result['score']} tags={','.join(verifier_result['failure_tags']) or 'none'}",
            "run_kind": run_kind,
        }
    )

    restore_result = restore_workspace(
        workspace_root=workspace_root,
        manifest_path=Path(snapshot["manifest_path"]),
        cleanup_after_restore=bool(restore_config.get("cleanup_after_restore", False)),
    )
    writer.append_event(
        {
            "type": "system",
            "name": "workspace_restore",
            "text": (
                f"restore success={restore_result['success']} "
                f"modified={len(restore_result['restored_modified_files'])} "
                f"missing={len(restore_result['restored_missing_files'])} "
                f"removed_new={len(restore_result['removed_new_files'])}"
            ),
            "run_kind": run_kind,
        }
    )
    rollback_safety = compute_rollback_safety(
        config_restore_success=1.0,
        workspace_restore_success=1.0 if restore_result["success"] else 0.0,
        regression_preservation=1.0 if verifier_result["passed"] else 0.5,
    )

    fitness = verifier_result["score"]
    candidate_payload = _build_candidate_payload(
        config=config,
        run_id=run_id,
        timestamp=timestamp,
        score=fitness,
        verifier_result=verifier_result,
        rollback_safety=rollback_safety,
    )
    candidate_payload["source_config_path"] = str(config_path.resolve())

    if manage_baseline:
        baseline_change = maybe_promote_baseline(
            baseline_path,
            candidate_payload,
            gate=config.get("baseline_gate", {}),
        )
    else:
        current = load_baseline(baseline_path)
        baseline_change = {
            "promoted": False,
            "rollback_required": False,
            "reasons": ["managed_externally"],
            "before_config_id": current.get("config_id"),
            "after_config_id": current.get("config_id"),
            "before_fitness": current.get("fitness", 0.0),
            "after_fitness": current.get("fitness", 0.0),
        }

    rollback_reason = "workspace diff restore"
    if baseline_change["promoted"]:
        rollback_reason = "baseline promotion after validated run"
    elif baseline_change.get("rollback_required"):
        rollback_reason = "baseline gate rollback"

    rollback_event = {
        "ts": timestamp,
        "reason": rollback_reason,
        "before_config_id": baseline_change["before_config_id"],
        "after_config_id": baseline_change["after_config_id"],
        "regression_status": "single_run_proxy",
        "baseline_restored": restore_result["success"] and not baseline_change["promoted"],
        "success": restore_result["success"],
        "run_id": run_id,
        "run_kind": run_kind,
        "suite_id": suite_id,
        "case_id": case_id,
        "gate_reasons": baseline_change.get("reasons", []),
        "restore_summary": {
            "modified": len(restore_result["restored_modified_files"]),
            "missing": len(restore_result["restored_missing_files"]),
            "removed_new": len(restore_result["removed_new_files"]),
        },
    }

    if append_rollback_history:
        append_history_entry(reports_dir / "rollback_events.json", rollback_event)

    if append_baseline_history:
        baseline_event = {
            "ts": timestamp,
            "run_id": run_id,
            "config_id": config["config_id"],
            "fitness": fitness,
            "suite_score_c": verifier_result["score"],
            "status": "promoted" if baseline_change["promoted"] else "candidate",
            "run_kind": run_kind,
            "suite_id": suite_id,
            "task_type": task["task_type"],
            "gate_reasons": baseline_change.get("reasons", []),
        }
        append_history_entry(reports_dir / "baseline_history.json", baseline_event)

    if append_score_history:
        score_entry = {
            "ts": timestamp,
            "run_id": run_id,
            "label": run_id[-8:],
            "config_id": config["config_id"],
            "overall": verifier_result["score"],
            "fitness": fitness,
            "fitness_mode": config["fitness_mode"],
            "suite_score_c": verifier_result["score"],
            "passed": verifier_result["passed"],
            "run_kind": run_kind,
            "suite_id": suite_id,
            "case_id": case_id,
            "task_type": task["task_type"],
            "suite_progress_current": suite_index,
            "suite_progress_target": suite_runs,
            "progress_current": progress_current,
            "progress_target": progress_target,
            "honesty_score": verifier_result["subscores"]["honesty"],
        }
        append_history_entry(reports_dir / "score_history.json", score_entry)

    summary = {
        "run_id": run_id,
        "created_at": timestamp,
        "task_id": task["id"],
        "config_id": config["config_id"],
        "status": "passed" if verifier_result["passed"] else "failed",
        "score": verifier_result["score"],
        "fitness": fitness,
        "fitness_mode": config["fitness_mode"],
        "suite_score_c": verifier_result["score"],
        "elapsed_sec": runner_result.elapsed_sec,
        "retries": runner_result.retries,
        "failure_tags": verifier_result["failure_tags"],
        "run_kind": run_kind,
        "suite_id": suite_id,
        "case_id": case_id,
        "task_type": task["task_type"],
        "suite_progress_current": suite_index,
        "suite_progress_target": suite_runs,
        "progress_current": progress_current,
        "progress_target": progress_target,
        "task": task,
        "benchmark": benchmark,
        "runner_result": {
            "final_output": runner_result.final_output,
            "step_count": runner_result.step_count,
            "retries": runner_result.retries,
            "elapsed_sec": runner_result.elapsed_sec,
            "token_estimate": runner_result.token_estimate,
            "tool_trace": runner_result.tool_trace,
            "last_error": runner_result.last_error,
            "metadata": runner_result.metadata,
        },
        "verifier": verifier_result,
        "snapshot": snapshot,
        "rollback": {
            "workspace_restore_success": restore_result["success"],
            "rollback_safety_score": rollback_safety,
            "event": rollback_event,
            "restore_result": restore_result,
        },
        "baseline": baseline_change,
    }
    summary["diagnostics"] = {
        "failure": build_failure_diagnostic(summary),
        "trace": build_trace_diagnostic(summary),
    }

    append_history_entry(reports_dir / "failure_clusters.json", summary["diagnostics"]["failure"])
    append_history_entry(reports_dir / "trace_analysis_history.json", summary["diagnostics"]["trace"])

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    write_json(artifacts_dir / f"{run_id}.json", summary)

    insert_run_summary(
        db_path,
        {
            "run_id": run_id,
            "task_id": task["id"],
            "config_id": config["config_id"],
            "status": summary["status"],
            "score": summary["score"],
            "fitness": summary["fitness"],
            "elapsed_sec": summary["elapsed_sec"],
            "retries": summary["retries"],
            "failure_tags": summary["failure_tags"],
            "created_at": timestamp,
        },
    )

    writer.write_status(
        _build_status_payload(
            run_id=run_id,
            config_id=config["config_id"],
            runner=config["runner"],
            timestamp=timestamp,
            fitness_mode=config["fitness_mode"],
            run_kind=run_kind,
            task_type=task["task_type"],
            suite_id=suite_id,
            case_id=case_id,
            suite_index=suite_index,
            suite_runs=suite_runs,
            progress_current=progress_current,
            progress_target=progress_target,
            status=summary["status"],
            task_id=task["id"],
            current_tool=None,
            last_error=runner_result.last_error,
            step_count=runner_result.step_count,
            max_steps=config["max_steps"],
            elapsed_sec=runner_result.elapsed_sec,
            score=summary["score"],
            fitness=summary["fitness"],
            rollback_time=rollback_event["ts"],
            rollback_reason=rollback_event["reason"],
            rollback_before_config_id=rollback_event["before_config_id"],
            rollback_after_config_id=rollback_event["after_config_id"],
            regression_status=rollback_event["regression_status"],
            baseline_restored=rollback_event["baseline_restored"],
            honesty_score=verifier_result["subscores"]["honesty"],
            rollback_safety_score=rollback_safety,
            sandbox_backend=runner_result.metadata.get("sandbox", {}).get("sandbox_backend")
            if isinstance(runner_result.metadata.get("sandbox"), dict)
            else None,
        )
    )

    baseline_text = "Baseline managed externally."
    if manage_baseline:
        if baseline_change["promoted"]:
            baseline_text = "Baseline promoted."
        elif baseline_change.get("reasons"):
            baseline_text = f"Baseline kept unchanged: {', '.join(baseline_change['reasons'])}."
        else:
            baseline_text = "Baseline kept unchanged."

    writer.append_event(
        {
            "type": "system",
            "name": "baseline_manager",
            "text": baseline_text,
            "run_kind": run_kind,
        }
    )

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a single local Layer C evaluation task.")
    parser.add_argument("--config", default=str(ROOT / "configs" / "experiments" / "default_mvp.json"))
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--task-type", choices=list(TASK_TYPE_CHOICES), default="auto")
    args = parser.parse_args()

    summary = execute_single_run(config_path=Path(args.config), seed=args.seed, task_type=args.task_type)
    print(
        json.dumps(
            {
                "run_id": summary["run_id"],
                "status": summary["status"],
                "task_type": summary["task_type"],
                "score": summary["score"],
                "fitness": summary["fitness"],
                "failure_tags": summary["failure_tags"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
