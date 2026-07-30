from __future__ import annotations

import unittest

import pandas as pd

from app.profitability_table_styles import margin_cell_style, profit_cell_style


class ProfitabilityTableStyleTests(unittest.TestCase):
    def test_gross_profit_colors_use_numeric_sign(self) -> None:
        self.assertIn("#166534", profit_cell_style(35050))
        self.assertIn("#991b1b", profit_cell_style(-1355))
        self.assertIn("#991b1b", profit_cell_style(-10857))
        self.assertEqual("", profit_cell_style(0))
        self.assertIn("#6b7280", profit_cell_style(pd.NA))

    def test_gross_margin_bands_use_ratio_values(self) -> None:
        self.assertIn("#991b1b", margin_cell_style(-0.01))
        self.assertIn("#9a3412", margin_cell_style(0.05))
        self.assertIn("#854d0e", margin_cell_style(0.15))
        self.assertIn("#166534", margin_cell_style(0.20))
        self.assertIn("#6b7280", margin_cell_style(pd.NA))


if __name__ == "__main__":
    unittest.main()
