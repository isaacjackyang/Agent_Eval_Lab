from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    baseline = _load_json(ROOT / "configs" / "baselines" / "best_stable_config.json")
    nightly = _load_json(ROOT / "reports" / "nightly_history.json").get("history", [])
    score_history = _load_json(ROOT / "reports" / "score_history.json").get("history", [])

    latest_nightly = nightly[-1] if nightly else {}
    latest_score = score_history[-1] if score_history else {}

    print(
        json.dumps(
            {
                "baseline_config_id": baseline.get("config_id"),
                "baseline_fitness": baseline.get("fitness"),
                "baseline_archive_path": baseline.get("config_archive_path"),
                "latest_nightly_status": latest_nightly.get("status"),
                "latest_nightly_config_id": latest_nightly.get("config_id"),
                "latest_nightly_fitness": latest_nightly.get("fitness"),
                "latest_score_run_id": latest_score.get("run_id"),
                "latest_score_fitness": latest_score.get("fitness"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
