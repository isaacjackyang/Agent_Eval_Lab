from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evolution.relayer_plan import RelayerConfig
from evolution.relayer_scan import evaluate_mock_relayer_candidate, run_relayer_scan
from storage.history_writer import ensure_report_files


def _config() -> dict:
    return {
        "config_id": "relayer_test_config",
        "paths": {
            "reports_dir": "reports",
        },
        "nightly": {
            "heat_map": {
                "top_k": 3,
            }
        },
        "relayer": {
            "enabled": False,
            "mode": "metadata_only",
            "num_layers": 8,
            "repeat_count": 1,
            "scan": {
                "start_layer_min": 0,
                "end_layer_max": 7,
                "min_block_len": 1,
                "max_block_len": 3,
                "repeat_count": 1,
            },
            "mock_probe": {
                "preferred_start_layer": 2,
                "preferred_end_layer": 4,
                "preferred_repeat_count": 1,
            },
        },
        "llama_cpp": {
            "model": "mock-model.gguf",
        },
    }


class RelayerScanTests(unittest.TestCase):
    def test_evaluate_mock_relayer_candidate_prefers_target_window(self) -> None:
        config = _config()
        near = evaluate_mock_relayer_candidate(
            config=config,
            relayer_config=RelayerConfig(start_layer=2, end_layer=4, repeat_count=1),
        )
        far = evaluate_mock_relayer_candidate(
            config=config,
            relayer_config=RelayerConfig(start_layer=0, end_layer=0, repeat_count=1),
        )
        self.assertGreater(near["raw_score"], far["raw_score"])
        self.assertTrue(near["execution_ok"])

    def test_run_relayer_scan_writes_artifacts_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reports_dir = root / "reports"
            output_dir = root / "output"
            ensure_report_files(reports_dir)
            progress_updates: list[dict] = []
            events: list[dict] = []

            result = run_relayer_scan(
                base_config_path=None,
                base_config=_config(),
                output_dir=output_dir,
                reports_dir=reports_dir,
                max_candidates=5,
                progress_callback=progress_updates.append,
                event_callback=events.append,
            )

            self.assertEqual(len(result["candidate_results"]), 5)
            self.assertTrue((output_dir / "aggregated.csv").exists())
            self.assertTrue((output_dir / "artifacts" / "heatmap.png").exists())
            self.assertTrue((output_dir / "manifest.json").exists())
            self.assertTrue((output_dir / "README.md").exists())
            self.assertEqual(progress_updates[0]["status"], "starting_relayer_scan")
            self.assertEqual(progress_updates[-1]["status"], "completed")
            self.assertEqual(progress_updates[-1]["progress_current"], 5)
            self.assertTrue(any("Evaluating candidate" in event.get("text", "") for event in events))

            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["candidate_count"], 5)
            self.assertIn("mock_layer_stack", manifest["notes"])
            self.assertTrue(str(manifest["readme_path"]).endswith("README.md"))

            readme_text = (output_dir / "README.md").read_text(encoding="utf-8")
            self.assertIn("## TL;DR / 快速摘要", readme_text)
            self.assertIn("Quick Start / 先看哪裡", readme_text)
            self.assertIn("Interpretation / 怎麼解讀", readme_text)
            self.assertIn("先看 `artifacts/heatmap.png`", readme_text)

            history = json.loads((reports_dir / "relayer_scan_history.json").read_text(encoding="utf-8"))
            self.assertEqual(history["history"][-1]["candidate_count"], 5)


if __name__ == "__main__":
    unittest.main()
