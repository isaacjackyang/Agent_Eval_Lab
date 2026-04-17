from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evolution.heat_map_verifier import run_heat_map_verification


def _heat_map_summary() -> dict:
    baseline = {
        "config_id": "baseline_cfg",
        "x_value": 5,
        "y_value": "focused_then_broad",
    }
    return {
        "cell_count": 4,
        "baseline": baseline,
        "top_candidates": [
            {
                "config_id": "candidate_a",
                "x_value": 8,
                "y_value": "broad_then_focused",
            },
            {
                "config_id": "candidate_b",
                "x_value": 4,
                "y_value": "focused_only",
            },
        ],
    }


def _fake_result(config_id: str, config_path: str, suite_score: float, fitness: float, pass_rate: float) -> dict:
    return {
        "config": {"config_id": config_id},
        "config_path": config_path,
        "mutation_profile": config_id,
        "candidate_summary": {
            "suite_score": suite_score,
            "honesty_score": 1.0,
        },
        "regression_summary": {
            "pass_rate": pass_rate,
        },
        "fitness": fitness,
    }


class HeatMapVerifierTests(unittest.TestCase):
    def test_run_heat_map_verification_ranks_verified_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            reports_dir = temp_root / "reports"
            artifact_dir = temp_root / "artifacts"
            base_config_path = temp_root / "base_config.json"
            regression_suite_path = temp_root / "regression_suite.json"
            candidate_a_path = temp_root / "candidate_a.json"
            candidate_b_path = temp_root / "candidate_b.json"

            regression_suite_path.write_text(json.dumps({"cases": [{"case_id": "reg_01", "seed": 101}]}), encoding="utf-8")
            base_config_path.write_text(json.dumps({"config_id": "baseline_cfg"}), encoding="utf-8")
            candidate_a_path.write_text(json.dumps({"config_id": "candidate_a"}), encoding="utf-8")
            candidate_b_path.write_text(json.dumps({"config_id": "candidate_b"}), encoding="utf-8")

            base_config = {
                "config_id": "baseline_cfg",
                "nightly": {
                    "heat_map": {
                        "verify": {
                            "enabled": True,
                            "candidate_runs_per_config": 3,
                            "top_k": 2,
                        }
                    }
                },
                "regression_suite": {
                    "path": str(regression_suite_path.resolve()),
                    "min_pass_rate": 0.75,
                },
            }
            evaluated_candidates = [
                {
                    "config": {"config_id": "candidate_a"},
                    "config_path": str(candidate_a_path.resolve()),
                },
                {
                    "config": {"config_id": "candidate_b"},
                    "config_path": str(candidate_b_path.resolve()),
                },
            ]

            def fake_evaluate_candidate_config(*, config_path, config, **kwargs):
                config_id = config["config_id"]
                if config_id == "baseline_cfg":
                    return _fake_result(config_id, str(config_path), 0.90, 0.91, 1.0)
                if config_id == "candidate_a":
                    return _fake_result(config_id, str(config_path), 0.95, 0.96, 1.0)
                return _fake_result(config_id, str(config_path), 0.89, 0.90, 0.6)

            with patch("evolution.heat_map_verifier.evaluate_candidate_config", side_effect=fake_evaluate_candidate_config):
                report = run_heat_map_verification(
                    base_config_path=base_config_path,
                    base_config=base_config,
                    heat_map_summary=_heat_map_summary(),
                    evaluated_candidates=evaluated_candidates,
                    suite_id="nightly_suite",
                    reports_dir=reports_dir,
                    artifact_dir=artifact_dir,
                    seed_start=500,
                )

            self.assertIsNotNone(report)
            self.assertEqual(report["winner"]["config_id"], "candidate_a")
            self.assertEqual(report["survivor_count"], 1)
            self.assertTrue(Path(report["report_path"]).exists())

            history_path = reports_dir / "heat_map_verification_history.json"
            self.assertTrue(history_path.exists())
            history = json.loads(history_path.read_text(encoding="utf-8"))
            self.assertEqual(history["history"][-1]["winner"]["config_id"], "candidate_a")


if __name__ == "__main__":
    unittest.main()
