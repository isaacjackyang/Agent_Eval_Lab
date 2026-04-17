from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from evolution.relayer_plan import RelayerPlan


class LayerExecutionBackend(ABC):
    @abstractmethod
    def run_layer(self, layer_index: int, state: Any) -> Any:
        raise NotImplementedError


@dataclass
class RelayerRunResult:
    final_state: Any
    layer_trace: list[int]
    executed_layers: int


class RelayerRunner:
    def __init__(self, backend: LayerExecutionBackend) -> None:
        self.backend = backend

    def execute(self, *, plan: RelayerPlan, initial_state: Any = None) -> RelayerRunResult:
        state = initial_state
        layer_trace: list[int] = []
        for layer_index in plan.execution_order:
            state = self.backend.run_layer(layer_index, state)
            layer_trace.append(layer_index)
        return RelayerRunResult(
            final_state=state,
            layer_trace=layer_trace,
            executed_layers=len(layer_trace),
        )


class RecordingLayerBackend(LayerExecutionBackend):
    def __init__(self) -> None:
        self.calls: list[int] = []

    def run_layer(self, layer_index: int, state: Any) -> Any:
        self.calls.append(layer_index)
        state_list = list(state or [])
        state_list.append(layer_index)
        return state_list
