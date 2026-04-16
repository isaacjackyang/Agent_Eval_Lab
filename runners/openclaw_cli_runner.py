from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from runners.base import BaseRunner, RunnerResult
from sandbox.openclaw_agent_runtime import OpenClawRuntime
from storage.jsonish import load_jsonish_text


class OpenClawCliRunner(BaseRunner):
    name = "openclaw_cli"

    def run(self, task: dict, live_writer, context: dict) -> RunnerResult:
        started = time.perf_counter()
        runtime = OpenClawRuntime(
            root=Path(context["root"]),
            config=self.runner_config,
            run_id=context["run_id"],
        )
        token_estimate = 0
        status_context = {
            "run_kind": context.get("run_kind"),
            "suite_id": context.get("suite_id"),
            "case_id": context.get("case_id"),
            "task_type": task.get("task_type") or context.get("task_type"),
            "suite_progress_current": context.get("suite_progress_current"),
            "suite_progress_target": context.get("suite_progress_target"),
            "progress_current": context.get("progress_current"),
            "progress_target": context.get("progress_target"),
        }
        if (
            status_context["suite_progress_current"] is not None
            and status_context["suite_progress_target"] is not None
        ):
            status_context["suite_progress_text"] = (
                f"{status_context['suite_progress_current']}/{status_context['suite_progress_target']}"
            )
        if (
            status_context["progress_current"] is not None
            and status_context["progress_target"] is not None
        ):
            status_context["progress_text"] = (
                f"{status_context['progress_current']}/{status_context['progress_target']}"
            )

        def emit(event_type: str, text: str, **extra) -> None:
            nonlocal token_estimate
            token_estimate += max(1, len(text) // 4)
            payload = {"type": event_type, "text": text, **extra}
            live_writer.append_event(payload)

        emit("system", f"Preparing OpenClaw agent {runtime.agent_id}.", name="openclaw")
        sandbox_metadata = runtime.prepare(Path(task["workspace_root"]))
        live_writer.write_status(
            {
                "run_id": context["run_id"],
                "status": "running_openclaw",
                "task_id": task["id"],
                "config_id": context["config_id"],
                "runner": self.name,
                "current_tool": "openclaw_agent",
                "last_error": None,
                "step_count": 0,
                "max_steps": self.max_steps,
                "elapsed_sec": round(time.perf_counter() - started, 3),
                "updated_at": context["started_at"],
                "fitness_mode": context["fitness_mode"],
                **status_context,
            }
        )
        if sandbox_metadata.get("sandbox_info"):
            emit("system", "OpenClaw sandbox metadata loaded.", name="sandbox")

        prompt = task["prompt"]
        emit("assistant", f"轉交 OpenClaw：{prompt}")
        completed = runtime.run_agent(prompt)
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        payload = self._parse_payload(stdout)

        last_error = None
        if completed.returncode != 0:
            last_error = self._extract_error(payload) or stderr or f"openclaw exit={completed.returncode}"

        final_output = self._extract_output_text(payload) if payload else ""
        tool_trace = self._extract_tool_trace(payload)
        step_count = self._extract_step_count(payload, tool_trace)
        if not final_output and last_error:
            final_output = last_error

        self._emit_trace(payload, emit)

        if stderr:
            emit("system", stderr, name="openclaw_stderr")

        runtime.cleanup()

        elapsed_sec = round(time.perf_counter() - started, 3)
        usage_tokens = self._extract_token_estimate(payload)
        live_writer.write_status(
            {
                "run_id": context["run_id"],
                "status": "runner_finished",
                "task_id": task["id"],
                "config_id": context["config_id"],
                "runner": self.name,
                "current_tool": None,
                "last_error": last_error,
                "step_count": step_count,
                "max_steps": self.max_steps,
                "elapsed_sec": elapsed_sec,
                "updated_at": context["started_at"],
                "fitness_mode": context["fitness_mode"],
                **status_context,
            }
        )

        return RunnerResult(
            final_output=final_output,
            step_count=step_count,
            retries=0,
            elapsed_sec=elapsed_sec,
            current_tool=None,
            last_error=last_error,
            token_estimate=max(token_estimate, usage_tokens),
            tool_trace=tool_trace,
            metadata={
                "adapter": "openclaw_cli",
                "command": runtime.command_prefix,
                "agent_id": runtime.agent_id,
                "sandbox": sandbox_metadata,
                "raw_stdout": stdout,
                "raw_stderr": stderr,
            },
        )

    def _parse_payload(self, stdout: str) -> dict | None:
        if not stdout:
            return None

        try:
            return load_jsonish_text(stdout)
        except Exception:
            pass

        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        for line in reversed(lines):
            try:
                return load_jsonish_text(line)
            except Exception:
                continue
        return {"raw": stdout}

    def _extract_output_text(self, payload: dict | None) -> str:
        if not payload:
            return ""

        direct_keys = ["output_text", "final_output", "message", "text"]
        for key in direct_keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        result = payload.get("result")
        if isinstance(result, str) and result.strip():
            return result.strip()
        if isinstance(result, dict):
            for key in direct_keys:
                value = result.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            content = result.get("content")
            text = self._extract_content_text(content)
            if text:
                return text

        content = payload.get("content")
        text = self._extract_content_text(content)
        if text:
            return text

        return ""

    def _extract_content_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item.strip())
                elif isinstance(item, dict):
                    if item.get("type") == "text" and isinstance(item.get("text"), str):
                        parts.append(item["text"].strip())
                    elif isinstance(item.get("content"), str):
                        parts.append(item["content"].strip())
            return "\n".join(part for part in parts if part)
        return ""

    def _extract_error(self, payload: dict | None) -> str | None:
        if not payload:
            return None
        for key in ("error", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        if isinstance(payload.get("result"), dict):
            value = payload["result"].get("error")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _extract_tool_trace(self, payload: dict | None) -> list[dict]:
        events = []
        if not payload:
            return events

        candidates = [
            payload.get("trace"),
            payload.get("events"),
            payload.get("result", {}).get("trace") if isinstance(payload.get("result"), dict) else None,
            payload.get("result", {}).get("events") if isinstance(payload.get("result"), dict) else None,
        ]
        raw_events = next((item for item in candidates if isinstance(item, list)), [])

        order = 0
        pending_results: dict[str, Any] = {}
        for item in raw_events:
            if not isinstance(item, dict):
                continue
            event_type = item.get("type")
            if event_type == "tool_result":
                name = item.get("name") or item.get("tool")
                pending_results[name] = item.get("text") or item.get("result")
                continue
            if event_type != "tool_call":
                continue
            order += 1
            name = item.get("name") or item.get("tool") or "unknown_tool"
            trace_item = {
                "order": order,
                "tool": name,
                "args": item.get("args") or item.get("arguments") or {},
                "ok": True,
            }
            if name in pending_results:
                trace_item["result"] = pending_results[name]
            events.append(trace_item)
        return events

    def _extract_step_count(self, payload: dict | None, tool_trace: list[dict]) -> int:
        if payload:
            metrics = payload.get("metrics") or payload.get("usage") or {}
            for key in ("step_count", "steps", "stepCount"):
                value = metrics.get(key) if isinstance(metrics, dict) else None
                if isinstance(value, int):
                    return value
            if isinstance(payload.get("step_count"), int):
                return payload["step_count"]
        return max(1, len(tool_trace))

    def _extract_token_estimate(self, payload: dict | None) -> int:
        if not payload:
            return 0
        candidates = [
            payload.get("usage"),
            payload.get("metrics"),
            payload.get("result", {}).get("usage") if isinstance(payload.get("result"), dict) else None,
        ]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for key in ("total_tokens", "totalTokens", "tokens", "token_count", "tokenCount"):
                value = candidate.get(key)
                if isinstance(value, int):
                    return value
        return 0

    def _emit_trace(self, payload: dict | None, emit) -> None:
        if not payload:
            return

        candidates = [
            payload.get("trace"),
            payload.get("events"),
            payload.get("result", {}).get("trace") if isinstance(payload.get("result"), dict) else None,
            payload.get("result", {}).get("events") if isinstance(payload.get("result"), dict) else None,
        ]
        trace = next((item for item in candidates if isinstance(item, list)), [])
        if trace:
            for item in trace:
                if not isinstance(item, dict):
                    continue
                event_type = item.get("type", "system")
                if event_type not in {"assistant", "tool_call", "tool_result", "verifier", "system"}:
                    event_type = "system"
                text = item.get("text") or item.get("message") or json.dumps(item, ensure_ascii=False)
                emit(event_type, text, name=item.get("name") or item.get("tool"))
            return

        output_text = self._extract_output_text(payload)
        if output_text:
            emit("assistant", output_text)
