from __future__ import annotations

import itertools
import random
from pathlib import Path
from typing import Callable


TASK_TYPE_CHOICES = ("math",)
TASK_TYPE_OPTIONS = [
    {"value": "math", "label": "Math Calculation"},
]

PEOPLE = ["Ava", "Ben", "Cora", "Dylan", "Eli", "Faye", "Gabe", "Hana"]


def _normalize_answer_text(value: str) -> str:
    return " ".join(str(value).strip().split())


def _build_arithmetic_chain(rng: random.Random) -> dict:
    a = rng.randint(6, 19)
    b = rng.randint(3, 14)
    c = rng.randint(2, 7)
    d = rng.randint(4, 15)
    expression = f"(({a} + {b}) * {c}) - {d}"
    answer = ((a + b) * c) - d
    prompt = (
        "Solve the arithmetic problem exactly.\n"
        f"Compute the integer value of {expression}.\n"
        "Return the final answer only."
    )
    return {
        "family": "arithmetic_chain",
        "title": "Arithmetic Chain",
        "prompt": prompt,
        "expected_output": str(answer),
        "answer_kind": "integer",
        "explanation": f"Evaluate {expression}.",
    }


def _build_word_problem(rng: random.Random) -> dict:
    crates = rng.randint(4, 9)
    per_crate = rng.randint(6, 15)
    shipped = rng.randint(8, 24)
    new_boxes = rng.randint(5, 18)
    broken = rng.randint(1, 6)
    answer = crates * per_crate - shipped + new_boxes - broken
    prompt = (
        "Solve the word problem exactly.\n"
        f"A warehouse starts with {crates} crates and each crate holds {per_crate} boxes. "
        f"It ships {shipped} boxes, receives {new_boxes} new boxes, and then discards {broken} damaged boxes. "
        "How many boxes remain at the end?\n"
        "Return the final integer only."
    )
    return {
        "family": "word_problem",
        "title": "Warehouse Word Problem",
        "prompt": prompt,
        "expected_output": str(answer),
        "answer_kind": "integer",
        "explanation": "Multiply the starting crates by boxes per crate, then apply each change in order.",
    }


def _build_sequence_reasoning(rng: random.Random) -> dict:
    start = rng.randint(2, 9)
    first_gap = rng.randint(3, 7)
    gap_step = rng.randint(2, 4)
    values = [start]
    gap = first_gap
    for _ in range(4):
        values.append(values[-1] + gap)
        gap += gap_step
    next_value = values[-1] + gap
    series = ", ".join(str(item) for item in values)
    prompt = (
        "Solve the sequence reasoning task.\n"
        f"The sequence is {series}. The gaps between terms increase by a constant amount each step. "
        "What is the next number in the sequence?\n"
        "Return the final integer only."
    )
    return {
        "family": "sequence_reasoning",
        "title": "Sequence Reasoning",
        "prompt": prompt,
        "expected_output": str(next_value),
        "answer_kind": "integer",
        "explanation": "Track the changing gaps, then apply the next gap once more.",
    }


def _rank_text(order: tuple[str, ...]) -> str:
    return " > ".join(order)


def _position_map(order: tuple[str, ...]) -> dict[str, int]:
    return {name: index for index, name in enumerate(order)}


def _generate_logic_clue_pool(order: tuple[str, ...]) -> list[tuple[str, Callable[[tuple[str, ...]], bool]]]:
    positions = _position_map(order)
    names = list(order)
    clues: list[tuple[str, Callable[[tuple[str, ...]], bool]]] = []

    first_name = order[0]
    last_name = order[-1]
    clues.append(
        (
            f"{first_name} finished first.",
            lambda candidate, first_name=first_name: candidate[0] == first_name,
        )
    )
    clues.append(
        (
            f"{last_name} finished last.",
            lambda candidate, last_name=last_name: candidate[-1] == last_name,
        )
    )

    for left, right in itertools.combinations(names, 2):
        if positions[left] < positions[right]:
            clues.append(
                (
                    f"{left} finished before {right}.",
                    lambda candidate, left=left, right=right: candidate.index(left) < candidate.index(right),
                )
            )
        else:
            clues.append(
                (
                    f"{right} finished before {left}.",
                    lambda candidate, left=left, right=right: candidate.index(right) < candidate.index(left),
                )
            )

    for index in range(len(order) - 1):
        left = order[index]
        right = order[index + 1]
        clues.append(
            (
                f"{left} finished immediately before {right}.",
                lambda candidate, left=left, right=right: candidate.index(left) + 1 == candidate.index(right),
            )
        )

    for index in range(1, len(order) - 1):
        middle = order[index]
        left = order[index - 1]
        right = order[index + 1]
        clues.append(
            (
                f"{middle} finished somewhere between {left} and {right}.",
                lambda candidate, middle=middle, left=left, right=right: candidate.index(left)
                < candidate.index(middle)
                < candidate.index(right),
            )
        )

    for left, right in itertools.combinations(names, 2):
        if abs(positions[left] - positions[right]) > 1:
            clues.append(
                (
                    f"{left} did not finish next to {right}.",
                    lambda candidate, left=left, right=right: abs(candidate.index(left) - candidate.index(right)) > 1,
                )
            )

    return clues


