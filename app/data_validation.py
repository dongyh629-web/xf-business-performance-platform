from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


READINESS_TARGET_COST_COVERAGE = 0.80
READINESS_TARGET_UNIT_RISK = 0.05
READINESS_TARGET_INVALID_COST = 0.01
READINESS_TARGET_GIFT = 0.03
READINESS_TARGET_MISSING_COST = 0.05
HIGH_UNIT_COST_QUANTILE = 0.99
HIGH_QUANTITY_QUANTILE = 0.99

VALIDATION_STATUS_OPTIONS = [
    "Pending",
    "Verified",
    "Gift",
    "Unit Check",
    "Cost Check",
    "Pricing Check",
]

SIGN_OFF_CHECKLIST = [
    "历史成本快照已补齐",
    "Invalid Unit Cost 已确认",
    "Gift / Free of Charge 规则已确认",
    "Unit Mapping 已确认",
    "Negative Margin 已抽样核查",
    "成本单位已确认",
    "Cost Coverage 达到目标",
    "业务负责人已确认可以进入老板视图",
]


@dataclass(frozen=True)
class ProfitabilityReadinessScore:
    score: float
    grade: str
    details: dict[str, float]


def _numeric(data: pd.DataFrame, column: str) -> pd.Series:
    if column not in data.columns:
        return pd.Series(pd.NA, index=data.index, dtype="Float64")
    return pd.to_numeric(data[column], errors="coerce")


def _product_key(data: pd.DataFrame) -> pd.Series:
    if "Product Code" not in data.columns:
        return pd.Series(pd.NA, index=data.index, dtype="string")
    return data["Product Code"].astype("string").str.strip()


def _costed_mask(data: pd.DataFrame) -> pd.Series:
    return _numeric(data, "Gross Profit").notna()


def gift_free_of_charge_rows(metrics_df: pd.DataFrame) -> pd.DataFrame:
    sales = _numeric(metrics_df, "Sales Amount").fillna(0)
    quantity = _numeric(metrics_df, "Quantity").fillna(0)
    return metrics_df.loc[sales.eq(0) & quantity.gt(0)].copy()


def gift_free_of_charge_summary(metrics_df: pd.DataFrame) -> dict[str, float | int]:
    gifts = gift_free_of_charge_rows(metrics_df)
    return {
        "Gift Rows": int(len(gifts)),
        "Gift Sales": float(_numeric(gifts, "Sales Amount").fillna(0).sum()),
        "Gift Cost": float(_numeric(gifts, "Total Cost").fillna(0).sum()),
    }


def unit_validation_rows(metrics_df: pd.DataFrame) -> pd.DataFrame:
    if metrics_df.empty:
        return metrics_df.copy()
    data = metrics_df.copy()
    sales = _numeric(data, "Sales Amount")
    quantity = _numeric(data, "Quantity")
    unit_cost = _numeric(data, "Unit Cost")
    unit_price = _numeric(data, "Unit Selling Price")
    margin = _numeric(data, "Margin %")
    high_cost_threshold = float(unit_cost.dropna().quantile(HIGH_UNIT_COST_QUANTILE)) if not unit_cost.dropna().empty else 0.0
    high_quantity_threshold = float(quantity.dropna().quantile(HIGH_QUANTITY_QUANTILE)) if not quantity.dropna().empty else 0.0

    reasons: list[str] = []
    for idx in data.index:
        row_reasons: list[str] = []
        if pd.notna(unit_cost.loc[idx]) and pd.notna(unit_price.loc[idx]) and unit_cost.loc[idx] > unit_price.loc[idx]:
            row_reasons.append("Unit Cost > Unit Selling Price")
        if pd.notna(margin.loc[idx]) and margin.loc[idx] < 0:
            row_reasons.append("Margin < 0%")
        if pd.notna(margin.loc[idx]) and margin.loc[idx] < 0.10:
            row_reasons.append("Margin < 10%")
        if high_quantity_threshold and pd.notna(quantity.loc[idx]) and quantity.loc[idx] >= high_quantity_threshold:
            row_reasons.append("Quantity outlier")
        if pd.notna(quantity.loc[idx]) and float(quantity.loc[idx]) % 1 != 0:
            row_reasons.append("Fractional Quantity")
        if pd.notna(sales.loc[idx]) and sales.loc[idx] == 0:
            row_reasons.append("Sales Amount = 0")
        if high_cost_threshold and pd.notna(unit_cost.loc[idx]) and unit_cost.loc[idx] >= high_cost_threshold:
            row_reasons.append("Unit Cost high")
        reasons.append("; ".join(row_reasons))
    data["Validation Reason"] = reasons
    return data[data["Validation Reason"].astype(str).ne("")].copy()


