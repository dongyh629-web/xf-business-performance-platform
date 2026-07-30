from __future__ import annotations

import unittest

import pandas as pd

from app.auth import has_permission
from app.data_validation import (
    SIGN_OFF_CHECKLIST,
    VALIDATION_STATUS_OPTIONS,
    add_business_validation_status,
    coverage_by_dimension,
    coverage_by_month,
    coverage_summary,
    gift_free_of_charge_rows,
    gift_free_of_charge_summary,
    margin_band_analysis,
    profitability_readiness_score,
    top_exceptions,
    unit_validation_rows,
)


def _metrics() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Completed Date": pd.to_datetime(
                ["2026-07-02", "2026-07-03", "2026-07-04", "2026-08-02", "2026-08-03"]
            ),
            "Customer": ["A", "A", "B", "B", "C"],
            "Product Code": ["P1", "P2", "P3", "P4", "P5"],
            "Product Name": ["Alpha", "Beta", "Gamma", "Delta", "Echo"],
            "Product Group": ["G1", "G1", "G2", "G2", "G3"],
            "Quantity": [2, 3, 1.5, 10_000, 1],
            "Sales Amount": [20, 0, 30, 100, 50],
            "Unit Selling Price": [10, 0, 20, 0.01, 50],
            "Unit Cost": [5, 2, 25, 20, pd.NA],
            "Total Cost": [10, 6, 37.5, 200_000, pd.NA],
            "Gross Profit": [10, -6, -7.5, -199_900, pd.NA],
            "Margin %": [0.50, pd.NA, -0.25, -1999.0, pd.NA],
            "Cost Match Status": ["Matched", "Matched", "Matched", "Matched", "Missing Product Cost"],
        }
    )


class DataValidationTests(unittest.TestCase):
    def test_gift_free_of_charge_validation(self) -> None:
        metrics = _metrics()
        gifts = gift_free_of_charge_rows(metrics)
        self.assertEqual(["P2"], gifts["Product Code"].tolist())
        summary = gift_free_of_charge_summary(metrics)
        self.assertEqual(1, summary["Gift Rows"])
        self.assertEqual(0.0, summary["Gift Sales"])
        self.assertEqual(6.0, summary["Gift Cost"])

    def test_unit_validation_flags_expected_reasons(self) -> None:
        flagged = unit_validation_rows(_metrics())
        reasons = " | ".join(flagged["Validation Reason"].tolist())
        self.assertIn("Unit Cost > Unit Selling Price", reasons)
        self.assertIn("Margin < 0%", reasons)
        self.assertIn("Margin < 10%", reasons)
        self.assertIn("Fractional Quantity", reasons)
        self.assertIn("Sales Amount = 0", reasons)
        self.assertIn("Quantity outlier", reasons)
        self.assertIn("Unit Cost high", reasons)

    def test_coverage_summary_and_breakdowns(self) -> None:
        metrics = _metrics()
        summary = coverage_summary(metrics)
        self.assertEqual(5, summary["Total Rows"])
        self.assertEqual(4, summary["Costed Rows"])
        self.assertAlmostEqual(0.80, summary["Rows Coverage"])
        self.assertAlmostEqual(150 / 200, summary["Sales Coverage"])
        self.assertAlmostEqual(4 / 5, summary["SKU Coverage"])
        by_month = coverage_by_month(metrics)
        self.assertEqual(["2026-08", "2026-07"], by_month["Month"].tolist())
        by_group = coverage_by_dimension(metrics, "Product Group")
        self.assertEqual({"G1", "G2", "G3"}, set(by_group["Product Group"].tolist()))

    def test_margin_band_analysis_keeps_uncosted_rows(self) -> None:
        bands = margin_band_analysis(_metrics())
        self.assertIn("<0%", bands["Margin Band"].tolist())
        self.assertIn("40~60%", bands["Margin Band"].tolist())
        self.assertIn("No Margin", bands["Margin Band"].tolist())
        no_margin = bands[bands["Margin Band"].eq("No Margin")].iloc[0]
        self.assertEqual(2, int(no_margin["Rows"]))

    def test_business_validation_statuses(self) -> None:
        result = add_business_validation_status(_metrics())
        self.assertEqual("Gift", result.loc[1, "Business Validation Status"])
        self.assertEqual("Unit Check", result.loc[2, "Business Validation Status"])
        self.assertEqual("Cost Check", result.loc[4, "Business Validation Status"])

    def test_top_exceptions(self) -> None:
        exceptions = top_exceptions(_metrics(), limit=3)
        self.assertIn("Top Negative Margin Products", exceptions)
        self.assertIn("Top Missing Cost", exceptions)
        self.assertFalse(exceptions["Top Negative Margin Products"].empty)
        self.assertEqual("P5", exceptions["Top Missing Cost"].iloc[0]["Product Code"])

    def test_readiness_score_and_static_outputs(self) -> None:
        score = profitability_readiness_score(_metrics())
        self.assertLess(score.score, 85)
        self.assertIn(score.grade, {"Needs Review", "Not Ready"})
        self.assertIn("Sales Coverage", score.details)
        self.assertIn("Pending", VALIDATION_STATUS_OPTIONS)
        self.assertTrue(any("成本单位" in item for item in SIGN_OFF_CHECKLIST))

    def test_data_validation_permission_is_internal(self) -> None:
        self.assertTrue(has_permission("data_validation", "Admin"))
        self.assertTrue(has_permission("data_validation", "Finance"))
        self.assertFalse(has_permission("data_validation", "Sales"))
        self.assertFalse(has_permission("data_validation", "Executive"))


if __name__ == "__main__":
    unittest.main()
