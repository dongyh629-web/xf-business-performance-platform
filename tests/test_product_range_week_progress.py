from __future__ import annotations

import unittest

import pandas as pd

from app.product_groups import canonical_product_group, normalize_product_group_copy, unmatched_product_groups
from app.product_range_metrics import analysis_context, build_range_overview, build_week_progress


class ProductRangeWeekProgressTests(unittest.TestCase):
    def test_target_product_group_aliases_match_sales_without_mutating_source(self) -> None:
        targets = pd.DataFrame(
            {
                "Year": [2026, 2026],
                "Month": [10, 10],
                "Product Group": ["Shrimp Paste(虾滑)", "7.0干货Ambient Pack/空酱"],
                "Original Target": [41078.0, 7000.0],
                "Revised Target": [41078.0, 7000.0],
            }
        )
        sales = pd.DataFrame(
            {
                "Performance Date": pd.to_datetime(["2026-10-09", "2026-10-09"]),
                "Sales Amount": [1000.0, 500.0],
                "Product Group": ["8.0虾滑Shrimp Paste", "7.0 空酱部队Ambient Pack"],
            }
        )

        overview, _ctx = build_range_overview(sales, targets, 2026, 10)

        shrimp = overview.loc[overview["Product Group"].eq("8.0虾滑Shrimp Paste")].iloc[0]
        ambient = overview.loc[overview["Product Group"].eq("7.0 空酱部队Ambient Pack")].iloc[0]
        self.assertEqual(41078.0, shrimp["Monthly Target"])
        self.assertEqual(7000.0, ambient["Monthly Target"])
        self.assertEqual(48078.0, overview["Monthly Target"].sum())
        self.assertEqual("Shrimp Paste(虾滑)", targets.iloc[0]["Product Group"])
        self.assertEqual("7.0干货Ambient Pack/空酱", targets.iloc[1]["Product Group"])

    def test_unmatched_target_product_groups_are_reported_after_alias_mapping(self) -> None:
        unmatched = unmatched_product_groups(
            ["Shrimp Paste(虾滑)", "Future Range"],
            ["8.0虾滑Shrimp Paste", "Existing Range"],
        )

        self.assertEqual(["Future Range"], unmatched)
        self.assertEqual("7.0 空酱部队Ambient Pack", canonical_product_group("7.0干货Ambient Pack/空酱"))
        self.assertEqual("香肠系列 Sausage Series", canonical_product_group("Sausage Series/烤肠"))

    def test_normalization_returns_copy(self) -> None:
        source = pd.DataFrame({"Product Group": ["Shrimp Paste(虾滑)"]})
        normalized = normalize_product_group_copy(source)

        self.assertEqual("Shrimp Paste(虾滑)", source.iloc[0]["Product Group"])
        self.assertEqual("8.0虾滑Shrimp Paste", normalized.iloc[0]["Product Group"])

    def test_range_overview_has_per_group_prior_full_month_and_achievement(self) -> None:
        sales = pd.DataFrame(
            {
                "Performance Date": pd.to_datetime(
                    [
                        "2026-10-05",
                        "2026-10-09",
                        "2026-10-05",
                        "2025-10-05",
                        "2025-10-20",
                        "2025-10-05",
                        "2025-10-31",
                    ]
                ),
                "Sales Amount": [100.0, 50.0, 80.0, 75.0, 225.0, 40.0, 160.0],
                "Product Group": ["Range A", "Range A", "Range B", "Range A", "Range A", "Range B", "Range B"],
            }
        )

        overview, _ctx = build_range_overview(sales, None, 2026, 10)
        range_a = overview.loc[overview["Product Group"].eq("Range A")].iloc[0]
        range_b = overview.loc[overview["Product Group"].eq("Range B")].iloc[0]

        self.assertEqual(150.0, range_a["Current Month Sales"])
        self.assertEqual(75.0, range_a["Previous Year Same Period"])
        self.assertEqual(300.0, range_a["Previous Year Full Month Sales"])
        self.assertAlmostEqual(1.0, range_a["YoY Rate"])
        self.assertAlmostEqual(0.5, range_a["LY Achievement"])
        self.assertEqual(80.0, range_b["Current Month Sales"])
        self.assertEqual(40.0, range_b["Previous Year Same Period"])
        self.assertEqual(200.0, range_b["Previous Year Full Month Sales"])
        self.assertAlmostEqual(0.4, range_b["LY Achievement"])
        self.assertEqual(230.0, overview["Current Month Sales"].sum())
        self.assertEqual(500.0, overview["Previous Year Full Month Sales"].sum())
        self.assertAlmostEqual(0.46, overview["Current Month Sales"].sum() / overview["Previous Year Full Month Sales"].sum())

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
