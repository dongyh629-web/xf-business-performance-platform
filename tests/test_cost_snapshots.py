from __future__ import annotations

from io import BytesIO
import unittest

import pandas as pd

from app.cost_snapshots import (
    CostFileMetadata,
    build_cost_coverage_report,
    build_cost_snapshot_registry,
    load_cost_snapshot,
    match_sales_to_cost_versions,
    parse_cost_version_date_from_filename,
    read_cost_snapshot_workbook,
)


def _excel_bytes(rows: list[dict], header_offset: int = 0) -> BytesIO:
    output = BytesIO()
    df = pd.DataFrame(rows)
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        if header_offset:
            pd.DataFrame([["title"]]).to_excel(writer, sheet_name="Sheet", header=False, index=False)
            if header_offset > 1:
                pd.DataFrame([[""] for _ in range(header_offset - 1)]).to_excel(
                    writer,
                    sheet_name="Sheet",
                    header=False,
                    index=False,
                    startrow=1,
                )
        df.to_excel(writer, sheet_name="Sheet", index=False, startrow=header_offset)
    output.seek(0)
    return output


def _snapshot(rows: list[dict], version: str = "2026-07-01", name: str | None = None):
    return load_cost_snapshot(
        _excel_bytes(rows, header_offset=2),
        pd.Timestamp(version),
        name or f"XF_Product_Cost_{version}.xlsx",
    )


