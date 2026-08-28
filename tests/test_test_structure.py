import ast
import unittest
from pathlib import Path


class TestStructureTests(unittest.TestCase):
    def test_no_single_module_contains_most_tests(self):
        counts = []
        for path in Path(__file__).parent.glob("test_*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            counts.append(sum(node.name.startswith("test_") for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)))

        self.assertLessEqual(max(counts) * 2, sum(counts))

    def test_ci_entrypoint_discovers_the_offline_suite(self):
        script = (Path(__file__).parents[1] / "scripts" / "test.ps1").read_text(encoding="utf-8")

        self.assertIn("unittest discover -s tests", script)


if __name__ == "__main__":
    unittest.main()
