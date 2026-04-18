from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from runners.openclaw_cli_runner import OpenClawCliRunner


class _FakeLiveWriter:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.statuses: list[dict] = []

    def append_event(self, payload: dict) -> None:
        self.events.append(dict(payload))

    def write_status(self, payload: dict) -> None:
        self.statuses.append(dict(payload))


class OpenClawCliRunnerTests(unittest.TestCase):
    def test_build_prompt_embeds_architecture_guidance(self) -> None:
        runner = OpenClawCliRunner(max_steps=4, runner_config={})
        prompt = runner._build_prompt(
            task={"prompt": "Locate the canonical handoff document."},
            architecture={
                "variant": "planner_strict",
                "prompt_style": "planner",
                "query_policy": "focused_then_broad",
                "recovery_policy": "signal_boost",
                "search_result_limit": 8,
            },
        )

        self.assertIn("Plan briefly before searching", prompt)
        self.assertIn("Start focused, then broaden", prompt)
        self.assertIn("canonical answer", prompt)
        self.assertIn("8", prompt)
        self.assertIn("Locate the canonical handoff document.", prompt)

    def test_build_prompt_switches_to_math_protocol(self) -> None:
        runner = OpenClawCliRunner(max_steps=4, runner_config={})
        prompt = runner._build_prompt(
            task={
                "prompt": "Compute the integer value of ((8 + 3) * 4) - 5.",
                "category": "math_reasoning",
                "metadata": {"answer_kind": "integer"},
            },
            architecture={
                "variant": "planner_strict",
                "prompt_style": "planner",
                "query_policy": "focused_then_broad",
                "recovery_policy": "signal_boost",
                "search_result_limit": 8,
            },
        )

        self.assertIn("math and reasoning benchmark", prompt)
        self.assertIn('{"type":"final","answer":"42"}', prompt)
        self.assertNotIn("search result shortlist", prompt)

    def test_run_supports_runtime_patch_bridge_with_stubbed_openclaw(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            expected = root / "projects" / "Nova-42" / "knowledge" / "release" / "verified" / "deployment_checklist.md"
            expected.parent.mkdir(parents=True, exist_ok=True)
            expected.write_text(
                "Canonical: true\nproject: Nova-42\ndoc-slug: deployment\n",
                encoding="utf-8",
            )

            repo_root = Path(__file__).resolve().parents[1]
            openclaw_stub = repo_root / "scripts" / "fixtures" / "openclaw_cli_stub.py"
            relayer_stub = repo_root / "scripts" / "fixtures" / "relayer_runtime_stub.py"
            runner = OpenClawCliRunner(
                max_steps=4,
                runner_config={
                    "config_id": "openclaw_runtime_patch_test",
                    "runner": "openclaw_cli",
                    "openclaw": {
                        "command": [sys.executable, str(openclaw_stub)],
                        "agent_id_prefix": "eval-lab",
                        "local": True,
                        "timeout_sec": 30,
                        "cleanup_agent": True,
                        "cleanup_runtime_dir": False,
                    },
                    "sandbox": {
                        "backend": "off",
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
                        },
                    },
                },
            )
            writer = _FakeLiveWriter()
            result = runner.run(
                task={
                    "id": "openclaw-relayer-task",
                    "prompt": "Locate canonical deployment doc.",
                    "workspace_root": str(root),
                },
                live_writer=writer,
                context={
                    "root": str(root),
                    "run_id": "run_openclaw_runtime_patch",
                    "config_id": "openclaw_runtime_patch_test",
                    "started_at": "2026-04-17T00:00:00",
                    "fitness_mode": "fitness_weighted_v1",
                },
            )

            self.assertEqual(result.final_output, str(expected.resolve()))
            self.assertTrue(result.metadata["relayer"]["applied"])
            backend = result.metadata["relayer_runtime_backend"]
            self.assertIsNotNone(backend)
            self.assertTrue(backend["result"]["ok"])
            self.assertEqual(backend["result"]["runtime_label"], "openclaw_cli")
            self.assertTrue(Path(backend["manifest_path"]).exists())
            self.assertTrue(Path(backend["result"]["sidecar_path"]).exists())
            self.assertTrue(any(item.get("name") == "relayer_runtime" for item in writer.events))

            runtime_metadata = result.metadata["runtime"]
            self.assertTrue(Path(runtime_metadata["runtime_report_path"]).exists())
            self.assertTrue(Path(runtime_metadata["command_history_path"]).exists())
            self.assertTrue(result.metadata["cleanup"]["success"])

    def test_run_supports_math_reasoning_with_stubbed_openclaw(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace_root = root / "workspace"
            workspace_root.mkdir(parents=True, exist_ok=True)

            repo_root = Path(__file__).resolve().parents[1]
            openclaw_stub = repo_root / "scripts" / "fixtures" / "openclaw_cli_stub.py"
            runner = OpenClawCliRunner(
                max_steps=4,
                runner_config={
                    "config_id": "openclaw_math_test",
                    "runner": "openclaw_cli",
                    "openclaw": {
                        "command": [sys.executable, str(openclaw_stub)],
                        "agent_id_prefix": "eval-lab",
                        "local": True,
                        "timeout_sec": 30,
                        "cleanup_agent": True,
                        "cleanup_runtime_dir": False,
                    },
                    "sandbox": {
                        "backend": "off",
                    },
                },
            )
            writer = _FakeLiveWriter()
            result = runner.run(
                task={
                    "id": "openclaw-math-task",
                    "prompt": "Solve the arithmetic problem exactly.\nCompute the integer value of ((8 + 3) * 4) - 5.\nReturn the final answer only.",
                    "category": "math_reasoning",
                    "workspace_root": str(workspace_root),
                    "metadata": {"answer_kind": "integer"},
                },
                live_writer=writer,
                context={
                    "root": str(root),
                    "run_id": "run_openclaw_math",
                    "config_id": "openclaw_math_test",
                    "started_at": "2026-04-18T00:00:00",
                    "fitness_mode": "fitness_weighted_v1",
                },
            )

            self.assertEqual(result.final_output, "39")
            self.assertEqual(result.tool_trace, [])
            self.assertIsNone(result.last_error)
            self.assertTrue(any(item.get("text") == "39" for item in writer.events if item.get("type") == "assistant"))

    def test_run_returns_runtime_error_with_cleanup_metadata_when_command_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace_root = root / "workspace"
            workspace_root.mkdir(parents=True, exist_ok=True)
            runner = OpenClawCliRunner(
                max_steps=4,
                runner_config={
                    "config_id": "openclaw_missing_command_test",
                    "runner": "openclaw_cli",
                    "openclaw": {
                        "command": ["definitely_missing_openclaw.exe"],
                        "agent_id_prefix": "eval-lab",
                        "local": True,
                        "timeout_sec": 5,
                        "cleanup_agent": True,
                        "cleanup_runtime_dir": False,
                    },
                    "sandbox": {
                        "backend": "off",
                    },
                },
            )
            writer = _FakeLiveWriter()
            result = runner.run(
                task={
                    "id": "openclaw-missing-command-task",
                    "prompt": "Locate canonical deployment doc.",
                    "workspace_root": str(workspace_root),
                },
                live_writer=writer,
                context={
                    "root": str(root),
                    "run_id": "run_openclaw_missing_command",
                    "config_id": "openclaw_missing_command_test",
                    "started_at": "2026-04-18T00:00:00",
                    "fitness_mode": "fitness_weighted_v1",
                },
            )

            self.assertIn("OpenClaw CLI not found", result.last_error or "")
            runtime_metadata = result.metadata["runtime"]
            report = json.loads(Path(runtime_metadata["runtime_report_path"]).read_text(encoding="utf-8"))
            self.assertEqual(report["last_error"], result.last_error)
            self.assertFalse(result.metadata["cleanup"]["success"])
            self.assertTrue(any(item.get("name") == "openclaw_runtime_error" for item in writer.events))


if __name__ == "__main__":
    unittest.main()
