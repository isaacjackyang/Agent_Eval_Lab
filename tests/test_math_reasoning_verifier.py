from __future__ import annotations

import unittest

from runners.base import RunnerResult
from verifiers.math_reasoning_verifier import verify_task


def _config() -> dict:
    return {
        "efficiency_caps": {
            "tokens": 1000,
            "steps": 8,
            "time_sec": 60,
            "retries": 2,
        }
    }


class MathReasoningVerifierTests(unittest.TestCase):
    def test_exact_integer_answer_passes(self) -> None:
        task = {
            "expected_output": "42",
            "allowed_tools": [],
            "metadata": {"answer_kind": "integer"},
        }
        result = RunnerResult(
            final_output="42",
            step_count=1,
            retries=0,
            elapsed_sec=1.2,
            current_tool=None,
            last_error=None,
            token_estimate=80,
            tool_trace=[],
            metadata={},
        )

        verdict = verify_task(task=task, runner_result=result, config=_config())
        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["subscores"]["tool"], 1.0)

    def test_near_integer_answer_gets_partial_credit(self) -> None:
        task = {
            "expected_output": "42",
            "allowed_tools": [],
            "metadata": {"answer_kind": "integer"},
        }
        result = RunnerResult(
            final_output="43",
            step_count=1,
            retries=0,
            elapsed_sec=1.2,
            current_tool=None,
            last_error=None,
            token_estimate=80,
            tool_trace=[],
            metadata={},
        )

        verdict = verify_task(task=task, runner_result=result, config=_config())
        self.assertFalse(verdict["passed"])
        self.assertGreater(verdict["subscores"]["task"], 0.5)
        self.assertIn("near_numeric", verdict["failure_tags"])

    def test_partial_order_answer_is_scored_continuously(self) -> None:
        task = {
            "expected_output": "Ava > Ben > Cora > Dylan",
            "allowed_tools": [],
            "metadata": {"answer_kind": "ranking"},
        }
        result = RunnerResult(
            final_output="Ava > Cora > Ben > Dylan",
            step_count=1,
            retries=0,
            elapsed_sec=1.2,
            current_tool=None,
            last_error=None,
            token_estimate=80,
            tool_trace=[],
            metadata={},
        )

        verdict = verify_task(task=task, runner_result=result, config=_config())
        self.assertFalse(verdict["passed"])
        self.assertGreater(verdict["subscores"]["task"], 0.3)
        self.assertIn("partial_order", verdict["failure_tags"])


if __name__ == "__main__":
    unittest.main()
