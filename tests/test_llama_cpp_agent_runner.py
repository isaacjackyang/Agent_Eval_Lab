from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from runners.llama_cpp_agent_runner import LlamaCppAgentRunner


class _FakeLiveWriter:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.statuses: list[dict] = []

    def append_event(self, payload: dict) -> None:
        self.events.append(dict(payload))

    def write_status(self, payload: dict) -> None:
        self.statuses.append(dict(payload))


class _StubLlamaCppAgentRunner(LlamaCppAgentRunner):
    def __init__(self, *args, scripted_outputs: list[str] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.scripted_outputs = list(scripted_outputs or [])
        self.seen_base_urls: list[str] = []
        self.seen_models: list[str] = []
        self.seen_messages: list[list[dict[str, str]]] = []
        self.seen_sampling_options: list[dict[str, object]] = []

    def _chat(
        self,
        *,
        provider: str,
        base_url: str,
        model: str,
        messages: list[dict[str, str]],
        sampling_options: dict[str, object],
        timeout_sec: int,
    ) -> dict[str, object]:
        self.seen_base_urls.append(base_url)
        self.seen_models.append(model)
        self.seen_messages.append([dict(item) for item in messages])
        self.seen_sampling_options.append(dict(sampling_options))
        next_output = self.scripted_outputs.pop(0)
        return {
            "assistant_text": next_output,
            "usage": {
                "total_tokens": 24,
            },
        }

    def _extract_usage_tokens(self, response: dict[str, object], provider: str) -> int:
        usage = response.get("usage", {})
        if isinstance(usage, dict):
            return int(usage.get("total_tokens", 0))
        return 0

    def _extract_response_text(self, response: dict[str, object], provider: str) -> tuple[str, str]:
        return str(response.get("assistant_text", "")), ""


class LlamaCppAgentRunnerRuntimePatchTests(unittest.TestCase):
    def test_runtime_patch_bridge_runs_before_chat_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace_root = root / "workspace"
            workspace_root.mkdir(parents=True, exist_ok=True)
            expected = workspace_root / "handoff_packet.md"
            expected.write_text("canonical file", encoding="utf-8")

            relayer_stub = Path(__file__).resolve().parents[1] / "scripts" / "fixtures" / "relayer_runtime_stub.py"
            runtime_effects = {
                "prompt": {
                    "user_suffix": "RUNTIME_PATCH_USER_SUFFIX",
                },
                "llama_cpp": {
                    "base_url": "http://127.0.0.1:9091/v1",
                    "model": "patched-runtime-model.gguf",
                    "sampling_options": {
                        "temperature": 0.25,
                        "top_p": 0.75,
                    },
                },
            }
            runner = _StubLlamaCppAgentRunner(
                max_steps=2,
                runner_config={
                    "config_id": "llama_runtime_patch_test",
                    "runner": "llama_cpp_agent",
                    "llama_cpp": {
                        "provider": "llama-cpp",
                        "base_url": "http://127.0.0.1:8080/v1",
                        "model": "mock-model.gguf",
                        "timeout_sec": 30,
                        "temperature": 0.0,
                        "max_output_tokens": 64,
                    },
                    "relayer": {
                        "enabled": True,
                        "mode": "runtime_patch",
                        "num_layers": 8,
                        "start_layer": 2,
                        "end_layer": 4,
                        "repeat_count": 1,
                        "runtime_backend": {
                            "command": [sys.executable, str(relayer_stub)],
                            "timeout_sec": 30,
                            "extra_env": {
                                "AEL_STUB_RUNTIME_EFFECTS_JSON": json.dumps(runtime_effects, ensure_ascii=False),
                            },
                        },
                    },
                },
                scripted_outputs=[
                    json.dumps({"type": "final", "path": str(expected.resolve())}, ensure_ascii=False),
                ],
            )
            writer = _FakeLiveWriter()
            result = runner.run(
                task={
                    "id": "llama-relayer-task",
                    "prompt": "找出 Nova-42 的 handoff packet。",
                    "workspace_root": str(workspace_root),
                    "expected_output": str(expected.resolve()),
                    "project_name": "Nova-42",
                    "doc_slug": "handoff_packet",
                    "search_hints": {
                        "focused": "Nova-42 handoff packet verified",
                        "broad": "Nova-42 handoff",
                    },
                },
                live_writer=writer,
                context={
                    "root": str(root),
                    "run_id": "run_llama_runtime_patch",
                    "config_id": "llama_runtime_patch_test",
                    "started_at": "2026-04-17T00:00:00",
                    "fitness_mode": "fitness_weighted_v1",
                },
            )

            self.assertEqual(result.final_output, str(expected.resolve()))
            self.assertEqual(runner.seen_base_urls, ["http://127.0.0.1:9091/v1"])
            self.assertEqual(runner.seen_models, ["patched-runtime-model.gguf"])
            self.assertAlmostEqual(float(runner.seen_sampling_options[0]["temperature"]), 0.25)
            self.assertAlmostEqual(float(runner.seen_sampling_options[0]["top_p"]), 0.75)
            self.assertTrue(runner.seen_messages[0][1]["content"].endswith("RUNTIME_PATCH_USER_SUFFIX"))
            self.assertTrue(result.metadata["relayer"]["applied"])
            backend = result.metadata["relayer_runtime_backend"]
            self.assertIsNotNone(backend)
            self.assertTrue(backend["result"]["ok"])
            self.assertTrue(Path(backend["manifest_path"]).exists())
            self.assertTrue(Path(backend["result"]["sidecar_path"]).exists())
            self.assertEqual(
                result.metadata["relayer_runtime_effects"]["llama_cpp"]["base_url"],
                "http://127.0.0.1:9091/v1",
            )
            self.assertTrue(any(item.get("name") == "relayer_runtime" for item in writer.events))
            self.assertTrue(any(item.get("name") == "relayer_runtime_effects" for item in writer.events))


if __name__ == "__main__":
    unittest.main()
