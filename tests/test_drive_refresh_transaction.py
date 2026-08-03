from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from app import google_drive
from app.google_drive import DriveLoadItemStatus, DriveLoadStatus, DriveUserError


class _FakeCacheData:
    def clear(self) -> None:
        return None


class _FakeStreamlit:
    def __init__(self) -> None:
        self.session_state: dict = {}
        self.cache_data = _FakeCacheData()
        self.button_clicks: dict[str, bool] = {}
        self.button_calls: list[dict[str, object]] = []
        self.messages: list[tuple[str, str]] = []
        self.rerun_called = False

    def warning(self, message: str) -> None:
        self.messages.append(("warning", message))

    def markdown(self, message: str) -> None:
        self.messages.append(("markdown", message))

    def caption(self, message: str) -> None:
        self.messages.append(("caption", message))

    def info(self, message: str) -> None:
        self.messages.append(("info", message))

    def success(self, message: str) -> None:
        self.messages.append(("success", message))

    def error(self, message: str) -> None:
        self.messages.append(("error", message))

    def button(self, label: str, **kwargs):
        self.button_calls.append({"label": label, **kwargs})
        return bool(self.button_clicks.get(str(kwargs.get("key") or label), False))

    def spinner(self, message: str):
        self.messages.append(("spinner", message))
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def rerun(self) -> None:
        self.rerun_called = True


def _status(sales_status: str = "loaded") -> DriveLoadStatus:
    return DriveLoadStatus(
        configured=True,
        message="Google Drive 已配置。",
        sales=DriveLoadItemStatus(sales_status, f"sales {sales_status}"),
        targets=DriveLoadItemStatus("using_previous", "目标失败，继续使用旧数据。"),
    )


class DriveRefreshTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_st = _FakeStreamlit()
        self.fake_st.session_state.update(
            {
                "clean_data": pd.DataFrame({"Sales Amount": [100.0]}),
                "drive_sales_status": "旧销售",
                "drive_sales_row_count": 1,
                "target_data": pd.DataFrame({"Year": [2026], "Month": [7], "Revised Target": [180693.0]}),
                "drive_target_status": "旧目标",
                "cost_snapshot_registry": "old_registry",
                "cost_snapshots": ["old_cost"],
                "drive_cost_load_status": DriveLoadItemStatus("loaded", "old cost"),
                "drive_cost_status": "旧成本",
                "drive_cost_snapshot_count": 1,
            }
        )
        self.streamlit_patch = patch.object(google_drive, "_get_streamlit", return_value=self.fake_st)
        self.streamlit_patch.start()

    def tearDown(self) -> None:
        self.streamlit_patch.stop()

    def _cost_success(self):
        self.fake_st.session_state["cost_snapshot_registry"] = "new_registry"
        self.fake_st.session_state["cost_snapshots"] = ["new_cost"]
        self.fake_st.session_state["drive_cost_load_status"] = DriveLoadItemStatus("loaded", "new cost")
        self.fake_st.session_state["drive_cost_status"] = "新成本"
        return "new_registry", ["new_cost"]

    def _sales_success(self, force: bool = True):
        self.fake_st.session_state["clean_data"] = pd.DataFrame({"Sales Amount": [200.0]})
        self.fake_st.session_state["drive_sales_status"] = "新销售"
        self.fake_st.session_state["drive_sales_row_count"] = 1
        return _status("loaded")

    def test_refresh_success_updates_sales_and_cost(self) -> None:
        with patch.object(google_drive, "load_drive_cost_snapshots", side_effect=lambda force=True: self._cost_success()), patch.object(
            google_drive, "load_drive_business_files", side_effect=self._sales_success
        ):
            _status_result, message = google_drive.refresh_drive_data_transaction()

        self.assertIn("sales loaded", message)
        self.assertEqual(["new_cost"], self.fake_st.session_state["cost_snapshots"])
        self.assertEqual(200.0, float(self.fake_st.session_state["clean_data"]["Sales Amount"].sum()))

    def test_sales_success_cost_failure_keeps_previous_complete_state(self) -> None:
        with patch.object(google_drive, "load_drive_cost_snapshots", side_effect=DriveUserError("cost missing")), patch.object(
            google_drive, "load_drive_business_files", side_effect=self._sales_success
        ) as sales_loader:
            _status_result, message = google_drive.refresh_drive_data_transaction()

        sales_loader.assert_not_called()
        self.assertIn("继续使用旧数据", message)
        self.assertEqual(["old_cost"], self.fake_st.session_state["cost_snapshots"])
        self.assertEqual(100.0, float(self.fake_st.session_state["clean_data"]["Sales Amount"].sum()))

    def test_sales_failure_cost_success_keeps_previous_complete_state(self) -> None:
        def failed_sales(force: bool = True):
            self.fake_st.session_state["clean_data"] = pd.DataFrame({"Sales Amount": [200.0]})
            return _status("failed")

        with patch.object(google_drive, "load_drive_cost_snapshots", side_effect=lambda force=True: self._cost_success()), patch.object(
            google_drive, "load_drive_business_files", side_effect=failed_sales
        ):
            _status_result, message = google_drive.refresh_drive_data_transaction()

        self.assertIn("继续使用旧数据", message)
        self.assertEqual(["old_cost"], self.fake_st.session_state["cost_snapshots"])
        self.assertEqual(100.0, float(self.fake_st.session_state["clean_data"]["Sales Amount"].sum()))

    def test_sales_and_cost_failure_keeps_previous_complete_state(self) -> None:
        with patch.object(google_drive, "load_drive_cost_snapshots", side_effect=DriveUserError("cost missing")), patch.object(
            google_drive, "load_drive_business_files", return_value=_status("failed")
        ) as sales_loader:
            _status_result, message = google_drive.refresh_drive_data_transaction()

        sales_loader.assert_not_called()
        self.assertIn("继续使用旧数据", message)
        self.assertEqual(["old_cost"], self.fake_st.session_state["cost_snapshots"])
        self.assertEqual(100.0, float(self.fake_st.session_state["clean_data"]["Sales Amount"].sum()))

    def test_cost_coverage_state_is_not_reset_to_empty_after_refresh_failure(self) -> None:
        with patch.object(google_drive, "load_drive_cost_snapshots", side_effect=DriveUserError("cost missing")):
            google_drive.refresh_drive_data_transaction()

        self.assertEqual(["old_cost"], self.fake_st.session_state["cost_snapshots"])
        self.assertEqual(1, self.fake_st.session_state["drive_cost_snapshot_count"])
        self.assertNotEqual(0, len(self.fake_st.session_state["cost_snapshots"]))

    def test_target_failure_keeps_previous_target_data(self) -> None:
        with patch.object(google_drive, "load_drive_cost_snapshots", side_effect=lambda force=True: self._cost_success()), patch.object(
            google_drive, "load_drive_business_files", return_value=_status("loaded")
        ):
            _status_result, message = google_drive.refresh_drive_data_transaction()

        self.assertIn("目标失败", message)
        self.assertEqual(180693.0, float(self.fake_st.session_state["target_data"]["Revised Target"].iloc[0]))


class DriveStartupLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_st = _FakeStreamlit()
        self.streamlit_patch = patch.object(google_drive, "_get_streamlit", return_value=self.fake_st)
        self.streamlit_patch.start()

    def tearDown(self) -> None:
        self.streamlit_patch.stop()

    def test_startup_without_cache_does_not_call_drive_sync(self) -> None:
        with patch.object(google_drive, "_restore_any_local_cache", return_value=None), patch.object(
            google_drive, "load_drive_business_files"
        ) as loader:
            status = google_drive.ensure_drive_data_loaded(force=False)

        loader.assert_not_called()
        self.assertEqual("not_loaded", status.sales.status)
        self.assertEqual("not_loaded", status.targets.status)

    def test_startup_with_cache_restores_without_drive_sync(self) -> None:
        cached = _status("cached")
        with patch.object(google_drive, "_restore_any_local_cache", return_value=cached), patch.object(
            google_drive, "load_drive_business_files"
        ) as loader:
            status = google_drive.ensure_drive_data_loaded(force=False)

        loader.assert_not_called()
        self.assertIs(cached, status)

    def test_force_refresh_still_calls_drive_sync(self) -> None:
        with patch.object(google_drive, "load_drive_business_files", return_value=_status("loaded")) as loader:
            status = google_drive.ensure_drive_data_loaded(force=True)

        loader.assert_called_once_with(force=True)
        self.assertEqual("loaded", status.sales.status)

    def test_cost_snapshots_without_cache_do_not_scan_drive_on_startup(self) -> None:
        with patch.object(google_drive, "_restore_cost_snapshot_cache", return_value=None), patch.object(
            google_drive, "get_drive_service"
        ) as service_factory:
            registry, snapshots = google_drive.load_drive_cost_snapshots(force=False)

        service_factory.assert_not_called()
        self.assertEqual([], registry.entries)
        self.assertEqual([], snapshots)

    def test_load_prompt_does_not_render_when_session_has_data(self) -> None:
        self.fake_st.session_state["clean_data"] = pd.DataFrame({"Sales Amount": [1.0]})

        rendered = google_drive.render_drive_data_load_prompt()

        self.assertFalse(rendered)
        self.assertEqual([], self.fake_st.button_calls)

    def test_load_prompt_renders_when_no_session_data(self) -> None:
        rendered = google_drive.render_drive_data_load_prompt()

        self.assertTrue(rendered)
        self.assertEqual("加载最新 Google Drive 数据", self.fake_st.button_calls[-1]["label"])
        self.assertFalse(self.fake_st.button_calls[-1]["disabled"])

    def test_load_prompt_disables_button_while_refresh_in_progress(self) -> None:
        self.fake_st.session_state["drive_refresh_in_progress"] = True

        rendered = google_drive.render_drive_data_load_prompt()

        self.assertTrue(rendered)
        self.assertTrue(self.fake_st.button_calls[-1]["disabled"])

    def test_load_prompt_click_triggers_single_refresh_and_rerun(self) -> None:
        self.fake_st.button_clicks["drive_main_load_button"] = True
        with patch.object(google_drive, "refresh_drive_data_transaction", return_value=(_status("loaded"), "加载完成")) as refresh:
            rendered = google_drive.render_drive_data_load_prompt()

        self.assertTrue(rendered)
        refresh.assert_called_once_with()
        self.assertTrue(self.fake_st.rerun_called)
        self.assertFalse(self.fake_st.session_state["drive_refresh_in_progress"])
        self.assertEqual("加载完成", self.fake_st.session_state["drive_refresh_message"])

    def test_load_prompt_failure_releases_refresh_lock(self) -> None:
        self.fake_st.button_clicks["drive_main_load_button"] = True
        with patch.object(google_drive, "refresh_drive_data_transaction", side_effect=DriveUserError("network")):
            rendered = google_drive.render_drive_data_load_prompt()

        self.assertTrue(rendered)
        self.assertFalse(self.fake_st.session_state["drive_refresh_in_progress"])
        self.assertIn("Google Drive 数据加载失败", self.fake_st.session_state["drive_refresh_message"])


if __name__ == "__main__":
    unittest.main()
