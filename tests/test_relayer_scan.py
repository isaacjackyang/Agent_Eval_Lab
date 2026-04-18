from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evolution.relayer_plan import RelayerConfig
from evolution.relayer_scan import (
    _build_scan_candidates,
    build_relayer_scan_summary,
    evaluate_mock_relayer_candidate,
    resolve_relayer_scan_scoring_mode,
    run_relayer_scan,
)
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
            self.assertEqual(result["summary"]["x_axis"], "relayer.end_layer")
            self.assertEqual(result["summary"]["y_axis"], "relayer.start_layer")

            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["candidate_count"], 5)
            self.assertEqual(manifest["scoring_mode"], "mock")
            self.assertIn("mock_layer_stack", manifest["notes"])
            self.assertTrue(str(manifest["readme_path"]).endswith("README.md"))

            readme_text = (output_dir / "README.md").read_text(encoding="utf-8")
            self.assertIn("## TL;DR / 快速摘要", readme_text)
            self.assertIn("Quick Start / 先看哪裡", readme_text)
            self.assertIn("Interpretation / 怎麼解讀", readme_text)
            self.assertIn("先看 `artifacts/heatmap.png`", readme_text)

            history = json.loads((reports_dir / "relayer_scan_history.json").read_text(encoding="utf-8"))
            self.assertEqual(history["history"][-1]["candidate_count"], 5)

    def test_scoring_mode_prefers_rys_probe_scoring_for_runnable_configs(self) -> None:
        config = _config()
        config["runner"] = "openclaw_cli"
        config["regression_suite"] = {"path": "benchmarks/layer_b/regression_suite.json"}
        config["nightly"]["heat_map"].update(
            {
                "probe_a": {"label": "Deployment Probe", "task_type": "deployment", "seeds": [1101, 1103]},
                "probe_b": {"label": "Handoff Probe", "task_type": "handoff", "seeds": [2101, 2103]},
            }
        )
        config["relayer"]["scan"]["scoring_mode"] = "auto"

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            scoring = resolve_relayer_scan_scoring_mode(base_config=config, base_config_path=config_path)

        self.assertEqual(scoring["mode"], "heat_map_probes")
        self.assertTrue(scoring["uses_real_probes"])

    def test_probe_scored_summary_uses_heat_map_scan_axes(self) -> None:
        config = _config()
        config["nightly"]["heat_map"].update(
            {
                "scan": {
                    "start_layer_min": 2,
                    "end_layer_max": 4,
                    "min_block_len": 2,
                    "max_block_len": 2,
                    "repeat_count": 1,
                },
                "probe_a": {"label": "Deployment Probe", "task_type": "deployment", "seeds": [1101, 1103]},
                "probe_b": {"label": "Handoff Probe", "task_type": "handoff", "seeds": [2101, 2103]},
            }
        )
        config["relayer"]["scan"].update(
            {
                "start_layer_min": 0,
                "end_layer_max": 7,
                "min_block_len": 1,
                "max_block_len": 3,
            }
        )

        baseline_result = {
            "config_id": "baseline",
            "raw_score": 0.8,
            "fitness": 0.8,
            "regression_pass_rate": 1.0,
            "honesty_score": 1.0,
            "probe_summaries": {
                "probe_a": {"suite_score": 0.78},
                "probe_b": {"suite_score": 0.82},
            },
        }
        candidate_results = [
            {
                "config_id": "heat_map__s2_e3_r1",
                "start_layer": 2,
                "end_layer": 3,
                "repeat_count": 1,
                "block_len": 2,
                "raw_score": 0.86,
                "fitness": 0.84,
                "regression_pass_rate": 1.0,
                "honesty_score": 0.99,
                "probe_summaries": {
                    "probe_a": {"id": "probe_a", "label": "Deployment Probe", "task_type": "deployment", "suite_score": 0.84},
                    "probe_b": {"id": "probe_b", "label": "Handoff Probe", "task_type": "handoff", "suite_score": 0.88},
                },
            },
            {
                "config_id": "heat_map__s3_e4_r1",
                "start_layer": 3,
                "end_layer": 4,
                "repeat_count": 1,
                "block_len": 2,
                "raw_score": 0.83,
                "fitness": 0.82,
                "regression_pass_rate": 0.95,
                "honesty_score": 0.97,
                "probe_summaries": {
                    "probe_a": {"id": "probe_a", "label": "Deployment Probe", "task_type": "deployment", "suite_score": 0.8},
                    "probe_b": {"id": "probe_b", "label": "Handoff Probe", "task_type": "handoff", "suite_score": 0.86},
                },
            },
        ]

        summary = build_relayer_scan_summary(
            base_config=config,
            baseline_result=baseline_result,
            candidate_results=candidate_results,
            top_k=2,
        )

        self.assertEqual(summary["x_values"], [2, 3, 4])
        self.assertEqual(summary["y_values"], [2, 3, 4])
        self.assertEqual(summary["scan"]["start_layer_min"], 2)
        self.assertEqual(summary["scan"]["end_layer_max"], 4)
        self.assertEqual(summary["best_cell"]["config_id"], "heat_map__s2_e3_r1")
        self.assertEqual(summary["matrix"][0]["cells"][0]["status"], "not_run")
        self.assertEqual(summary["matrix"][0]["cells"][1]["config_id"], "heat_map__s2_e3_r1")
        self.assertEqual(summary["channels"]["probe_a"]["best_cell"]["config_id"], "heat_map__s2_e3_r1")

    def test_heat_map_probe_mode_builds_candidates_from_heat_map_scan(self) -> None:
        config = _config()
        config["nightly"]["heat_map"].update(
            {
                "scan": {
                    "start_layer_min": 2,
                    "end_layer_max": 4,
                    "min_block_len": 2,
                    "max_block_len": 2,
                    "repeat_count": 1,
                },
                "probe_a": {"label": "Deployment Probe", "task_type": "deployment", "seeds": [1101, 1103]},
                "probe_b": {"label": "Handoff Probe", "task_type": "handoff", "seeds": [2101, 2103]},
            }
        )
        config["relayer"]["scan"].update(
            {
                "start_layer_min": 0,
                "end_layer_max": 7,
                "min_block_len": 1,
                "max_block_len": 3,
                "repeat_count": 1,
            }
        )

        candidates = _build_scan_candidates(base_config=config, scoring_mode="heat_map_probes")

        coordinates = [
            (candidate["heat_map_coordinates"]["start_layer"], candidate["heat_map_coordinates"]["end_layer"])
            for candidate in candidates
        ]
        self.assertEqual(coordinates, [(2, 3), (3, 4)])


if __name__ == "__main__":
    unittest.main()
