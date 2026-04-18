from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from storage.heat_map_artifacts import build_heat_map_artifacts


def _sample_summary() -> dict:
    baseline = {
        "config_id": "baseline_cfg",
        "x_axis": "relayer.end_layer",
        "x_label": "End Layer (j)",
        "x_value": None,
        "y_axis": "relayer.start_layer",
        "y_label": "Start Layer (i)",
        "y_value": None,
        "suite_score_c": 0.9,
        "fitness": 0.91,
        "regression_pass_rate": 1.0,
        "honesty_score": 1.0,
        "delta_suite_score": 0.0,
        "delta_fitness": 0.0,
        "improved_vs_baseline": False,
        "probe_a": {"suite_score": 0.88, "delta_suite_score": 0.0},
        "probe_b": {"suite_score": 0.92, "delta_suite_score": 0.0},
        "combined": {"suite_score": 0.9, "delta_suite_score": 0.0},
    }
    best = {
        "config_id": "candidate_cfg",
        "x_axis": "relayer.end_layer",
        "x_label": "End Layer (j)",
        "x_value": 3,
        "y_axis": "relayer.start_layer",
        "y_label": "Start Layer (i)",
        "y_value": 1,
        "suite_score_c": 0.95,
        "fitness": 0.96,
        "regression_pass_rate": 1.0,
        "honesty_score": 1.0,
        "delta_suite_score": 0.05,
        "delta_fitness": 0.05,
        "improved_vs_baseline": True,
        "start_layer": 1,
        "end_layer": 3,
        "repeat_count": 1,
        "block_len": 3,
        "extra_layers": 3,
        "probe_a": {"suite_score": 0.93, "delta_suite_score": 0.05},
        "probe_b": {"suite_score": 0.97, "delta_suite_score": 0.05},
        "combined": {"suite_score": 0.95, "delta_suite_score": 0.05},
    }
    return {
        "heat_map_type": "rys_brain_scan",
        "x_axis": "relayer.end_layer",
        "x_label": "End Layer (j)",
        "x_values": [0, 1, 2, 3],
        "y_axis": "relayer.start_layer",
        "y_label": "Start Layer (i)",
        "y_values": [0, 1, 2, 3],
        "scan": {
            "start_layer_min": 0,
            "end_layer_max": 3,
            "min_block_len": 1,
            "max_block_len": 3,
            "repeat_count": 1,
        },
        "repeat_count": 1,
        "probe_a": {"label": "Deployment Probe", "task_type": "deployment", "seeds": [1101, 1103]},
        "probe_b": {"label": "Handoff Probe", "task_type": "handoff", "seeds": [2101, 2103]},
        "cell_count": 4,
        "baseline": baseline,
        "best_cell": best,
        "improved_cell_count": 1,
        "top_candidates": [best],
        "matrix": [
            {
                "y_value": 0,
                "cells": [
                    {"x_value": 0, "y_value": 0, "status": "not_run"},
                    {"x_value": 1, "y_value": 0, "status": "not_run"},
                    {"x_value": 2, "y_value": 0, "status": "not_run"},
                    {"x_value": 3, "y_value": 0, "status": "not_run"},
                ],
            },
            {
                "y_value": 1,
                "cells": [
                    {"x_value": 0, "y_value": 1, "status": "not_run"},
                    {"x_value": 1, "y_value": 1, "status": "not_run"},
                    {"x_value": 2, "y_value": 1, "status": "not_run"},
                    best,
                ],
            },
            {
                "y_value": 2,
                "cells": [
                    {"x_value": 0, "y_value": 2, "status": "not_run"},
                    {"x_value": 1, "y_value": 2, "status": "not_run"},
                    {"x_value": 2, "y_value": 2, "status": "not_run"},
                    {"x_value": 3, "y_value": 2, "status": "not_run"},
                ],
            },
            {
                "y_value": 3,
                "cells": [
                    {"x_value": 0, "y_value": 3, "status": "not_run"},
                    {"x_value": 1, "y_value": 3, "status": "not_run"},
                    {"x_value": 2, "y_value": 3, "status": "not_run"},
                    {"x_value": 3, "y_value": 3, "status": "not_run"},
                ],
            },
        ],
        "channels": {
            "combined": {
                "id": "combined",
                "label": "Combined Delta",
                "best_cell": {"config_id": "candidate_cfg", "x_value": 3, "y_value": 1, "suite_score": 0.95, "delta_suite_score": 0.05},
                "matrix": [
                    {
                        "y_value": 0,
                        "cells": [
                            {"x_value": 0, "y_value": 0, "status": "not_run"},
                            {"x_value": 1, "y_value": 0, "status": "not_run"},
                            {"x_value": 2, "y_value": 0, "status": "not_run"},
                            {"x_value": 3, "y_value": 0, "status": "not_run"},
                        ],
                    },
                    {
                        "y_value": 1,
                        "cells": [
                            {"x_value": 0, "y_value": 1, "status": "not_run"},
                            {"x_value": 1, "y_value": 1, "status": "not_run"},
                            {"x_value": 2, "y_value": 1, "status": "not_run"},
                            {"config_id": "candidate_cfg", "x_value": 3, "y_value": 1, "suite_score": 0.95, "delta_suite_score": 0.05, "status": "ok"},
                        ],
                    },
                ],
            },
            "probe_a": {
                "id": "probe_a",
                "label": "Deployment Probe",
                "best_cell": {"config_id": "candidate_cfg", "x_value": 3, "y_value": 1, "suite_score": 0.93, "delta_suite_score": 0.05},
                "matrix": [
                    {
                        "y_value": 1,
                        "cells": [
                            {"x_value": 0, "y_value": 1, "status": "not_run"},
                            {"x_value": 1, "y_value": 1, "status": "not_run"},
                            {"x_value": 2, "y_value": 1, "status": "not_run"},
                            {"config_id": "candidate_cfg", "x_value": 3, "y_value": 1, "suite_score": 0.93, "delta_suite_score": 0.05, "status": "ok"},
                        ],
                    },
                ],
            },
            "probe_b": {
                "id": "probe_b",
                "label": "Handoff Probe",
                "best_cell": {"config_id": "candidate_cfg", "x_value": 3, "y_value": 1, "suite_score": 0.97, "delta_suite_score": 0.05},
                "matrix": [
                    {
                        "y_value": 1,
                        "cells": [
                            {"x_value": 0, "y_value": 1, "status": "not_run"},
                            {"x_value": 1, "y_value": 1, "status": "not_run"},
                            {"x_value": 2, "y_value": 1, "status": "not_run"},
                            {"config_id": "candidate_cfg", "x_value": 3, "y_value": 1, "suite_score": 0.97, "delta_suite_score": 0.05, "status": "ok"},
                        ],
                    },
                ],
            },
        },
    }


