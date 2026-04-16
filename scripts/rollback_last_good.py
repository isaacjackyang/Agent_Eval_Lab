from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rollback.baseline_manager import restore_baseline_config
from storage.history_writer import append_history_entry, ensure_report_files, seed_static_histories
from storage.live_writer import LiveWriter


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore the latest stable baseline config into an experiment config path.")
    parser.add_argument("--baseline", default=str(ROOT / "configs" / "baselines" / "best_stable_config.json"))
    parser.add_argument("--target", default=str(ROOT / "configs" / "experiments" / "default_mvp.json"))
    args = parser.parse_args()

    timestamp = datetime.now().isoformat(timespec="seconds")
    baseline_path = Path(args.baseline) if Path(args.baseline).is_absolute() else (ROOT / args.baseline)
    target_path = Path(args.target) if Path(args.target).is_absolute() else (ROOT / args.target)
    reports_dir = ROOT / "reports"
    runs_dir = ROOT / "runs"

    ensure_report_files(reports_dir)
    seed_static_histories(reports_dir, timestamp)

    restored = restore_baseline_config(baseline_path=baseline_path, target_config_path=target_path)
    event = {
        "ts": timestamp,
        "reason": "manual rollback_last_good restore",
        "before_config_id": restored["baseline"].get("config_id"),
        "after_config_id": restored["baseline"].get("config_id"),
        "regression_status": "preserved_from_last_good_baseline",
        "baseline_restored": True,
        "success": True,
        "run_kind": "manual_rollback",
        "run_id": f"rollback_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "suite_id": None,
        "gate_reasons": ["manual_restore"],
    }
    append_history_entry(reports_dir / "rollback_events.json", event)
    append_history_entry(
        reports_dir / "config_history.json",
        {
            "ts": timestamp,
            "suite_id": None,
            "config_id": restored["baseline"].get("config_id"),
            "event": "manual_restore",
            "mutation_profile": "baseline_restore",
            "fitness": restored["baseline"].get("fitness"),
            "config_path": restored["target_path"],
            "archive_path": restored["archive_path"],
            "gate_reasons": ["manual_restore"],
        },
    )

    writer = LiveWriter(runs_dir)
    writer.write_status(
        {
            "run_id": event["run_id"],
            "status": "baseline_restored",
            "task_id": "rollback_last_good",
            "config_id": restored["baseline"].get("config_id"),
            "runner": "manual_restore",
            "current_tool": None,
            "last_error": None,
            "step_count": 1,
            "max_steps": 1,
            "elapsed_sec": 0,
            "updated_at": timestamp,
            "fitness": restored["baseline"].get("fitness"),
            "fitness_mode": "manual_restore",
            "rollback_time": timestamp,
            "rollback_reason": event["reason"],
            "rollback_before_config_id": restored["baseline"].get("config_id"),
            "rollback_after_config_id": restored["baseline"].get("config_id"),
            "regression_status": event["regression_status"],
            "baseline_restored": True,
            "run_kind": "manual_rollback",
        }
    )
    writer.append_event(
        {
            "type": "system",
            "name": "rollback",
            "text": f"Restored baseline config {restored['baseline'].get('config_id')} into {restored['target_path']}.",
        }
    )

    print(
        json.dumps(
            {
                "status": "baseline_restored",
                "config_id": restored["baseline"].get("config_id"),
                "target_path": restored["target_path"],
                "archive_path": restored["archive_path"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
