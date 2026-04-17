from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any


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

HEAT_MAP_DIMENSION_CATALOG: dict[str, dict[str, Any]] = {
    "agent_architecture.prompt_style": {
        "label": "Prompt Style",
        "path": ("agent_architecture", "prompt_style"),
        "choices": ("strict_json", "planner", "recall"),
    },
    "agent_architecture.query_policy": {
        "label": "Query Policy",
        "path": ("agent_architecture", "query_policy"),
        "choices": ("focused_only", "focused_then_broad", "broad_then_focused"),
    },
    "agent_architecture.recovery_policy": {
        "label": "Recovery Policy",
        "path": ("agent_architecture", "recovery_policy"),
        "choices": ("none", "ranked", "signal_boost"),
    },
    "agent_architecture.search_result_limit": {
        "label": "Search Result Limit",
        "path": ("agent_architecture", "search_result_limit"),
        "choices": (4, 5, 6, 8),
    },
}

DEFAULT_HEAT_MAP_X_AXIS = "agent_architecture.search_result_limit"
DEFAULT_HEAT_MAP_Y_AXIS = "agent_architecture.query_policy"


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


def _heat_map_dimension_spec(dimension_id: str) -> dict[str, Any]:
    spec = HEAT_MAP_DIMENSION_CATALOG.get(dimension_id)
    if spec is None:
        raise ValueError(f"Unsupported heat-map axis: {dimension_id}")
    return spec


def _normalize_heat_map_values(raw_values: Any, spec: dict[str, Any], *, axis_name: str) -> list[Any]:
    default_values = list(spec["choices"])
    if raw_values is None:
        return default_values
    if not isinstance(raw_values, list):
        raise ValueError(f"Heat-map {axis_name} values must be a JSON list.")

    lookup = {str(choice).strip(): choice for choice in default_values}
    normalized: list[Any] = []
    for item in raw_values:
        key = str(item).strip()
        if key not in lookup:
            raise ValueError(f"Unsupported heat-map value for {axis_name}: {item}")
        value = lookup[key]
        if value not in normalized:
            normalized.append(value)
    if not normalized:
        raise ValueError(f"Heat-map {axis_name} values cannot be empty.")
    return normalized


def _heat_map_value_for_path(config: dict[str, Any], path: tuple[str, ...]) -> Any:
    if path and path[0] == "agent_architecture":
        architecture = effective_agent_architecture(config)
        if len(path) == 2:
            return architecture.get(path[1])
    return _get_nested(config, path)


def resolve_heat_map_plan(base_config: dict[str, Any]) -> dict[str, Any]:
    nightly_cfg = base_config.get("nightly", {})
    heat_map_cfg = nightly_cfg.get("heat_map", {})
    if not isinstance(heat_map_cfg, dict):
        heat_map_cfg = {}

    x_axis = str(heat_map_cfg.get("x_axis", DEFAULT_HEAT_MAP_X_AXIS)).strip() or DEFAULT_HEAT_MAP_X_AXIS
    y_axis = str(heat_map_cfg.get("y_axis", DEFAULT_HEAT_MAP_Y_AXIS)).strip() or DEFAULT_HEAT_MAP_Y_AXIS
    if x_axis == y_axis:
        raise ValueError("Heat-map x_axis and y_axis must be different.")

    x_spec = _heat_map_dimension_spec(x_axis)
    y_spec = _heat_map_dimension_spec(y_axis)
    x_values = _normalize_heat_map_values(heat_map_cfg.get("x_values"), x_spec, axis_name="x_axis")
    y_values = _normalize_heat_map_values(heat_map_cfg.get("y_values"), y_spec, axis_name="y_axis")

    baseline_x = _heat_map_value_for_path(base_config, x_spec["path"])
    baseline_y = _heat_map_value_for_path(base_config, y_spec["path"])
    if baseline_x is None:
        baseline_x = x_values[0]
    if baseline_y is None:
        baseline_y = y_values[0]

    cell_count = len(x_values) * len(y_values)
    top_k = int(heat_map_cfg.get("top_k", min(5, cell_count)))
    top_k = max(1, min(top_k, cell_count))

    return {
        "x_axis": x_axis,
        "x_label": x_spec["label"],
        "x_path": x_spec["path"],
        "x_values": x_values,
        "y_axis": y_axis,
        "y_label": y_spec["label"],
        "y_path": y_spec["path"],
        "y_values": y_values,
        "cell_count": cell_count,
        "top_k": top_k,
        "baseline_coordinate": {
            "x_axis": x_axis,
            "x_label": x_spec["label"],
            "x_value": baseline_x,
            "y_axis": y_axis,
            "y_label": y_spec["label"],
            "y_value": baseline_y,
        },
    }


def build_heat_map_candidates(base_config: dict[str, Any]) -> list[dict[str, Any]]:
    plan = resolve_heat_map_plan(base_config)
    baseline_x = plan["baseline_coordinate"]["x_value"]
    baseline_y = plan["baseline_coordinate"]["y_value"]

    candidates: list[dict[str, Any]] = []
    for y_value in plan["y_values"]:
        for x_value in plan["x_values"]:
            if x_value == baseline_x and y_value == baseline_y:
                continue

            changes: dict[str, Any] = {}
            _set_nested(changes, plan["x_path"], x_value)
            _set_nested(changes, plan["y_path"], y_value)

            suffix = (
                "heat_map__"
                f"{_slugify(plan['y_axis'])}_{_slugify(str(y_value))}__"
                f"{_slugify(plan['x_axis'])}_{_slugify(str(x_value))}"
            )
            notes = (
                f"Heat-map scan cell with {plan['y_label']}={y_value} "
                f"and {plan['x_label']}={x_value}."
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
                    "mutation_target": f"{plan['y_axis']} x {plan['x_axis']}",
                    "mutation_before": {
                        plan["x_axis"]: baseline_x,
                        plan["y_axis"]: baseline_y,
                    },
                    "mutation_after": {
                        plan["x_axis"]: x_value,
                        plan["y_axis"]: y_value,
                    },
                    "parameter_snapshot": parameter_snapshot(preview),
                    "heat_map_coordinates": {
                        "x_axis": plan["x_axis"],
                        "x_label": plan["x_label"],
                        "x_value": x_value,
                        "y_axis": plan["y_axis"],
                        "y_label": plan["y_label"],
                        "y_value": y_value,
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
