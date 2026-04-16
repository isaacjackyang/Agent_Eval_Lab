from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from storage.jsonl import append_jsonl


TRACE_SCHEMA_VERSION = "raw_trace_v1"


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _to_json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value.resolve())
    if isinstance(value, dict):
        return {str(key): _to_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json_safe(item) for item in value]
    if isinstance(value, set):
        return sorted(_to_json_safe(item) for item in value)
    return value


class RawTraceWriter:
    def __init__(self, runs_dir: Path, run_id: str, trace_context: dict | None = None) -> None:
        self.runs_dir = runs_dir
        self.run_id = run_id
        self.trace_context = _to_json_safe(trace_context or {})
        self.trace_dir = runs_dir / "traces"
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.trace_dir / f"{run_id}.jsonl"
        self.trace_path.write_text("", encoding="utf-8")
        self._counter = 0

    @property
    def event_count(self) -> int:
        return self._counter

    def append(
        self,
        *,
        stage: str,
        event_type: str,
        summary: str,
        parent_event_id: str | None = None,
        correlation_id: str | None = None,
        ts_start: str | None = None,
        ts_end: str | None = None,
        latency_ms: float | None = None,
        tool_name: str | None = None,
        tool_args_raw: Any = None,
        tool_result_raw: Any = None,
        model_request_raw: Any = None,
        model_response_raw: Any = None,
        sandbox_state: Any = None,
        intermediate_state: Any = None,
        metrics: dict | None = None,
        status: str | None = None,
        extra: dict | None = None,
    ) -> dict:
        self._counter += 1
        event_id = f"evt_{self._counter:06d}"
        event_ts_end = ts_end or _iso_now()
        event_ts_start = ts_start or event_ts_end
        metric_payload = _to_json_safe(metrics or {})

        if latency_ms is None and event_ts_start and event_ts_end and event_ts_start == event_ts_end:
            latency_ms = 0.0

        payload = {
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "event_id": event_id,
            "parent_event_id": parent_event_id,
            "correlation_id": correlation_id or self.run_id,
            "stage": stage,
            "event_type": event_type,
            "summary": summary,
            "ts_start": event_ts_start,
            "ts_end": event_ts_end,
            "latency_ms": round(float(latency_ms), 3) if latency_ms is not None else None,
            "status": status,
            "tool_name": tool_name,
            "tool_args_raw": _to_json_safe(tool_args_raw),
            "tool_result_raw": _to_json_safe(tool_result_raw),
            "model_request_raw": _to_json_safe(model_request_raw),
            "model_response_raw": _to_json_safe(model_response_raw),
            "sandbox_state": _to_json_safe(sandbox_state),
            "intermediate_state": _to_json_safe(intermediate_state),
            "metrics": {
                "token_in": metric_payload.get("token_in"),
                "token_out": metric_payload.get("token_out"),
                "token_total": metric_payload.get("token_total"),
                "token_per_sec": metric_payload.get("token_per_sec"),
                "step_count": metric_payload.get("step_count"),
                "retries": metric_payload.get("retries"),
                "elapsed_sec": metric_payload.get("elapsed_sec"),
            },
        }

        payload.update(self.trace_context)
        if extra:
            payload.update(_to_json_safe(extra))

        append_jsonl(self.trace_path, payload)
        return payload

    def record_runtime_event(
        self,
        payload: dict,
        *,
        stage: str = "runner",
        parent_event_id: str | None = None,
        correlation_id: str | None = None,
        tool_name: str | None = None,
        tool_args_raw: Any = None,
        tool_result_raw: Any = None,
        model_request_raw: Any = None,
        model_response_raw: Any = None,
        sandbox_state: Any = None,
        intermediate_state: Any = None,
        metrics: dict | None = None,
        status: str | None = None,
        latency_ms: float | None = None,
    ) -> dict:
        event_type = str(payload.get("type") or "system")
        summary = str(payload.get("text") or payload.get("name") or event_type)
        return self.append(
            stage=stage,
            event_type=event_type,
            summary=summary,
            parent_event_id=parent_event_id,
            correlation_id=correlation_id,
            latency_ms=latency_ms,
            tool_name=tool_name or payload.get("name") or payload.get("tool"),
            tool_args_raw=tool_args_raw,
            tool_result_raw=tool_result_raw,
            model_request_raw=model_request_raw,
            model_response_raw=model_response_raw,
            sandbox_state=sandbox_state,
            intermediate_state=intermediate_state or payload,
            metrics=metrics,
            status=status,
        )

    def as_manifest(self) -> dict:
        return {
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "trace_path": str(self.trace_path.resolve()),
            "event_count": self.event_count,
        }


def load_raw_trace(path: Path) -> list[dict]:
    if not path.exists():
        return []
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        events.append(json.loads(line))
    return events
