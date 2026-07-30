from __future__ import annotations

import pandas as pd


def _number(value: object) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def profit_cell_style(value: object) -> str:
    number = _number(value)
    if number is None:
        return "color: #6b7280; background-color: #f9fafb;"
    if number < 0:
        return "color: #991b1b; background-color: #fff1f2;"
    if number > 0:
        return "color: #166534; background-color: #f0fdf4;"
    return ""


def margin_cell_style(value: object) -> str:
    number = _number(value)
    if number is None:
        return "color: #6b7280; background-color: #f9fafb;"
    if number < 0:
        return "color: #991b1b; background-color: #fff1f2;"
    if number < 0.10:
        return "color: #9a3412; background-color: #fff7ed;"
    if number < 0.20:
        return "color: #854d0e; background-color: #fefce8;"
    return "color: #166534; background-color: #f0fdf4;"


def coverage_cell_style(value: object) -> str:
    number = _number(value)
    if number is None:
        return "color: #6b7280; background-color: #f9fafb;"
    if number < 0.30:
        return "color: #991b1b; background-color: #fff1f2;"
    if number < 0.70:
        return "color: #854d0e; background-color: #fefce8;"
    return "color: #166534; background-color: #f0fdf4;"


def cost_to_sales_style(value: object) -> str:
    number = _number(value)
    if number is None:
        return "color: #6b7280; background-color: #f9fafb;"
    if number >= 1.0:
        return "color: #991b1b; background-color: #fff1f2;"
    if number >= 0.80:
        return "color: #9a3412; background-color: #fff7ed;"
    if number >= 0.60:
        return "color: #854d0e; background-color: #fefce8;"
    return ""


def zero_value_cost_style(value: object) -> str:
    number = _number(value)
    if number is None or number <= 0:
        return ""
    if number >= 100:
        return "color: #9a3412; background-color: #fff7ed;"
    return "color: #9a3412;"


def status_cell_style(value: object) -> str:
    text = str(value)
    if "Invalid" in text or "Missing" in text or "Duplicate" in text:
        return "color: #991b1b; background-color: #fff1f2;"
    if "No Cost" in text:
        return "color: #6b7280; background-color: #f9fafb;"
    return ""
