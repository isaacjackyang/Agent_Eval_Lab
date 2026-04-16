from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from run_single import ROOT, TASK_TYPE_CHOICES, execute_single_run
from scoring.aggregation import compute_stability_score, compute_suite_score
from storage.history_writer import write_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a batch of local Layer C evaluation tasks.")
    parser.add_argument("--config", default=str(ROOT / "configs" / "experiments" / "default_mvp.json"))
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--seed-start", type=int, default=None)
    parser.add_argument("--task-type", choices=list(TASK_TYPE_CHOICES), default="auto")
    args = parser.parse_args()

    suite_id = datetime.now().strftime("suite_%Y%m%d_%H%M%S")
    results = []
    for index in range(args.runs):
        seed = args.seed_start + index if args.seed_start is not None else None
        results.append(
            execute_single_run(
                config_path=Path(args.config),
                seed=seed,
                task_type=args.task_type,
                append_score_history=True,
                append_baseline_history=False,
                append_rollback_history=False,
                manage_baseline=False,
                reset_stream=index == 0,
                run_kind="suite_candidate",
                suite_id=suite_id,
                case_id=f"suite_{index + 1:02d}",
                suite_index=index + 1,
                suite_runs=args.runs,
                progress_current=index + 1,
                progress_target=args.runs,
            )
        )

    scores = [item["score"] for item in results]
    fitnesses = [item["fitness"] for item in results]
    summary = {
        "suite_id": suite_id,
        "runs": args.runs,
        "suite_score_c": compute_suite_score(scores),
        "fitness_proxy": compute_suite_score(fitnesses),
        "stability_score": compute_stability_score(scores),
        "pass_rate": round(sum(1 for item in results if item["status"] == "passed") / max(1, len(results)), 4),
        "run_ids": [item["run_id"] for item in results],
        "task_type": args.task_type,
    }

    artifact_path = ROOT / "runs" / "artifacts" / f"{summary['suite_id']}.json"
    write_json(artifact_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
