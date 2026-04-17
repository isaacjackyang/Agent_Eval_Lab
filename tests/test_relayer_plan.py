from __future__ import annotations

import unittest

from evolution.relayer_plan import (
    RelayerConfig,
    RelayerScanSettings,
    build_relayer_plan,
    generate_relayer_configs,
    relayer_config_id,
    summarize_relayer_config,
    resolve_relayer_runtime_context,
)


class RelayerPlanTests(unittest.TestCase):
    def test_build_relayer_plan_repeats_selected_block(self) -> None:
        plan = build_relayer_plan(8, RelayerConfig(start_layer=2, end_layer=4, repeat_count=1))
        self.assertEqual(plan.execution_order, [0, 1, 2, 3, 4, 2, 3, 4, 5, 6, 7])

    def test_generate_relayer_configs_respects_min_and_max_block_len(self) -> None:
        settings = RelayerScanSettings(
            num_layers=5,
            start_layer_min=1,
            end_layer_max=4,
            min_block_len=2,
            max_block_len=3,
            repeat_count=1,
        )
        configs = list(generate_relayer_configs(settings))
        pairs = [(item.start_layer, item.end_layer) for item in configs]
        self.assertEqual(
            pairs,
            [
                (1, 2),
                (1, 3),
                (2, 3),
                (2, 4),
                (3, 4),
            ],
        )

    def test_relayer_config_id_matches_spec_shape(self) -> None:
        config_id = relayer_config_id("qwen2_72b", RelayerConfig(start_layer=45, end_layer=51, repeat_count=1))
        self.assertEqual(config_id, "qwen2_72b__s45_e51_r1")

    def test_runtime_patch_is_rejected_when_backend_is_unsupported(self) -> None:
        config = {
            "relayer": {
                "enabled": True,
                "mode": "runtime_patch",
                "num_layers": 8,
                "start_layer": 2,
                "end_layer": 4,
                "repeat_count": 1,
            }
        }
        with self.assertRaises(RuntimeError):
            resolve_relayer_runtime_context(
                config,
                runtime_patch_supported=False,
                runtime_label="llama_cpp_agent",
            )

    def test_mock_layer_stack_mode_is_allowed_when_runner_supports_it(self) -> None:
        config = {
            "relayer": {
                "enabled": True,
                "mode": "mock_layer_stack",
                "num_layers": 8,
                "start_layer": 2,
                "end_layer": 4,
                "repeat_count": 1,
            }
        }
        context = resolve_relayer_runtime_context(
            config,
            runtime_patch_supported=False,
            runtime_label="session_mock",
            supported_modes=["metadata_only", "mock_layer_stack"],
        )
        self.assertTrue(context["applied"])
        self.assertEqual(context["mode"], "mock_layer_stack")
        self.assertIn("mock_layer_stack", context["runtime_supported_modes"])

    def test_summarize_relayer_config_reports_scan_capability(self) -> None:
        config = {
            "runner": "session_mock",
            "relayer": {
                "enabled": False,
                "mode": "metadata_only",
                "num_layers": 8,
                "scan": {
                    "start_layer_min": 0,
                    "end_layer_max": 7,
                    "min_block_len": 1,
                    "max_block_len": 3,
                    "repeat_count": 1,
                },
            },
        }
        summary = summarize_relayer_config(config)
        self.assertTrue(summary["scan_supported"])
        self.assertEqual(summary["scan_candidate_count"], 21)
        self.assertEqual(summary["runtime_supported_modes"], ["metadata_only", "mock_layer_stack"])
        self.assertFalse(summary["runtime_patch_supported"])

    def test_summarize_relayer_config_adds_runtime_patch_when_bridge_is_configured(self) -> None:
        config = {
            "runner": "llama_cpp_agent",
            "relayer": {
                "enabled": True,
                "mode": "runtime_patch",
                "num_layers": 8,
                "start_layer": 2,
                "end_layer": 4,
                "repeat_count": 1,
                "runtime_backend": {
                    "command": ["python", "-c", "print('{}')"],
                },
                "scan": {
                    "start_layer_min": 0,
                    "end_layer_max": 7,
                    "min_block_len": 1,
                    "max_block_len": 3,
                    "repeat_count": 1,
                },
            },
        }
        summary = summarize_relayer_config(config)
        self.assertTrue(summary["external_runtime_bridge"])
        self.assertIn("runtime_patch", summary["runtime_supported_modes"])
        self.assertTrue(summary["runtime_patch_supported"])


if __name__ == "__main__":
    unittest.main()
