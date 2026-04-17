from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runners.session_runner import SessionRunner


class _FakeLiveWriter:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.statuses: list[dict] = []

    def append_event(self, payload: dict) -> None:
        self.events.append(dict(payload))

    def write_status(self, payload: dict) -> None:
        self.statuses.append(dict(payload))


class SessionRunnerRelayerTests(unittest.TestCase):
    def test_session_runner_executes_mock_layer_stack_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            expected = root / "projects" / "Nova-42" / "docs" / "verified" / "handoff_packet.md"
            expected.parent.mkdir(parents=True, exist_ok=True)
            expected.write_text(
                "project: Nova-42\ndoc-slug: handoff_packet\ncanonical: true\n",
                encoding="utf-8",
            )

            distractor = root / "scratch" / "notes.txt"
            distractor.parent.mkdir(parents=True, exist_ok=True)
            distractor.write_text("Nova-42 random notes", encoding="utf-8")

            runner = SessionRunner(
                max_steps=4,
                runner_config={
                    "relayer": {
                        "enabled": True,
                        "mode": "mock_layer_stack",
                        "num_layers": 8,
                        "start_layer": 2,
                        "end_layer": 4,
                        "repeat_count": 1,
                    }
                },
            )
            writer = _FakeLiveWriter()
            task = {
                "id": "mock-task",
                "prompt": "找出 Nova-42 的 handoff packet。",
                "workspace_root": str(root),
                "expected_output": str(expected),
                "project_name": "Nova-42",
                "doc_slug": "handoff_packet",
                "search_hints": {
                    "broad": "Nova-42 handoff",
                    "focused": "Nova-42 handoff packet verified",
                },
            }
            result = runner.run(
                task=task,
                live_writer=writer,
                context={
                    "run_id": "run_test",
                    "config_id": "session_mock_relayers",
                    "started_at": "2026-04-17T00:00:00",
                    "fitness_mode": "fitness_weighted_v1",
                },
            )

            self.assertEqual(result.final_output, str(expected.resolve()))
            self.assertTrue(result.metadata["relayer"]["applied"])
            runtime = result.metadata["relayer_runtime"]
            self.assertIsNotNone(runtime)
            self.assertEqual(runtime["backend"], "mock_layer_stack")
            self.assertTrue(runtime["execution_ok"])
            self.assertEqual(runtime["layer_trace"], [0, 1, 2, 3, 4, 2, 3, 4, 5, 6, 7])
            self.assertTrue(any(item.get("name") == "relayer_runtime" for item in writer.events))


if __name__ == "__main__":
    unittest.main()
