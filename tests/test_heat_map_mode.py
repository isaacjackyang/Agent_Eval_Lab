from __future__ import annotations

import unittest

from evolution.mutator import (
    apply_heat_map_overrides,
    build_heat_map_candidates,
    build_mutation_candidates,
    heat_map_dimension_options,
    heat_map_task_type_options,
    resolve_heat_map_plan,
    supports_architecture_evolution,
)


def _base_config() -> dict:
    return {
        "config_id": "local_llama_cpp_agent_v1",
        "runner": "llama_cpp_agent",
        "fitness_mode": "fitness_weighted_v1",
        "layer_a_proxy_score": 1.0,
        "weights": {
            "success": 0.35,
            "verifier_pass": 0.2,
        },
        "nightly": {
            "candidate_pool_size": 4,
            "candidate_runs_per_config": 4,
            "include_base_config": True,
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
                "top_k": 3,
            },
        },
        "llama_cpp": {
            "provider": "llama-cpp",
            "temperature": 0.0,
            "max_output_tokens": 256,
        },
        "relayer": {
            "enabled": False,
            "mode": "metadata_only",
            "num_layers": 4,
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


class HeatMapModeTests(unittest.TestCase):
    def test_resolve_heat_map_plan_uses_rys_brain_scan_shape(self) -> None:
        plan = resolve_heat_map_plan(_base_config())
        self.assertEqual(plan["heat_map_type"], "rys_brain_scan")
        self.assertEqual(plan["x_axis"], "relayer.end_layer")
        self.assertEqual(plan["y_axis"], "relayer.start_layer")
        self.assertEqual(plan["cell_count"], 7)
        self.assertEqual(plan["probe_a"]["task_type"], "deployment")
        self.assertEqual(plan["probe_b"]["task_type"], "handoff")
        self.assertEqual(plan["probe_eval_count"], 4)

    def test_build_heat_map_candidates_scans_valid_relayer_windows(self) -> None:
        candidates = build_heat_map_candidates(_base_config())
        self.assertEqual(len(candidates), 7)
        coordinates = [candidate["heat_map_coordinates"] for candidate in candidates]
        self.assertTrue(
            any(
                item["start_layer"] == 1
                and item["end_layer"] == 2
                and item["block_len"] == 2
                and item["extra_layers"] == 2
                for item in coordinates
            )
        )
        self.assertTrue(all(item["x_value"] >= item["y_value"] for item in coordinates))

    def test_build_mutation_candidates_accepts_heat_map_mode(self) -> None:
        direct = build_heat_map_candidates(_base_config())
        via_mode = build_mutation_candidates(_base_config(), "heat_map")
        self.assertEqual(len(via_mode), len(direct))

    def test_apply_heat_map_overrides_replaces_scan_and_probe_settings(self) -> None:
        overridden = apply_heat_map_overrides(
            _base_config(),
            start_layer_min=1,
            end_layer_max=3,
            min_block_len=2,
            max_block_len=2,
            repeat_count=2,
            probe_a_task_type="operations",
            probe_a_seeds=[3001, 3002],
            probe_b_task_type="deployment",
            probe_b_seeds=[4001, 4002],
            top_k=2,
            verify_overrides={"enabled": False, "candidate_runs_per_config": 7},
        )
        plan = resolve_heat_map_plan(overridden)
        self.assertEqual(plan["scan"]["start_layer_min"], 1)
        self.assertEqual(plan["scan"]["end_layer_max"], 3)
        self.assertEqual(plan["scan"]["min_block_len"], 2)
        self.assertEqual(plan["scan"]["max_block_len"], 2)
        self.assertEqual(plan["repeat_count"], 2)
        self.assertEqual(plan["probe_a"]["task_type"], "operations")
        self.assertEqual(plan["probe_a"]["seeds"], [3001, 3002])
        self.assertEqual(plan["probe_b"]["task_type"], "deployment")
        self.assertEqual(plan["probe_b"]["seeds"], [4001, 4002])
        self.assertEqual(plan["top_k"], 2)
        self.assertEqual(overridden["nightly"]["heat_map"]["verify"]["candidate_runs_per_config"], 7)
        self.assertFalse(overridden["nightly"]["heat_map"]["verify"]["enabled"])

    def test_architecture_compatible_runner_exposes_heat_map_controls(self) -> None:
        config = _base_config()
        config["runner"] = "openclaw_cli"
        self.assertTrue(supports_architecture_evolution(config))
        self.assertTrue(any(item["value"] == "relayer.end_layer" for item in heat_map_dimension_options()))
        self.assertTrue(any(item["value"] == "deployment" for item in heat_map_task_type_options()))
        self.assertTrue(any(item["value"] == "math" for item in heat_map_task_type_options()))
        plan = resolve_heat_map_plan(config)
        self.assertEqual(plan["cell_count"], 7)


if __name__ == "__main__":
    unittest.main()
