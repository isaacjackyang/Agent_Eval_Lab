from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from evolution.relayer_plan import resolve_relayer_scan_runtime_mode, resolve_relayer_scan_settings


DEFAULT_AGENT_ARCHITECTURE: dict[str, Any] = {
    "variant": "baseline",
    "prompt_style": "strict_json",
    "query_policy": "focused_then_broad",
    "recovery_policy": "ranked",
    "search_result_limit": 5,
}

SAMPLING_PROVIDER_ALIASES: dict[str, str] = {
    "ollama": "ollama",
    "llama-cpp": "llama-cpp",
    "llama.cpp": "llama-cpp",
    "llama_cpp": "llama-cpp",
}

EVOLUTION_MODE_OPTIONS: list[dict[str, str]] = [
    {
        "value": "model_params",
        "label": "模型參數 / Model Params",
        "description": "Mutate provider-specific sampling parameters such as top_p, temperature, top_k, or repeat penalties.",
    },
    {
        "value": "architecture_program",
        "label": "架構程式 / Architecture Program",
        "description": "Mutate retrieval loop policies such as prompt style, query strategy, recovery, and search window.",
    },
    {
        "value": "heat_map",
        "label": "熱區掃描 / Heat Map",
        "description": "Scan a two-axis architecture grid against a fixed baseline and build a heat-map style candidate ranking.",
    },
]

ARCHITECTURE_EVOLUTION_RUNNERS = {
    "llama_cpp_agent",
    "openclaw_cli",
}

SAMPLING_PARAMETER_CATALOG: list[dict[str, Any]] = [
    {
        "id": "temperature",
        "label": "temperature",
        "description": "Sampling temperature.",
        "providers": ("ollama", "llama-cpp"),
        "path": ("llama_cpp", "temperature"),
        "default_by_provider": {"ollama": 0.8, "llama-cpp": 0.8},
        "step": 0.1,
        "minimum": 0.0,
        "maximum": 1.5,
        "precision": 2,
    },
    {
        "id": "top_p",
        "label": "top_p",
        "description": "Nucleus sampling threshold.",
        "providers": ("ollama", "llama-cpp"),
        "path": ("llama_cpp", "top_p"),
        "default_by_provider": {"ollama": 0.9, "llama-cpp": 0.95},
        "step": 0.05,
        "minimum": 0.1,
        "maximum": 1.0,
        "precision": 2,
    },
    {
        "id": "top_k",
        "label": "top_k",
        "description": "Top-k token cutoff.",
        "providers": ("ollama", "llama-cpp"),
        "path": ("llama_cpp", "top_k"),
        "default_by_provider": {"ollama": 40, "llama-cpp": 40},
        "step": 5,
        "minimum": 1,
        "maximum": 100,
    },
    {
        "id": "min_p",
        "label": "min_p",
        "description": "Minimum relative probability floor.",
        "providers": ("ollama", "llama-cpp"),
        "path": ("llama_cpp", "min_p"),
        "default_by_provider": {"ollama": 0.0, "llama-cpp": 0.05},
        "step": 0.05,
        "minimum": 0.0,
        "maximum": 0.5,
        "precision": 2,
    },
    {
        "id": "repeat_penalty",
        "label": "repeat_penalty",
        "description": "Penalty applied to repeated tokens.",
        "providers": ("ollama", "llama-cpp"),
        "path": ("llama_cpp", "repeat_penalty"),
        "default_by_provider": {"ollama": 1.1, "llama-cpp": 1.1},
        "step": 0.05,
        "minimum": 0.8,
        "maximum": 1.5,
        "precision": 2,
    },
    {
        "id": "repeat_last_n",
        "label": "repeat_last_n",
        "description": "How many recent tokens are considered for repetition penalty.",
        "providers": ("ollama", "llama-cpp"),
        "path": ("llama_cpp", "repeat_last_n"),
        "default_by_provider": {"ollama": 64, "llama-cpp": 64},
        "step": 16,
        "minimum": 0,
        "maximum": 256,
    },
    {
        "id": "seed",
        "label": "seed",
        "description": "Random seed for reproducible sampling.",
        "providers": ("ollama", "llama-cpp"),
        "path": ("llama_cpp", "seed"),
        "default_by_provider": {"ollama": 0, "llama-cpp": -1},
        "step": 17,
        "minimum": -1,
        "maximum": 9999,
    },
    {
        "id": "presence_penalty",
        "label": "presence_penalty",
        "description": "Penalty for reusing already-present tokens.",
        "providers": ("llama-cpp",),
        "path": ("llama_cpp", "presence_penalty"),
        "default_by_provider": {"llama-cpp": 0.0},
        "step": 0.1,
        "minimum": 0.0,
        "maximum": 2.0,
        "precision": 2,
    },
    {
        "id": "frequency_penalty",
        "label": "frequency_penalty",
        "description": "Penalty for frequent tokens.",
        "providers": ("llama-cpp",),
        "path": ("llama_cpp", "frequency_penalty"),
        "default_by_provider": {"llama-cpp": 0.0},
        "step": 0.1,
        "minimum": 0.0,
        "maximum": 2.0,
        "precision": 2,
    },
]