def _build_logic_ordering(rng: random.Random) -> dict:
    names = tuple(rng.sample(PEOPLE, 4))
    answer_order = tuple(rng.sample(names, len(names)))
    clue_pool = _generate_logic_clue_pool(answer_order)
    rng.shuffle(clue_pool)

    all_orders = list(itertools.permutations(names))
    selected: list[tuple[str, Callable[[tuple[str, ...]], bool]]] = []
    remaining = list(all_orders)

    for clue in clue_pool:
        filtered = [candidate for candidate in remaining if clue[1](candidate)]
        if not filtered or len(filtered) == len(remaining):
            continue
        selected.append(clue)
        remaining = filtered
        if len(remaining) == 1 and remaining[0] == answer_order and len(selected) >= 3:
            break

    if not selected or len(remaining) != 1 or remaining[0] != answer_order:
        selected = clue_pool[:4]
        remaining = [candidate for candidate in all_orders if all(clue[1](candidate) for clue in selected)]

    if len(remaining) != 1 or remaining[0] != answer_order:
        # Fallback to a deterministic but still reasoning-based set of clues.
        selected = [
            (
                f"{answer_order[0]} finished first.",
                lambda candidate, name=answer_order[0]: candidate[0] == name,
            ),
            (
                f"{answer_order[1]} finished immediately before {answer_order[2]}.",
                lambda candidate, left=answer_order[1], right=answer_order[2]: candidate.index(left) + 1 == candidate.index(right),
            ),
            (
                f"{answer_order[3]} finished last.",
                lambda candidate, name=answer_order[3]: candidate[-1] == name,
            ),
        ]

    clue_lines = [f"{index + 1}. {clue[0]}" for index, clue in enumerate(selected[:4])]
    prompt = (
        "Solve the ordering puzzle.\n"
        "Four people finished a race from first to last.\n"
        + "\n".join(clue_lines)
        + "\nReturn the final order from first to last using the exact format `Name1 > Name2 > Name3 > Name4`."
    )
    return {
        "family": "logic_ordering",
        "title": "Ordering Logic Puzzle",
        "prompt": prompt,
        "expected_output": _rank_text(answer_order),
        "answer_kind": "ranking",
        "choices": list(names),
        "explanation": "Combine the clues to recover the unique finish order.",
    }


BUILDERS = (
    _build_arithmetic_chain,
    _build_word_problem,
    _build_sequence_reasoning,
    _build_logic_ordering,
)


def generate_task(run_id: str, workspace_root: Path, seed: int | None = None, task_type: str | None = None) -> dict:
    normalized_task_type = str(task_type or "math").strip().lower() or "math"
    if normalized_task_type not in {"math", "auto"}:
        raise ValueError(f"Unsupported math reasoning task type: {task_type}")

    rng = random.Random(seed if seed is not None else run_id)
    workspace_root.mkdir(parents=True, exist_ok=True)
    builder = rng.choice(BUILDERS)
    spec = builder(rng)
    expected_output = _normalize_answer_text(spec["expected_output"])

    return {
        "id": "math_reasoning_01",
        "category": "math_reasoning",
        "task_type": "math",
        "task_type_requested": normalized_task_type,
        "prompt": spec["prompt"],
        "workspace_root": str(workspace_root.resolve()),
        "expected_output": expected_output,
        "allowed_tools": [],
        "search_hints": {
            "broad": "",
            "focused": "",
        },
        "metadata": {
            "run_id": run_id,
            "seed": seed if seed is not None else run_id,
            "family": spec["family"],
            "title": spec["title"],
            "answer_kind": spec["answer_kind"],
            "choices": spec.get("choices", []),
            "explanation": spec["explanation"],
        },
    }
