from __future__ import annotations

import time
from pathlib import Path

from evolution.relayer_plan import RelayerPlan, resolve_relayer_runtime_context
from runners.base import BaseRunner, RunnerResult
from runners.relayer_runner import RecordingLayerBackend, RelayerRunner
from runners.search_helpers import pick_best_match, search_file


class SessionRunner(BaseRunner):
    name = "session_mock"

    def run(self, task: dict, live_writer, context: dict) -> RunnerResult:
        workspace_root = Path(task["workspace_root"])
        started = time.perf_counter()
        relayer_context = resolve_relayer_runtime_context(
            self.runner_config,
            runtime_patch_supported=False,
            runtime_label=self.name,
            supported_modes=["metadata_only", "mock_layer_stack"],
        )
        step_count = 0
        retries = 0
        last_error: str | None = None
        current_tool: str | None = None
        token_estimate = 0
        tool_trace: list[dict] = []
        relayer_runtime: dict | None = None

        def emit(event_type: str, text: str, **extra) -> None:
            nonlocal token_estimate
            token_estimate += max(1, len(text) // 4)
            payload = {"type": event_type, "text": text, **extra}
            live_writer.append_event(payload)

        def write_status() -> None:
            elapsed = round(time.perf_counter() - started, 3)
            live_writer.write_status(
                {
                    "run_id": context["run_id"],
                    "status": "running",
                    "task_id": task["id"],
                    "config_id": context["config_id"],
                    "runner": self.name,
                    "current_tool": current_tool,
                    "last_error": last_error,
                    "step_count": step_count,
                    "max_steps": self.max_steps,
                    "elapsed_sec": elapsed,
                    "updated_at": context["started_at"],
                    "fitness_mode": context["fitness_mode"],
                    "relayer_mode": relayer_context["mode"],
                    "relayer_applied": relayer_context["applied"],
                    "relayer_range": relayer_context.get("range_text"),
                }
            )

        if relayer_context["enabled"]:
            emit("system", relayer_context["message"], name="relayer")
        if relayer_context["applied"] and relayer_context["mode"] == "mock_layer_stack":
            plan_payload = relayer_context.get("plan") or {}
            execution_order = list(plan_payload.get("execution_order") or [])
            runtime_backend = RecordingLayerBackend()
            runtime_result = RelayerRunner(runtime_backend).execute(
                plan=RelayerPlan(execution_order=execution_order),
                initial_state=[],
            )
            relayer_runtime = {
                "backend": "mock_layer_stack",
                "execution_order": execution_order,
                "layer_trace": runtime_result.layer_trace,
                "executed_layers": runtime_result.executed_layers,
                "execution_ok": runtime_result.layer_trace == execution_order,
            }
            emit(
                "system",
                (
                    f"mock_layer_stack executed {runtime_result.executed_layers} layers "
                    f"for relayer range {relayer_context.get('range_text')}."
                ),
                name="relayer_runtime",
            )
        emit("assistant", f"收到任務：{task['prompt']}")
        write_status()

        broad_query = task["search_hints"]["broad"]
        step_count += 1
        current_tool = "search_file"
        emit("tool_call", f"search_file query={broad_query!r}", name="search_file")
        broad_matches = search_file(workspace_root, broad_query)
        tool_trace.append(
            {
                "order": step_count,
                "tool": "search_file",
                "args": {"query": broad_query},
                "result_count": len(broad_matches),
                "matches": [item["path"] for item in broad_matches[:5]],
                "ok": bool(broad_matches),
            }
        )
        emit("tool_result", f"search_file 命中 {len(broad_matches)} 筆候選", name="search_file")
        write_status()

        chosen = pick_best_match(broad_matches, task)
        exact_match = chosen and Path(chosen["path"]).resolve() == Path(task["expected_output"]).resolve()

        if not exact_match:
            retries += 1
            emit("assistant", "第一次搜尋結果太廣，縮小到專案名稱與文件類型後重試。")
            focused_query = task["search_hints"]["focused"]
            step_count += 1
            current_tool = "search_file"
            emit("tool_call", f"search_file query={focused_query!r}", name="search_file")
            focused_matches = search_file(workspace_root, focused_query)
            tool_trace.append(
                {
                    "order": step_count,
                    "tool": "search_file",
                    "args": {"query": focused_query},
                    "result_count": len(focused_matches),
                    "matches": [item["path"] for item in focused_matches[:5]],
                    "ok": bool(focused_matches),
                }
            )
            emit("tool_result", f"精準搜尋後命中 {len(focused_matches)} 筆候選", name="search_file")
            write_status()
            chosen = pick_best_match(focused_matches, task)

        final_output = "找不到符合條件的檔案"

        if chosen:
            step_count += 1
            current_tool = "open_file_location"
            emit("tool_call", f"open_file_location path={chosen['path']!r}", name="open_file_location")
            resolved = Path(chosen["path"]).resolve()
            tool_trace.append(
                {
                    "order": step_count,
                    "tool": "open_file_location",
                    "args": {"path": str(resolved)},
                    "selected": str(resolved),
                    "ok": resolved.exists(),
                }
            )
            if resolved.exists():
                final_output = str(resolved)
                emit("tool_result", final_output, name="open_file_location")
                emit("assistant", f"任務完成，輸出唯一路徑：{final_output}")
            else:
                last_error = "Selected path no longer exists."
                emit("tool_result", last_error, name="open_file_location")
                emit("assistant", "候選路徑在開啟前消失，這次 run 視為失敗。")
        else:
            last_error = "No candidate found."
            emit("assistant", "搜尋流程結束，但沒有找到可驗證的候選檔案。")

        current_tool = None
        elapsed_sec = round(time.perf_counter() - started, 3)
        live_writer.write_status(
            {
                "run_id": context["run_id"],
                "status": "runner_finished",
                "task_id": task["id"],
                "config_id": context["config_id"],
                "runner": self.name,
                "current_tool": current_tool,
                "last_error": last_error,
                "step_count": step_count,
                "max_steps": self.max_steps,
                "elapsed_sec": elapsed_sec,
                "updated_at": context["started_at"],
                "fitness_mode": context["fitness_mode"],
                "relayer_mode": relayer_context["mode"],
                "relayer_applied": relayer_context["applied"],
                "relayer_range": relayer_context.get("range_text"),
            }
        )

        return RunnerResult(
            final_output=final_output,
            step_count=step_count,
            retries=retries,
            elapsed_sec=elapsed_sec,
            current_tool=current_tool,
            last_error=last_error,
            token_estimate=token_estimate,
            tool_trace=tool_trace,
            metadata={
                "runner_mode": "mock",
                "relayer": relayer_context,
                "relayer_runtime": relayer_runtime,
            },
        )
