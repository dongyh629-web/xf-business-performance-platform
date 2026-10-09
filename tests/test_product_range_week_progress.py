from __future__ import annotations

import unittest

import pandas as pd

from app.product_range_metrics import analysis_context, build_week_progress


class ProductRangeWeekProgressTests(unittest.TestCase):
    def test_prior_full_month_is_separate_from_same_day_yoy_window(self) -> None:
        sales = pd.DataFrame(
            {
                "Performance Date": pd.to_datetime(
                    [
                        "2026-10-05",
                        "2026-10-10",
                        "2025-10-05",
                        "2025-10-10",
                        "2025-10-20",
                        "2025-10-31",
                    ]
                ),
                "Sales Amount": [100.0, 200.0, 50.0, 50.0, 300.0, 600.0],
                "Product Group": ["Range A"] * 6,
            }
        )
        ctx = analysis_context(sales, 2026, 10)

        result = build_week_progress(sales, None, ctx, "Range A")

        week_1 = result.loc[result["Week"].eq(1)].iloc[0]
        week_2 = result.loc[result["Week"].eq(2)].iloc[0]
        self.assertEqual(1000.0, week_1["Previous Year Full Month Sales"])
        self.assertEqual(1000.0, week_2["Previous Year Full Month Sales"])
        self.assertAlmostEqual(0.10, week_1["Percent of Previous Year Full Month"])
        self.assertAlmostEqual(0.30, week_2["Percent of Previous Year Full Month"])
        self.assertEqual(50.0, week_1["Previous Year Cumulative"])
        self.assertEqual(100.0, week_2["Previous Year Cumulative"])
        self.assertAlmostEqual(1.0, week_1["Cumulative YoY"])
        self.assertAlmostEqual(2.0, week_2["Cumulative YoY"])

    def test_missing_prior_full_month_uses_no_benchmark(self) -> None:
        sales = pd.DataFrame(
            {
                "Performance Date": pd.to_datetime(["2026-10-05"]),
                "Sales Amount": [100.0],
                "Product Group": ["Range A"],
            }
        )
        ctx = analysis_context(sales, 2026, 10)

        result = build_week_progress(sales, None, ctx, "Range A")

        self.assertTrue(result["Previous Year Full Month Sales"].isna().all())
        self.assertTrue(result["Percent of Previous Year Full Month"].isna().all())


if __name__ == "__main__":
    unittest.main()
