import unittest

import pandas as pd

from app.executive_insights import (
    build_monthly_summary,
    build_upcoming_month_target_summary,
    england_wales_bank_holidays,
    england_wales_business_days,
    monthly_summary_context,
    should_show_upcoming_month,
)


def _sales_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Performance Date": [
                "2026-07-10",
                "2026-07-20",
                "2026-07-25",
                "2026-07-30",
                "2025-07-10",
                "2025-07-20",
                "2025-07-25",
                "2025-07-30",
                "2026-06-10",
                "2026-06-20",
                "2026-06-25",
                "2026-06-30",
            ],
            "Sales Amount": [1000, 2000, 3000, 4000, 800, 900, 1000, 1100, 700, 800, 900, 1000],
            "Order No.": ["A1", "A2", "A3", "A4", "P1", "P2", "P3", "P4", "J1", "J2", "J3", "J4"],
            "Customer Code": ["C1", "C2", "C1", "C3", "C1", "C2", "C4", "C4", "C1", "C2", "C3", "C4"],
            "Customer Label": ["Alpha", "Bravo", "Alpha", "Charlie", "Alpha", "Bravo", "Delta", "Delta", "Alpha", "Bravo", "Charlie", "Delta"],
            "Product Group": ["Fresh", "Ambient", "Fresh", "Frozen", "Fresh", "Ambient", "Fresh", "Frozen", "Fresh", "Ambient", "Fresh", "Frozen"],
            "Product Code": ["P1", "P2", "P1", "P3", "P1", "P2", "P1", "P3", "P1", "P2", "P1", "P3"],
            "Quantity": [10, 20, 30, 40, 8, 9, 10, 11, 7, 8, 9, 10],
        }
    )


def _targets_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Year": [2026, 2026, 2026],
            "Month": [7, 8, 6],
            "Original Target": [12000.0, 15000.0, 10000.0],
            "Revised Target": [pd.NA, pd.NA, pd.NA],
        }
    )


def _amount_targets_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Year": [2026, 2026, 2026, 2026, 2026, 2026],
            "Month": [8, 8, 8, 7, 7, 7],
            "Product Group": ["Fresh", "Ambient", "Frozen", "Fresh", "Ambient", "Frozen"],
            "Original Target": [7000.0, 5000.0, 3000.0, 6000.0, 5500.0, 500.0],
            "Revised Target": [pd.NA, pd.NA, pd.NA, pd.NA, pd.NA, pd.NA],
        }
    )


class ExecutiveInsightsTests(unittest.TestCase):
    def test_monthly_summary_collapsed_before_month_end_window(self) -> None:
        context = monthly_summary_context(pd.Timestamp("2026-07-20"))
        self.assertEqual(pd.Timestamp("2026-07-01"), context.period_start)
        self.assertFalse(context.expanded_by_default)
        self.assertFalse(should_show_upcoming_month(pd.Timestamp("2026-07-20")))

    def test_monthly_summary_expands_and_upcoming_shows_on_july_25(self) -> None:
        context = monthly_summary_context(pd.Timestamp("2026-07-25"))
        self.assertEqual(pd.Timestamp("2026-07-01"), context.period_start)
        self.assertTrue(context.expanded_by_default)
        self.assertTrue(should_show_upcoming_month(pd.Timestamp("2026-07-25")))

    def test_august_first_keeps_previous_month_summary_and_hides_august_preview(self) -> None:
        context = monthly_summary_context(pd.Timestamp("2026-08-01"))
        self.assertEqual(pd.Timestamp("2026-07-01"), context.period_start)
        self.assertEqual(pd.Timestamp("2026-07-31"), context.period_end)
        self.assertTrue(context.is_previous_month_summary)
        self.assertTrue(context.expanded_by_default)
        self.assertFalse(should_show_upcoming_month(pd.Timestamp("2026-08-01")))

    def test_monthly_summary_uses_target_and_same_month_last_year(self) -> None:
        summary = build_monthly_summary(_sales_fixture(), _targets_fixture(), anchor_date=pd.Timestamp("2026-07-30"))
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(10000.0, summary.sales)
        self.assertEqual(12000.0, summary.target)
        self.assertAlmostEqual(10000.0 / 12000.0, summary.completion or 0)
        self.assertEqual(3800.0, summary.previous_year_sales)
        self.assertAlmostEqual((10000.0 - 3800.0) / 3800.0, summary.yoy or 0)
        self.assertEqual("上月同期", summary.comparison_label)
        self.assertTrue(summary.context.is_partial_month)

    def test_yoy_without_last_year_base_returns_none(self) -> None:
        sales = _sales_fixture()
        sales = sales[~sales["Performance Date"].astype(str).str.startswith("2025")]
        summary = build_monthly_summary(sales, _targets_fixture(), anchor_date=pd.Timestamp("2026-07-30"))
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(0.0, summary.previous_year_sales)
        self.assertIsNone(summary.yoy)

    def test_upcoming_month_summary_uses_target_not_zero(self) -> None:
        summary = build_upcoming_month_target_summary(
            _sales_fixture(),
            _targets_fixture(),
            _amount_targets_fixture(),
            anchor_date=pd.Timestamp("2026-07-25"),
        )
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(pd.Timestamp("2026-08-01"), summary.month_start)
        self.assertEqual(15000.0, summary.target)
        self.assertEqual(12000.0, summary.current_month_target)
        self.assertEqual(3000.0, summary.change_amount)
        self.assertFalse(summary.missing_target)
        self.assertEqual(6, summary.calendar_week_count)
        self.assertEqual(20, summary.business_day_count)
        self.assertEqual(750.0, summary.business_day_target)
        self.assertEqual(["Frozen", "Fresh"], summary.highest_increase_groups)

    def test_missing_upcoming_target_is_not_zero(self) -> None:
        targets = _targets_fixture()
        targets = targets[~targets["Month"].eq(8)]
        summary = build_upcoming_month_target_summary(
            _sales_fixture(),
            targets,
            _amount_targets_fixture(),
            anchor_date=pd.Timestamp("2026-07-25"),
        )
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertIsNone(summary.target)
        self.assertTrue(summary.missing_target)

    def test_august_2026_workdays_exclude_summer_bank_holiday(self) -> None:
        holidays = england_wales_bank_holidays(2026)
        self.assertIn(pd.Timestamp("2026-08-31").date(), holidays)
        self.assertEqual(
            20,
            england_wales_business_days(pd.Timestamp("2026-08-01"), pd.Timestamp("2026-08-31")),
        )


if __name__ == "__main__":
    unittest.main()
