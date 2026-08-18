from __future__ import annotations

import pandas as pd
import streamlit as st

from app.credit_metrics import (
    aggregate_credit_reasons,
    aggregate_customer_credits,
    aggregate_product_credits,
    aggregate_product_group_credits,
    credit_kpis,
)
from app.credit_notes import filter_credit_by_date
from app.google_drive import load_drive_credit_snapshot


def _empty_credit() -> pd.DataFrame:
    return pd.DataFrame()


def get_credit_data() -> pd.DataFrame:
    """Return Credit Notes already available in session/cache without forcing Drive sync."""
    data = st.session_state.get("credit_data")
    if isinstance(data, pd.DataFrame):
        return data
    try:
        _registry, snapshot = load_drive_credit_snapshot(force=False)
    except Exception:
        return _empty_credit()
    if snapshot is None:
        return _empty_credit()
    return snapshot.data


def sales_scope_date_range(sales_df: pd.DataFrame) -> tuple[object, object] | None:
    if sales_df is None or sales_df.empty or "Performance Date" not in sales_df.columns:
        return None
    dates = pd.to_datetime(sales_df["Performance Date"], errors="coerce").dropna()
    if dates.empty:
        return None
    return dates.min().date(), dates.max().date()


def credit_for_sales_scope(sales_df: pd.DataFrame) -> pd.DataFrame:
    credit_df = get_credit_data()
    date_range = sales_scope_date_range(sales_df)
    if credit_df.empty or date_range is None:
        return credit_df.iloc[0:0].copy() if isinstance(credit_df, pd.DataFrame) else _empty_credit()

    start_date, end_date = date_range
    scoped = filter_credit_by_date(credit_df, start_date, end_date)

    product_groups = sales_df.attrs.get("product_groups")
    all_product_count = sales_df.attrs.get("all_product_group_count")
    if (
        product_groups is not None
        and all_product_count is not None
        and len(product_groups) != all_product_count
        and "Product Group" in scoped.columns
    ):
        scoped = scoped[scoped["Product Group"].fillna("未分类").astype(str).isin([str(value) for value in product_groups])]

    customer_types = sales_df.attrs.get("customer_types")
    all_customer_count = sales_df.attrs.get("all_customer_type_count")
    if (
        customer_types is not None
        and all_customer_count is not None
        and len(customer_types) != all_customer_count
        and "Customer Key" in sales_df.columns
        and "Customer Key" in scoped.columns
    ):
        scoped = scoped[scoped["Customer Key"].astype(str).isin(set(sales_df["Customer Key"].dropna().astype(str)))]

    return scoped.copy()


def scoped_credit_kpis(sales_df: pd.DataFrame) -> dict[str, float | int | None]:
    return credit_kpis(credit_for_sales_scope(sales_df), sales_df)


def scoped_customer_credits(sales_df: pd.DataFrame) -> pd.DataFrame:
    return aggregate_customer_credits(credit_for_sales_scope(sales_df), sales_df)


def scoped_product_credits(sales_df: pd.DataFrame) -> pd.DataFrame:
    return aggregate_product_credits(credit_for_sales_scope(sales_df), sales_df)


def scoped_product_group_credits(sales_df: pd.DataFrame) -> pd.DataFrame:
    return aggregate_product_group_credits(scoped_product_credits(sales_df))


def scoped_credit_reasons(sales_df: pd.DataFrame) -> pd.DataFrame:
    return aggregate_credit_reasons(credit_for_sales_scope(sales_df))


def top_credit_value(table: pd.DataFrame, label_column: str, value_column: str = "Credit") -> str:
    if table is None or table.empty or label_column not in table.columns or value_column not in table.columns:
        return "无 / N/A"
    ranked = table.sort_values(value_column, ascending=False)
    value = ranked.iloc[0].get(label_column)
    if pd.isna(value) or str(value).strip() == "":
        return "无 / N/A"
    return str(value)
