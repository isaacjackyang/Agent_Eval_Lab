from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evolution.relayer_plan import (
    build_relayer_plan,
    generate_relayer_configs,
    relayer_config_id,
    relayer_scan_candidate_count,
    resolve_relayer_model_name,
    resolve_active_relayer_config,
    resolve_active_relayer_plan,
    resolve_relayer_num_layers,
    resolve_relayer_scan_settings,
    resolve_relayer_scan_runtime_mode,
)


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

    scan_settings = resolve_relayer_scan_settings(config)
    scan_runtime_mode = resolve_relayer_scan_runtime_mode(config)
    preview_candidates = []
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

    payload = {
        "config_path": str(config_path),
        "config_id": config.get("config_id"),
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
        "scan_settings": {
            "num_layers": scan_settings.num_layers,
            "start_layer_min": scan_settings.start_layer_min,
            "end_layer_max": scan_settings.end_layer_max,
            "min_block_len": scan_settings.min_block_len,
            "max_block_len": scan_settings.max_block_len,
            "repeat_count": scan_settings.repeat_count,
            "runtime_mode": scan_runtime_mode,
        },
        "total_candidate_count": relayer_scan_candidate_count(config),
        "preview_candidate_count": len(preview_candidates),
        "preview_candidates": preview_candidates,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
