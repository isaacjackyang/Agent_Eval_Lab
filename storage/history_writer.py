from __future__ import annotations

import json
from pathlib import Path


REPORT_FILES = [
    "score_history.json",
    "architecture_history.json",
    "test_method_history.json",
    "baseline_history.json",
    "rollback_events.json",
    "nightly_history.json",
    "heat_map_history.json",
    "heat_map_verification_history.json",
    "relayer_scan_history.json",
    "config_history.json",
    "parameter_history.json",
]


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_report_files(reports_dir: Path) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    for file_name in REPORT_FILES:
        path = reports_dir / file_name
        if not path.exists():
            write_json(path, {"history": []})


def load_history(path: Path) -> dict:
    if not path.exists():
        return {"history": []}
    return json.loads(path.read_text(encoding="utf-8"))


def append_history_entry(path: Path, entry: dict, limit: int = 200) -> None:
    payload = load_history(path)
    history = payload.setdefault("history", [])
    history.append(entry)
    payload["history"] = history[-limit:]
    write_json(path, payload)


def seed_static_histories(reports_dir: Path, timestamp: str) -> None:
    architecture_path = reports_dir / "architecture_history.json"
    test_path = reports_dir / "test_method_history.json"

    if not load_history(architecture_path)["history"]:
        append_history_entry(
            architecture_path,
            {
                "ts": timestamp,
                "summary": "MVP 已落地：Python orchestrator、Layer C 動態任務、verifier、SQLite/JSONL storage、單檔 dashboard。",
            },
        )

    if not load_history(test_path)["history"]:
        append_history_entry(
            test_path,
            {
                "ts": timestamp,
                "summary": "目前測試方式採用動態 file retrieval sandbox，透過決定性 verifier 檢查輸出路徑與工具序列。",
            },
        )
