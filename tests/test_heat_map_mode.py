from __future__ import annotations

import unittest

from evolution.mutator import (
    apply_heat_map_overrides,
    build_heat_map_candidates,
    build_mutation_candidates,
    heat_map_dimension_options,
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
                "x_axis": "agent_architecture.search_result_limit",
                "x_values": [4, 5, 6, 8],
                "y_axis": "agent_architecture.query_policy",
                "y_values": ["focused_only", "focused_then_broad", "broad_then_focused"],
                "top_k": 5,
            },
        },
        "llama_cpp": {
            "provider": "llama-cpp",
            "temperature": 0.0,
            "max_output_tokens": 256,
        },
    }


class HeatMapModeTests(unittest.TestCase):
    def test_resolve_heat_map_plan_uses_configured_axes(self) -> None:
        plan = resolve_heat_map_plan(_base_config())
        self.assertEqual(plan["x_axis"], "agent_architecture.search_result_limit")
        self.assertEqual(plan["y_axis"], "agent_architecture.query_policy")
        self.assertEqual(plan["cell_count"], 12)
        self.assertEqual(plan["baseline_coordinate"]["x_value"], 5)
        self.assertEqual(plan["baseline_coordinate"]["y_value"], "focused_then_broad")

    def test_build_heat_map_candidates_skips_baseline_cell(self) -> None:
        candidates = build_heat_map_candidates(_base_config())
        self.assertEqual(len(candidates), 11)
        coordinates = [candidate["heat_map_coordinates"] for candidate in candidates]
        self.assertNotIn(
            {
                "x_axis": "agent_architecture.search_result_limit",
                "x_label": "Search Result Limit",
                "x_value": 5,
                "y_axis": "agent_architecture.query_policy",
                "y_label": "Query Policy",
                "y_value": "focused_then_broad",
            },
            coordinates,
        )
        self.assertTrue(
            any(
                candidate["heat_map_coordinates"]["x_value"] == 8
                and candidate["heat_map_coordinates"]["y_value"] == "broad_then_focused"
                for candidate in candidates
            )
        )

    def test_build_mutation_candidates_accepts_heat_map_mode(self) -> None:
        direct = build_heat_map_candidates(_base_config())
        via_mode = build_mutation_candidates(_base_config(), "heat_map")
        self.assertEqual(len(via_mode), len(direct))

    def test_rejects_duplicate_heat_map_axes(self) -> None:
        config = _base_config()
        config["nightly"]["heat_map"]["y_axis"] = config["nightly"]["heat_map"]["x_axis"]
        with self.assertRaises(ValueError):
            resolve_heat_map_plan(config)

    def test_apply_heat_map_overrides_replaces_axes_and_values(self) -> None:
        overridden = apply_heat_map_overrides(
            _base_config(),
            x_axis="agent_architecture.prompt_style",
            y_axis="agent_architecture.recovery_policy",
            x_values=["strict_json", "planner"],
            y_values=["none", "ranked"],
            top_k=2,
            verify_overrides={"enabled": False, "candidate_runs_per_config": 7},
        )
        plan = resolve_heat_map_plan(overridden)
        self.assertEqual(plan["x_axis"], "agent_architecture.prompt_style")
        self.assertEqual(plan["y_axis"], "agent_architecture.recovery_policy")
        self.assertEqual(plan["x_values"], ["strict_json", "planner"])
        self.assertEqual(plan["y_values"], ["none", "ranked"])
        self.assertEqual(plan["top_k"], 2)
        self.assertEqual(overridden["nightly"]["heat_map"]["verify"]["candidate_runs_per_config"], 7)
        self.assertFalse(overridden["nightly"]["heat_map"]["verify"]["enabled"])

    def test_openclaw_config_is_architecture_compatible(self) -> None:
        config = _base_config()
        config["runner"] = "openclaw_cli"
        self.assertTrue(supports_architecture_evolution(config))
        self.assertTrue(any(item["value"] == "agent_architecture.query_policy" for item in heat_map_dimension_options()))
        plan = resolve_heat_map_plan(config)
        self.assertEqual(plan["cell_count"], 12)


if __name__ == "__main__":
    unittest.main()
