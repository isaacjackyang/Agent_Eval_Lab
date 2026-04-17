from __future__ import annotations

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
            task={"prompt": "找出專案 Quartz-84 的交接清單，並只顯示檔案位置。"},
            architecture={
                "variant": "planner_strict",
                "prompt_style": "planner",
                "query_policy": "focused_then_broad",
                "recovery_policy": "signal_boost",
                "search_result_limit": 8,
            },
        )

        self.assertIn("先列一個極短的檢索計畫", prompt)
        self.assertIn("先精準查詢", prompt)
        self.assertIn("verified", prompt)
        self.assertIn("8", prompt)
        self.assertIn("任務：", prompt)

    def test_run_supports_runtime_patch_bridge_with_stubbed_openclaw(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            expected = root / "projects" / "Nova-42" / "knowledge" / "release" / "verified" / "handoff_checklist.md"
            expected.parent.mkdir(parents=True, exist_ok=True)
            expected.write_text(
                "Canonical: true\nproject: Nova-42\ndoc-slug: handoff\n",
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
                    "prompt": "找出專案 Nova-42 的交接清單，並只顯示檔案位置。",
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


if __name__ == "__main__":
    unittest.main()