def _build_model_parameter_mutations() -> list[dict[str, Any]]:
    mutations: list[dict[str, Any]] = []
    for catalog in SAMPLING_PARAMETER_CATALOG:
        step = catalog["step"]
        for direction, delta in (("up", step), ("down", -step)):
            mutations.append(
                {
                    "parameter_id": catalog["id"],
                    "providers": catalog["providers"],
                    "default_by_provider": catalog.get("default_by_provider", {}),
                    "name": f"{catalog['id']}_{direction}",
                    "path": catalog["path"],
                    "delta": delta,
                    "minimum": catalog["minimum"],
                    "maximum": catalog["maximum"],
                    "precision": catalog.get("precision"),
                    "notes": f"{'Increase' if direction == 'up' else 'Decrease'} {catalog['id']} by {abs(step)}.",
                }
            )
    return mutations


MODEL_PARAMETER_MUTATIONS = _build_model_parameter_mutations()
SINGLE_PARAMETER_MUTATIONS = MODEL_PARAMETER_MUTATIONS

ARCHITECTURE_MUTATION_PRESETS: list[dict[str, Any]] = [
    {
        "name": "planner_strict",
        "changes": {
            "agent_architecture": {
                "variant": "planner_strict",
                "prompt_style": "planner",
                "query_policy": "focused_then_broad",
                "recovery_policy": "ranked",
                "search_result_limit": 6,
            }
        },
        "notes": "Use a planner-style prompt with focused-first search and ranked recovery.",
    },
    {
        "name": "broad_recall",
        "changes": {
            "agent_architecture": {
                "variant": "broad_recall",
                "prompt_style": "recall",
                "query_policy": "broad_then_focused",
                "recovery_policy": "ranked",
                "search_result_limit": 8,
            }
        },
        "notes": "Favor broad recall first, then refine with focused search using a larger candidate window.",
    },
    {
        "name": "strict_no_recovery",
        "changes": {
            "agent_architecture": {
                "variant": "strict_no_recovery",
                "prompt_style": "strict_json",
                "query_policy": "focused_only",
                "recovery_policy": "none",
                "search_result_limit": 4,
            }
        },
        "notes": "Use a stricter control loop with focused-only search and no fallback recovery.",
    },
    {
        "name": "aggressive_recovery",
        "changes": {
            "agent_architecture": {
                "variant": "aggressive_recovery",
                "prompt_style": "planner",
                "query_policy": "focused_then_broad",
                "recovery_policy": "signal_boost",
                "search_result_limit": 8,
            }
        },
        "notes": "Keep structured planning but widen search results and strengthen recovery heuristics.",
    },
]

HEAT_MAP_SCAN_FIELD_OPTIONS: list[dict[str, Any]] = [
    {
        "value": "relayer.end_layer",
        "label": "End Layer (j)",
        "description": "RYS x-axis: the end layer of the duplicated block.",
    },
    {
        "value": "relayer.start_layer",
        "label": "Start Layer (i)",
        "description": "RYS y-axis: the start layer of the duplicated block.",
    },
    {
        "value": "relayer.repeat_count",
        "label": "Repeat Count",
        "description": "How many extra traversals of the duplicated block are applied.",
    },
]

