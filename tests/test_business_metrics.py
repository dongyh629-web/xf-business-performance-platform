from __future__ import annotations

from io import BytesIO
import unittest

import pandas as pd

from app.business_metrics import (
    aggregate_customer_profitability,
    aggregate_product_group_profitability,
    aggregate_product_profitability,
    build_business_metrics_dataframe,
    calculate_cost_coverage,
    cost_coverage_report,
    monthly_profitability,
    negative_gross_profit_transactions,
    profitability_kpis,
    suspicious_unit_comparison,
)
from app.cost_snapshots import load_cost_snapshot


def _snapshot(rows: list[dict], version: str = "2026-07-01"):
    output = BytesIO()
    pd.DataFrame(rows).to_excel(output, index=False)
    output.seek(0)
    return load_cost_snapshot(output, pd.Timestamp(version), f"XF_Product_Cost_{version}.xlsx")


class BusinessMetricsTests(unittest.TestCase):
    def test_normal_profit_calculation(self) -> None:
        snapshot = _snapshot([{"Product Code": "A", "Unit Cost": 4, "Effective From": "2026-07-01"}])
        sales = pd.DataFrame(
            {"Completed Date": [pd.Timestamp("2026-07-02")], "Product Code": ["A"], "Quantity": [2], "Sales Amount": [12]}
        )
        metrics = build_business_metrics_dataframe(sales, [snapshot])
        self.assertEqual(8.0, metrics.loc[0, "Total Cost"])
        self.assertEqual(4.0, metrics.loc[0, "Gross Profit"])
        self.assertAlmostEqual(1 / 3, metrics.loc[0, "Margin %"])

    def test_sales_amount_zero_keeps_margin_empty(self) -> None:
        snapshot = _snapshot([{"Product Code": "A", "Unit Cost": 4, "Effective From": "2026-07-01"}])
        sales = pd.DataFrame(
            {"Completed Date": [pd.Timestamp("2026-07-02")], "Product Code": ["A"], "Quantity": [2], "Sales Amount": [0]}
        )
        metrics = build_business_metrics_dataframe(sales, [snapshot])
        self.assertEqual(8.0, metrics.loc[0, "Total Cost"])
        self.assertEqual(-8.0, metrics.loc[0, "Gross Profit"])
        self.assertTrue(pd.isna(metrics.loc[0, "Margin %"]))
        self.assertEqual("Unclassified", metrics.loc[0, "Zero-value Reason"])
        self.assertEqual("Pending Business Review", metrics.loc[0, "Zero-value Validation Status"])

    def test_commercial_profitability_excludes_zero_value_cost_from_margin(self) -> None:
        snapshot = _snapshot(
            [
                {"Product Code": "A", "Unit Cost": 7, "Effective From": "2026-07-01"},
                {"Product Code": "B", "Unit Cost": 10, "Effective From": "2026-07-01"},
            ]
        )
        sales = pd.DataFrame(
            {
                "Completed Date": pd.to_datetime(["2026-07-02", "2026-07-02"]),
                "Product Code": ["A", "B"],
                "Quantity": [100, 10],
                "Sales Amount": [1000, 0],
            }
        )
        metrics = build_business_metrics_dataframe(sales, [snapshot])
        kpis = profitability_kpis(metrics)
        self.assertEqual(1000.0, kpis["Commercial Sales"])
        self.assertEqual(700.0, kpis["Commercial Cost"])
        self.assertEqual(300.0, kpis["Commercial Gross Profit"])
        self.assertAlmostEqual(0.30, kpis["Commercial Gross Margin"])
        self.assertEqual(100.0, kpis["Zero-value Outbound Cost"])
        self.assertEqual(200.0, kpis["Contribution After Zero-value Cost"])
        self.assertNotAlmostEqual(0.20, kpis["Commercial Gross Margin"])
        self.assertEqual(2, len(metrics))

        trend = monthly_profitability(metrics)
        self.assertEqual(100.0, float(trend.loc[0, "Zero-value Outbound Cost"]))
        self.assertAlmostEqual(0.30, float(trend.loc[0, "Commercial Gross Margin"]))

    def test_warehouse_loan_zero_value_does_not_enter_commercial_margin(self) -> None:
        snapshot = _snapshot([{"Product Code": "A", "Unit Cost": 10, "Effective From": "2026-07-01"}])
        sales = pd.DataFrame(
            {
                "Completed Date": [pd.Timestamp("2026-07-02")],
                "Product Code": ["A"],
                "Quantity": [3],
                "Sales Amount": [0],
                "Zero-value Reason": ["Warehouse Loan"],
            }
        )
        metrics = build_business_metrics_dataframe(sales, [snapshot])
        metrics.loc[0, "Zero-value Reason"] = "Warehouse Loan"
        kpis = profitability_kpis(metrics)
        self.assertEqual(0.0, kpis["Commercial Sales"])
        self.assertIsNone(kpis["Commercial Gross Margin"])
        self.assertEqual(30.0, kpis["Zero-value Outbound Cost"])

    def test_quantity_zero_keeps_profit_empty(self) -> None:
        snapshot = _snapshot([{"Product Code": "A", "Unit Cost": 4, "Effective From": "2026-07-01"}])
        sales = pd.DataFrame(
            {"Completed Date": [pd.Timestamp("2026-07-02")], "Product Code": ["A"], "Quantity": [0], "Sales Amount": [12]}
        )
        metrics = build_business_metrics_dataframe(sales, [snapshot])
        self.assertTrue(pd.isna(metrics.loc[0, "Total Cost"]))
        self.assertTrue(pd.isna(metrics.loc[0, "Gross Profit"]))
        self.assertTrue(pd.isna(metrics.loc[0, "Margin %"]))

    def test_unit_cost_zero_is_invalid_cost_and_profit_empty(self) -> None:
        snapshot = _snapshot([{"Product Code": "A", "Unit Cost": 0, "Effective From": "2026-07-01"}])
        sales = pd.DataFrame(
            {"Completed Date": [pd.Timestamp("2026-07-02")], "Product Code": ["A"], "Quantity": [2], "Sales Amount": [12]}
        )
        metrics = build_business_metrics_dataframe(sales, [snapshot])
        self.assertEqual("Invalid Unit Cost", metrics.loc[0, "Cost Match Status"])
        self.assertTrue(pd.isna(metrics.loc[0, "Gross Profit"]))

    def test_unit_cost_missing_is_invalid_cost(self) -> None:
        snapshot = _snapshot([{"Product Code": "A", "Unit Cost": pd.NA, "Effective From": "2026-07-01"}])
        sales = pd.DataFrame(
            {"Completed Date": [pd.Timestamp("2026-07-02")], "Product Code": ["A"], "Quantity": [2], "Sales Amount": [12]}
        )
        metrics = build_business_metrics_dataframe(sales, [snapshot])
        self.assertEqual("Invalid Unit Cost", metrics.loc[0, "Cost Match Status"])
        self.assertTrue(pd.isna(metrics.loc[0, "Total Cost"]))

    def test_missing_cost_preserves_sales_row(self) -> None:
        snapshot = _snapshot([{"Product Code": "A", "Unit Cost": 4, "Effective From": "2026-07-01"}])
        sales = pd.DataFrame(
            {"Completed Date": [pd.Timestamp("2026-07-02")], "Product Code": ["B"], "Quantity": [2], "Sales Amount": [12]}
        )
        metrics = build_business_metrics_dataframe(sales, [snapshot])
        self.assertEqual(1, len(metrics))
        self.assertEqual("Missing Product Cost", metrics.loc[0, "Cost Match Status"])
        self.assertTrue(pd.isna(metrics.loc[0, "Gross Profit"]))

    def test_no_cost_version_preserves_sales_row(self) -> None:
        snapshot = _snapshot([{"Product Code": "A", "Unit Cost": 4, "Effective From": "2026-07-01"}])
        sales = pd.DataFrame(
            {"Completed Date": [pd.Timestamp("2026-06-30")], "Product Code": ["A"], "Quantity": [2], "Sales Amount": [12]}
        )
        metrics = build_business_metrics_dataframe(sales, [snapshot])
        self.assertEqual("No Cost Version", metrics.loc[0, "Cost Match Status"])
        self.assertTrue(pd.isna(metrics.loc[0, "Gross Profit"]))

    def test_non_sale_does_not_calculate_profit(self) -> None:
        snapshot = _snapshot([{"Product Code": "A", "Unit Cost": 4, "Status": "non-sale", "Effective From": "2026-07-01"}])
        sales = pd.DataFrame(
            {"Completed Date": [pd.Timestamp("2026-07-02")], "Product Code": ["A"], "Quantity": [2], "Sales Amount": [12]}
        )
        metrics = build_business_metrics_dataframe(sales, [snapshot])
        self.assertEqual("Non-sale Product", metrics.loc[0, "Cost Match Status"])
        self.assertTrue(pd.isna(metrics.loc[0, "Gross Profit"]))

    def test_discontinued_calculates_profit(self) -> None:
        snapshot = _snapshot(
            [{"Product Code": "A", "Unit Cost": 4, "Status": "Discontinued", "Effective From": "2026-07-01"}]
        )
        sales = pd.DataFrame(
            {"Completed Date": [pd.Timestamp("2026-07-02")], "Product Code": ["A"], "Quantity": [2], "Sales Amount": [12]}
        )
        metrics = build_business_metrics_dataframe(sales, [snapshot])
        self.assertEqual("Matched", metrics.loc[0, "Cost Match Status"])
        self.assertEqual(4.0, metrics.loc[0, "Gross Profit"])

    def test_negative_profit_and_margin(self) -> None:
        snapshot = _snapshot([{"Product Code": "A", "Unit Cost": 10, "Effective From": "2026-07-01"}])
        sales = pd.DataFrame(
            {"Completed Date": [pd.Timestamp("2026-07-02")], "Product Code": ["A"], "Quantity": [2], "Sales Amount": [12]}
        )
        metrics = build_business_metrics_dataframe(sales, [snapshot])
        self.assertEqual(-8.0, metrics.loc[0, "Gross Profit"])
        self.assertAlmostEqual(-8 / 12, metrics.loc[0, "Margin %"])

    def test_coverage_metrics(self) -> None:
        snapshot = _snapshot([{"Product Code": "A", "Unit Cost": 4, "Effective From": "2026-07-01"}])
        sales = pd.DataFrame(
            {
                "Completed Date": pd.to_datetime(["2026-07-02", "2026-07-02", "2026-06-30"]),
                "Product Code": ["A", "B", "A"],
                "Quantity": [2, 2, 2],
                "Sales Amount": [12, 8, 10],
            }
        )
        metrics = build_business_metrics_dataframe(sales, [snapshot])
        coverage = calculate_cost_coverage(metrics)
        self.assertEqual(3, coverage.total_rows)
        self.assertEqual(1, coverage.matched_rows)
        self.assertAlmostEqual(1 / 3, coverage.rows_coverage)
        self.assertAlmostEqual(12 / 30, coverage.sales_coverage)
        self.assertAlmostEqual(1 / 2, coverage.sku_coverage)
        report = cost_coverage_report(metrics)
        self.assertEqual(coverage.status_counts, report["Cost Match Status Counts"])

    def test_product_customer_and_group_aggregation(self) -> None:
        snapshot = _snapshot(
            [
                {"Product Code": "A", "Unit Cost": 4, "Product Group": "Group A", "Effective From": "2026-07-01"},
                {"Product Code": "B", "Unit Cost": 2, "Product Group": "Group B", "Effective From": "2026-07-01"},
            ]
        )
        sales = pd.DataFrame(
            {
                "Completed Date": pd.to_datetime(["2026-07-02", "2026-07-02", "2026-07-02"]),
                "Customer": ["C1", "C1", "C2"],
                "Product Code": ["A", "A", "B"],
                "Product Name": ["Alpha", "Alpha", "Beta"],
                "Product Group": ["Group A", "Group A", "Group B"],
                "Quantity": [2, 1, 2],
                "Sales Amount": [12, 6, 8],
            }
        )
        metrics = build_business_metrics_dataframe(sales, [snapshot])
        product = aggregate_product_profitability(metrics)
        alpha = product[product["Product Code"].eq("A")].iloc[0]
        self.assertEqual(18.0, alpha["Sales Amount"])
        self.assertEqual(6.0, alpha["Gross Profit"])
        customer = aggregate_customer_profitability(metrics)
        self.assertEqual(2, int(customer.loc[customer["Customer"].eq("C1"), "Sales Rows"].iloc[0]))
        group = aggregate_product_group_profitability(metrics)
        self.assertEqual({"Group A", "Group B"}, set(group["Product Group"].tolist()))

    def test_negative_gross_profit_and_unit_cost_diagnostics(self) -> None:
        snapshot = _snapshot([{"Product Code": "A", "Unit Cost": 10, "Effective From": "2026-07-01"}])
        sales = pd.DataFrame(
            {
                "Completed Date": [pd.Timestamp("2026-07-02")],
                "Product Code": ["A"],
                "Product Name": ["Alpha"],
                "Quantity": [2],
                "Sales Amount": [12],
            }
        )
        metrics = build_business_metrics_dataframe(sales, [snapshot])
        self.assertEqual(1, len(negative_gross_profit_transactions(metrics)))
        suspicious = suspicious_unit_comparison(metrics)
        self.assertEqual(1, len(suspicious))
        self.assertIn("Unit Cost > Unit Selling Price", suspicious.iloc[0]["Suspicion Reason"])

    def test_zero_amount_and_fractional_quantity_diagnostics(self) -> None:
        snapshot = _snapshot([{"Product Code": "A", "Unit Cost": 1, "Effective From": "2026-07-01"}])
        sales = pd.DataFrame(
            {
                "Completed Date": [pd.Timestamp("2026-07-02")],
                "Product Code": ["A"],
                "Product Name": ["Alpha"],
                "Quantity": [0.5],
                "Sales Amount": [0],
            }
        )
        metrics = build_business_metrics_dataframe(sales, [snapshot])
        suspicious = suspicious_unit_comparison(metrics)
        reason = suspicious.iloc[0]["Suspicion Reason"]
        self.assertIn("Fractional Quantity", reason)
        self.assertIn("Zero-value Outbound", reason)

    def test_future_metric_columns_are_reserved(self) -> None:
        snapshot = _snapshot([{"Product Code": "A", "Unit Cost": 4, "Effective From": "2026-07-01"}])
        sales = pd.DataFrame(
            {"Completed Date": [pd.Timestamp("2026-07-02")], "Product Code": ["A"], "Quantity": [2], "Sales Amount": [12]}
        )
        metrics = build_business_metrics_dataframe(sales, [snapshot])
        for column in ["Markup %", "Current Price", "Standard Price", "Discount %", "Contribution %"]:
            self.assertIn(column, metrics.columns)
            self.assertTrue(pd.isna(metrics.loc[0, column]))
        self.assertIn("ASP", metrics.columns)
        self.assertEqual(6.0, metrics.loc[0, "ASP"])


if __name__ == "__main__":
    unittest.main()
