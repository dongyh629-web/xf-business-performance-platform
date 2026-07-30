from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.cost_snapshots import CostSnapshot, match_sales_to_cost_versions, normalize_product_code


FUTURE_METRIC_COLUMNS = [
    "Markup %",
    "Current Price",
    "Standard Price",
    "Discount %",
    "Contribution %",
]

LOW_MARGIN_THRESHOLD = 0.10
HIGH_MARGIN_THRESHOLD = 0.80
UNIT_COST_CLOSE_TO_SELLING_PRICE_RATIO = 0.90
LOW_UNIT_SELLING_PRICE_QUANTILE = 0.05
HIGH_QUANTITY_QUANTILE = 0.99


@dataclass(frozen=True)
class CostCoverageMetrics:
    total_rows: int
    total_sales_amount: float
    total_skus: int
    matched_rows: int
    matched_sales_amount: float
    matched_skus: int
    rows_coverage: float
    sales_coverage: float
    sku_coverage: float
    profit_calculated_rows: int
    profit_calculated_sales_amount: float
    status_counts: dict[str, int]


def _empty_series(df: pd.DataFrame, dtype: str = "object") -> pd.Series:
    return pd.Series(pd.NA, index=df.index, dtype=dtype)


def _numeric_column(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(pd.NA, index=df.index, dtype="Float64")
    return pd.to_numeric(df[column], errors="coerce")


def _status_column(df: pd.DataFrame) -> pd.Series:
    if "Cost Match Status" not in df.columns:
        return pd.Series("Missing Product Cost", index=df.index, dtype="string")
    return df["Cost Match Status"].astype("string")


def ensure_cost_match_columns(metrics: pd.DataFrame) -> pd.DataFrame:
    defaults = {
        "Cost Version Date": pd.NaT,
        "Unit Cost": pd.NA,
        "Cost Match Status": "Missing Product Cost",
        "Cost File Name": pd.NA,
        "Cost Product Status": pd.NA,
        "Cost Product Group": pd.NA,
    }
    for column, default in defaults.items():
        if column not in metrics.columns:
            metrics[column] = default
    return metrics


def calculate_profit_metrics(metrics: pd.DataFrame) -> pd.DataFrame:
    result = ensure_cost_match_columns(metrics.copy())
    quantity = _numeric_column(result, "Quantity")
    sales_amount = _numeric_column(result, "Sales Amount")
    unit_cost = _numeric_column(result, "Unit Cost")
    status = _status_column(result)

    result["Total Cost"] = pd.NA
    result["Gross Profit"] = pd.NA
    result["Margin %"] = pd.NA
    result["Unit Selling Price"] = pd.NA

    cost_calculable = status.eq("Matched") & quantity.gt(0) & unit_cost.gt(0)
    selling_price_calculable = quantity.gt(0) & sales_amount.notna()
    unit_selling_price = sales_amount / quantity
    result.loc[selling_price_calculable, "Unit Selling Price"] = unit_selling_price.loc[selling_price_calculable].astype(float)
    total_cost = quantity * unit_cost
    result.loc[cost_calculable, "Total Cost"] = total_cost.loc[cost_calculable].astype(float)

    profit_calculable = cost_calculable & sales_amount.notna()
    gross_profit = sales_amount - total_cost
    result.loc[profit_calculable, "Gross Profit"] = gross_profit.loc[profit_calculable].astype(float)

    margin_calculable = profit_calculable & sales_amount.gt(0)
    margin = gross_profit / sales_amount
    result.loc[margin_calculable, "Margin %"] = margin.loc[margin_calculable].astype(float)

    for column in FUTURE_METRIC_COLUMNS:
        if column not in result.columns:
            result[column] = pd.NA
    result["ASP"] = result["Unit Selling Price"]
    return result


def build_business_metrics_dataframe(
    sales_df: pd.DataFrame,
    cost_snapshots: list[CostSnapshot] | None = None,
) -> pd.DataFrame:
    if cost_snapshots is not None:
        base = match_sales_to_cost_versions(sales_df, cost_snapshots)
    else:
        base = sales_df.copy()
    return calculate_profit_metrics(base)


def calculate_cost_coverage(metrics_df: pd.DataFrame) -> CostCoverageMetrics:
    if metrics_df.empty:
        return CostCoverageMetrics(
            total_rows=0,
            total_sales_amount=0.0,
            total_skus=0,
            matched_rows=0,
            matched_sales_amount=0.0,
            matched_skus=0,
            rows_coverage=0.0,
            sales_coverage=0.0,
            sku_coverage=0.0,
            profit_calculated_rows=0,
            profit_calculated_sales_amount=0.0,
            status_counts={},
        )

    status = _status_column(metrics_df)
    sales_amount = _numeric_column(metrics_df, "Sales Amount").fillna(0)
    product_codes = (
        metrics_df.get("Product Code", pd.Series(pd.NA, index=metrics_df.index))
        .map(normalize_product_code)
        .astype("string")
    )
    matched = status.eq("Matched")
    profit_calculated = pd.to_numeric(metrics_df.get("Gross Profit", _empty_series(metrics_df)), errors="coerce").notna()
    total_sales = float(sales_amount.sum())
    total_skus = int(product_codes.dropna().nunique())
    matched_sales = float(sales_amount[matched].sum())
    matched_skus = int(product_codes[matched].dropna().nunique())

    return CostCoverageMetrics(
        total_rows=int(len(metrics_df)),
        total_sales_amount=total_sales,
        total_skus=total_skus,
        matched_rows=int(matched.sum()),
        matched_sales_amount=matched_sales,
        matched_skus=matched_skus,
        rows_coverage=float(matched.sum() / len(metrics_df)) if len(metrics_df) else 0.0,
        sales_coverage=float(matched_sales / total_sales) if total_sales else 0.0,
        sku_coverage=float(matched_skus / total_skus) if total_skus else 0.0,
        profit_calculated_rows=int(profit_calculated.sum()),
        profit_calculated_sales_amount=float(sales_amount[profit_calculated].sum()),
        status_counts={str(key): int(value) for key, value in status.value_counts(dropna=False).to_dict().items()},
    )


def cost_coverage_report(metrics_df: pd.DataFrame) -> dict[str, Any]:
    coverage = calculate_cost_coverage(metrics_df)
    return {
        "Total Rows": coverage.total_rows,
        "Total Sales Amount": coverage.total_sales_amount,
        "Total SKUs": coverage.total_skus,
        "Matched Rows": coverage.matched_rows,
        "Matched Sales Amount": coverage.matched_sales_amount,
        "Matched SKUs": coverage.matched_skus,
        "Rows Coverage": coverage.rows_coverage,
        "Sales Coverage": coverage.sales_coverage,
        "SKU Coverage": coverage.sku_coverage,
        "Profit Calculated Rows": coverage.profit_calculated_rows,
        "Profit Calculated Sales Amount": coverage.profit_calculated_sales_amount,
        "Cost Match Status Counts": coverage.status_counts,
    }


def _weighted_margin(gross_profit: pd.Series, costed_sales: pd.Series) -> float | None:
    gross_profit_sum = float(pd.to_numeric(gross_profit, errors="coerce").sum())
    costed_sales_sum = float(pd.to_numeric(costed_sales, errors="coerce").sum())
    if costed_sales_sum <= 0:
        return None
    return gross_profit_sum / costed_sales_sum


def profitability_kpis(metrics_df: pd.DataFrame) -> dict[str, float | None]:
    sales_amount = _numeric_column(metrics_df, "Sales Amount").fillna(0)
    gross_profit = _numeric_column(metrics_df, "Gross Profit")
    total_cost = _numeric_column(metrics_df, "Total Cost")
    profit_calculated = gross_profit.notna()
    total_sales = float(sales_amount.sum())
    costed_sales = float(sales_amount[profit_calculated].sum())
    gross_profit_sum = float(gross_profit[profit_calculated].sum())
    return {
        "Total Sales": total_sales,
        "Costed Sales": costed_sales,
        "Uncosted Sales": total_sales - costed_sales,
        "Total Cost": float(total_cost[profit_calculated].sum()),
        "Gross Profit": gross_profit_sum if costed_sales else None,
        "Weighted Margin": gross_profit_sum / costed_sales if costed_sales > 0 else None,
        "Cost Coverage": costed_sales / total_sales if total_sales > 0 else 0.0,
    }


def monthly_profitability(metrics_df: pd.DataFrame) -> pd.DataFrame:
    columns = ["Month", "Total Sales", "Costed Sales", "Gross Profit", "Weighted Margin", "Cost Coverage"]
    if metrics_df.empty:
        return pd.DataFrame(columns=columns)
    data = metrics_df.copy()
    dates = pd.to_datetime(data.get("Completed Date"), errors="coerce")
    data["Month"] = dates.dt.to_period("M").astype("string")
    data["Sales Amount"] = _numeric_column(data, "Sales Amount").fillna(0)
    data["Gross Profit"] = _numeric_column(data, "Gross Profit")
    data["_profit_calculated"] = data["Gross Profit"].notna()
    rows = []
    for month, group in data.groupby("Month", dropna=False):
        total_sales = float(group["Sales Amount"].sum())
        costed = group[group["_profit_calculated"]]
        costed_sales = float(costed["Sales Amount"].sum())
        gross_profit = float(costed["Gross Profit"].sum()) if costed_sales else 0.0
        rows.append(
            {
                "Month": month,
                "Total Sales": total_sales,
                "Costed Sales": costed_sales,
                "Gross Profit": gross_profit if costed_sales else pd.NA,
                "Weighted Margin": gross_profit / costed_sales if costed_sales else pd.NA,
                "Cost Coverage": costed_sales / total_sales if total_sales else 0.0,
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values("Month").reset_index(drop=True)


def _status_summary(status: pd.Series) -> str:
    counts = status.astype("string").value_counts(dropna=False)
    return ", ".join(f"{key}: {int(value)}" for key, value in counts.items())


def aggregate_product_profitability(metrics_df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Product Code",
        "Product Description",
        "Product Group",
        "Quantity",
        "Sales Amount",
        "Unit Selling Price / ASP",
        "Unit Cost",
        "Total Cost",
        "Gross Profit",
        "Weighted Margin",
        "Cost Coverage",
        "Sales Rows",
        "Cost Match Status summary",
    ]
    if metrics_df.empty:
        return pd.DataFrame(columns=columns)
    data = metrics_df.copy()
    data["_Product Code"] = data.get("Product Code", _empty_series(data)).map(normalize_product_code).astype("string")
    data["_Product Description"] = data.get("Product Name", data.get("Product Description", _empty_series(data))).astype("string")
    data["_Product Group"] = data.get("Product Group", _empty_series(data)).astype("string")
    data["Sales Amount"] = _numeric_column(data, "Sales Amount").fillna(0)
    data["Quantity"] = _numeric_column(data, "Quantity").fillna(0)
    data["Total Cost"] = _numeric_column(data, "Total Cost")
    data["Gross Profit"] = _numeric_column(data, "Gross Profit")
    data["Unit Cost"] = _numeric_column(data, "Unit Cost")
    data["_profit_calculated"] = data["Gross Profit"].notna()
    rows = []
    for product_code, group in data.groupby("_Product Code", dropna=False):
        costed = group[group["_profit_calculated"]]
        total_sales = float(group["Sales Amount"].sum())
        costed_sales = float(costed["Sales Amount"].sum())
        gross_profit = float(costed["Gross Profit"].sum()) if costed_sales else pd.NA
        total_cost = float(costed["Total Cost"].sum()) if costed_sales else pd.NA
        quantity = float(group["Quantity"].sum())
        costed_quantity = float(costed["Quantity"].sum())
        rows.append(
            {
                "Product Code": product_code,
                "Product Description": group["_Product Description"].dropna().iloc[0] if group["_Product Description"].notna().any() else "",
                "Product Group": group["_Product Group"].dropna().iloc[0] if group["_Product Group"].notna().any() else "",
                "Quantity": quantity,
                "Sales Amount": total_sales,
                "Unit Selling Price / ASP": costed_sales / costed_quantity if costed_quantity else pd.NA,
                "Unit Cost": float(costed["Unit Cost"].mean()) if not costed.empty else pd.NA,
                "Total Cost": total_cost,
                "Gross Profit": gross_profit,
                "Weighted Margin": gross_profit / costed_sales if costed_sales else pd.NA,
                "Cost Coverage": costed_sales / total_sales if total_sales else 0.0,
                "Sales Rows": int(len(group)),
                "Cost Match Status summary": _status_summary(group["Cost Match Status"]),
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values("Sales Amount", ascending=False).reset_index(drop=True)


def aggregate_customer_profitability(metrics_df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Customer",
        "Sales Amount",
        "Costed Sales",
        "Total Cost",
        "Gross Profit",
        "Weighted Margin",
        "Cost Coverage",
        "Sales Rows",
        "Costed Rows",
        "Coverage Status",
    ]
    if metrics_df.empty:
        return pd.DataFrame(columns=columns)
    data = metrics_df.copy()
    customer_col = "Customer Label" if "Customer Label" in data.columns else "Customer"
    data["_Customer"] = data.get(customer_col, _empty_series(data)).astype("string")
    data["Sales Amount"] = _numeric_column(data, "Sales Amount").fillna(0)
    data["Total Cost"] = _numeric_column(data, "Total Cost")
    data["Gross Profit"] = _numeric_column(data, "Gross Profit")
    data["_profit_calculated"] = data["Gross Profit"].notna()
    rows = []
    for customer, group in data.groupby("_Customer", dropna=False):
        costed = group[group["_profit_calculated"]]
        total_sales = float(group["Sales Amount"].sum())
        costed_sales = float(costed["Sales Amount"].sum())
        gross_profit = float(costed["Gross Profit"].sum()) if costed_sales else pd.NA
        coverage = costed_sales / total_sales if total_sales else 0.0
        rows.append(
            {
                "Customer": customer,
                "Sales Amount": total_sales,
                "Costed Sales": costed_sales,
                "Total Cost": float(costed["Total Cost"].sum()) if costed_sales else pd.NA,
                "Gross Profit": gross_profit,
                "Weighted Margin": gross_profit / costed_sales if costed_sales else pd.NA,
                "Cost Coverage": coverage,
                "Sales Rows": int(len(group)),
                "Costed Rows": int(len(costed)),
                "Coverage Status": "Low Coverage" if coverage < 0.5 else "OK",
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values("Sales Amount", ascending=False).reset_index(drop=True)


def aggregate_product_group_profitability(metrics_df: pd.DataFrame) -> pd.DataFrame:
    if metrics_df.empty:
        return pd.DataFrame()
    products = aggregate_product_profitability(metrics_df)
    if products.empty:
        return products
    data = metrics_df.copy()
    data["_Product Group"] = data.get("Product Group", _empty_series(data)).astype("string").fillna("Unclassified")
    data["Sales Amount"] = _numeric_column(data, "Sales Amount").fillna(0)
    data["Total Cost"] = _numeric_column(data, "Total Cost")
    data["Gross Profit"] = _numeric_column(data, "Gross Profit")
    data["_profit_calculated"] = data["Gross Profit"].notna()
    rows = []
    for group_name, group in data.groupby("_Product Group", dropna=False):
        costed = group[group["_profit_calculated"]]
        total_sales = float(group["Sales Amount"].sum())
        costed_sales = float(costed["Sales Amount"].sum())
        gross_profit = float(costed["Gross Profit"].sum()) if costed_sales else pd.NA
        rows.append(
            {
                "Product Group": group_name,
                "Sales Amount": total_sales,
                "Costed Sales": costed_sales,
                "Total Cost": float(costed["Total Cost"].sum()) if costed_sales else pd.NA,
                "Gross Profit": gross_profit,
                "Weighted Margin": gross_profit / costed_sales if costed_sales else pd.NA,
                "Cost Coverage": costed_sales / total_sales if total_sales else 0.0,
                "Sales Rows": int(len(group)),
                "Costed Rows": int(len(costed)),
            }
        )
    return pd.DataFrame(rows).sort_values("Sales Amount", ascending=False).reset_index(drop=True)


def negative_gross_profit_transactions(metrics_df: pd.DataFrame) -> pd.DataFrame:
    gross_profit = _numeric_column(metrics_df, "Gross Profit")
    return metrics_df.loc[gross_profit.lt(0).fillna(False)].copy()


def suspicious_unit_comparison(metrics_df: pd.DataFrame) -> pd.DataFrame:
    if metrics_df.empty:
        return pd.DataFrame(columns=[*metrics_df.columns, "Suspicion Reason"])
    data = metrics_df.copy()
    sales_amount = _numeric_column(data, "Sales Amount")
    quantity = _numeric_column(data, "Quantity")
    unit_cost = _numeric_column(data, "Unit Cost")
    unit_price = _numeric_column(data, "Unit Selling Price")
    margin = _numeric_column(data, "Margin %")
    valid_unit_price = unit_price[unit_price.gt(0)]
    low_price_threshold = float(valid_unit_price.quantile(LOW_UNIT_SELLING_PRICE_QUANTILE)) if not valid_unit_price.empty else 0.0
    high_quantity_threshold = float(quantity.dropna().quantile(HIGH_QUANTITY_QUANTILE)) if not quantity.dropna().empty else 0.0
    reasons: list[list[str]] = []
    for idx in data.index:
        row_reasons: list[str] = []
        if pd.notna(unit_cost.loc[idx]) and pd.notna(unit_price.loc[idx]) and unit_cost.loc[idx] > unit_price.loc[idx]:
            row_reasons.append("Unit Cost > Unit Selling Price")
        if (
            pd.notna(unit_cost.loc[idx])
            and pd.notna(unit_price.loc[idx])
            and unit_price.loc[idx] > 0
            and unit_cost.loc[idx] >= unit_price.loc[idx] * UNIT_COST_CLOSE_TO_SELLING_PRICE_RATIO
        ):
            row_reasons.append("Unit Cost close to Unit Selling Price")
        if pd.notna(margin.loc[idx]) and margin.loc[idx] < LOW_MARGIN_THRESHOLD:
            row_reasons.append("Margin < 10%")
        if pd.notna(margin.loc[idx]) and margin.loc[idx] > HIGH_MARGIN_THRESHOLD:
            row_reasons.append("Margin > 80%")
        if pd.notna(quantity.loc[idx]) and float(quantity.loc[idx]) % 1 != 0:
            row_reasons.append("Fractional Quantity")
        if pd.notna(sales_amount.loc[idx]) and sales_amount.loc[idx] == 0:
            row_reasons.append("Sales Amount = 0")
        if pd.notna(unit_price.loc[idx]) and 0 < unit_price.loc[idx] <= low_price_threshold:
            row_reasons.append("Unit Selling Price unusually low")
        if high_quantity_threshold and pd.notna(quantity.loc[idx]) and quantity.loc[idx] >= high_quantity_threshold:
            row_reasons.append("Quantity unusually high")
        reasons.append(row_reasons)
    data["Suspicion Reason"] = ["; ".join(items) for items in reasons]
    return data[data["Suspicion Reason"].astype(str).ne("")].copy()


def invalid_unit_cost_rows(metrics_df: pd.DataFrame) -> pd.DataFrame:
    status = _status_column(metrics_df)
    return metrics_df.loc[status.eq("Invalid Unit Cost")].copy()


def product_margin_reconciliation(metrics_df: pd.DataFrame) -> pd.DataFrame:
    products = aggregate_product_profitability(metrics_df)
    if products.empty:
        return products
    total_profit = pd.to_numeric(products["Gross Profit"], errors="coerce").sum()
    total_sales = pd.to_numeric(products["Sales Amount"], errors="coerce").sum()
    total_cost = pd.to_numeric(products["Total Cost"], errors="coerce").sum()
    products["Gross Profit Contribution"] = (
        pd.to_numeric(products["Gross Profit"], errors="coerce") / total_profit if total_profit else pd.NA
    )
    products["Sales Contribution"] = pd.to_numeric(products["Sales Amount"], errors="coerce") / total_sales if total_sales else pd.NA
    products["Cost Contribution"] = pd.to_numeric(products["Total Cost"], errors="coerce") / total_cost if total_cost else pd.NA
    return products.sort_values("Gross Profit", ascending=True, na_position="last").reset_index(drop=True)
