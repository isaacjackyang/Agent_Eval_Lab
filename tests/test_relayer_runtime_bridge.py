from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from runners.relayer_runtime_bridge import invoke_external_relayer_runtime


class RelayerRuntimeBridgeTests(unittest.TestCase):
    def test_invoke_external_relayer_runtime_writes_manifest_and_runs_stub(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stub_path = Path(__file__).resolve().parents[1] / "scripts" / "fixtures" / "relayer_runtime_stub.py"
            config = {
                "config_id": "bridge_test_config",
                "runner": "openclaw_cli",
                "openclaw": {
                    "command": ["openclaw"],
                    "model": "mock-openclaw",
                },
                "sandbox": {
                    "backend": "off",
                },
                "relayer": {
                    "runtime_backend": {
                        "command": [sys.executable, str(stub_path)],
                        "timeout_sec": 30,
                        "extra_env": {
                            "AEL_STUB_FLAG": "1",
                        },
                    }
                },
            }
            relayer_context = {
                "mode": "runtime_patch",
                "config": {"start_layer": 2, "end_layer": 4, "repeat_count": 1},
                "plan": {"execution_order": [0, 1, 2, 3, 4, 2, 3, 4, 5]},
            }

            result = invoke_external_relayer_runtime(
                root=root,
                run_id="run_test",
                runtime_label="llama_cpp_agent",
                config=config,
                relayer_context=relayer_context,
            )

            self.assertEqual(result["returncode"], 0)
            self.assertTrue(Path(result["manifest_path"]).exists())
            self.assertTrue(Path(result["stdout_path"]).exists())
            self.assertTrue(Path(result["stderr_path"]).exists())
            self.assertTrue(Path(result["result_path"]).exists())
            self.assertEqual(result["result"]["runtime_label"], "llama_cpp_agent")
            self.assertEqual(result["result"]["config_id"], "bridge_test_config")
            self.assertEqual(result["result"]["env_manifest"], result["manifest_path"])
            self.assertTrue(Path(result["result"]["sidecar_path"]).exists())

            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["runtime_config"], {})
            self.assertEqual(manifest["sandbox"]["backend"], "off")
            self.assertEqual(manifest["runtime_backend"]["extra_env"]["AEL_STUB_FLAG"], "1")


if __name__ == "__main__":
    unittest.main()
