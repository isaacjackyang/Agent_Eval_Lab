from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from storage.jsonl import append_jsonl


class LiveWriter:
    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = runs_dir
        self.status_path = runs_dir / "live_status.json"
        self.stream_path = runs_dir / "live_stream.jsonl"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def reset_stream(self) -> None:
        self.stream_path.write_text("", encoding="utf-8")

    def write_status(self, payload: dict) -> None:
        data = dict(payload)
        data.setdefault("updated_at", datetime.now().isoformat(timespec="seconds"))
        self.status_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def append_event(self, payload: dict) -> None:
        data = dict(payload)
        data.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
        append_jsonl(self.stream_path, data)