class CostSnapshotTests(unittest.TestCase):
    def test_valid_file_name(self) -> None:
        self.assertEqual(
            pd.Timestamp("2026-07-01"),
            parse_cost_version_date_from_filename("XF_Product_Cost_2026-07-01.xlsx"),
        )

    def test_invalid_file_name(self) -> None:
        self.assertIsNone(parse_cost_version_date_from_filename("XF_Product_Cost_2026-07-01xlsx.xlsx"))

    def test_temp_file_is_ignored(self) -> None:
        registry = build_cost_snapshot_registry(
            [
                CostFileMetadata("1", "~$XF_Product_Cost_2026-07-01.xlsx"),
                CostFileMetadata("2", ".XF_Product_Cost_2026-07-01.xlsx"),
            ]
        )
        self.assertEqual([], registry.entries)

    def test_duplicate_version_date_is_conflict(self) -> None:
        registry = build_cost_snapshot_registry(
            [
                CostFileMetadata("1", "XF_Product_Cost_2026-07-01.xlsx"),
                CostFileMetadata("2", "XF_Product_Cost_2026-07-01.xlsx"),
            ]
        )
        self.assertTrue(all(entry.validation_status == "Conflict" for entry in registry.entries))
        self.assertEqual([], registry.valid_entries)

    def test_header_on_third_row(self) -> None:
        raw, sheet = read_cost_snapshot_workbook(
            _excel_bytes(
                [
                    {"Product Code": "A", "Unit Cost": 1.2},
                    {"Product Code": "B", "Unit Cost": 2.3},
                ],
                header_offset=2,
            )
        )
        self.assertEqual("Sheet", sheet)
        self.assertEqual(["Product Code", "Unit Cost"], list(raw.columns))
        self.assertEqual(2, len(raw))

    def test_missing_required_columns_invalid(self) -> None:
        with self.assertRaises(ValueError):
            read_cost_snapshot_workbook(_excel_bytes([{"Product Code": "A"}], header_offset=0))

    def test_duplicate_product_code_invalid(self) -> None:
        snapshot = _snapshot(
            [
                {"Product Code": "A", "Unit Cost": 1, "Effective From": "2026-07-01"},
                {"Product Code": "A", "Unit Cost": 2, "Effective From": "2026-07-01"},
            ]
        )
        self.assertTrue(snapshot.registry_entry.participates_in_matching)
        self.assertIn("Duplicate Product Code", " ".join(snapshot.registry_entry.warnings))
        sales = pd.DataFrame({"Completed Date": [pd.Timestamp("2026-07-02")], "Product Code": ["A"], "Sales Amount": [10]})
        matched = match_sales_to_cost_versions(sales, [snapshot])
        self.assertEqual("Duplicate Cost Record", matched.loc[0, "Cost Match Status"])

    def test_effective_from_mismatch_warning(self) -> None:
        snapshot = _snapshot([{"Product Code": "A", "Unit Cost": 1, "Effective From": "2026-08-01"}])
        self.assertTrue(snapshot.registry_entry.participates_in_matching)
        self.assertEqual("Warning", snapshot.registry_entry.validation_status)

    def test_version_date_matching(self) -> None:
        snapshots = [
            _snapshot([{"Product Code": "A", "Unit Cost": 1, "Effective From": "2026-07-01"}], "2026-07-01"),
            _snapshot([{"Product Code": "A", "Unit Cost": 2, "Effective From": "2026-10-01"}], "2026-10-01"),
        ]
        sales = pd.DataFrame(
            {
                "Completed Date": pd.to_datetime(["2026-08-15", "2026-11-20"]),
                "Product Code": ["A", "A"],
                "Quantity": [1, 1],
                "Sales Amount": [10, 10],
            }
        )
        matched = match_sales_to_cost_versions(sales, snapshots)
        self.assertEqual([1.0, 2.0], matched["Unit Cost"].tolist())

    def test_early_sale_has_no_cost_version(self) -> None:
        snapshots = [_snapshot([{"Product Code": "A", "Unit Cost": 1, "Effective From": "2026-07-01"}])]
        sales = pd.DataFrame({"Completed Date": [pd.Timestamp("2026-06-30")], "Product Code": ["A"], "Sales Amount": [10]})
        matched = match_sales_to_cost_versions(sales, snapshots)
        self.assertEqual("No Cost Version", matched.loc[0, "Cost Match Status"])

    def test_empty_completed_date_is_invalid(self) -> None:
        snapshots = [_snapshot([{"Product Code": "A", "Unit Cost": 1, "Effective From": "2026-07-01"}])]
        sales = pd.DataFrame({"Completed Date": [pd.NaT], "Product Code": ["A"], "Sales Amount": [10]})
        matched = match_sales_to_cost_versions(sales, snapshots)
        self.assertEqual("Invalid Sale Date", matched.loc[0, "Cost Match Status"])

    def test_missing_product_cost(self) -> None:
        snapshots = [_snapshot([{"Product Code": "A", "Unit Cost": 1, "Effective From": "2026-07-01"}])]
        sales = pd.DataFrame({"Completed Date": [pd.Timestamp("2026-07-02")], "Product Code": ["B"], "Sales Amount": [10]})
        matched = match_sales_to_cost_versions(sales, snapshots)
        self.assertEqual("Missing Product Cost", matched.loc[0, "Cost Match Status"])

    def test_invalid_unit_cost(self) -> None:
        snapshot = _snapshot([{"Product Code": "A", "Unit Cost": 0, "Effective From": "2026-07-01"}])
        self.assertTrue(snapshot.registry_entry.participates_in_matching)
        sales = pd.DataFrame({"Completed Date": [pd.Timestamp("2026-07-02")], "Product Code": ["A"], "Sales Amount": [10]})
        matched = match_sales_to_cost_versions(sales, [snapshot])
        self.assertEqual("Invalid Unit Cost", matched.loc[0, "Cost Match Status"])

    def test_non_sale_product(self) -> None:
        snapshots = [_snapshot([{"Product Code": "A", "Unit Cost": 1, "Status": "non-sale", "Effective From": "2026-07-01"}])]
        sales = pd.DataFrame({"Completed Date": [pd.Timestamp("2026-07-02")], "Product Code": ["A"], "Sales Amount": [10]})
        matched = match_sales_to_cost_versions(sales, snapshots)
        self.assertEqual("Non-sale Product", matched.loc[0, "Cost Match Status"])

    def test_discontinued_product_matches(self) -> None:
        snapshots = [_snapshot([{"Product Code": "A", "Unit Cost": 1, "Status": "Discontinued", "Effective From": "2026-07-01"}])]
        sales = pd.DataFrame({"Completed Date": [pd.Timestamp("2026-07-02")], "Product Code": ["A"], "Sales Amount": [10]})
        matched = match_sales_to_cost_versions(sales, snapshots)
        self.assertEqual("Matched", matched.loc[0, "Cost Match Status"])

    def test_coverage_report(self) -> None:
        snapshots = [_snapshot([{"Product Code": "A", "Unit Cost": 1, "Effective From": "2026-07-01"}])]
        sales = pd.DataFrame(
            {
                "Completed Date": pd.to_datetime(["2026-07-02", "2026-07-03"]),
                "Product Code": ["A", "B"],
                "Sales Amount": [10, 5],
            }
        )
        matched = match_sales_to_cost_versions(sales, snapshots)
        report = build_cost_coverage_report(matched)
        self.assertEqual(2, report["Total Sales Rows"])
        self.assertEqual(1, report["Matched Rows"])
        self.assertEqual(["B"], report["Missing Product Codes"])


if __name__ == "__main__":
    unittest.main()