def coverage_summary(metrics_df: pd.DataFrame) -> dict[str, float | int]:
    total_rows = len(metrics_df)
    sales = _numeric(metrics_df, "Sales Amount").fillna(0)
    product = _product_key(metrics_df)
    costed = _costed_mask(metrics_df)
    total_sales = float(sales.sum())
    costed_sales = float(sales[costed].sum())
    total_skus = int(product.dropna().nunique())
    costed_skus = int(product[costed].dropna().nunique())
    return {
        "Rows Coverage": float(costed.sum() / total_rows) if total_rows else 0.0,
        "Sales Coverage": float(costed_sales / total_sales) if total_sales else 0.0,
        "SKU Coverage": float(costed_skus / total_skus) if total_skus else 0.0,
        "Costed Rows": int(costed.sum()),
        "Total Rows": int(total_rows),
        "Costed Sales": costed_sales,
        "Total Sales": total_sales,
        "Costed SKUs": costed_skus,
        "Total SKUs": total_skus,
    }


def coverage_by_dimension(metrics_df: pd.DataFrame, dimension: str) -> pd.DataFrame:
    columns = [dimension, "Rows Coverage", "Sales Coverage", "SKU Coverage", "Total Sales", "Costed Sales", "Total Rows", "Costed Rows"]
    if metrics_df.empty or dimension not in metrics_df.columns:
        return pd.DataFrame(columns=columns)
    rows = []
    for value, group in metrics_df.groupby(dimension, dropna=False):
        summary = coverage_summary(group)
        rows.append({dimension: value, **summary})
    return pd.DataFrame(rows)[columns].sort_values("Total Sales", ascending=False).reset_index(drop=True)


def coverage_by_month(metrics_df: pd.DataFrame) -> pd.DataFrame:
    data = metrics_df.copy()
    data["Month"] = pd.to_datetime(data.get("Completed Date"), errors="coerce").dt.to_period("M").astype("string")
    return coverage_by_dimension(data, "Month")


def margin_band_analysis(metrics_df: pd.DataFrame) -> pd.DataFrame:
    columns = ["Margin Band", "Rows", "Sales", "Gross Profit", "Product Count"]
    if metrics_df.empty:
        return pd.DataFrame(columns=columns)
    data = metrics_df.copy()
    margin = _numeric(data, "Margin %")
    bands = pd.cut(
        margin,
        bins=[float("-inf"), 0, 0.10, 0.20, 0.40, 0.60, float("inf")],
        labels=["<0%", "0~10%", "10~20%", "20~40%", "40~60%", "60%+"],
        right=False,
    )
    data["Margin Band"] = bands.astype("string").fillna("No Margin")
    data["Sales Amount"] = _numeric(data, "Sales Amount").fillna(0)
    data["Gross Profit"] = _numeric(data, "Gross Profit")
    data["_Product Code"] = _product_key(data)
    grouped = (
        data.groupby("Margin Band", dropna=False)
        .agg(
            Rows=("Margin Band", "size"),
            Sales=("Sales Amount", "sum"),
            **{"Gross Profit": ("Gross Profit", "sum"), "Product Count": ("_Product Code", "nunique")},
        )
        .reset_index()
    )
    order = ["<0%", "0~10%", "10~20%", "20~40%", "40~60%", "60%+", "No Margin"]
    grouped["_order"] = grouped["Margin Band"].map({label: i for i, label in enumerate(order)})
    return grouped.sort_values("_order").drop(columns="_order").reset_index(drop=True)[columns]


def add_business_validation_status(metrics_df: pd.DataFrame) -> pd.DataFrame:
    data = metrics_df.copy()
    data["Business Validation Status"] = "Pending"
    sales = _numeric(data, "Sales Amount").fillna(0)
    quantity = _numeric(data, "Quantity").fillna(0)
    status = data.get("Cost Match Status", pd.Series("", index=data.index)).astype("string")
    unit_risk = unit_validation_rows(data)
    data.loc[status.isin(["Invalid Unit Cost", "Missing Product Cost", "No Cost Version"]), "Business Validation Status"] = "Cost Check"
    data.loc[unit_risk.index, "Business Validation Status"] = "Unit Check"
    data.loc[sales.eq(0) & quantity.gt(0), "Business Validation Status"] = "Gift"
    return data


