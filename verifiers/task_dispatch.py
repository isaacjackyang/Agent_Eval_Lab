from __future__ import annotations

from verifiers.km_dynamic_verifier import verify_task as verify_retrieval_task
from verifiers.math_reasoning_verifier import verify_task as verify_math_reasoning_task


def verify_task(task: dict, runner_result, config: dict) -> dict:
    category = str(task.get("category", "")).strip().lower()
    if category == "math_reasoning":
        return verify_math_reasoning_task(task=task, runner_result=runner_result, config=config)
    return verify_retrieval_task(task=task, runner_result=runner_result, config=config)
