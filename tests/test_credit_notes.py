from __future__ import annotations

from io import BytesIO
import unittest

import pandas as pd

from app.credit_metrics import (
    aggregate_credit_reasons,
    aggregate_customer_credits,
    aggregate_product_credits,
    aggregate_product_group_credits,
    credit_kpis,
    sales_for_credit_period,
)
from app.credit_notes import UNKNOWN_REASON, import_credit_enquiry
from app.credit_notes import build_credit_snapshot_registry, parse_credit_snapshot_date_from_filename


def _credit_workbook(rows: list[dict], header_offset: int = 2) -> BytesIO:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(
            writer,
            sheet_name="Sheet",
            index=False,
            startrow=header_offset,
        )
    output.seek(0)
    output.name = "CreditEnquiryList.xlsx"
    return output


class CreditNotesTest(unittest.TestCase):
    def test_credit_snapshot_filename_registry(self) -> None:
        self.assertEqual(pd.Timestamp("2026-08-14"), parse_credit_snapshot_date_from_filename("XF_Credit_2026-08-14.xlsx"))
        self.assertIsNone(parse_credit_snapshot_date_from_filename("CreditEnquiryList.xlsx"))

        registry = build_credit_snapshot_registry(
            [
                {"id": "1", "name": "XF_Credit_2026-07-31.xlsx", "modifiedTime": "2026-08-01T00:00:00Z"},
                {"id": "2", "name": "XF_Credit_2026-08-14.xlsx", "modifiedTime": "2026-08-15T00:00:00Z"},
                {"id": "3", "name": "~$XF_Credit_2026-08-14.xlsx", "modifiedTime": "2026-08-15T00:00:00Z"},
                {"id": "4", "name": "CreditEnquiryList.xlsx", "modifiedTime": "2026-08-15T00:00:00Z"},
            ]
        )

        self.assertEqual(["XF_Credit_2026-08-14.xlsx", "XF_Credit_2026-07-31.xlsx", "CreditEnquiryList.xlsx"], [entry.file_name for entry in registry.entries])
        self.assertEqual(["XF_Credit_2026-08-14.xlsx", "XF_Credit_2026-07-31.xlsx"], [entry.file_name for entry in registry.valid_entries()])

    def test_credit_file_imports_real_export_shape(self) -> None:
        result = import_credit_enquiry(
            _credit_workbook(
                [
                    {
                        "Credit Date": "2026-08-01",
                        "Receipt Date": "01/08/2026",
                        "Credit Number": "CN-1",
                        "Customer Code": "C1",
                        "Customer": "Customer A",
                        "Product": "Product A",
                        "Product Group": "Group A",
                        "Product Code": "P1",
                        "Credit Reason": "Expired",
                        "Status": "Completed",
                        "Quantity": 2,
                        "Sub Total": 12.5,
                    }
                ]
            )
        )

        self.assertEqual(["Credit Date", "Receipt Date", "Credit Number", "Customer Code", "Customer", "Product", "Product Group", "Product Code", "Credit Reason", "Status", "Quantity", "Sub Total"], list(result.raw.columns))
        self.assertEqual(1, len(result.clean))
        self.assertEqual(12.5, result.clean.iloc[0]["Credit Amount"])
        self.assertEqual("Expired", result.clean.iloc[0]["Credit Reason"])
        self.assertEqual(1, result.quality["Credit Note Count"])

    def test_missing_reason_is_unknown_and_rows_are_not_dropped(self) -> None:
        result = import_credit_enquiry(
            _credit_workbook(
                [
                    {
                        "Credit Date": "2026-08-01",
                        "Credit Number": "CN-1",
                        "Customer": "Customer A",
                        "Product": "Product A",
                        "Quantity": 1,
                        "Sub Total": -10,
                    }
                ]
            )
        )

        self.assertEqual(1, len(result.clean))
        self.assertEqual(UNKNOWN_REASON, result.clean.iloc[0]["Credit Reason"])
        self.assertEqual(10, result.clean.iloc[0]["Credit Amount"])
        self.assertEqual(1, result.quality["Unknown Reason"])

    def test_credit_kpis_and_net_sales(self) -> None:
        credit = pd.DataFrame(
            {
                "Credit Number": ["CN-1", "CN-2"],
                "Customer Key": ["C1", "C2"],
                "Product Key": ["P1", "P2"],
                "Credit Amount": [25.0, 75.0],
            }
        )
        sales = pd.DataFrame({"Sales Amount": [1000.0, 500.0]})

        kpis = credit_kpis(credit, sales)

        self.assertEqual(1500.0, kpis["Gross Sales"])
        self.assertEqual(100.0, kpis["Credit Amount"])
        self.assertAlmostEqual(100 / 1500, kpis["Credit Rate"])
        self.assertEqual(1400.0, kpis["Net Sales"])
        self.assertEqual(2, kpis["Affected Customers"])
        self.assertEqual(2, kpis["Affected Products"])

    def test_customer_product_and_reason_aggregations(self) -> None:
        credit = pd.DataFrame(
            {
                "Credit Date": pd.to_datetime(["2026-08-01", "2026-08-02", "2026-08-02"]),
                "Credit Number": ["CN-1", "CN-1", "CN-2"],
                "Customer Key": ["C1", "C1", "C2"],
                "Customer Label": ["Customer A", "Customer A", "Customer B"],
                "Product Key": ["P1", "P2", "P1"],
                "Product Code": ["P1", "P2", "P1"],
                "Product Label": ["Product A", "Product B", "Product A"],
                "Product Group": ["Group A", "Group B", "Group A"],
                "Credit Reason": ["Expired", "Damaged", UNKNOWN_REASON],
                "Quantity": [1, 2, 3],
                "Credit Amount": [10.0, 20.0, 30.0],
            }
        )
        sales = pd.DataFrame(
            {
                "Performance Date": pd.to_datetime(["2026-08-01", "2026-08-02"]),
                "Customer Key": ["C1", "C2"],
                "Customer Label": ["Customer A", "Customer B"],
                "Product Key": ["P1", "P1"],
                "Product Code": ["P1", "P1"],
                "Product Label": ["Product A", "Product A"],
                "Product Group": ["Group A", "Group A"],
                "Sales Amount": [100.0, 200.0],
            }
        )
        period_sales = sales_for_credit_period(sales, pd.Timestamp("2026-08-01").date(), pd.Timestamp("2026-08-31").date())

        customers = aggregate_customer_credits(credit, period_sales)
        products = aggregate_product_credits(credit, period_sales)
        product_groups = aggregate_product_group_credits(products)
        reasons = aggregate_credit_reasons(credit)

        self.assertEqual(30.0, float(customers.loc[customers["Customer Key"].eq("C1"), "Credit"].iloc[0]))
        self.assertEqual(40.0, float(products.loc[products["Product Key"].eq("P1"), "Credit"].iloc[0]))
        self.assertEqual(40.0, float(product_groups.loc[product_groups["Product Group"].eq("Group A"), "Credit"].iloc[0]))
        self.assertEqual(30.0, float(reasons.loc[reasons["Reason"].eq(UNKNOWN_REASON), "Credit Amount"].iloc[0]))

    def test_product_group_aggregation_accepts_raw_credit_table(self) -> None:
        credit = pd.DataFrame(
            {
                "Credit Number": ["CN-1", "CN-2", "CN-2"],
                "Product Group": ["Group A", "Group A", None],
                "Quantity": [1, 2, 3],
                "Credit Amount": [10.0, 20.0, 30.0],
            }
        )

        product_groups = aggregate_product_group_credits(credit)

        self.assertEqual(30.0, float(product_groups.loc[product_groups["Product Group"].eq("Group A"), "Credit"].iloc[0]))
        self.assertEqual(30.0, float(product_groups.loc[product_groups["Product Group"].eq("未分类 / Unclassified"), "Credit"].iloc[0]))

    def test_no_credit_does_not_error(self) -> None:
        empty = pd.DataFrame()
        sales = pd.DataFrame({"Sales Amount": [100.0]})

        kpis = credit_kpis(empty, sales)

        self.assertEqual(0.0, kpis["Credit Amount"])
        self.assertEqual(100.0, kpis["Net Sales"])


if __name__ == "__main__":
    unittest.main()
