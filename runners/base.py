from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunnerResult:
    final_output: str
    step_count: int
    retries: int
    elapsed_sec: float
    current_tool: str | None
    last_error: str | None
    token_estimate: int
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseRunner(ABC):
    name = "base"

    def __init__(self, max_steps: int, runner_config: dict | None = None) -> None:
        self.max_steps = max_steps
        self.runner_config = runner_config or {}

    @abstractmethod
    def run(self, task: dict, live_writer, context: dict) -> RunnerResult:
        raise NotImplementedError
