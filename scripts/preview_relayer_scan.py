from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evolution.mutator import build_heat_map_candidates, resolve_heat_map_plan
from evolution.relayer_plan import (
    RelayerConfig,
    build_relayer_plan,
    generate_relayer_configs,
    relayer_config_id,
    resolve_relayer_model_name,
    resolve_active_relayer_config,
    resolve_active_relayer_plan,
    resolve_relayer_num_layers,
    resolve_relayer_scan_settings,
    resolve_relayer_scan_runtime_mode,
)
from evolution.relayer_scan import resolve_relayer_scan_scoring_mode


def _load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview relayer plan and scan candidates from an experiment config.")
    parser.add_argument("--config", default=str(ROOT / "configs" / "experiments" / "local_llama_cpp_agent.json"))
    parser.add_argument("--limit", type=int, default=12, help="How many scan candidates to preview.")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config = _load_json(config_path)
    model_name = resolve_relayer_model_name(config)

    active_config = resolve_active_relayer_config(config)
    active_plan = resolve_active_relayer_plan(config)
    num_layers = resolve_relayer_num_layers(config)

    scoring = resolve_relayer_scan_scoring_mode(base_config=config, base_config_path=config_path)
    scoring_mode = str(scoring["mode"])
    scan_settings = resolve_relayer_scan_settings(config)
    scan_runtime_mode = resolve_relayer_scan_runtime_mode(config)
    preview_candidates = []
    if scoring_mode == "heat_map_probes":
        heat_map_plan = resolve_heat_map_plan(config)
        all_candidates = build_heat_map_candidates(config)
        for candidate in all_candidates[: max(1, args.limit)]:
            relayer_cfg = candidate.get("relayer", {})
            relayer_config = RelayerConfig(
                start_layer=int(relayer_cfg["start_layer"]),
                end_layer=int(relayer_cfg["end_layer"]),
                repeat_count=int(relayer_cfg.get("repeat_count", 1)),
            )
            coords = candidate.get("heat_map_coordinates", {})
            preview_candidates.append(
                {
                    "config_id": candidate["config_id"],
                    "start_layer": relayer_config.start_layer,
                    "end_layer": relayer_config.end_layer,
                    "repeat_count": relayer_config.repeat_count,
                    "block_len": relayer_config.block_len,
                    "x_axis": coords.get("x_axis"),
                    "x_value": coords.get("x_value"),
                    "y_axis": coords.get("y_axis"),
                    "y_value": coords.get("y_value"),
                    "execution_order": build_relayer_plan(num_layers, relayer_config).execution_order,
                }
            )
        total_candidate_count = len(all_candidates)
        scan_settings_payload = {
            "num_layers": heat_map_plan["num_layers"],
            **heat_map_plan["scan"],
            "runtime_mode": heat_map_plan["runtime_mode"],
            "x_axis": heat_map_plan["x_axis"],
            "y_axis": heat_map_plan["y_axis"],
        }
    else:
        for index, relayer_config in enumerate(generate_relayer_configs(scan_settings)):
            if index >= max(1, args.limit):
                break
            preview_candidates.append(
                {
                    "config_id": relayer_config_id(model_name, relayer_config),
                    "start_layer": relayer_config.start_layer,
                    "end_layer": relayer_config.end_layer,
                    "repeat_count": relayer_config.repeat_count,
                    "block_len": relayer_config.block_len,
                    "execution_order": build_relayer_plan(scan_settings.num_layers, relayer_config).execution_order,
                }
            )
        total_candidate_count = len(list(generate_relayer_configs(scan_settings)))
        scan_settings_payload = {
            "num_layers": scan_settings.num_layers,
            "start_layer_min": scan_settings.start_layer_min,
            "end_layer_max": scan_settings.end_layer_max,
            "min_block_len": scan_settings.min_block_len,
            "max_block_len": scan_settings.max_block_len,
            "repeat_count": scan_settings.repeat_count,
            "runtime_mode": scan_runtime_mode,
        }

    payload = {
        "config_path": str(config_path),
        "config_id": config.get("config_id"),
        "scoring_mode": scoring_mode,
        "scoring_note": scoring.get("note"),
        "num_layers": num_layers,
        "active_relayer": {
            "config": None if active_config is None else {
                "start_layer": active_config.start_layer,
                "end_layer": active_config.end_layer,
                "repeat_count": active_config.repeat_count,
                "block_len": active_config.block_len,
            },
            "plan": None if active_plan is None else {
                "execution_order": active_plan.execution_order,
            },
        },
        "scan_settings": scan_settings_payload,
        "total_candidate_count": total_candidate_count,
        "preview_candidate_count": len(preview_candidates),
        "preview_candidates": preview_candidates,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
