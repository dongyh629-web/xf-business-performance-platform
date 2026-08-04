import unittest

import pandas as pd

from app.tracking_metrics import build_monthly_tracking_table


def _sales(rows: list[tuple[str, float, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Performance Date": [pd.Timestamp(date) for date, _, _ in rows],
            "Sales Amount": [amount for _, amount, _ in rows],
            "Product Group": [group for _, _, group in rows],
        }
    )


def _targets(year: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Year": [year] * 12,
            "Month": list(range(1, 13)),
            "Original Target": [1000.0] * 12,
            "Revised Target": [1000.0] * 12,
            "Notes": [""] * 12,
        }
    )


class MonthlyTrackingYoYTests(unittest.TestCase):
    def test_previous_year_months_use_comparison_data_not_current_year_filter(self) -> None:
        current_year_sales = _sales(
            [
                ("2026-07-30", 700.0, "Fresh"),
            ]
        )
        comparison_sales = _sales(
            [
                ("2025-07-30", 350.0, "Fresh"),
                ("2025-08-15", 800.0, "Fresh"),
                ("2025-12-15", 1200.0, "Fresh"),
                ("2026-07-30", 700.0, "Fresh"),
            ]
        )

        table, summary = build_monthly_tracking_table(
            current_year_sales,
            _targets(2026),
            2026,
            comparison_sales,
        )

        july = table.loc[table["Month"].eq(7)].iloc[0]
        self.assertEqual(july["实际销售"], 700.0)
        self.assertEqual(july["去年同期"], 350.0)
        self.assertAlmostEqual(july["同比"], 1.0)

        august = table.loc[table["Month"].eq(8)].iloc[0]
        self.assertEqual(august["实际销售"], 0.0)
        self.assertEqual(august["去年同期"], 800.0)
        self.assertAlmostEqual(august["同比"], -1.0)
        self.assertEqual(august["状态"], "尚未开始")

        december = table.loc[table["Month"].eq(12)].iloc[0]
        self.assertEqual(december["实际销售"], 0.0)
        self.assertEqual(december["去年同期"], 1200.0)
        self.assertAlmostEqual(december["同比"], -1.0)
        self.assertEqual(december["状态"], "尚未开始")

        self.assertEqual(summary.previous_year_actual, 350.0)
        self.assertAlmostEqual(summary.annual_yoy, 1.0)

    def test_zero_previous_year_sales_keeps_yoy_without_base(self) -> None:
        current_year_sales = _sales([("2026-07-30", 700.0, "Fresh")])
        comparison_sales = _sales(
            [
                ("2025-07-30", 0.0, "Fresh"),
                ("2026-07-30", 700.0, "Fresh"),
            ]
        )

        table, _ = build_monthly_tracking_table(
            current_year_sales,
            _targets(2026),
            2026,
            comparison_sales,
        )

        july = table.loc[table["Month"].eq(7)].iloc[0]
        self.assertEqual(july["去年同期"], 0.0)
        self.assertIsNone(july["同比"])


if __name__ == "__main__":
    unittest.main()
