from __future__ import annotations

import ast
import importlib
import sys
import unittest
from datetime import date
from pathlib import Path

import streamlit as st


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
    def setUp(self) -> None:
        for key in list(st.session_state.keys()):
            if key.startswith("unit_test_") or key.startswith("another_test_") or key.startswith("profitability_completed"):
                del st.session_state[key]

    def test_all_app_ui_imports_exist(self) -> None:
        ui = importlib.import_module("app.ui")
        missing: dict[str, list[str]] = {}

        for path in _python_files():
            names = _ui_names_from_file(path)
            absent = sorted(name for name in names if not hasattr(ui, name))
            if absent:
                missing[str(path.relative_to(PROJECT_ROOT))] = absent

        self.assertEqual({}, missing)

    def test_current_data_year_range_uses_latest_data_year(self) -> None:
        ui = importlib.import_module("app.ui")

        self.assertEqual(
            (date(2026, 1, 1), date(2026, 7, 30)),
            ui.current_data_year_range(date(2024, 1, 1), date(2026, 7, 30)),
        )

    def test_current_data_year_range_respects_available_minimum(self) -> None:
        ui = importlib.import_module("app.ui")

        self.assertEqual(
            (date(2026, 3, 15), date(2026, 7, 30)),
            ui.current_data_year_range(date(2026, 3, 15), date(2026, 7, 30)),
        )

    def test_date_state_migrates_old_full_history_default_to_current_year(self) -> None:
        ui = importlib.import_module("app.ui")
        st.session_state["unit_test_date_range"] = (date(2024, 1, 3), date(2026, 7, 30))

        selected = ui.initialize_date_range_state(
            "unit_test",
            date(2024, 1, 3),
            date(2026, 7, 30),
            legacy_key="unit_test_date_range",
        )

        self.assertEqual((date(2026, 1, 1), date(2026, 7, 30)), selected)
        self.assertEqual(date(2026, 1, 1), st.session_state["unit_test_start_date"])
        self.assertEqual(date(2026, 7, 30), st.session_state["unit_test_end_date"])

    def test_date_state_preserves_user_modified_range(self) -> None:
        ui = importlib.import_module("app.ui")
        st.session_state["unit_test_start_date"] = date(2025, 1, 1)
        st.session_state["unit_test_end_date"] = date(2025, 12, 31)
        st.session_state["unit_test_date_user_modified"] = True
        st.session_state["unit_test_date_default_version"] = ui.DATE_FILTER_DEFAULT_VERSION

        selected = ui.initialize_date_range_state("unit_test", date(2024, 1, 3), date(2026, 7, 30))

        self.assertEqual((date(2025, 1, 1), date(2025, 12, 31)), selected)

    def test_date_state_detects_new_widget_value_before_modified_flag_updates(self) -> None:
        ui = importlib.import_module("app.ui")
        st.session_state["unit_test_start_date"] = date(2025, 1, 1)
        st.session_state["unit_test_end_date"] = date(2025, 12, 31)
        st.session_state["unit_test_default_start_date"] = date(2026, 1, 1)
        st.session_state["unit_test_default_end_date"] = date(2026, 7, 30)
        st.session_state["unit_test_date_user_modified"] = False
        st.session_state["unit_test_date_default_version"] = ui.DATE_FILTER_DEFAULT_VERSION

        selected = ui.initialize_date_range_state("unit_test", date(2024, 1, 3), date(2026, 7, 30))

        self.assertEqual((date(2025, 1, 1), date(2025, 12, 31)), selected)
        self.assertTrue(st.session_state["unit_test_date_user_modified"])

    def test_date_state_updates_default_when_data_latest_year_changes(self) -> None:
        ui = importlib.import_module("app.ui")
        st.session_state["unit_test_start_date"] = date(2026, 1, 1)
        st.session_state["unit_test_end_date"] = date(2026, 7, 30)
        st.session_state["unit_test_default_start_date"] = date(2026, 1, 1)
        st.session_state["unit_test_default_end_date"] = date(2026, 7, 30)
        st.session_state["unit_test_date_user_modified"] = False
        st.session_state["unit_test_date_default_version"] = ui.DATE_FILTER_DEFAULT_VERSION

        selected = ui.initialize_date_range_state("unit_test", date(2024, 1, 3), date(2027, 2, 15))

        self.assertEqual((date(2027, 1, 1), date(2027, 2, 15)), selected)

    def test_date_state_is_independent_by_key_prefix(self) -> None:
        ui = importlib.import_module("app.ui")

        first = ui.initialize_date_range_state("unit_test", date(2024, 1, 3), date(2026, 7, 30))
        second = ui.initialize_date_range_state("another_test", date(2023, 5, 1), date(2027, 2, 15))

        self.assertEqual((date(2026, 1, 1), date(2026, 7, 30)), first)
        self.assertEqual((date(2027, 1, 1), date(2027, 2, 15)), second)

    def test_date_state_clamps_invalid_or_out_of_bounds_range(self) -> None:
        ui = importlib.import_module("app.ui")
        st.session_state["unit_test_start_date"] = date(2028, 1, 1)
        st.session_state["unit_test_end_date"] = date(2024, 1, 1)
        st.session_state["unit_test_date_user_modified"] = True
        st.session_state["unit_test_date_default_version"] = ui.DATE_FILTER_DEFAULT_VERSION

        selected = ui.initialize_date_range_state("unit_test", date(2024, 1, 3), date(2026, 7, 30))

        self.assertEqual((date(2026, 7, 30), date(2026, 7, 30)), selected)


if __name__ == "__main__":
    unittest.main()