HEAT_MAP_TASK_TYPE_OPTIONS: list[dict[str, str]] = [
    {"value": "deployment", "label": "Deployment"},
    {"value": "handoff", "label": "Handoff"},
    {"value": "operations", "label": "Operations"},
    {"value": "math", "label": "Math Calculation"},
]

DEFAULT_HEAT_MAP_PROBE_A = {
    "id": "probe_a",
    "label": "Deployment Probe",
    "task_type": "deployment",
    "seeds": [1101, 1103, 1105, 1107],
}

DEFAULT_HEAT_MAP_PROBE_B = {
    "id": "probe_b",
    "label": "Handoff Probe",
    "task_type": "handoff",
    "seeds": [2101, 2103, 2105, 2107],
}


def normalize_weights(weights: dict) -> dict:
    total = sum(float(value) for value in weights.values())
    if total <= 0:
        return weights
    return {key: round(float(value) / total, 6) for key, value in weights.items()}


def build_candidate_variant(
    base_config: dict,
    suffix: str,
    changes: dict,
    notes: str,
    extra_metadata: dict | None = None,
) -> dict:
    config = copy.deepcopy(base_config)
    config["config_id"] = f"{base_config['config_id']}__{suffix}"
    config["mutation_profile"] = suffix
    config["mutation_notes"] = notes

    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value

    if "weights" in config and isinstance(config["weights"], dict):
        config["weights"] = normalize_weights(config["weights"])
    if extra_metadata:
        config.update(copy.deepcopy(extra_metadata))
    return config


