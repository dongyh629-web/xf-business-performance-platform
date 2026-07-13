from __future__ import annotations

import math

import pandas as pd

from app.config import ABC_A_THRESHOLD, ABC_B_THRESHOLD


def _latest_non_empty(group: pd.DataFrame, value_col: str) -> object:
    values = group.sort_values("Performance Date").dropna(subset=[value_col])
    if values.empty:
        return pd.NA
    return values.iloc[-1][value_col]


def _average_order_gap_days(order_dates: pd.Series) -> float | None:
    sorted_dates = pd.to_datetime(order_dates, errors="coerce").dropna().sort_values()
    if len(sorted_dates) <= 1:
        return None
    gaps = sorted_dates.diff().dt.days.dropna()
    if gaps.empty:
        return None
    return float(gaps.mean())


def _covered_months(first_date, last_date) -> int:
    if pd.isna(first_date) or pd.isna(last_date):
        return 1
    first = pd.Timestamp(first_date)
    last = pd.Timestamp(last_date)
    months = (last.year - first.year) * 12 + (last.month - first.month) + 1
    return max(int(months), 1)


def build_customer_summary(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    if df.empty:
        return pd.DataFrame(), {"name_conflicts": 0, "type_conflicts": 0}

    customer_key = "Customer Key" if "Customer Key" in df.columns else "Customer"
    product_key = "Product Key" if "Product Key" in df.columns else "Product"

    orders = (
        df.groupby([customer_key, "Order No."], dropna=False)
        .agg(
            OrderDate=("Performance Date", "min"),
            OrderSales=("Sales Amount", "sum"),
        )
        .reset_index()
    )

    rows = []
    conflict_counts = {"name_conflicts": 0, "type_conflicts": 0}
    total_sales = float(df["Sales Amount"].sum())

    for key, group in df.groupby(customer_key, dropna=False):
        customer_orders = orders[orders[customer_key].eq(key)].copy()
        order_count = int(customer_orders["Order No."].nunique())
        total = float(group["Sales Amount"].sum())
        first_order = group["Performance Date"].min()
        last_order = group["Performance Date"].max()
        active_months = _covered_months(first_order, last_order)
        avg_order_value = total / order_count if order_count else 0.0
        avg_gap = _average_order_gap_days(customer_orders["OrderDate"])
        orders_per_month = order_count / active_months if active_months else 0.0

        customer_names = group["Customer Name"].dropna().astype(str).str.strip()
        customer_types = group["Customer Type"].dropna().astype(str).str.strip()
        name_conflict = int(customer_names.nunique() > 1)
        type_conflict = int(customer_types.nunique() > 1)
        conflict_counts["name_conflicts"] += name_conflict
        conflict_counts["type_conflicts"] += type_conflict

        name = _latest_non_empty(group, "Customer Name")
        customer_type = _latest_non_empty(group, "Customer Type")

        rows.append(
            {
                "Customer Key": key,
                "Customer Code": group["Customer Code"].dropna().iloc[0] if "Customer Code" in group.columns and group["Customer Code"].notna().any() else pd.NA,
                "Customer Name": name,
                "Customer Label": f"{key} — {name}" if pd.notna(name) and str(key) != str(name) else str(name),
                "Customer Type": customer_type,
                "Name Conflict": bool(name_conflict),
                "Type Conflict": bool(type_conflict),
                "Total Sales": total,
                "Sales Contribution": total / total_sales if total_sales > 0 else 0.0,
                "Order Count": order_count,
                "Average Order Value": avg_order_value,
                "First Order Date": first_order,
                "Last Order Date": last_order,
                "Active Months": active_months,
                "Orders per Month": orders_per_month,
                "Average Order Gap Days": avg_gap,
                "Product Count": int(group[product_key].nunique()),
                "Product Group Count": int(group["Product Group"].nunique()),
            }
        )

    summary = pd.DataFrame(rows).sort_values("Total Sales", ascending=False).reset_index(drop=True)
    summary["Cumulative Sales"] = summary["Total Sales"].cumsum()
    summary["Cumulative Contribution"] = summary["Cumulative Sales"] / total_sales if total_sales > 0 else 0.0
    summary["ABC Class"] = "未分类"
    if total_sales > 0:
        summary["ABC Class"] = "C"
        previous_cumulative = summary["Total Sales"].cumsum().shift(fill_value=0) / total_sales
        cumulative = summary["Cumulative Contribution"]
        summary.loc[previous_cumulative < ABC_A_THRESHOLD, "ABC Class"] = "A"
        summary.loc[(previous_cumulative >= ABC_A_THRESHOLD) & (previous_cumulative < ABC_B_THRESHOLD), "ABC Class"] = "B"
        summary.loc[summary["Total Sales"] <= 0, "ABC Class"] = "C"
    return summary, conflict_counts


def concentration_metrics(summary: pd.DataFrame) -> dict[str, float | int]:
    if summary.empty or summary["Total Sales"].sum() <= 0:
        return {
            "Top 5 Contribution": 0.0,
            "Top 10 Contribution": 0.0,
            "Top 20 Contribution": 0.0,
            "Top 20 Percent Contribution": 0.0,
            "Top 20 Percent Customer Count": 0,
        }

    total = float(summary["Total Sales"].sum())
    customer_count = int(len(summary))
    top_20_percent_count = max(int(math.ceil(customer_count * 0.20)), 1)

    def contribution(n: int) -> float:
        return float(summary.head(min(n, customer_count))["Total Sales"].sum() / total)

    return {
        "Top 5 Contribution": contribution(5),
        "Top 10 Contribution": contribution(10),
        "Top 20 Contribution": contribution(20),
        "Top 20 Percent Contribution": contribution(top_20_percent_count),
        "Top 20 Percent Customer Count": top_20_percent_count,
    }


def abc_distribution(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return pd.DataFrame(columns=["ABC Class", "Customers", "Sales", "Contribution"])
    total = float(summary["Total Sales"].sum())
    grouped = (
        summary.groupby("ABC Class", dropna=False)
        .agg(Customers=("Customer Key", "nunique"), Sales=("Total Sales", "sum"))
        .reset_index()
    )
    grouped["Contribution"] = grouped["Sales"] / total if total > 0 else 0.0
    order = {"A": 0, "B": 1, "C": 2, "未分类": 3}
    grouped["_order"] = grouped["ABC Class"].map(order).fillna(9)
    return grouped.sort_values("_order").drop(columns=["_order"])