def top_exceptions(metrics_df: pd.DataFrame, limit: int = 20) -> dict[str, pd.DataFrame]:
    data = metrics_df.copy()
    sales = _numeric(data, "Sales Amount").fillna(0)
    gross_profit = _numeric(data, "Gross Profit")
    status = data.get("Cost Match Status", pd.Series("", index=data.index)).astype("string")
    unit_risk = unit_validation_rows(data)
    product_col = "Product Code" if "Product Code" in data.columns else "Product"

    negative_products = (
        data.loc[gross_profit.lt(0).fillna(False)]
        .assign(_sales=sales, _gross_profit=gross_profit)
        .groupby(product_col, dropna=False)
        .agg(Sales=("_sales", "sum"), **{"Gross Profit": ("_gross_profit", "sum"), "Rows": (product_col, "size")})
        .reset_index()
        .sort_values("Sales", ascending=False)
        .head(limit)
    )
    invalid_cost = data.loc[status.eq("Invalid Unit Cost")].assign(_sales=sales).sort_values("_sales", ascending=False).head(limit)
    missing_cost = data.loc[status.isin(["Missing Product Cost", "No Cost Version"])].assign(_sales=sales).sort_values("_sales", ascending=False).head(limit)
    suspicious = unit_risk.assign(_sales=sales.reindex(unit_risk.index)).sort_values("_sales", ascending=False).head(limit)
    zero_sales = data.loc[sales.eq(0)].assign(_quantity=_numeric(data, "Quantity")).sort_values("_quantity", ascending=False).head(limit)
    return {
        "Top Negative Margin Products": negative_products,
        "Top Invalid Unit Cost": invalid_cost,
        "Top Missing Cost": missing_cost,
        "Top Suspicious Unit Price": suspicious,
        "Top Zero Sales Amount": zero_sales,
    }


def profitability_readiness_score(metrics_df: pd.DataFrame) -> ProfitabilityReadinessScore:
    coverage = coverage_summary(metrics_df)
    total_rows = max(int(coverage["Total Rows"]), 1)
    total_sales = float(coverage["Total Sales"])
    gift_rows = len(gift_free_of_charge_rows(metrics_df))
    unit_risk_rows = len(unit_validation_rows(metrics_df))
    status = metrics_df.get("Cost Match Status", pd.Series("", index=metrics_df.index)).astype("string")
    invalid_cost_rows = int(status.eq("Invalid Unit Cost").sum())
    missing_cost_sales = float(_numeric(metrics_df.loc[status.isin(["Missing Product Cost", "No Cost Version"])], "Sales Amount").fillna(0).sum())

    cost_coverage_score = min(float(coverage["Sales Coverage"]) / READINESS_TARGET_COST_COVERAGE, 1.0) * 45
    unit_risk_rate = unit_risk_rows / total_rows
    invalid_cost_rate = invalid_cost_rows / total_rows
    gift_rate = gift_rows / total_rows
    missing_cost_rate = missing_cost_sales / total_sales if total_sales else 0.0

    unit_score = max(0.0, 1 - unit_risk_rate / READINESS_TARGET_UNIT_RISK) * 20
    invalid_score = max(0.0, 1 - invalid_cost_rate / READINESS_TARGET_INVALID_COST) * 15
    gift_score = max(0.0, 1 - gift_rate / READINESS_TARGET_GIFT) * 10
    missing_score = max(0.0, 1 - missing_cost_rate / READINESS_TARGET_MISSING_COST) * 10
    score = cost_coverage_score + unit_score + invalid_score + gift_score + missing_score
    if score >= 85:
        grade = "Ready"
    elif score >= 65:
        grade = "Needs Review"
    else:
        grade = "Not Ready"
    return ProfitabilityReadinessScore(
        score=float(round(score, 1)),
        grade=grade,
        details={
            "Sales Coverage": float(coverage["Sales Coverage"]),
            "Unit Risk Rate": float(unit_risk_rate),
            "Invalid Cost Rate": float(invalid_cost_rate),
            "Gift Row Rate": float(gift_rate),
            "Missing Cost Sales Rate": float(missing_cost_rate),
        },
    )
