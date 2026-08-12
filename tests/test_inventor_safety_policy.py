from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
NORMALIZER = ROOT / "tools" / "obj_region_normalizer.py"
TESTS = ROOT / "tests"
FORBIDDEN_COM_IDENTIFIERS = (
    "python" + "com",
    "win32" + "com",
    "Inventor" + ".Application",
    "Documents" + ".Open",
)


class InventorSafetyPolicyTests(unittest.TestCase):
    def test_normalizer_has_no_com_identifiers(self) -> None:
        source = NORMALIZER.read_text(encoding="utf-8")

        for identifier in FORBIDDEN_COM_IDENTIFIERS:
            with self.subTest(identifier=identifier):
                self.assertNotIn(identifier, source)

    def test_tests_only_bind_fake_com_modules_for_guard_coverage(self) -> None:
        for test_path in TESTS.glob("test_*.py"):
            tree = ast.parse(test_path.read_text(encoding="utf-8"), filename=str(test_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                    self.assertFalse(
                        any(name == identifier or name.startswith(identifier + ".")
                            for name in imported for identifier in FORBIDDEN_COM_IDENTIFIERS[:2]),
                        test_path,
                    )
                if isinstance(node, ast.ImportFrom):
                    self.assertFalse(
                        node.module is not None
                        and any(node.module == identifier or node.module.startswith(identifier + ".")
                                for identifier in FORBIDDEN_COM_IDENTIFIERS[:2]),
                        test_path,
                    )
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    base = node.func.value
                    self.assertFalse(
                        isinstance(base, ast.Name)
                        and base.id == FORBIDDEN_COM_IDENTIFIERS[0]
                        and node.func.attr in {"CoInitialize", "CoUninitialize"},
                        f"live COM call in {test_path}",
                    )


if __name__ == "__main__":
    unittest.main()
