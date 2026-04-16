from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any


SINGLE_PARAMETER_MUTATIONS: list[dict[str, Any]] = [
    {
        "name": "max_steps_up",
        "path": ("max_steps",),
        "delta": 1,
        "minimum": 4,
        "maximum": 12,
        "notes": "Increase max_steps by 1.",
    },
    {
        "name": "time_budget_up",
        "path": ("time_budget_sec",),
        "delta": 6,
        "minimum": 18,
        "maximum": 120,
        "notes": "Increase time_budget_sec by 6 seconds.",
    },
    {
        "name": "efficiency_steps_up",
        "path": ("efficiency_caps", "steps"),
        "delta": 1,
        "minimum": 4,
        "maximum": 12,
        "notes": "Increase efficiency_caps.steps by 1.",
    },
    {
        "name": "efficiency_tokens_up",
        "path": ("efficiency_caps", "tokens"),
        "delta": 200,
        "minimum": 600,
        "maximum": 4000,
        "notes": "Increase efficiency_caps.tokens by 200.",
    },
    {
        "name": "efficiency_retries_up",
        "path": ("efficiency_caps", "retries"),
        "delta": 1,
        "minimum": 1,
        "maximum": 6,
        "notes": "Increase efficiency_caps.retries by 1.",
    },
    {
        "name": "llama_temperature_up",
        "path": ("llama_cpp", "temperature"),
        "delta": 0.1,
        "minimum": 0.0,
        "maximum": 0.6,
        "precision": 2,
        "notes": "Increase llama_cpp.temperature by 0.1.",
    },
    {
        "name": "llama_max_output_up",
        "path": ("llama_cpp", "max_output_tokens"),
        "delta": 64,
        "minimum": 128,
        "maximum": 768,
        "notes": "Increase llama_cpp.max_output_tokens by 64.",
    },
    {
        "name": "llama_timeout_up",
        "path": ("llama_cpp", "timeout_sec"),
        "delta": 15,
        "minimum": 45,
        "maximum": 240,
        "notes": "Increase llama_cpp.timeout_sec by 15 seconds.",
    },
    {
        "name": "openclaw_thinking_up",
        "path": ("openclaw", "thinking"),
        "choices": ["low", "medium", "high"],
        "mode": "next_choice",
        "notes": "Move openclaw.thinking to the next higher preset.",
    },
]


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


def parameter_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "max_steps": config.get("max_steps"),
        "time_budget_sec": config.get("time_budget_sec"),
        "efficiency_caps.steps": config.get("efficiency_caps", {}).get("steps"),
        "efficiency_caps.tokens": config.get("efficiency_caps", {}).get("tokens"),
        "efficiency_caps.retries": config.get("efficiency_caps", {}).get("retries"),
        "llama_cpp.temperature": config.get("llama_cpp", {}).get("temperature"),
        "llama_cpp.max_output_tokens": config.get("llama_cpp", {}).get("max_output_tokens"),
        "llama_cpp.timeout_sec": config.get("llama_cpp", {}).get("timeout_sec"),
        "openclaw.thinking": config.get("openclaw", {}).get("thinking"),
    }


def _apply_single_parameter_mutation(base_config: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any] | None:
    current_value = _get_nested(base_config, spec["path"])
    if current_value is None:
        return None

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
    candidates: list[dict] = []
    for spec in SINGLE_PARAMETER_MUTATIONS:
        candidate = _apply_single_parameter_mutation(base_config, spec)
        if candidate is not None:
            candidate["parameter_snapshot"] = parameter_snapshot(candidate)
            candidates.append(candidate)
    return candidates


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
