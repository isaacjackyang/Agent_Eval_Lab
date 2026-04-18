from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from evolution.mutator import effective_agent_architecture, normalize_sampling_provider
from evolution.relayer_plan import relayer_supported_modes_for_runner, resolve_relayer_runtime_context
from runners.base import BaseRunner, RunnerResult
from runners.relayer_runtime_bridge import invoke_external_relayer_runtime
from runners.search_helpers import search_file


class LlamaCppAgentRunner(BaseRunner):
    name = "llama_cpp_agent"
    PROVIDER_DEFAULT_SAMPLING = {
        "ollama": {
            "temperature": 0.8,
            "top_p": 0.9,
            "top_k": 40,
            "min_p": 0.0,
            "repeat_penalty": 1.1,
            "repeat_last_n": 64,
            "seed": 0,
        },
        "llama-cpp": {
            "temperature": 0.8,
            "top_p": 0.95,
            "top_k": 40,
            "min_p": 0.05,
            "repeat_penalty": 1.1,
            "repeat_last_n": 64,
            "presence_penalty": 0.0,
            "frequency_penalty": 0.0,
            "seed": -1,
        },
    }

    def run(self, task: dict, live_writer, context: dict) -> RunnerResult:
        cfg = self.runner_config.get("llama_cpp", {})
        architecture = effective_agent_architecture(self.runner_config)
        relayer_supported_modes = relayer_supported_modes_for_runner(self.name, config=self.runner_config)
        relayer_context = resolve_relayer_runtime_context(
            self.runner_config,
            runtime_patch_supported="runtime_patch" in relayer_supported_modes,
            runtime_label=self.name,
            supported_modes=relayer_supported_modes,
        )
        provider = self._resolve_provider_name(cfg)
        model = str(cfg.get("model", "gemma-4-31B-it-Q5_K_M.gguf"))
        timeout_sec = int(cfg.get("timeout_sec", 90))
        max_output_tokens = int(cfg.get("max_output_tokens", cfg.get("num_predict", 256)))
        sampling_options = self._collect_sampling_options(cfg, provider=provider, max_output_tokens=max_output_tokens)
        temperature = float(sampling_options.get("temperature", 0.0))
        top_p = float(sampling_options.get("top_p", 1.0))
        base_url = self._resolve_base_url(cfg)
        prompt_style = str(architecture.get("prompt_style", "strict_json"))
        query_policy = str(architecture.get("query_policy", "focused_then_broad"))
        recovery_policy = str(architecture.get("recovery_policy", "ranked"))
        search_result_limit = int(architecture.get("search_result_limit", 5))

        workspace_root = Path(task["workspace_root"]).resolve()
        task_category = str(task.get("category", "")).strip().lower()
        math_mode = task_category == "math_reasoning"
        started = time.perf_counter()
        step_count = 0
        retries = 0
        last_error: str | None = None
        current_tool: str | None = None
        token_estimate = 0
        tool_trace: list[dict[str, Any]] = []
        last_search_matches: list[dict[str, Any]] = []
        recovery_used = False
        relayer_runtime_backend: dict[str, Any] | None = None
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

        if math_mode:
            system_prompt = self._build_math_system_prompt(prompt_style=prompt_style)
            user_prompt = self._build_math_user_prompt(task=task)
        else:
            system_prompt = self._build_system_prompt(
                prompt_style=prompt_style,
                query_policy=query_policy,
                recovery_policy=recovery_policy,
                search_result_limit=search_result_limit,
            )
            user_prompt = self._build_user_prompt(
                task=task,
                workspace_root=workspace_root,
                query_policy=query_policy,
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
                    "transport": "ollama-chat" if provider == "ollama" else "llama.cpp-openai",
                    "top_p": top_p,
                    "provider": provider,
                    "architecture_variant": architecture.get("variant"),
                    "relayer_mode": relayer_context["mode"],
                    "relayer_applied": relayer_context["applied"],
                    "relayer_range": relayer_context.get("range_text"),
                    **status_context,
                }
            )

        emit(
            "system",
            (
                f"Using llama.cpp model={model} base_url={base_url} temperature={temperature} top_p={top_p} "
                f"provider={provider} architecture={architecture.get('variant')}"
            ),
            name="llama_cpp",
        )
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
        write_status()

        final_output = ""
        for _ in range(self.max_steps):
            step_count += 1
            current_tool = "llama_cpp_chat"
            write_status()

            try:
                response = self._chat(
                    provider=provider,
                    base_url=base_url,
                    model=model,
                    messages=messages,
                    sampling_options=sampling_options,
                    timeout_sec=timeout_sec,
                )
                token_estimate += self._extract_usage_tokens(response, provider)
                assistant_text, reasoning_text = self._extract_response_text(response, provider)
                emit("assistant", assistant_text or reasoning_text or "[empty assistant output]")
            except Exception as exc:
                last_error = f"{provider} request failed: {exc}"
                emit("system", last_error, name="llama_cpp_error")
                retries += 1
                break

            try:
                action = self._extract_json_object(assistant_text)
            except Exception as exc:
                last_error = f"bad_json_output: {exc}"
                retries += 1
                emit("system", last_error, name="parser")
                candidate = None if math_mode else self._recover_best_match(task, last_search_matches, policy=recovery_policy)
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
                        "content": (
                            "Your previous answer was invalid. Return one valid JSON object only. "
                            'For math tasks use {"type":"final","answer":"..."} and do not call tools.'
                            if math_mode
                            else "Your previous answer was invalid. Return one valid JSON object only."
                        ),
                    }
                )
                continue

            if action.get("type") == "final":
                answer_key = "answer" if math_mode else "path"
                final_output = str(action.get(answer_key, "")).strip()
                emit("assistant", final_output, name="final_answer" if math_mode else "final_path")
                last_error = None
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
                        "content": (
                            'Unsupported JSON shape. Return {"type":"final","answer":"..."} only.'
                            if math_mode
                            else "Unsupported JSON shape. Return a tool_call or final object only."
                        ),
                    }
                )
                continue

            if math_mode:
                last_error = "wrong_tool_for_math"
                retries += 1
                emit("system", last_error, name="parser")
                messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
                messages.append(
                    {
                        "role": "user",
                        "content": 'Tools are disabled for this benchmark. Return {"type":"final","answer":"..."} only.',
                    }
                )
                continue

            tool_name = str(action.get("tool", "")).strip()
            args = action.get("args") or {}
            current_tool = tool_name
            emit("tool_call", f"{tool_name} {json.dumps(args, ensure_ascii=False)}", name=tool_name)

            if tool_name == "search_file":
                query = str(args.get("query", "")).strip()
                result = self._tool_search_file(workspace_root, query, limit=search_result_limit)
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
                "transport": "ollama-chat" if provider == "ollama" else "llama.cpp-openai",
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
                "adapter": "ollama_chat" if provider == "ollama" else "llama_cpp_openai",
                "base_url": base_url,
                "model": model,
                "top_p": top_p,
                "transport": "ollama-chat" if provider == "ollama" else "llama.cpp-openai",
                "provider": provider,
                "sampling_options": sampling_options,
                "recovery_policy_used": recovery_used,
                "architecture_variant": architecture.get("variant"),
                "architecture": architecture,
                "task_category": task_category,
                "relayer": relayer_context,
                "relayer_runtime_backend": relayer_runtime_backend,
            },
        )

    def _build_system_prompt(
        self,
        *,
        prompt_style: str,
        query_policy: str,
        recovery_policy: str,
        search_result_limit: int,
    ) -> str:
        base_rules = [
            "You are a filesystem retrieval agent inside Agent Eval Lab.",
            "Always return exactly one JSON object and nothing else.",
            "Allowed tools:",
            '- search_file(query): returns ranked absolute paths that match the query in path or file content.',
            '- open_file_location(path): validates one absolute path and returns its canonical path if it exists.',
            "Valid outputs:",
            '{"type":"tool_call","tool":"search_file","args":{"query":"..."} }',
            '{"type":"tool_call","tool":"open_file_location","args":{"path":"ABSOLUTE_PATH"} }',
            '{"type":"final","path":"ABSOLUTE_PATH"}',
            "Core rules:",
            "- Search before opening a path.",
            "- Before the final answer, call open_file_location on the best candidate.",
            "- Never invent a path.",
            "- Use only the workspace root given by the user.",
            f"- search_file will return up to {search_result_limit} matches.",
        ]

        if prompt_style == "planner":
            base_rules.extend(
                [
                    "Planning style:",
                    "- Treat retrieval as a two-stage decision problem.",
                    "- If the first search is ambiguous, refine with another search before the final answer.",
                    "- Prefer the most canonical and up-to-date path over drafts or archives.",
                ]
            )
        elif prompt_style == "recall":
            base_rules.extend(
                [
                    "Recall style:",
                    "- Start with broader recall, then narrow to the canonical file.",
                    "- Use a second search when the result list mixes current and archive material.",
                ]
            )
        else:
            base_rules.extend(
                [
                    "Strict JSON style:",
                    "- Keep outputs compact and schema-correct.",
                    "- Avoid commentary outside the JSON object.",
                ]
            )

        if query_policy == "focused_only":
            base_rules.append("- Prefer focused, high-precision search queries.")
        elif query_policy == "broad_then_focused":
            base_rules.append("- Start broad, then narrow with a second search if needed.")
        else:
            base_rules.append("- Start focused, then broaden only when the first search is ambiguous.")

        if recovery_policy == "none":
            base_rules.append("- If your previous JSON was invalid, do not rely on fallback recovery.")
        elif recovery_policy == "signal_boost":
            base_rules.append("- Prefer recovery candidates that look verified/current and match project/doc signals.")
        else:
            base_rules.append("- Recovery favors the best-ranked canonical-looking candidate.")

        return "\n".join(base_rules) + "\n"

    def _build_user_prompt(self, *, task: dict[str, Any], workspace_root: Path, query_policy: str) -> str:
        focused = str(task["search_hints"]["focused"])
        broad = str(task["search_hints"]["broad"])
        prompt_lines = [
            f"Workspace root: {workspace_root}",
            f"Project: {task['project_name']}",
            f"Document type slug: {task['doc_slug']}",
        ]

        if query_policy == "focused_only":
            prompt_lines.extend(
                [
                    f"Recommended search query: {focused}",
                    "Use a precise query first and stay focused unless the result is empty.",
                ]
            )
        elif query_policy == "broad_then_focused":
            prompt_lines.extend(
                [
                    f"Suggested broad query: {broad}",
                    f"Suggested focused query: {focused}",
                    "Start broad, then narrow if multiple plausible candidates appear.",
                ]
            )
        else:
            prompt_lines.extend(
                [
                    f"Suggested focused query: {focused}",
                    f"Suggested broad query: {broad}",
                    "Start focused; if the result is ambiguous, use the broad query next.",
                ]
            )

        prompt_lines.append("Find the canonical file and return only its absolute path via JSON.")
        return "\n".join(prompt_lines) + "\n"

    def _build_math_system_prompt(self, *, prompt_style: str) -> str:
        base_rules = [
            "You are a math reasoning agent inside Agent Eval Lab.",
            "Always return exactly one JSON object and nothing else.",
            'The only valid output is {"type":"final","answer":"..."}',
            "Do not call tools.",
            "Do not return explanations or derivations in the final answer.",
        ]
        if prompt_style == "planner":
            base_rules.append("- Plan internally, but output only the final answer JSON.")
        elif prompt_style == "recall":
            base_rules.append("- Re-check the final answer before you emit the JSON object.")
        else:
            base_rules.append("- Keep the output compact and schema-correct.")
        return "\n".join(base_rules) + "\n"

    def _build_math_user_prompt(self, *, task: dict[str, Any]) -> str:
        metadata = task.get("metadata", {}) if isinstance(task.get("metadata"), dict) else {}
        answer_kind = str(metadata.get("answer_kind", "integer"))
        prompt_lines = [
            f"Benchmark family: {metadata.get('family', 'math_reasoning')}",
            task["prompt"],
        ]
        if answer_kind == "ranking":
            prompt_lines.append('Return format: {"type":"final","answer":"Name1 > Name2 > Name3 > Name4"}')
        else:
            prompt_lines.append('Return format: {"type":"final","answer":"INTEGER"}')
        return "\n".join(prompt_lines) + "\n"

    def _resolve_base_url(self, cfg: dict[str, Any]) -> str:
        explicit = str(cfg.get("base_url", "")).strip()
        if explicit:
            return explicit.rstrip("/")

        state_dir = Path(str(cfg.get("openclaw_state_dir", Path.home() / ".openclaw")))
        openclaw_config = state_dir / "openclaw.json"
        if openclaw_config.exists():
            payload = json.loads(openclaw_config.read_text(encoding="utf-8"))
            provider_name = normalize_sampling_provider(cfg.get("provider"), default="llama-cpp") or "llama-cpp"
            providers = payload.get("models", {}).get("providers", {})
            provider = providers.get(provider_name, {})
            if not provider and provider_name == "llama-cpp":
                provider = providers.get("llama.cpp", {}) or providers.get("llama_cpp", {})
            base_url = str(provider.get("baseUrl", "")).strip()
            if base_url:
                return base_url.rstrip("/")

        return "http://127.0.0.1:8080/v1"

    def _resolve_provider_name(self, cfg: dict[str, Any]) -> str:
        provider = normalize_sampling_provider(cfg.get("provider"), default="llama-cpp")
        if provider is None:
            raise ValueError(f"Unsupported llama_cpp provider: {cfg.get('provider')}")
        return provider

    def _collect_sampling_options(self, cfg: dict[str, Any], *, provider: str, max_output_tokens: int) -> dict[str, Any]:
        options: dict[str, Any] = dict(self.PROVIDER_DEFAULT_SAMPLING.get(provider, {}))
        optional_fields = (
            "temperature",
            "top_p",
            "top_k",
            "min_p",
            "repeat_penalty",
            "repeat_last_n",
            "seed",
            "presence_penalty",
            "frequency_penalty",
        )
        for field in optional_fields:
            value = cfg.get(field)
            if value is not None:
                options[field] = value
        options["max_tokens"] = max_output_tokens
        options["num_predict"] = int(cfg.get("num_predict", max_output_tokens))
        return options

    def _chat(
        self,
        *,
        provider: str,
        base_url: str,
        model: str,
        messages: list[dict[str, str]],
        sampling_options: dict[str, Any],
        timeout_sec: int,
    ) -> dict[str, Any]:
        if provider == "ollama":
            return self._chat_ollama(
                base_url=base_url,
                model=model,
                messages=messages,
                sampling_options=sampling_options,
                timeout_sec=timeout_sec,
            )
        return self._chat_openai_compatible(
            base_url=base_url,
            model=model,
            messages=messages,
            sampling_options=sampling_options,
            timeout_sec=timeout_sec,
        )

    def _chat_openai_compatible(
        self,
        *,
        base_url: str,
        model: str,
        messages: list[dict[str, str]],
        sampling_options: dict[str, Any],
        timeout_sec: int,
    ) -> dict[str, Any]:
        body = json.dumps(
            {
                "model": model,
                "messages": messages,
                **{key: value for key, value in sampling_options.items() if key != "num_predict"},
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

    def _chat_ollama(
        self,
        *,
        base_url: str,
        model: str,
        messages: list[dict[str, str]],
        sampling_options: dict[str, Any],
        timeout_sec: int,
    ) -> dict[str, Any]:
        api_root = base_url[:-3] if base_url.endswith("/v1") else base_url
        body = json.dumps(
            {
                "model": model,
                "messages": messages,
                "stream": False,
                "format": "json",
                "options": {
                    key: value
                    for key, value in sampling_options.items()
                    if key != "max_tokens"
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{api_root}/api/chat",
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

    def _extract_usage_tokens(self, response: dict[str, Any], provider: str) -> int:
        if provider == "ollama":
            prompt_tokens = int(response.get("prompt_eval_count", 0) or 0)
            eval_tokens = int(response.get("eval_count", 0) or 0)
            return prompt_tokens + eval_tokens
        return int(response.get("usage", {}).get("total_tokens", 0) or 0)

    def _extract_response_text(self, response: dict[str, Any], provider: str) -> tuple[str, str]:
        if provider == "ollama":
            message = response.get("message") or {}
            assistant_text = str(message.get("content") or "").strip()
            reasoning_text = str(message.get("thinking") or "").strip()
            return assistant_text, reasoning_text
        message = (response.get("choices") or [{}])[0].get("message") or {}
        assistant_text = str(message.get("content") or "").strip()
        reasoning_text = str(message.get("reasoning_content") or "").strip()
        return assistant_text, reasoning_text

    def _tool_search_file(self, workspace_root: Path, query: str, limit: int = 5) -> dict[str, Any]:
        matches = search_file(workspace_root, query)
        return {"matches": matches[: max(1, limit)]}

    def _tool_open_file_location(self, workspace_root: Path, path_value: str) -> dict[str, Any]:
        candidate = Path(path_value).resolve()
        try:
            candidate.relative_to(workspace_root)
        except ValueError:
            return {"error": "Path is outside the workspace root."}

        if not candidate.exists():
            return {"error": "Path does not exist."}
        return {"path": str(candidate)}

    def _recover_best_match(self, task: dict, matches: list[dict[str, Any]], policy: str = "ranked") -> str | None:
        if not matches:
            return None
        if policy == "none":
            return None
        if policy == "first_match":
            first = str(matches[0].get("path", ""))
            return first or None

        project_name = str(task.get("project_name", "")).lower()
        doc_slug = str(task.get("doc_slug", "")).lower()
        positive_terms = ("verified", "current", "delivery", doc_slug, project_name)
        negative_terms = ("archive", "archived", "draft", "template", "scratch", "notes")

        def rank(item: dict[str, Any]) -> tuple[int, int, int, int]:
            path = str(item.get("path", "")).lower()
            positive = sum(1 for term in positive_terms if term and term in path)
            negative = sum(1 for term in negative_terms if term in path)
            raw_score = int(item.get("score", 0) or 0)
            if policy == "signal_boost":
                if "verified" in path or "current" in path:
                    positive += 2
                if doc_slug and doc_slug in path:
                    positive += 1
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