def _slugify(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "candidate"


def _get_nested(config: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = config
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _set_nested(changes: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cursor = changes
    for key in path[:-1]:
        cursor = cursor.setdefault(key, {})
    cursor[path[-1]] = value


def effective_agent_architecture(config: dict[str, Any]) -> dict[str, Any]:
    effective = copy.deepcopy(DEFAULT_AGENT_ARCHITECTURE)
    user_config = config.get("agent_architecture", {})
    if isinstance(user_config, dict):
        effective.update(user_config)
    return effective


def supports_architecture_evolution(config: dict[str, Any]) -> bool:
    runner = str(config.get("runner", "")).strip()
    return runner in ARCHITECTURE_EVOLUTION_RUNNERS


def heat_map_dimension_options() -> list[dict[str, Any]]:
    return copy.deepcopy(HEAT_MAP_SCAN_FIELD_OPTIONS)


def heat_map_task_type_options() -> list[dict[str, str]]:
    return copy.deepcopy(HEAT_MAP_TASK_TYPE_OPTIONS)


def _normalize_heat_map_seed_values(raw_values: Any, *, fallback: list[int]) -> list[int]:
    if raw_values is None:
        return list(fallback)
    if not isinstance(raw_values, list):
        raise ValueError("Heat-map probe seeds must be a JSON list.")

    seeds: list[int] = []
    for item in raw_values:
        value = int(item)
        if value not in seeds:
            seeds.append(value)
    if not seeds:
        raise ValueError("Heat-map probe seeds cannot be empty.")
    return seeds


def _normalize_heat_map_task_type(raw_value: Any, *, default: str) -> str:
    task_type = str(raw_value or default).strip().lower() or default
    valid = {item["value"] for item in HEAT_MAP_TASK_TYPE_OPTIONS}
    if task_type not in valid:
        raise ValueError(f"Unsupported heat-map probe task type: {task_type}")
    return task_type


def _resolve_heat_map_probe(heat_map_cfg: dict[str, Any], *, key: str, fallback: dict[str, Any]) -> dict[str, Any]:
    raw_probe = heat_map_cfg.get(key, {})
    if not isinstance(raw_probe, dict):
        raw_probe = {}
    task_type = _normalize_heat_map_task_type(raw_probe.get("task_type"), default=str(fallback["task_type"]))
    return {
        "id": str(raw_probe.get("id") or fallback["id"]),
        "label": str(raw_probe.get("label") or fallback["label"]),
        "task_type": task_type,
        "seeds": _normalize_heat_map_seed_values(raw_probe.get("seeds"), fallback=list(fallback["seeds"])),
    }


def apply_heat_map_overrides(
    base_config: dict[str, Any],
    *,
    x_axis: str | None = None,
    y_axis: str | None = None,
    x_values: list[Any] | None = None,
    y_values: list[Any] | None = None,
    top_k: int | None = None,
    verify_overrides: dict[str, Any] | None = None,
    start_layer_min: int | None = None,
    end_layer_max: int | None = None,
    min_block_len: int | None = None,
    max_block_len: int | None = None,
    repeat_count: int | None = None,
    probe_a_task_type: str | None = None,
    probe_a_seeds: list[int] | None = None,
    probe_b_task_type: str | None = None,
    probe_b_seeds: list[int] | None = None,
) -> dict[str, Any]:
    config = copy.deepcopy(base_config)
    nightly_cfg = config.setdefault("nightly", {})
    if not isinstance(nightly_cfg, dict):
        nightly_cfg = {}
        config["nightly"] = nightly_cfg

    heat_map_cfg = nightly_cfg.get("heat_map", {})
    if not isinstance(heat_map_cfg, dict):
        heat_map_cfg = {}
    nightly_cfg["heat_map"] = heat_map_cfg

    # Legacy fields are still accepted so older configs do not crash, but
    # RYS-style heat-map scans now use relayer start/end windows plus probe sets.
    if x_axis:
        heat_map_cfg["x_axis"] = str(x_axis).strip()
    if y_axis:
        heat_map_cfg["y_axis"] = str(y_axis).strip()
    if x_values is not None:
        heat_map_cfg["x_values"] = list(x_values)
    if y_values is not None:
        heat_map_cfg["y_values"] = list(y_values)
    if top_k is not None:
        heat_map_cfg["top_k"] = int(top_k)

    scan_cfg = heat_map_cfg.get("scan", {})
    if not isinstance(scan_cfg, dict):
        scan_cfg = {}
    if start_layer_min is not None:
        scan_cfg["start_layer_min"] = int(start_layer_min)
    if end_layer_max is not None:
        scan_cfg["end_layer_max"] = int(end_layer_max)
    if min_block_len is not None:
        scan_cfg["min_block_len"] = int(min_block_len)
    if max_block_len is not None:
        scan_cfg["max_block_len"] = int(max_block_len)
    if repeat_count is not None:
        scan_cfg["repeat_count"] = int(repeat_count)
    if scan_cfg:
        heat_map_cfg["scan"] = scan_cfg

    if probe_a_task_type is not None or probe_a_seeds is not None:
        probe_a_cfg = heat_map_cfg.get("probe_a", {})
        if not isinstance(probe_a_cfg, dict):
            probe_a_cfg = {}
        if probe_a_task_type is not None:
            probe_a_cfg["task_type"] = str(probe_a_task_type).strip().lower()
        if probe_a_seeds is not None:
            probe_a_cfg["seeds"] = [int(item) for item in probe_a_seeds]
        heat_map_cfg["probe_a"] = probe_a_cfg

    if probe_b_task_type is not None or probe_b_seeds is not None:
        probe_b_cfg = heat_map_cfg.get("probe_b", {})
        if not isinstance(probe_b_cfg, dict):
            probe_b_cfg = {}
        if probe_b_task_type is not None:
            probe_b_cfg["task_type"] = str(probe_b_task_type).strip().lower()
        if probe_b_seeds is not None:
            probe_b_cfg["seeds"] = [int(item) for item in probe_b_seeds]
        heat_map_cfg["probe_b"] = probe_b_cfg

    if verify_overrides:
        verify_cfg = heat_map_cfg.get("verify", {})
        if not isinstance(verify_cfg, dict):
            verify_cfg = {}
        for key, value in verify_overrides.items():
            if value is not None:
                verify_cfg[key] = value
        heat_map_cfg["verify"] = verify_cfg

    return config


def normalize_sampling_provider(raw_value: Any, *, default: str | None = None) -> str | None:
    normalized = str(raw_value or "").strip().lower()
    if not normalized:
        return default
    return SAMPLING_PROVIDER_ALIASES.get(normalized)


def sampling_provider_for_config(config: dict[str, Any]) -> str | None:
    if config.get("runner") != "llama_cpp_agent":
        return None
    return normalize_sampling_provider(config.get("llama_cpp", {}).get("provider"), default="llama-cpp")


def sampling_parameters_for_provider(provider: str | None) -> list[dict[str, Any]]:
    normalized = str(provider or "").strip().lower()
    if not normalized:
        return []
    return [
        {
            "id": item["id"],
            "label": item["label"],
            "description": item["description"],
        }
        for item in SAMPLING_PARAMETER_CATALOG
        if normalized in item["providers"]
    ]


def selected_sampling_parameters_for_config(config: dict[str, Any]) -> list[str]:
    provider = sampling_provider_for_config(config)
    available = [item["id"] for item in sampling_parameters_for_provider(provider)]
    supported = set(available)
    configured = config.get("nightly", {}).get("sampling_parameters")
    if isinstance(configured, list) and configured:
        configured_set = {str(item).strip() for item in configured}
        return [item for item in available if item in configured_set and item in supported]
    return available


def resolve_heat_map_plan(base_config: dict[str, Any]) -> dict[str, Any]:
    nightly_cfg = base_config.get("nightly", {})
    heat_map_cfg = nightly_cfg.get("heat_map", {})
    if not isinstance(heat_map_cfg, dict):
        heat_map_cfg = {}
    scan_cfg = heat_map_cfg.get("scan", {})
    if not isinstance(scan_cfg, dict):
        scan_cfg = {}

    relayer_scan_settings = resolve_relayer_scan_settings(base_config)
    start_layer_min = int(scan_cfg.get("start_layer_min", relayer_scan_settings.start_layer_min))
    end_layer_max = int(scan_cfg.get("end_layer_max", relayer_scan_settings.end_layer_max))
    min_block_len = int(scan_cfg.get("min_block_len", relayer_scan_settings.min_block_len))
    raw_max_block_len = scan_cfg.get("max_block_len", relayer_scan_settings.max_block_len)
    max_block_len = int(raw_max_block_len) if raw_max_block_len not in (None, "") else None
    repeat_count = int(scan_cfg.get("repeat_count", relayer_scan_settings.repeat_count))

    if start_layer_min < 0:
        raise ValueError("Heat-map start_layer_min must be >= 0.")
    if end_layer_max < start_layer_min:
        raise ValueError("Heat-map end_layer_max must be >= start_layer_min.")
    if end_layer_max >= relayer_scan_settings.num_layers:
        raise ValueError("Heat-map end_layer_max must be < relayer.num_layers.")
    if min_block_len < 1:
        raise ValueError("Heat-map min_block_len must be >= 1.")
    if max_block_len is not None and max_block_len < min_block_len:
        raise ValueError("Heat-map max_block_len must be >= min_block_len.")
    if repeat_count < 1:
        raise ValueError("Heat-map repeat_count must be >= 1.")

    probe_a = _resolve_heat_map_probe(heat_map_cfg, key="probe_a", fallback=DEFAULT_HEAT_MAP_PROBE_A)
    probe_b = _resolve_heat_map_probe(heat_map_cfg, key="probe_b", fallback=DEFAULT_HEAT_MAP_PROBE_B)

    valid_cells = 0
    for start_layer in range(start_layer_min, end_layer_max + 1):
        for end_layer in range(start_layer, end_layer_max + 1):
            block_len = end_layer - start_layer + 1
            if block_len < min_block_len:
                continue
            if max_block_len is not None and block_len > max_block_len:
                continue
            valid_cells += 1

    top_k = int(heat_map_cfg.get("top_k", min(5, valid_cells or 1)))
    top_k = max(1, min(top_k, max(1, valid_cells)))
    runtime_mode = resolve_relayer_scan_runtime_mode(base_config)
    x_values = list(range(start_layer_min, end_layer_max + 1))
    y_values = list(range(start_layer_min, end_layer_max + 1))

    return {
        "heat_map_type": "rys_brain_scan",
        "x_axis": "relayer.end_layer",
        "x_label": "End Layer (j)",
        "x_values": x_values,
        "y_axis": "relayer.start_layer",
        "y_label": "Start Layer (i)",
        "y_values": y_values,
        "cell_count": valid_cells,
        "top_k": top_k,
        "num_layers": relayer_scan_settings.num_layers,
        "repeat_count": repeat_count,
        "runtime_mode": runtime_mode,
        "scan": {
            "start_layer_min": start_layer_min,
            "end_layer_max": end_layer_max,
            "min_block_len": min_block_len,
            "max_block_len": max_block_len,
            "repeat_count": repeat_count,
        },
        "probe_a": probe_a,
        "probe_b": probe_b,
        "probe_eval_count": len(probe_a["seeds"]) + len(probe_b["seeds"]),
        "baseline_coordinate": {
            "x_axis": "relayer.end_layer",
            "x_label": "End Layer (j)",
            "x_value": None,
            "y_axis": "relayer.start_layer",
            "y_label": "Start Layer (i)",
            "y_value": None,
        },
    }


def build_heat_map_candidates(base_config: dict[str, Any]) -> list[dict[str, Any]]:
    plan = resolve_heat_map_plan(base_config)
    candidates: list[dict[str, Any]] = []
    scan = plan["scan"]
    for start_layer in range(scan["start_layer_min"], scan["end_layer_max"] + 1):
        for end_layer in range(start_layer, scan["end_layer_max"] + 1):
            block_len = end_layer - start_layer + 1
            if block_len < scan["min_block_len"]:
                continue
            if scan["max_block_len"] is not None and block_len > scan["max_block_len"]:
                continue

            repeat_count = int(plan["repeat_count"])
            extra_layers = block_len * repeat_count
            changes = {
                "relayer": {
                    "enabled": True,
                    "mode": plan["runtime_mode"],
                    "start_layer": start_layer,
                    "end_layer": end_layer,
                    "repeat_count": repeat_count,
                }
            }
            suffix = f"heat_map__s{start_layer}_e{end_layer}_r{repeat_count}"
            notes = (
                f"RYS-style brain-scan cell with Start Layer (i)={start_layer}, "
                f"End Layer (j)={end_layer}, repeat_count={repeat_count}, block_len={block_len}."
            )
            preview = build_candidate_variant(
                base_config=base_config,
                suffix=suffix,
                changes=changes,
                notes=notes,
            )
            candidate = build_candidate_variant(
                base_config=base_config,
                suffix=suffix,
                changes=changes,
                notes=notes,
                extra_metadata={
                    "mutation_strategy": "heat_map_scan",
                    "mutation_target": "relayer.start_layer x relayer.end_layer",
                    "mutation_before": {
                        "relayer.enabled": False,
                        "relayer.start_layer": base_config.get("relayer", {}).get("start_layer"),
                        "relayer.end_layer": base_config.get("relayer", {}).get("end_layer"),
                        "relayer.repeat_count": base_config.get("relayer", {}).get("repeat_count"),
                    },
                    "mutation_after": {
                        "relayer.enabled": True,
                        "relayer.start_layer": start_layer,
                        "relayer.end_layer": end_layer,
                        "relayer.repeat_count": repeat_count,
                    },
                    "parameter_snapshot": parameter_snapshot(preview),
                    "heat_map_coordinates": {
                        "x_axis": plan["x_axis"],
                        "x_label": plan["x_label"],
                        "x_value": end_layer,
                        "y_axis": plan["y_axis"],
                        "y_label": plan["y_label"],
                        "y_value": start_layer,
                        "start_layer": start_layer,
                        "end_layer": end_layer,
                        "repeat_count": repeat_count,
                        "block_len": block_len,
                        "extra_layers": extra_layers,
                    },
                },
            )
            candidate["parameter_snapshot"] = parameter_snapshot(candidate)
            candidates.append(candidate)
    return candidates


def heat_map_candidate_count(base_config: dict[str, Any]) -> int:
    return len(build_heat_map_candidates(base_config))


def _sampling_default_for_mutation(spec: dict[str, Any], provider: str) -> Any:
    defaults = spec.get("default_by_provider") or {}
    if provider in defaults:
        return defaults[provider]
    if "default" in spec:
        return spec["default"]
    return None


def parameter_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    architecture = effective_agent_architecture(config)
    return {
        "max_steps": config.get("max_steps"),
        "time_budget_sec": config.get("time_budget_sec"),
        "efficiency_caps.steps": config.get("efficiency_caps", {}).get("steps"),
        "efficiency_caps.tokens": config.get("efficiency_caps", {}).get("tokens"),
        "efficiency_caps.retries": config.get("efficiency_caps", {}).get("retries"),
        "llama_cpp.temperature": config.get("llama_cpp", {}).get("temperature"),
        "llama_cpp.top_p": config.get("llama_cpp", {}).get("top_p"),
        "llama_cpp.top_k": config.get("llama_cpp", {}).get("top_k"),
        "llama_cpp.min_p": config.get("llama_cpp", {}).get("min_p"),
        "llama_cpp.repeat_penalty": config.get("llama_cpp", {}).get("repeat_penalty"),
        "llama_cpp.repeat_last_n": config.get("llama_cpp", {}).get("repeat_last_n"),
        "llama_cpp.seed": config.get("llama_cpp", {}).get("seed"),
        "llama_cpp.presence_penalty": config.get("llama_cpp", {}).get("presence_penalty"),
        "llama_cpp.frequency_penalty": config.get("llama_cpp", {}).get("frequency_penalty"),
        "llama_cpp.max_output_tokens": config.get("llama_cpp", {}).get("max_output_tokens"),
        "llama_cpp.timeout_sec": config.get("llama_cpp", {}).get("timeout_sec"),
        "openclaw.thinking": config.get("openclaw", {}).get("thinking"),
        "agent_architecture.variant": architecture.get("variant"),
        "agent_architecture.prompt_style": architecture.get("prompt_style"),
        "agent_architecture.query_policy": architecture.get("query_policy"),
        "agent_architecture.recovery_policy": architecture.get("recovery_policy"),
        "agent_architecture.search_result_limit": architecture.get("search_result_limit"),
        "relayer.enabled": config.get("relayer", {}).get("enabled"),
        "relayer.mode": config.get("relayer", {}).get("mode"),
        "relayer.start_layer": config.get("relayer", {}).get("start_layer"),
        "relayer.end_layer": config.get("relayer", {}).get("end_layer"),
        "relayer.repeat_count": config.get("relayer", {}).get("repeat_count"),
    }


def _apply_single_parameter_mutation(base_config: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any] | None:
    current_value = _get_nested(base_config, spec["path"])
    if current_value is None:
        if "default" not in spec:
            return None
        current_value = spec["default"]

    if spec.get("mode") == "next_choice":
        choices = list(spec.get("choices", []))
        if current_value not in choices:
            return None
        index = choices.index(current_value)
        if index >= len(choices) - 1:
            return None
        mutated_value = choices[index + 1]
    else:
        mutated_value = float(current_value) + float(spec.get("delta", 0))
        mutated_value = max(float(spec.get("minimum", mutated_value)), mutated_value)
        mutated_value = min(float(spec.get("maximum", mutated_value)), mutated_value)
        precision = spec.get("precision")
        if precision is not None:
            mutated_value = round(mutated_value, int(precision))
        elif isinstance(current_value, int):
            mutated_value = int(round(mutated_value))

    if mutated_value == current_value:
        return None

    changes: dict[str, Any] = {}
    _set_nested(changes, spec["path"], mutated_value)
    parameter_name = ".".join(spec["path"])
    suffix = f"{spec['name']}_{_slugify(str(mutated_value))}"
    notes = spec["notes"]
    return build_candidate_variant(
        base_config=base_config,
        suffix=suffix,
        changes=changes,
        notes=notes,
        extra_metadata={
            "mutation_strategy": "single_parameter",
            "mutation_target": parameter_name,
            "mutation_before": current_value,
            "mutation_after": mutated_value,
            "parameter_snapshot": parameter_snapshot({**copy.deepcopy(base_config), **changes}),
        },
    )


def build_single_parameter_candidates(base_config: dict[str, Any]) -> list[dict]:
    provider = sampling_provider_for_config(base_config)
    selected_parameters = set(selected_sampling_parameters_for_config(base_config))
    if not provider or not selected_parameters:
        return []

    candidates: list[dict] = []
    for spec in MODEL_PARAMETER_MUTATIONS:
        if provider not in spec.get("providers", ()):
            continue
        if spec.get("parameter_id") not in selected_parameters:
            continue
        working_spec = dict(spec)
        default_value = _sampling_default_for_mutation(working_spec, provider)
        if default_value is not None:
            working_spec["default"] = default_value
        candidate = _apply_single_parameter_mutation(base_config, working_spec)
        if candidate is not None:
            candidate["parameter_snapshot"] = parameter_snapshot(candidate)
            candidates.append(candidate)
    return candidates


def build_architecture_candidates(base_config: dict[str, Any]) -> list[dict]:
    current_architecture = effective_agent_architecture(base_config)
    current_variant = str(current_architecture.get("variant", "baseline"))
    candidates: list[dict[str, Any]] = []
    for preset in ARCHITECTURE_MUTATION_PRESETS:
        if preset["name"] == current_variant:
            continue
        candidate = build_candidate_variant(
            base_config=base_config,
            suffix=preset["name"],
            changes=preset["changes"],
            notes=preset["notes"],
            extra_metadata={
                "mutation_strategy": "architecture_program",
                "mutation_target": "agent_architecture.variant",
                "mutation_before": current_variant,
                "mutation_after": preset["name"],
                "parameter_snapshot": parameter_snapshot(
                    build_candidate_variant(
                        base_config=base_config,
                        suffix=preset["name"],
                        changes=preset["changes"],
                        notes=preset["notes"],
                    )
                ),
                "architecture_summary": preset["notes"],
            },
        )
        candidate["parameter_snapshot"] = parameter_snapshot(candidate)
        candidates.append(candidate)
    return candidates


def build_mutation_candidates(base_config: dict[str, Any], evolution_mode: str) -> list[dict[str, Any]]:
    mode = str(evolution_mode or "model_params").strip().lower() or "model_params"
    if mode == "model_params":
        return build_single_parameter_candidates(base_config)
    if mode == "architecture_program":
        return build_architecture_candidates(base_config)
    if mode == "heat_map":
        return build_heat_map_candidates(base_config)
    raise ValueError(f"Unsupported evolution mode: {evolution_mode}")


def build_candidate_pool(base_config: dict, pool_size: int, include_base: bool = True) -> list[dict]:
    variants: list[dict] = []
    if include_base:
        config = copy.deepcopy(base_config)
        config["mutation_profile"] = "baseline"
        config["mutation_notes"] = "Original experiment config."
        config["parameter_snapshot"] = parameter_snapshot(config)
        variants.append(config)

    for candidate in build_single_parameter_candidates(base_config):
        if len(variants) >= pool_size:
            break
        variants.append(candidate)

    return variants[:pool_size]


def persist_candidate_pool(root: Path, suite_id: str, candidates: list[dict]) -> list[Path]:
    target_dir = root / "configs" / "candidates" / suite_id
    target_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index, candidate in enumerate(candidates, start=1):
        path = target_dir / f"{index:02d}_{_slugify(candidate['config_id'])}.json"
        path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")
        paths.append(path)
    return paths


def persist_candidate_config(root: Path, suite_id: str, round_index: int, candidate: dict[str, Any]) -> Path:
    target_dir = root / "configs" / "candidates" / suite_id
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{round_index:02d}_{_slugify(candidate['config_id'])}.json"
    path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
