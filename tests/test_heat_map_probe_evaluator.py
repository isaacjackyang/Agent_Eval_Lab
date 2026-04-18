from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from evolution.nightly_evaluator import evaluate_heat_map_candidate_config


def _config() -> dict:
    return {
        "config_id": "heat_map_cfg",
        "layer_a_proxy_score": 1.0,
        "nightly": {
            "heat_map": {
                "scan": {
                    "start_layer_min": 0,
                    "end_layer_max": 3,
                    "min_block_len": 1,
                    "max_block_len": 2,
                    "repeat_count": 1,
                },
                "probe_a": {
                    "label": "Deployment Probe",
                    "task_type": "deployment",
                    "seeds": [1101, 1103],
                },
                "probe_b": {
                    "label": "Handoff Probe",
                    "task_type": "handoff",
                    "seeds": [2101, 2103],
                },
            }
        },
        "regression_suite": {
            "path": "benchmarks/layer_b/regression_suite.json",
        },
        "relayer": {
            "enabled": True,
            "mode": "metadata_only",
            "num_layers": 4,
            "start_layer": 1,
            "end_layer": 2,
            "repeat_count": 1,
            "scan": {
                "start_layer_min": 0,
                "end_layer_max": 3,
                "min_block_len": 1,
                "max_block_len": 2,
                "repeat_count": 1,
            },
        },
    }


def _fake_run(*, seed, task_type=None, **kwargs) -> dict:
    score = {
        ("deployment", 1101): 0.80,
        ("deployment", 1103): 0.90,
        ("handoff", 2101): 0.70,
        ("handoff", 2103): 0.80,
        (None, 101): 0.85,
        (None, 202): 0.84,
    }.get((task_type, seed), 0.75)
    passed = score >= 0.8
    return {
        "run_id": f"run_{seed}",
        "status": "passed" if passed else "failed",
        "score": score,
        "verifier": {"subscores": {"honesty": 1.0}},
        "rollback": {"rollback_safety_score": 1.0},
    }


class HeatMapProbeEvaluatorTests(unittest.TestCase):
    def test_evaluate_heat_map_candidate_config_aggregates_two_probe_sets(self) -> None:
        regression_suite = {
            "cases": [
                {"case_id": "reg_101", "seed": 101},
                {"case_id": "reg_202", "seed": 202},
            ]
        }
        with patch("evolution.nightly_evaluator.execute_single_run", side_effect=_fake_run):
            result = evaluate_heat_map_candidate_config(
                config_path=Path("configs/experiments/local_llama_cpp_agent.json"),
                config=_config(),
                suite_id="nightly_suite",
                regression_suite=regression_suite,
                progress_offset=0,
                progress_target=6,
            )

        self.assertAlmostEqual(result["candidate_summary"]["probe_summaries"]["probe_a"]["suite_score"], 0.85, places=4)
        self.assertAlmostEqual(result["candidate_summary"]["probe_summaries"]["probe_b"]["suite_score"], 0.75, places=4)
        self.assertAlmostEqual(result["candidate_summary"]["suite_score"], 0.80, places=4)
        self.assertEqual(len(result["candidate_runs"]), 4)
        self.assertEqual(len(result["regression_runs"]), 2)


if __name__ == "__main__":
    unittest.main()
