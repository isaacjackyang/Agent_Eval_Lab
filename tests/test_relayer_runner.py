from __future__ import annotations

import unittest

from evolution.relayer_plan import RelayerPlan
from runners.relayer_runner import RecordingLayerBackend, RelayerRunner


class RelayerRunnerTests(unittest.TestCase):
    def test_mock_backend_records_execution_order(self) -> None:
        backend = RecordingLayerBackend()
        runner = RelayerRunner(backend)
        plan = RelayerPlan(execution_order=[0, 1, 2, 3, 4, 2, 3, 4, 5])

        result = runner.execute(plan=plan, initial_state=[])

        self.assertEqual(result.layer_trace, [0, 1, 2, 3, 4, 2, 3, 4, 5])
        self.assertEqual(result.executed_layers, 9)
        self.assertEqual(result.final_state, [0, 1, 2, 3, 4, 2, 3, 4, 5])
        self.assertEqual(backend.calls, result.layer_trace)


if __name__ == "__main__":
    unittest.main()
