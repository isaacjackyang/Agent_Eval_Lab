from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from evolution.mutator import effective_agent_architecture
from evolution.relayer_plan import relayer_supported_modes_for_runner, resolve_relayer_runtime_context
from runners.base import BaseRunner, RunnerResult
from runners.relayer_runtime_bridge import (
    extract_runtime_backend_effects,
    invoke_external_relayer_runtime,
    summarize_runtime_backend_effects,
)
from sandbox.openclaw_agent_runtime import OpenClawRuntime
from storage.jsonish import load_jsonish_text


class OpenClawCliRunner(BaseRunner):
    name = "openclaw_cli"

    def run(self, task: dict, live_writer, context: dict) -> RunnerResult:
        started = time.perf_counter()
        task_category = str(task.get("category", "")).strip().lower()
        architecture = effective_agent_architecture(self.runner_config)
        relayer_supported_modes = relayer_supported_modes_for_runner(self.name, config=self.runner_config)
        relayer_context = resolve_relayer_runtime_context(
            self.runner_config,
            runtime_patch_supported="runtime_patch" in relayer_supported_modes,
            runtime_label=self.name,
            supported_modes=relayer_supported_modes,
        )
        runtime = OpenClawRuntime(
            root=Path(context["root"]),
            config=self.runner_config,
            run_id=context["run_id"],
        )
        token_estimate = 0
        relayer_runtime_backend: dict[str, Any] | None = None
        relayer_runtime_effects: dict[str, Any] = {}
        sandbox_metadata: dict[str, Any] = {}
        cleanup_summary: dict[str, Any] = {}
        stdout = ""
        stderr = ""
        payload: dict[str, Any] | None = None
        last_error: str | None = None
        final_output = ""
        tool_trace: list[dict[str, Any]] = []
        step_count = 0
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
            event_payload = {"type": event_type, "text": text, **extra}
            live_writer.append_event(event_payload)

        emit("system", f"Preparing OpenClaw agent {runtime.agent_id}.", name="openclaw")
        try:
            if relayer_context["enabled"]:
                emit("system", relayer_context["message"], name="relayer")
            if relayer_context["applied"] and relayer_context["mode"] == "runtime_patch":
                relayer_runtime_backend = invoke_external_relayer_runtime(
                    root=Path(context["root"]),
                    run_id=context["run_id"],
                    runtime_label=self.name,
                    config=self.runner_config,
                    relayer_context=relayer_context,
                )
                emit(
                    "system",
                    f"External relayer runtime backend prepared via {relayer_runtime_backend['manifest_path']}.",
                    name="relayer_runtime",
                )
                relayer_runtime_effects = extract_runtime_backend_effects(relayer_runtime_backend)
                if relayer_runtime_effects:
                    runtime.apply_runtime_effects(relayer_runtime_effects)
                    summary = summarize_runtime_backend_effects(relayer_runtime_effects)
                    if summary:
                        emit(
                            "system",
                            f"Applied relayer runtime effects: {summary}.",
                            name="relayer_runtime_effects",
                        )

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
                    "architecture_variant": architecture.get("variant"),
                    "relayer_mode": relayer_context["mode"],
                    "relayer_applied": relayer_context["applied"],
                    "relayer_range": relayer_context.get("range_text"),
                    **status_context,
                }
            )

            smoke_test = sandbox_metadata.get("smoke_test")
            if isinstance(smoke_test, dict) and smoke_test.get("ok"):
                emit(
                    "system",
                    f"OpenClaw smoke test passed via {smoke_test.get('selected_check')}.",
                    name="openclaw_smoke",
                )
            if sandbox_metadata.get("sandbox_info"):
                emit("system", "OpenClaw sandbox metadata loaded.", name="sandbox")

            prompt = self._build_prompt(task=task, architecture=architecture)
            prompt = self._apply_runtime_prompt_effects(prompt, relayer_runtime_effects.get("prompt"))
            emit(
                "system",
                (
                    f"Using OpenClaw architecture={architecture.get('variant')} "
                    f"prompt_style={architecture.get('prompt_style')} "
                    f"query_policy={architecture.get('query_policy')} "
                    f"recovery_policy={architecture.get('recovery_policy')} "
                    f"search_limit={architecture.get('search_result_limit')}"
                ),
                name="openclaw_architecture",
            )

            emit("system", "Sending task prompt to OpenClaw agent.", name="openclaw")
            completed = runtime.run_agent(prompt)
            stdout = completed.stdout.strip()
            stderr = completed.stderr.strip()
            payload = self._parse_payload(stdout)

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
        except Exception as exc:
            last_error = str(exc)
            final_output = final_output or last_error
            emit("system", last_error, name="openclaw_runtime_error")
        finally:
            cleanup_summary = runtime.cleanup()
            if cleanup_summary and not cleanup_summary.get("success", True):
                cleanup_error = "; ".join(cleanup_summary.get("errors", [])) or "OpenClaw cleanup failed."
                emit("system", cleanup_error, name="openclaw_cleanup")
                if not last_error:
                    last_error = cleanup_error
                    final_output = final_output or cleanup_error

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
                "architecture": architecture,
                "task_category": task_category,
                "relayer": relayer_context,
                "relayer_runtime_backend": relayer_runtime_backend,
                "relayer_runtime_effects": relayer_runtime_effects,
                "sandbox": sandbox_metadata,
                "runtime": runtime.describe_runtime(),
                "cleanup": cleanup_summary,
                "raw_stdout": stdout,
                "raw_stderr": stderr,
            },
        )

    def _apply_runtime_prompt_effects(
        self,
        prompt: str,
        prompt_effects: dict[str, Any] | None,
    ) -> str:
        if not isinstance(prompt_effects, dict):
            return prompt
        result = str(prompt)
        prefix = str(prompt_effects.get("task_prefix") or prompt_effects.get("user_prefix") or "").strip()
        suffix = str(prompt_effects.get("task_suffix") or prompt_effects.get("user_suffix") or "").strip()
        if prefix:
            result = f"{prefix}\n{result}"
        if suffix:
            result = f"{result}\n{suffix}"
        return result

    def _build_prompt(self, *, task: dict[str, Any], architecture: dict[str, Any]) -> str:
        prompt_style = str(architecture.get("prompt_style", "strict_json"))
        query_policy = str(architecture.get("query_policy", "focused_then_broad"))
        recovery_policy = str(architecture.get("recovery_policy", "ranked"))
        search_result_limit = int(architecture.get("search_result_limit", 5))

        if str(task.get("category", "")).strip().lower() == "math_reasoning":
            answer_kind = str(task.get("metadata", {}).get("answer_kind", "integer"))
            format_hint = (
                'Return the final order as {"type":"final","answer":"Name1 > Name2 > Name3 > Name4"}.'
                if answer_kind == "ranking"
                else 'Return the final integer as {"type":"final","answer":"42"}.'
            )
            instructions = [
                "You are operating through OpenClaw inside the local evaluation sandbox.",
                "This is a math and reasoning benchmark, not a retrieval task.",
                "Do not search for files or paths.",
                "Solve the task directly and return only the final answer.",
                format_hint,
            ]
            return "\n".join(instructions) + "\n\nTask:\n" + str(task["prompt"])

        style_guidance = {
            "planner": "Plan briefly before searching so the final answer is deliberate and easy to verify.",
            "recall": "Lean on retrieved evidence and keep the response grounded in the workspace contents.",
            "strict_json": "Return a compact, machine-friendly answer and avoid unrelated prose.",
        }
        query_guidance = {
            "focused_only": "Start with the most specific query you can infer from the task and avoid broad search unless needed.",
            "focused_then_broad": "Start focused, then broaden only if the first search pass is weak or ambiguous.",
            "broad_then_focused": "Start broad to map the space, then narrow to the strongest candidate.",
        }
        recovery_guidance = {
            "none": "Do not keep exploring once you have a confident answer.",
            "ranked": "If the first candidate is weak, rank alternates and verify the strongest one before answering.",
            "signal_boost": "Prefer canonical, verified, release, delivery, or operations artifacts when multiple matches exist.",
        }

        instructions = [
            "You are operating through OpenClaw inside the local evaluation sandbox.",
            style_guidance.get(prompt_style, style_guidance["strict_json"]),
            query_guidance.get(query_policy, query_guidance["focused_then_broad"]),
            recovery_guidance.get(recovery_policy, recovery_guidance["ranked"]),
            f"Keep the search result shortlist at or below {search_result_limit} strong candidates.",
            "Return the final resolved file path when you identify the canonical answer.",
        ]
        return "\n".join(instructions) + "\n\nTask:\n" + str(task["prompt"])

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

        direct_keys = ["output_text", "final_output", "answer", "message", "text"]
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
        return max(1, len(tool_trace)) if tool_trace else 0

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
