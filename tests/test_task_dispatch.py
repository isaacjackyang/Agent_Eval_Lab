from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from generators.task_dispatch import TASK_TYPE_CHOICES, TASK_TYPE_OPTIONS, build_task_and_benchmark


class TaskDispatchTests(unittest.TestCase):
    def test_math_task_dispatches_to_math_benchmark(self) -> None:
        self.assertIn("math", TASK_TYPE_CHOICES)
        self.assertTrue(any(item["value"] == "math" for item in TASK_TYPE_OPTIONS))

        with tempfile.TemporaryDirectory() as temp_dir:
            task, benchmark = build_task_and_benchmark(
                run_id="dispatch_math_run",
                workspace_root=Path(temp_dir),
                seed=321,
                task_type="math",
            )

        self.assertEqual(task["category"], "math_reasoning")
        self.assertEqual(benchmark["id"], "math_reasoning_01")

    def test_retrieval_task_dispatches_to_retrieval_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task, benchmark = build_task_and_benchmark(
                run_id="dispatch_retrieval_run",
                workspace_root=Path(temp_dir),
                seed=11,
                task_type="handoff",
            )

        self.assertEqual(task["category"], "file_retrieval")
        self.assertEqual(benchmark["id"], "km_dynamic_retrieval_01")


if __name__ == "__main__":
    unittest.main()
