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
        self._event_seq = self._load_existing_event_seq()

    def _load_existing_event_seq(self) -> int:
        if not self.stream_path.exists():
            return 0
        try:
            lines = [line for line in self.stream_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except Exception:
            return 0
        for line in reversed(lines):
            try:
                payload = json.loads(line)
            except Exception:
                continue
            value = payload.get("event_seq")
            if isinstance(value, int):
                return value
        return 0

    def _read_status_context(self) -> dict:
        if not self.status_path.exists():
            return {}
        try:
            payload = json.loads(self.status_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def reset_stream(self) -> None:
        self.stream_path.write_text("", encoding="utf-8")
        self._event_seq = 0

    def write_status(self, payload: dict) -> None:
        data = dict(payload)
        data.setdefault("updated_at", datetime.now().isoformat(timespec="seconds"))
        self.status_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def append_event(self, payload: dict) -> None:
        data = dict(payload)
        context = self._read_status_context()
        data.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
        for key in ("run_id", "suite_id", "case_id", "run_kind", "task_type", "config_id"):
            if data.get(key) is None and context.get(key) is not None:
                data[key] = context.get(key)
        self._event_seq = max(self._event_seq + 1, int(data.get("event_seq", 0) or 0))
        data.setdefault("schema_version", "ael.live.v1")
        data.setdefault("stream", "live")
        data.setdefault("channel", data.get("type", "system"))
        data.setdefault("event_seq", self._event_seq)
        stream_run_id = data.get("run_id") or data.get("suite_id") or "stream"
        data.setdefault("event_id", f"{stream_run_id}:{self._event_seq:06d}")
        data.setdefault(
            "replay_key",
            f"{data.get('run_kind') or 'run'}:{data.get('suite_id') or data.get('run_id') or 'standalone'}:{data.get('case_id') or 'all'}",
        )
        append_jsonl(self.stream_path, data)
