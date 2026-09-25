from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd
import streamlit as st

from app import business_dashboard, google_drive
from app.product_range_metrics import _annual_target_table, _target_table
from app.target_metrics import XFTargetWorkbook
from app.tracking_metrics import build_monthly_tracking_table


def _target_workbook(september: float) -> XFTargetWorkbook:
    monthly = [
        301554.26,
        335665.84,
        348655.49,
        257065.71,
        284008.00,
        279906.00,
        180693.00,
        309794.66,
        september,
        507966.00,
        440983.00,
        420093.00,
    ]
    company_targets = pd.DataFrame(
        {
            "Year": [2026] * 12,
            "Month": list(range(1, 13)),
            "Month Label": [f"{month}月" for month in range(1, 13)],
            "Original Target": monthly,
            "Revised Target": monthly,
            "Notes": [""] * 12,
        }
    )
    amount_data = pd.concat(
        [
            company_targets.assign(**{"Product Group": "公司整体"}),
            company_targets.assign(**{"Product Group": "Test Group"}),
        ],
        ignore_index=True,
    )
    case_data = pd.DataFrame(
        {
            "Year": [2026],
            "Month": [9],
            "Product Group": ["Test Group"],
            "Case Target": [1.0],
        }
    )
    annual = float(sum(monthly))
    return XFTargetWorkbook(
        amount_data=amount_data,
        case_data=case_data,
        company_targets=company_targets,
        annual_targets={2026: annual},
        amount_sheet="2026销售目标金额",
        case_sheet="月度箱数需求",
        target_year=2026,
        product_group_count=1,
        company_annual_amount_target=annual,
        company_annual_case_target=1.0,
        structure_label="2026销售目标金额 + 月度箱数需求",
    )


class TargetRefreshConsistencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_st = SimpleNamespace(session_state={})
        self.streamlit_patch = patch.object(google_drive, "_get_streamlit", return_value=self.fake_st)
        self.streamlit_patch.start()

    def tearDown(self) -> None:
        self.streamlit_patch.stop()

    def _store(self, workbook: XFTargetWorkbook, modified_time: str) -> None:
        google_drive.store_target_workbook_in_session(
            workbook,
            "XF_Targets_Latest.xlsx",
            google_drive.DRIVE_SOURCE_LABEL,
            "drive",
            modified_time,
            "target-file-id",
        )

    def test_new_target_version_replaces_all_target_state(self) -> None:
        self._store(_target_workbook(341643.00), "2026-07-14T14:29:24.487Z")
        self._store(_target_workbook(329643.00), "2026-09-25T11:10:04.131Z")

        target_data = self.fake_st.session_state["target_data"]
        september = target_data.loc[target_data["Month"].eq(9)].iloc[0]
        self.assertEqual(329643.00, float(september["Original Target"]))
        self.assertEqual(329643.00, float(september["Revised Target"]))
        self.assertAlmostEqual(3996027.96, float(target_data["Revised Target"].sum()))
        self.assertAlmostEqual(3996027.96, self.fake_st.session_state["target_annual_targets"][2026])

        version = self.fake_st.session_state["target_source_version"]
        self.assertEqual(version, self.fake_st.session_state["target_data_version"])
        self.assertEqual(version, self.fake_st.session_state["target_amount_data_version"])
        self.assertEqual(version, self.fake_st.session_state["target_case_data_version"])
        self.assertEqual(version, self.fake_st.session_state["target_annual_targets_version"])
        self.assertEqual(version, target_data.attrs["target_source_version"])
        self.assertEqual(version, self.fake_st.session_state["target_amount_data"].attrs["target_source_version"])

    def test_new_target_version_clears_old_widget_and_revised_state(self) -> None:
        self._store(_target_workbook(341643.00), "2026-07-14T14:29:24.487Z")
        old_targets = self.fake_st.session_state["target_data"].copy()
        old_targets.loc[old_targets["Month"].eq(9), "Revised Target"] = 500000.00
        self.fake_st.session_state["target_data"] = old_targets
        self.fake_st.session_state["target_editor_2026"] = {"edited_rows": {8: {"Revised Target": 500000.00}}}
        self.fake_st.session_state["target_annual_input_2026"] = 4500000.00
        self.fake_st.session_state["target_revised_source_version"] = self.fake_st.session_state["target_source_version"]

        self._store(_target_workbook(329643.00), "2026-09-25T11:10:04.131Z")

        self.assertNotIn("target_editor_2026", self.fake_st.session_state)
        self.assertNotIn("target_annual_input_2026", self.fake_st.session_state)
        self.assertNotIn("target_revised_source_version", self.fake_st.session_state)
        september = self.fake_st.session_state["target_data"].loc[
            self.fake_st.session_state["target_data"]["Month"].eq(9), "Revised Target"
        ].iloc[0]
        self.assertEqual(329643.00, float(september))

    def test_home_tracking_and_product_range_use_new_target_version(self) -> None:
        latest = _target_workbook(329643.00)
        self._store(latest, "2026-09-25T11:10:04.131Z")
        target_data = self.fake_st.session_state["target_data"]
        amount_data = self.fake_st.session_state["target_amount_data"]
        sales = pd.DataFrame(
            {
                "Performance Date": [pd.Timestamp("2026-09-25")],
                "Sales Amount": [100.0],
                "Product Group": ["Test Group"],
            }
        )

        with patch.object(st, "session_state", self.fake_st.session_state):
            homepage = business_dashboard._target_excel_for_anchor(sales)
        tracking, summary = build_monthly_tracking_table(sales, target_data, 2026)
        product_month = _target_table(amount_data, 2026, 9)
        product_year = _annual_target_table(amount_data, 2026)

        self.assertEqual(329643.00, homepage.monthly_target)
        self.assertAlmostEqual(3996027.96, homepage.annual_target)
        self.assertEqual(329643.00, float(tracking.loc[tracking["Month"].eq(9), "调整后目标"].iloc[0]))
        self.assertAlmostEqual(3996027.96, summary.annual_target)
        self.assertEqual(329643.00, float(product_month["Monthly Target"].iloc[0]))
        self.assertAlmostEqual(3996027.96, float(product_year["Annual Target"].iloc[0]))

    def test_target_cache_and_metadata_restore_the_same_version(self) -> None:
        latest = _target_workbook(329643.00)
        metadata = google_drive.DriveFileMetadata(
            file_id="target-file-id",
            name="XF_Targets_Latest.xlsx",
            modified_time="2026-09-25T11:10:04.131Z",
        )
        with TemporaryDirectory() as temp_dir, patch.object(
            google_drive, "CACHE_DIR", Path(temp_dir)
        ), patch.object(
            google_drive, "CACHE_TARGETS_PATH", Path(temp_dir) / "targets_clean.pkl"
        ), patch.object(
            google_drive, "CACHE_METADATA_PATH", Path(temp_dir) / "metadata.json"
        ):
            self.assertTrue(google_drive._write_target_cache(metadata, latest))
            cache_metadata = google_drive._read_cache_metadata()
            self.assertEqual(
                google_drive._target_source_version(
                    metadata.name,
                    "drive",
                    metadata.modified_time,
                    metadata.file_id,
                ),
                cache_metadata["target_source_version"],
            )

            self.fake_st.session_state.clear()
            self.assertTrue(google_drive._restore_target_cache(metadata))

        version = self.fake_st.session_state["target_source_version"]
        self.assertEqual(version, self.fake_st.session_state["target_data_version"])
        self.assertEqual(version, self.fake_st.session_state["target_amount_data_version"])
        self.assertAlmostEqual(3996027.96, self.fake_st.session_state["target_annual_targets"][2026])


if __name__ == "__main__":
    unittest.main()
