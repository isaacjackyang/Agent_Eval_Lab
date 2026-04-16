from __future__ import annotations

import copy
import json
from pathlib import Path


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

    config["weights"] = normalize_weights(config["weights"])
    if extra_metadata:
        config.update(copy.deepcopy(extra_metadata))
    return config


def build_candidate_pool(base_config: dict, pool_size: int, include_base: bool = True) -> list[dict]:
    variants: list[dict] = []
    if include_base:
        config = copy.deepcopy(base_config)
        config["mutation_profile"] = "baseline"
        config["mutation_notes"] = "Original experiment config."
        variants.append(config)

    templates = [
        (
            "fast_lane",
            {
                "max_steps": max(4, int(base_config["max_steps"]) - 2),
                "time_budget_sec": max(12, int(base_config["time_budget_sec"]) - 6),
                "efficiency_caps": {
                    "steps": max(4, int(base_config["efficiency_caps"]["steps"]) - 2),
                    "time_sec": max(12, int(base_config["efficiency_caps"]["time_sec"]) - 6),
                    "tokens": max(600, int(base_config["efficiency_caps"]["tokens"]) - 200),
                },
                "weights": {
                    **base_config["weights"],
                    "efficiency": base_config["weights"]["efficiency"] + 0.03,
                    "success": base_config["weights"]["success"] - 0.03,
                },
            },
            "Bias toward cheaper runs with tighter execution caps.",
        ),
        (
            "deep_search",
            {
                "max_steps": int(base_config["max_steps"]) + 2,
                "time_budget_sec": int(base_config["time_budget_sec"]) + 10,
                "efficiency_caps": {
                    "steps": int(base_config["efficiency_caps"]["steps"]) + 2,
                    "time_sec": int(base_config["efficiency_caps"]["time_sec"]) + 10,
                    "tokens": int(base_config["efficiency_caps"]["tokens"]) + 250,
                    "retries": int(base_config["efficiency_caps"]["retries"]) + 1,
                },
                "weights": {
                    **base_config["weights"],
                    "recovery": base_config["weights"]["recovery"] + 0.03,
                    "efficiency": max(0.02, base_config["weights"]["efficiency"] - 0.02),
                    "success": max(0.2, base_config["weights"]["success"] - 0.01),
                },
            },
            "Allow deeper search and more recovery attempts.",
        ),
        (
            "strict_honesty",
            {
                "weights": {
                    **base_config["weights"],
                    "honesty_boundary": base_config["weights"]["honesty_boundary"] + 0.04,
                    "tool_correctness": base_config["weights"]["tool_correctness"] + 0.02,
                    "success": max(0.2, base_config["weights"]["success"] - 0.04),
                    "recovery": max(0.04, base_config["weights"]["recovery"] - 0.02),
                },
                "baseline_gate": {
                    **base_config["baseline_gate"],
                    "min_honesty_score": min(0.99, base_config["baseline_gate"]["min_honesty_score"] + 0.01),
                },
            },
            "Prefer configurations with stricter honesty and tool-discipline scoring.",
        ),
        (
            "balanced_retry",
            {
                "time_budget_sec": int(base_config["time_budget_sec"]) + 4,
                "efficiency_caps": {
                    "retries": int(base_config["efficiency_caps"]["retries"]) + 1,
                    "time_sec": int(base_config["efficiency_caps"]["time_sec"]) + 4,
                },
                "weights": {
                    **base_config["weights"],
                    "recovery": base_config["weights"]["recovery"] + 0.02,
                    "tool_correctness": base_config["weights"]["tool_correctness"] + 0.01,
                    "efficiency": max(0.05, base_config["weights"]["efficiency"] - 0.02),
                    "success": max(0.2, base_config["weights"]["success"] - 0.01),
                },
            },
            "Slightly more tolerant retry budget without going fully deep-search.",
        ),
    ]

    for suffix, changes, notes in templates:
        if len(variants) >= pool_size:
            break
        variants.append(build_candidate_variant(base_config, suffix, changes, notes))

    return variants[:pool_size]


def persist_candidate_pool(root: Path, suite_id: str, candidates: list[dict]) -> list[Path]:
    target_dir = root / "configs" / "candidates" / suite_id
    target_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index, candidate in enumerate(candidates, start=1):
        path = target_dir / f"{index:02d}_{candidate['config_id']}.json"
        path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")
        paths.append(path)
    return paths
