from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from storage.heat_map_artifacts import build_heat_map_artifacts


def _sample_summary() -> dict:
    baseline = {
        "config_id": "baseline_cfg",
        "x_axis": "agent_architecture.search_result_limit",
        "x_label": "Search Result Limit",
        "x_value": 5,
        "y_axis": "agent_architecture.query_policy",
        "y_label": "Query Policy",
        "y_value": "focused_then_broad",
        "suite_score_c": 0.9,
        "fitness": 0.91,
        "regression_pass_rate": 1.0,
        "honesty_score": 1.0,
        "delta_suite_score": 0.0,
        "delta_fitness": 0.0,
        "improved_vs_baseline": False,
    }
    best = {
        "config_id": "candidate_cfg",
        "x_axis": "agent_architecture.search_result_limit",
        "x_label": "Search Result Limit",
        "x_value": 8,
        "y_axis": "agent_architecture.query_policy",
        "y_label": "Query Policy",
        "y_value": "broad_then_focused",
        "suite_score_c": 0.95,
        "fitness": 0.96,
        "regression_pass_rate": 1.0,
        "honesty_score": 1.0,
        "delta_suite_score": 0.05,
        "delta_fitness": 0.05,
        "improved_vs_baseline": True,
    }
    return {
        "x_axis": "agent_architecture.search_result_limit",
        "x_label": "Search Result Limit",
        "x_values": [5, 8],
        "y_axis": "agent_architecture.query_policy",
        "y_label": "Query Policy",
        "y_values": ["focused_then_broad", "broad_then_focused"],
        "cell_count": 4,
        "baseline": baseline,
        "best_cell": best,
        "improved_cell_count": 1,
        "top_candidates": [best],
        "matrix": [
            {"y_value": "focused_then_broad", "cells": [baseline, {"x_value": 8, "y_value": "focused_then_broad", "status": "not_run"}]},
            {"y_value": "broad_then_focused", "cells": [{"x_value": 5, "y_value": "broad_then_focused", "status": "not_run"}, best]},
        ],
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
            self.assertTrue((output_dir / "heatmap.png").exists())
            self.assertEqual(manifest["selected_config_id"], "candidate_cfg")

            with (output_dir / "matrix.csv").open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows[0][0], "agent_architecture.query_policy")
            self.assertIn("0.0", rows[1])

            png_header = (output_dir / "heatmap.png").read_bytes()[:8]
            self.assertEqual(png_header, b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