class HeatMapArtifactTests(unittest.TestCase):
    def test_build_heat_map_artifacts_writes_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            manifest = build_heat_map_artifacts(
                output_dir=output_dir,
                suite_id="nightly_test_suite",
                heat_map_summary=_sample_summary(),
                created_at="2026-04-17T12:00:00",
                base_config_id="base_cfg",
                selected_config_id="candidate_cfg",
                status="promoted",
            )

            self.assertTrue((output_dir / "matrix.csv").exists())
            self.assertTrue((output_dir / "cells.csv").exists())
            self.assertTrue((output_dir / "top_candidates.json").exists())
            self.assertTrue((output_dir / "top_candidates.csv").exists())
            self.assertTrue((output_dir / "summary.json").exists())
            self.assertTrue((output_dir / "manifest.json").exists())
            self.assertTrue((output_dir / "README.md").exists())
            self.assertTrue((output_dir / "heatmap.png").exists())
            self.assertEqual(manifest["selected_config_id"], "candidate_cfg")
            self.assertIn("combined", manifest["channel_artifacts"])
            self.assertTrue((output_dir / "combined" / "heatmap.png").exists())
            self.assertTrue((output_dir / "probe_a" / "heatmap.png").exists())
            self.assertTrue((output_dir / "probe_b" / "heatmap.png").exists())
            self.assertTrue(str(manifest["readme_path"]).endswith("README.md"))

            readme_text = (output_dir / "README.md").read_text(encoding="utf-8")
            self.assertIn("## TL;DR / 快速摘要", readme_text)
            self.assertIn("Quick Start / 先看哪裡", readme_text)
            self.assertIn("Coordinates / 座標怎麼看", readme_text)
            self.assertIn("先看 `heatmap.png`", readme_text)

            with (output_dir / "matrix.csv").open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows[0][0], "relayer.start_layer")
            self.assertIn("0.05", rows[2])

            png_header = (output_dir / "heatmap.png").read_bytes()[:8]
            self.assertEqual(png_header, b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
