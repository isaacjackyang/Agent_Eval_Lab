from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from generators.km_file_tree_gen import TASK_TYPE_CHOICES, TASK_TYPE_OPTIONS, generate_task


class KmFileTreeGeneratorTests(unittest.TestCase):
    def test_retrieval_task_types_are_exposed(self) -> None:
        self.assertEqual(TASK_TYPE_CHOICES, ("auto", "deployment", "handoff", "operations"))
        self.assertEqual([item["value"] for item in TASK_TYPE_OPTIONS], ["auto", "deployment", "handoff", "operations"])

    def test_generate_task_builds_retrieval_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir)
            task = generate_task(
                run_id="retrieval_task_run",
                workspace_root=workspace_root,
                seed=123,
                task_type="deployment",
            )

        self.assertEqual(task["task_type"], "deployment")
        self.assertEqual(task["task_type_requested"], "deployment")
        self.assertEqual(task["category"], "file_retrieval")
        self.assertEqual(task["doc_slug"], "deployment")
        self.assertTrue(task["expected_output"].endswith(".md"))


if __name__ == "__main__":
    unittest.main()
