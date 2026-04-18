from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evolution.relayer_plan import summarize_relayer_config
from evolution.relayer_scan import estimate_relayer_scan_total_evals, run_relayer_scan
from storage.history_writer import ensure_report_files
from storage.live_writer import LiveWriter


def _load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return payload


def _resolve_resume_run_id(output_dir: Path, fallback: str) -> str:
    for candidate in (output_dir / "manifest.json", output_dir / "resume_state.json"):
        if not candidate.exists():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict) and payload.get("run_id"):
            return str(payload["run_id"])
    return fallback


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a synthetic relayer scan with the mock_layer_stack backend.")
    parser.add_argument("--config", default=str(ROOT / "configs" / "experiments" / "local_llama_cpp_agent.json"))
    parser.add_argument("--output-dir", default=None, help="Defaults to reports/relayer_scans/<run_id> after creation.")
    parser.add_argument("--max-candidates", type=int, default=None, help="Limit the number of scanned candidates for smoke tests.")
    parser.add_argument("--seed-start", type=int, default=500, help="Seed offset used by relayer top-k verification.")
    parser.add_argument("--resume", action="store_true", help="Resume from an existing relayer scan output directory.")
    parser.add_argument("--skip-completed", action="store_true", help="Skip candidate cells already stored under output-dir/cells.")
    parser.add_argument("--max-workers", type=int, default=1, help="Number of synthetic scan workers to run in parallel.")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config = _load_json(config_path)
    reports_dir = ROOT / config["paths"]["reports_dir"]
    ensure_report_files(reports_dir)
    runs_dir = ROOT / "runs"
    live_writer = LiveWriter(runs_dir)
    live_writer.reset_stream()
    baseline_path = ROOT / config["paths"].get("baseline_path", "configs/baselines/best_stable_config.json")

    if args.resume and not args.output_dir:
        raise RuntimeError("--resume requires --output-dir so the existing scan directory can be reused.")

    run_id = datetime.now().strftime("relayer_scan_%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir).resolve() if args.output_dir else (ROOT / "reports" / "relayer_scans" / run_id)
    if args.resume or args.skip_completed:
        run_id = _resolve_resume_run_id(output_dir, run_id)
    relayer_summary = summarize_relayer_config(config)
    candidate_target = int(relayer_summary.get("scan_candidate_count") or 0)
    if args.max_candidates is not None:
        candidate_target = min(candidate_target, int(args.max_candidates))
    progress_target_total = estimate_relayer_scan_total_evals(
        config,
        candidate_target,
        seed_start=args.seed_start,
        base_config_path=config_path,
    ) or candidate_target
    started = time.perf_counter()

    def write_status(status: str, *, progress_current: int, progress_target: int, current_candidate: str | None = None, best_config_id: str | None = None, baseline_score: float | None = None) -> None:
        progress_text = f"{progress_current}/{progress_target}" if progress_target else "0/0"
        live_writer.write_status(
            {
                "run_id": run_id,
                "status": status,
                "task_id": current_candidate,
                "config_id": config.get("config_id"),
                "runner": "relayer_scan",
                "current_tool": "mock_layer_stack",
                "last_error": None,
                "step_count": progress_current,
                "max_steps": progress_target or 1,
                "elapsed_sec": round(time.perf_counter() - started, 3),
                "fitness_mode": "synthetic_relayer_scan",
                "progress_current": progress_current,
                "progress_target": progress_target,
                "progress_text": progress_text,
                "run_kind": "relayer_scan",
                "suite_id": run_id,
                "case_id": None,
                "relayer_mode": relayer_summary.get("mode"),
                "relayer_applied": False,
                "relayer_scan_backend": relayer_summary.get("scan_backend"),
                "relayer_runtime_patch_supported": relayer_summary.get("runtime_patch_supported"),
                "best_config_id": best_config_id,
                "baseline_score": baseline_score,
            }
        )

    live_writer.append_event(
        {
            "type": "system",
            "name": "relayer_scan",
            "text": f"Preparing relayer scan from {config_path.name}.",
        }
    )
    write_status("starting_relayer_scan", progress_current=0, progress_target=progress_target_total)

    def on_progress(payload: dict[str, object]) -> None:
        write_status(
            str(payload.get("status", "running_relayer_scan")),
            progress_current=int(payload.get("progress_current", 0) or 0),
            progress_target=int(payload.get("progress_target", progress_target_total) or 0),
            current_candidate=str(payload.get("current_candidate")) if payload.get("current_candidate") else None,
            best_config_id=str(payload.get("best_config_id")) if payload.get("best_config_id") else None,
            baseline_score=float(payload.get("baseline_score")) if payload.get("baseline_score") is not None else None,
        )

    def on_event(payload: dict[str, object]) -> None:
        live_writer.append_event(dict(payload))

    result = run_relayer_scan(
        base_config_path=config_path,
        base_config=config,
        output_dir=output_dir,
        reports_dir=reports_dir,
        baseline_path=baseline_path,
        max_candidates=args.max_candidates,
        run_id=run_id,
        seed_start=args.seed_start,
        resume=args.resume,
        skip_completed=args.skip_completed,
        max_workers=args.max_workers,
        progress_callback=on_progress,
        event_callback=on_event,
    )
    result["manifest"]["output_dir"] = str(output_dir.resolve())
    write_status(
        "completed",
        progress_current=int(result.get("progress_current", len(result["candidate_results"]))),
        progress_target=int(result.get("progress_target", progress_target_total)),
        best_config_id=result.get("baseline_decision", {}).get("winner_config_id")
        or result["summary"].get("best_cell", {}).get("config_id"),
        baseline_score=result["baseline"].get("raw_score"),
    )

    print(json.dumps(result["manifest"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
