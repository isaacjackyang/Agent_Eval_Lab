from __future__ import annotations

import json
import random
from pathlib import Path

from generators.km_file_tree_gen import generate_task as generate_retrieval_task
from generators.math_reasoning_gen import generate_task as generate_math_task


TASK_TYPE_CHOICES = ("auto", "deployment", "handoff", "operations", "math")
TASK_TYPE_OPTIONS = [
    {"value": "auto", "label": "Auto (Random)"},
    {"value": "deployment", "label": "Deployment"},
    {"value": "handoff", "label": "Handoff"},
    {"value": "operations", "label": "Operations"},
    {"value": "math", "label": "Math Calculation"},
]

RETRIEVAL_TASK_TYPES = {"deployment", "handoff", "operations"}
BENCHMARK_BY_TASK_TYPE = {
    "deployment": "km_dynamic_retrieval_01.json",
    "handoff": "km_dynamic_retrieval_01.json",
    "operations": "km_dynamic_retrieval_01.json",
    "math": "math_reasoning_01.json",
}


def normalize_task_type(raw_value: str | None) -> str:
    normalized = str(raw_value or "auto").strip().lower() or "auto"
    valid_values = {item["value"] for item in TASK_TYPE_OPTIONS}
    if normalized not in valid_values:
        raise ValueError(f"Unsupported task type: {raw_value}")
    return normalized


def resolve_task_type(run_id: str, seed: int | None = None, task_type: str | None = None) -> str:
    normalized = normalize_task_type(task_type)
    if normalized != "auto":
        return normalized

    rng = random.Random(seed if seed is not None else run_id)
    return rng.choice(tuple(BENCHMARK_BY_TASK_TYPE.keys()))


def load_benchmark_for_task_type(task_type: str) -> dict:
    normalized = normalize_task_type(task_type)
    if normalized == "auto":
        raise ValueError("Task type must be resolved before loading a benchmark.")
    benchmark_path = Path(__file__).resolve().parents[1] / "benchmarks" / "layer_c" / BENCHMARK_BY_TASK_TYPE[normalized]
    return json.loads(benchmark_path.read_text(encoding="utf-8"))


def build_task_and_benchmark(
    *,
    run_id: str,
    workspace_root: Path,
    seed: int | None = None,
    task_type: str | None = None,
) -> tuple[dict, dict]:
    resolved_task_type = resolve_task_type(run_id=run_id, seed=seed, task_type=task_type)
    benchmark = load_benchmark_for_task_type(resolved_task_type)
    if resolved_task_type == "math":
        task = generate_math_task(run_id=run_id, workspace_root=workspace_root, seed=seed, task_type=resolved_task_type)
    else:
        task = generate_retrieval_task(run_id=run_id, workspace_root=workspace_root, seed=seed, task_type=resolved_task_type)
    return task, benchmark
