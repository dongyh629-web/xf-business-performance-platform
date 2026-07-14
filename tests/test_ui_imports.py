from __future__ import annotations

import ast
import importlib
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

SCAN_PATHS = [PROJECT_ROOT / "app.py", PROJECT_ROOT / "app", PROJECT_ROOT / "pages"]


def _python_files() -> list[Path]:
    files: list[Path] = []
    for path in SCAN_PATHS:
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(path.rglob("*.py"))
    return sorted(files)


def _ui_names_from_file(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_aliases: set[str] = set()
    names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.ui":
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app.ui":
                    imported_aliases.add(alias.asname or "app.ui")

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            value = node.value
            if isinstance(value, ast.Name) and value.id in imported_aliases:
                names.add(node.attr)
            elif (
                isinstance(value, ast.Attribute)
                and value.attr == "ui"
                and isinstance(value.value, ast.Name)
                and value.value.id == "app"
            ):
                names.add(node.attr)

    return names


class UiImportCompatibilityTest(unittest.TestCase):
    def test_all_app_ui_imports_exist(self) -> None:
        ui = importlib.import_module("app.ui")
        missing: dict[str, list[str]] = {}

        for path in _python_files():
            names = _ui_names_from_file(path)
            absent = sorted(name for name in names if not hasattr(ui, name))
            if absent:
                missing[str(path.relative_to(PROJECT_ROOT))] = absent

        self.assertEqual({}, missing)


if __name__ == "__main__":
    unittest.main()
