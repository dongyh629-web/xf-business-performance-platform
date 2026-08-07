import unittest

import pandas as pd

from app.google_drive import _dedupe_sales_rows


def _base_row(**overrides):
    row = {
        "Order No.": "SO-1",
        "Customer Code": "CA1",
        "Product Code": "P1",
        "Order Date": pd.Timestamp("2026-08-01"),
        "Quantity": 1.0,
        "Sales Amount": 100.0,
        "Status": "Completed",
        "Source File": "snapshot-new.xlsx",
        "Source Snapshot Rank": 0,
    }
    row.update(overrides)
    return row


class SalesSnapshotAwareDeduplicationTests(unittest.TestCase):
    def test_identical_rows_in_same_source_file_are_preserved(self) -> None:
        rows = [_base_row(**{"Sales Amount": 200.0, "Product Code": "DL01"}) for _ in range(5)]
        deduped, stats = _dedupe_sales_rows(pd.DataFrame(rows))

        self.assertEqual(5, len(deduped))
        self.assertEqual(1000.0, float(deduped["Sales Amount"].sum()))
        self.assertEqual(0, stats["duplicate_rows_removed"])
        self.assertEqual(5, stats["intra_file_identical_rows_preserved"])

    def test_cross_file_duplicate_keeps_latest_snapshot_rows(self) -> None:
        rows = [
            _base_row(**{"Source File": "snapshot-new.xlsx", "Source Snapshot Rank": 0}),
            _base_row(**{"Source File": "snapshot-old.xlsx", "Source Snapshot Rank": 1}),
        ]
        deduped, stats = _dedupe_sales_rows(pd.DataFrame(rows))

        self.assertEqual(1, len(deduped))
        self.assertEqual("snapshot-new.xlsx", deduped["Source File"].iloc[0])
        self.assertEqual(1, stats["cross_file_duplicates_removed"])

    def test_old_placed_snapshot_new_completed_snapshot_keeps_completed_clean_row(self) -> None:
        rows = [
            _base_row(**{"Status": "Completed", "Source File": "snapshot-new.xlsx", "Source Snapshot Rank": 0}),
        ]
        deduped, stats = _dedupe_sales_rows(pd.DataFrame(rows))

        self.assertEqual(1, len(deduped))
        self.assertEqual("Completed", deduped["Status"].iloc[0])
        self.assertEqual(0, stats["duplicate_rows_removed"])

    def test_same_order_product_quantity_with_changed_amount_is_preserved(self) -> None:
        rows = [
            _base_row(**{"Sales Amount": 100.0, "Source File": "snapshot-new.xlsx", "Source Snapshot Rank": 0}),
            _base_row(**{"Sales Amount": 90.0, "Source File": "snapshot-old.xlsx", "Source Snapshot Rank": 1}),
        ]
        deduped, stats = _dedupe_sales_rows(pd.DataFrame(rows))

        self.assertEqual(2, len(deduped))
        self.assertEqual(190.0, float(deduped["Sales Amount"].sum()))
        self.assertEqual(0, stats["duplicate_rows_removed"])
        self.assertEqual(2, stats["ambiguous_duplicates_preserved"])

    def test_so_00039359_delivery_fee_regression(self) -> None:
        rows = [
            _base_row(
                **{
                    "Order No.": "SO-00039359",
                    "Customer Code": "CA00442",
                    "Product Code": "DL01",
                    "Quantity": 1.0,
                    "Sales Amount": 200.0,
                    "Source File": "XF_Sales_2026-08-07.xlsx",
                    "Source Snapshot Rank": 0,
                }
            )
            for _ in range(5)
        ]
        deduped, stats = _dedupe_sales_rows(pd.DataFrame(rows))

        self.assertEqual(5, len(deduped))
        self.assertEqual(1000.0, float(deduped["Sales Amount"].sum()))
        self.assertEqual(0, stats["duplicate_rows_removed"])


if __name__ == "__main__":
    unittest.main()
