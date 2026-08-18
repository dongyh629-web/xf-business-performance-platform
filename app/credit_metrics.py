from __future__ import annotations

import pandas as pd


def _sum_numeric(data: pd.DataFrame, column: str) -> float:
    if data is None or data.empty or column not in data.columns:
        return 0.0
    return float(pd.to_numeric(data[column], errors="coerce").fillna(0).sum())


def _safe_rate(numerator: float, denominator: float) -> float | None:
    return float(numerator / denominator) if denominator else None


def _date_filtered_sales(sales_df: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
    if sales_df is None or sales_df.empty:
        return pd.DataFrame()
    date_column = "Performance Date" if "Performance Date" in sales_df.columns else "Completed Date"
    if date_column not in sales_df.columns:
        return sales_df.iloc[0:0].copy()
    dates = pd.to_datetime(sales_df[date_column], errors="coerce").dt.date
    return sales_df.loc[dates.ge(start_date) & dates.le(end_date)].copy()


def sales_for_credit_period(sales_df: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
    return _date_filtered_sales(sales_df, start_date, end_date)


def credit_kpis(credit_df: pd.DataFrame, sales_df: pd.DataFrame) -> dict[str, float | int | None]:
    credit_amount = _sum_numeric(credit_df, "Credit Amount")
    gross_sales = _sum_numeric(sales_df, "Sales Amount")
    return {
        "Gross Sales": gross_sales,
        "Credit Amount": credit_amount,
        "Credit Rate": _safe_rate(credit_amount, gross_sales),
        "Credit Note Count": int(credit_df["Credit Number"].nunique(dropna=True)) if credit_df is not None and not credit_df.empty and "Credit Number" in credit_df.columns else 0,
        "Affected Customers": int(credit_df["Customer Key"].replace("", pd.NA).nunique(dropna=True)) if credit_df is not None and not credit_df.empty and "Customer Key" in credit_df.columns else 0,
        "Affected Products": int(credit_df["Product Key"].replace("", pd.NA).nunique(dropna=True)) if credit_df is not None and not credit_df.empty and "Product Key" in credit_df.columns else 0,
        "Net Sales": gross_sales - credit_amount,
    }


def _sales_by_dimension(sales_df: pd.DataFrame, key_col: str, label_cols: list[str]) -> pd.DataFrame:
    if sales_df is None or sales_df.empty or key_col not in sales_df.columns:
        return pd.DataFrame(columns=[key_col, "Gross Sales"])
    columns = [key_col, "Sales Amount", *[column for column in label_cols if column in sales_df.columns]]
    data = sales_df.loc[:, columns].copy()
    grouped = data.groupby(key_col, dropna=False).agg({"Sales Amount": "sum", **{column: "first" for column in label_cols if column in data.columns}}).reset_index()
    return grouped.rename(columns={"Sales Amount": "Gross Sales"})


def aggregate_customer_credits(credit_df: pd.DataFrame, sales_df: pd.DataFrame) -> pd.DataFrame:
    credit_columns = ["Customer Key", "Customer Label", "Credit Amount", "Credit Number", "Product Key", "Credit Date"]
    if credit_df is None or credit_df.empty:
        credits = pd.DataFrame(columns=["Customer Key", "Customer", "Credit", "Credit Note Count", "Affected Products", "Latest Credit Date"])
    else:
        data = credit_df.loc[:, [column for column in credit_columns if column in credit_df.columns]].copy()
        credits = (
            data.groupby("Customer Key", dropna=False)
            .agg(
                Customer=("Customer Label", "first"),
                Credit=("Credit Amount", "sum"),
                **{
                    "Credit Note Count": ("Credit Number", pd.Series.nunique),
                    "Affected Products": ("Product Key", pd.Series.nunique),
                    "Latest Credit Date": ("Credit Date", "max"),
                },
            )
            .reset_index()
        )
    sales = _sales_by_dimension(sales_df, "Customer Key", ["Customer Label"])
    table = sales.merge(credits, on="Customer Key", how="outer")
    if "Customer" not in table.columns:
        table["Customer"] = pd.NA
    if "Customer Label" in table.columns:
        table["Customer"] = table["Customer"].fillna(table["Customer Label"])
    table["Customer"] = table["Customer"].fillna(table["Customer Key"])
    for column in ["Gross Sales", "Credit", "Credit Note Count", "Affected Products"]:
        table[column] = pd.to_numeric(table.get(column), errors="coerce").fillna(0)
    table["Net Sales"] = table["Gross Sales"] - table["Credit"]
    table["Credit Rate"] = table.apply(lambda row: _safe_rate(float(row["Credit"]), float(row["Gross Sales"])), axis=1)
    return table.sort_values("Credit", ascending=False).reset_index(drop=True)


def aggregate_product_credits(credit_df: pd.DataFrame, sales_df: pd.DataFrame) -> pd.DataFrame:
    if credit_df is None or credit_df.empty:
        credits = pd.DataFrame(columns=["Product Key", "Product Code", "Product", "Product Group", "Credit", "Credit Quantity", "Credit Note Count"])
    else:
        data = credit_df.copy()
        credits = (
            data.groupby("Product Key", dropna=False)
            .agg(
                **{
                    "Product Code": ("Product Code", "first"),
                    "Product": ("Product Label", "first"),
                    "Product Group": ("Product Group", "first"),
                    "Credit": ("Credit Amount", "sum"),
                    "Credit Quantity": ("Quantity", "sum"),
                    "Credit Note Count": ("Credit Number", pd.Series.nunique),
                }
            )
            .reset_index()
        )
    sales = _sales_by_dimension(sales_df, "Product Key", ["Product Code", "Product Label", "Product Group"])
    table = sales.merge(credits, on="Product Key", how="outer", suffixes=("_Sales", ""))
    if "Product" not in table.columns:
        table["Product"] = pd.NA
    if "Product Label" in table.columns:
        table["Product"] = table["Product"].fillna(table["Product Label"])
    for column in ["Product Code", "Product Group"]:
        sales_column = f"{column}_Sales"
        if sales_column in table.columns:
            table[column] = table.get(column).fillna(table[sales_column]) if column in table.columns else table[sales_column]
    table["Product"] = table["Product"].fillna(table["Product Key"])
    for column in ["Gross Sales", "Credit", "Credit Quantity", "Credit Note Count"]:
        table[column] = pd.to_numeric(table.get(column), errors="coerce").fillna(0)
    table["Net Sales"] = table["Gross Sales"] - table["Credit"]
    table["Credit Rate"] = table.apply(lambda row: _safe_rate(float(row["Credit"]), float(row["Gross Sales"])), axis=1)
    return table.sort_values("Credit", ascending=False).reset_index(drop=True)


def aggregate_product_group_credits(product_table: pd.DataFrame) -> pd.DataFrame:
    columns = ["Product Group", "Gross Sales", "Credit", "Net Sales", "Credit Rate", "Credit Quantity", "Credit Note Count"]
    if product_table is None or product_table.empty:
        return pd.DataFrame(columns=columns)
    if "Credit" not in product_table.columns and "Credit Amount" in product_table.columns:
        data = product_table.loc[:, [column for column in ["Product Group", "Credit Amount", "Quantity", "Credit Number"] if column in product_table.columns]].copy()
        data["Product Group"] = data.get("Product Group", pd.Series(dtype="object")).fillna("未分类 / Unclassified").replace("", "未分类 / Unclassified")
        table = (
            data.groupby("Product Group", dropna=False)
            .agg(
                **{
                    "Credit": ("Credit Amount", "sum"),
                    "Credit Quantity": ("Quantity", "sum") if "Quantity" in data.columns else ("Credit Amount", "size"),
                    "Credit Note Count": ("Credit Number", pd.Series.nunique) if "Credit Number" in data.columns else ("Credit Amount", "size"),
                }
            )
            .reset_index()
        )
        table["Gross Sales"] = 0.0
        table["Net Sales"] = -table["Credit"]
        table["Credit Rate"] = None
        return table.loc[:, columns].sort_values("Credit", ascending=False).reset_index(drop=True)
    required = {"Product Group", "Gross Sales", "Credit", "Net Sales", "Credit Quantity", "Credit Note Count"}
    if not required.issubset(product_table.columns):
        return pd.DataFrame(columns=["Product Group", "Gross Sales", "Credit", "Net Sales", "Credit Rate", "Credit Quantity", "Credit Note Count"])
    data = product_table.copy()
    data["Product Group"] = data["Product Group"].fillna("未分类 / Unclassified")
    table = (
        data.groupby("Product Group", dropna=False)
        .agg(
            **{
                "Gross Sales": ("Gross Sales", "sum"),
                "Credit": ("Credit", "sum"),
                "Net Sales": ("Net Sales", "sum"),
                "Credit Quantity": ("Credit Quantity", "sum"),
                "Credit Note Count": ("Credit Note Count", "sum"),
            }
        )
        .reset_index()
    )
    table["Credit Rate"] = table.apply(lambda row: _safe_rate(float(row["Credit"]), float(row["Gross Sales"])), axis=1)
    return table.sort_values("Credit", ascending=False).reset_index(drop=True)


def aggregate_credit_reasons(credit_df: pd.DataFrame) -> pd.DataFrame:
    if credit_df is None or credit_df.empty:
        return pd.DataFrame(columns=["Reason", "Credit Amount", "Credit %", "Credit Note Count"])
    data = credit_df.copy()
    data["Credit Reason"] = data["Credit Reason"].fillna("Unknown / 未知").replace("", "Unknown / 未知")
    total = _sum_numeric(data, "Credit Amount")
    table = (
        data.groupby("Credit Reason", dropna=False)
        .agg(**{"Credit Amount": ("Credit Amount", "sum"), "Credit Note Count": ("Credit Number", pd.Series.nunique)})
        .reset_index()
        .rename(columns={"Credit Reason": "Reason"})
    )
    table["Credit %"] = table["Credit Amount"].map(lambda value: _safe_rate(float(value), total))
    return table.sort_values("Credit Amount", ascending=False).reset_index(drop=True)


def monthly_credit_trend(credit_df: pd.DataFrame) -> pd.DataFrame:
    if credit_df is None or credit_df.empty or "Credit Date" not in credit_df.columns:
        return pd.DataFrame(columns=["Month", "Credit Amount"])
    data = credit_df.loc[:, ["Credit Date", "Credit Amount"]].copy()
    data["Month"] = pd.to_datetime(data["Credit Date"], errors="coerce").dt.to_period("M").astype("string")
    return data.dropna(subset=["Month"]).groupby("Month", as_index=False)["Credit Amount"].sum()
