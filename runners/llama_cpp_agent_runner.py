from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from runners.base import BaseRunner, RunnerResult
from runners.search_helpers import search_file


class LlamaCppAgentRunner(BaseRunner):
    name = "llama_cpp_agent"

    def run(self, task: dict, live_writer, context: dict) -> RunnerResult:
        cfg = self.runner_config.get("llama_cpp", {})
        model = str(cfg.get("model", "gemma-4-31B-it-Q5_K_M.gguf"))
        timeout_sec = int(cfg.get("timeout_sec", 90))
        max_output_tokens = int(cfg.get("max_output_tokens", 256))
        temperature = float(cfg.get("temperature", 0.0))
        base_url = self._resolve_base_url(cfg)

        workspace_root = Path(task["workspace_root"]).resolve()
        started = time.perf_counter()
        step_count = 0
        retries = 0
        last_error: str | None = None
        current_tool: str | None = None
        token_estimate = 0
        tool_trace: list[dict[str, Any]] = []
        last_search_matches: list[dict[str, Any]] = []
        recovery_used = False
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

        system_prompt = (
            "You are a filesystem retrieval agent inside Agent Eval Lab.\n"
            "Always return exactly one JSON object and nothing else.\n"
            "Allowed tools:\n"
            '- search_file(query): returns ranked absolute paths that match the query in path or file content.\n'
            '- open_file_location(path): validates one absolute path and returns its canonical path if it exists.\n'
            "Valid outputs:\n"
            '{"type":"tool_call","tool":"search_file","args":{"query":"..."} }\n'
            '{"type":"tool_call","tool":"open_file_location","args":{"path":"ABSOLUTE_PATH"} }\n'
            '{"type":"final","path":"ABSOLUTE_PATH"}\n'
            "Rules:\n"
            "- Search first.\n"
            "- Before the final answer, call open_file_location on the best candidate.\n"
            "- Never invent a path.\n"
            "- Use only the workspace root given by the user.\n"
        )
        user_prompt = (
            f"Workspace root: {workspace_root}\n"
            f"Project: {task['project_name']}\n"
            f"Document type slug: {task['doc_slug']}\n"
            f"Suggested focused query: {task['search_hints']['focused']}\n"
            f"Suggested broad query: {task['search_hints']['broad']}\n"
            "Find the canonical file and return only its absolute path via JSON.\n"
        )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        def emit(event_type: str, text: str, **extra) -> None:
            nonlocal token_estimate
            token_estimate += max(1, len(text) // 4)
            payload = {"type": event_type, "text": text, **extra}
            live_writer.append_event(payload)

        def write_status() -> None:
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
                    "elapsed_sec": round(time.perf_counter() - started, 3),
                    "updated_at": context["started_at"],
                    "fitness_mode": context["fitness_mode"],
                    "model": model,
                    "transport": "llama.cpp-openai",
                    **status_context,
                }
            )

        emit("system", f"Using llama.cpp model={model} base_url={base_url}", name="llama_cpp")
        write_status()

        final_output = ""
        for _ in range(self.max_steps):
            step_count += 1
            current_tool = "llama_cpp_chat"
            write_status()

            try:
                response = self._chat(
                    base_url=base_url,
                    model=model,
                    messages=messages,
                    max_tokens=max_output_tokens,
                    temperature=temperature,
                    timeout_sec=timeout_sec,
                )
                token_estimate += int(response.get("usage", {}).get("total_tokens", 0) or 0)
                message = (response.get("choices") or [{}])[0].get("message") or {}
                assistant_text = (message.get("content") or "").strip()
                reasoning_text = (message.get("reasoning_content") or "").strip()
                emit("assistant", assistant_text or reasoning_text or "[empty assistant output]")
            except Exception as exc:
                last_error = f"llama.cpp request failed: {exc}"
                emit("system", last_error, name="llama_cpp_error")
                retries += 1
                break

            try:
                action = self._extract_json_object(assistant_text)
            except Exception as exc:
                last_error = f"bad_json_output: {exc}"
                retries += 1
                emit("system", last_error, name="parser")
                candidate = self._recover_best_match(task, last_search_matches)
                if candidate:
                    recovery_used = True
                    current_tool = "open_file_location"
                    emit("system", f"Recovery policy selected candidate: {candidate}", name="recovery_policy")
                    emit(
                        "tool_call",
                        f"open_file_location {json.dumps({'path': candidate}, ensure_ascii=False)}",
                        name="open_file_location",
                    )
                    result = self._tool_open_file_location(workspace_root, candidate)
                    tool_trace.append(
                        {
                            "order": step_count,
                            "tool": "open_file_location",
                            "args": {"path": candidate},
                            "ok": "error" not in result,
                            "selected": result.get("path"),
                            "recovered": True,
                        }
                    )
                    emit("tool_result", json.dumps(result, ensure_ascii=False), name="open_file_location")
                    if "path" in result:
                        final_output = str(result["path"])
                        emit("assistant", final_output, name="final_path")
                        last_error = None
                        current_tool = None
                        break
                messages.append({"role": "assistant", "content": assistant_text or reasoning_text})
                messages.append(
                    {
                        "role": "user",
                        "content": "Your previous answer was invalid. Return one valid JSON object only.",
                    }
                )
                continue

            if action.get("type") == "final":
                final_output = str(action.get("path", "")).strip()
                emit("assistant", final_output, name="final_path")
                current_tool = None
                break

            if action.get("type") != "tool_call":
                last_error = "unsupported_action_type"
                retries += 1
                emit("system", last_error, name="parser")
                messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
                messages.append(
                    {
                        "role": "user",
                        "content": "Unsupported JSON shape. Return a tool_call or final object only.",
                    }
                )
                continue

            tool_name = str(action.get("tool", "")).strip()
            args = action.get("args") or {}
            current_tool = tool_name
            emit("tool_call", f"{tool_name} {json.dumps(args, ensure_ascii=False)}", name=tool_name)

            if tool_name == "search_file":
                query = str(args.get("query", "")).strip()
                result = self._tool_search_file(workspace_root, query)
                last_search_matches = list(result.get("matches", []))
            elif tool_name == "open_file_location":
                path_value = str(args.get("path", "")).strip()
                result = self._tool_open_file_location(workspace_root, path_value)
            else:
                result = {"error": f"Unsupported tool: {tool_name}"}
                last_error = result["error"]
                retries += 1

            tool_trace.append(
                {
                    "order": step_count,
                    "tool": tool_name,
                    "args": args,
                    "ok": "error" not in result,
                    "result_count": len(result.get("matches", [])) if isinstance(result.get("matches"), list) else None,
                    "matches_preview": [item.get("path") for item in result.get("matches", [])[:5]]
                    if isinstance(result.get("matches"), list)
                    else None,
                    "selected": result.get("path"),
                }
            )
            emit("tool_result", json.dumps(result, ensure_ascii=False), name=tool_name)
            write_status()

            messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
            messages.append(
                {
                    "role": "user",
                    "content": f"Tool result: {json.dumps(result, ensure_ascii=False)}\nReturn the next action as JSON only.",
                }
            )

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
                "model": model,
                "transport": "llama.cpp-openai",
                **status_context,
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
                "adapter": "llama_cpp_openai",
                "base_url": base_url,
                "model": model,
                "transport": "llama.cpp-openai",
                "recovery_policy_used": recovery_used,
            },
        )

    def _resolve_base_url(self, cfg: dict[str, Any]) -> str:
        explicit = str(cfg.get("base_url", "")).strip()
        if explicit:
            return explicit.rstrip("/")

        state_dir = Path(str(cfg.get("openclaw_state_dir", Path.home() / ".openclaw")))
        openclaw_config = state_dir / "openclaw.json"
        if openclaw_config.exists():
            payload = json.loads(openclaw_config.read_text(encoding="utf-8"))
            provider_name = str(cfg.get("provider", "llama-cpp"))
            provider = payload.get("models", {}).get("providers", {}).get(provider_name, {})
            base_url = str(provider.get("baseUrl", "")).strip()
            if base_url:
                return base_url.rstrip("/")

        return "http://127.0.0.1:8080/v1"

    def _chat(
        self,
        *,
        base_url: str,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        timeout_sec: int,
    ) -> dict[str, Any]:
        body = json.dumps(
            {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "response_format": {"type": "json_object"},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(str(exc)) from exc

    def _tool_search_file(self, workspace_root: Path, query: str) -> dict[str, Any]:
        matches = search_file(workspace_root, query)
        return {"matches": matches[:5]}

    def _tool_open_file_location(self, workspace_root: Path, path_value: str) -> dict[str, Any]:
        candidate = Path(path_value).resolve()
        try:
            candidate.relative_to(workspace_root)
        except ValueError:
            return {"error": "Path is outside the workspace root."}

        if not candidate.exists():
            return {"error": "Path does not exist."}
        return {"path": str(candidate)}

    def _recover_best_match(self, task: dict, matches: list[dict[str, Any]]) -> str | None:
        if not matches:
            return None

        project_name = str(task.get("project_name", "")).lower()
        doc_slug = str(task.get("doc_slug", "")).lower()
        positive_terms = ("verified", "current", "delivery", doc_slug, project_name)
        negative_terms = ("archive", "archived", "draft", "template", "scratch", "notes")

        def rank(item: dict[str, Any]) -> tuple[int, int, int, int]:
            path = str(item.get("path", "")).lower()
            positive = sum(1 for term in positive_terms if term and term in path)
            negative = sum(1 for term in negative_terms if term in path)
            raw_score = int(item.get("score", 0) or 0)
            return (positive, -negative, raw_score, -len(path))

        best = max(matches, key=rank)
        best_path = str(best.get("path", ""))
        if not best_path:
            return None
        return best_path

    def _extract_json_object(self, text: str) -> dict[str, Any]:
        start = text.find("{")
        if start < 0:
            raise ValueError("No JSON object found.")

        depth = 0
        in_string = False
        escaped = False
        for index, char in enumerate(text[start:], start=start):
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : index + 1])

        raise ValueError("Incomplete JSON object.")
