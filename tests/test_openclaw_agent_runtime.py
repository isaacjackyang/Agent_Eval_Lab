from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from sandbox.openclaw_agent_runtime import OpenClawRuntime


class OpenClawAgentRuntimeTests(unittest.TestCase):
    def test_runtime_writes_lifecycle_smoke_and_command_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace_root = root / "workspace"
            expected = workspace_root / "projects" / "Nova-42" / "knowledge" / "release" / "verified" / "deployment_checklist.md"
            expected.parent.mkdir(parents=True, exist_ok=True)
            expected.write_text(
                "Canonical: true\nproject: Nova-42\ndoc-slug: deployment\n",
                encoding="utf-8",
            )

            stub_path = Path(__file__).resolve().parents[1] / "scripts" / "fixtures" / "openclaw_cli_stub.py"
            runtime = OpenClawRuntime(
                root=root,
                config={
                    "config_id": "runtime_test",
                    "openclaw": {
                        "command": [sys.executable, str(stub_path)],
                        "timeout_sec": 30,
                        "cleanup_agent": True,
                        "cleanup_runtime_dir": False,
                    },
                    "sandbox": {
                        "backend": "off",
                    },
                },
                run_id="runtime_test_run",
            )

            metadata = runtime.prepare(workspace_root)
            completed = runtime.run_agent("Locate canonical deployment doc.")
            cleanup = runtime.cleanup()

            self.assertTrue(metadata["smoke_test"]["ok"])
            self.assertEqual(completed.returncode, 0)
            self.assertTrue(cleanup["success"])

            lifecycle_path = Path(runtime.describe_runtime()["lifecycle_path"])
            command_history_path = Path(runtime.describe_runtime()["command_history_path"])
            runtime_report_path = Path(runtime.describe_runtime()["runtime_report_path"])
            smoke_report_path = Path(runtime.describe_runtime()["smoke_report_path"])

            self.assertTrue(lifecycle_path.exists())
            self.assertTrue(command_history_path.exists())
            self.assertTrue(runtime_report_path.exists())
            self.assertTrue(smoke_report_path.exists())

            lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))
            command_history = json.loads(command_history_path.read_text(encoding="utf-8"))
            report = json.loads(runtime_report_path.read_text(encoding="utf-8"))
            smoke_report = json.loads(smoke_report_path.read_text(encoding="utf-8"))

            stages = [item["stage"] for item in lifecycle["events"]]
            self.assertIn("prepare_started", stages)
            self.assertIn("smoke_test_passed", stages)
            self.assertIn("run_finished", stages)
            self.assertIn("cleanup_finished", stages)

            self.assertGreaterEqual(len(command_history["commands"]), 4)
            for item in command_history["commands"]:
                self.assertTrue(Path(item["stdout_path"]).exists())
                self.assertTrue(Path(item["stderr_path"]).exists())
                self.assertTrue(Path(item["record_path"]).exists())

            self.assertTrue(report["prepared"])
            self.assertTrue(report["cleaned_up"])
            self.assertTrue(smoke_report["ok"])


if __name__ == "__main__":
    unittest.main()
