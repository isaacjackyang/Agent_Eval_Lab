from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from generators.math_reasoning_gen import generate_task


class MathReasoningGeneratorTests(unittest.TestCase):
    def test_generate_task_creates_true_math_reasoning_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir)
            task = generate_task(
                run_id="math_reasoning_run",
                workspace_root=workspace_root,
                seed=123,
                task_type="math",
            )

        self.assertEqual(task["task_type"], "math")
        self.assertEqual(task["category"], "math_reasoning")
        self.assertEqual(task["allowed_tools"], [])
        self.assertTrue(task["expected_output"])
        self.assertIn(task["metadata"]["family"], {"arithmetic_chain", "word_problem", "sequence_reasoning", "logic_ordering"})
        self.assertIn("Return", task["prompt"])


if __name__ == "__main__":
    unittest.main()
