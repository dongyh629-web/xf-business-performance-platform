from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.config import TRACKING_CLOSE_TO_TARGET_THRESHOLD
from app.target_metrics import MONTH_LABELS


@dataclass(frozen=True)
class TrackingSummary:
    target_year: int
    analysis_date: pd.Timestamp
    annual_target: float
    annual_actual: float
    annual_completion: float | None
    previous_year_actual: float
    annual_yoy: float | None
    annual_target_shortfall: float
    formal_shortfall: float
    future_month_count: int
    average_shortfall_allocation: float


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _safe_float(value: object, default: float = 0.0) -> float:
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number):
        return default
    return float(number)


def analysis_date(df: pd.DataFrame) -> pd.Timestamp | None:
    if "Performance Date" not in df.columns:
        return None
    dates = pd.to_datetime(df["Performance Date"], errors="coerce").dropna()
    if dates.empty:
        return None
    return dates.max().normalize()


def analysis_year(df: pd.DataFrame) -> int | None:
    anchor = analysis_date(df)
    return None if anchor is None else int(anchor.year)


def _month_bounds(year: int, month: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(year=year, month=month, day=1)
    end = start + pd.offsets.MonthEnd(0)
    return start, end


def _sales_between(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float:
    dates = pd.to_datetime(df["Performance Date"], errors="coerce")
    mask = dates.between(start, end, inclusive="both")
    return float(df.loc[mask, "Sales Amount"].sum())


def _month_status(target_year: int, month: int, anchor: pd.Timestamp, actual: float, revised_target: float) -> str:
    if target_year > anchor.year or (target_year == anchor.year and month > anchor.month):
        return "尚未开始"
    if target_year == anchor.year and month == anchor.month:
        return "当前进行中"
    if revised_target <= 0:
        return "无目标"
    completion = actual / revised_target
    if completion >= 1:
        return "已达成"
    if completion >= TRACKING_CLOSE_TO_TARGET_THRESHOLD:
        return "接近目标"
    return "未达成"


def _month_actual(df: pd.DataFrame, target_year: int, month: int, anchor: pd.Timestamp) -> float:
    if target_year > anchor.year or (target_year == anchor.year and month > anchor.month):
        return 0.0
    start, end = _month_bounds(target_year, month)
    if target_year == anchor.year and month == anchor.month:
        end = min(end, anchor)
    return _sales_between(df, start, end)


def _previous_year_month_sales(df: pd.DataFrame, target_year: int, month: int, anchor: pd.Timestamp) -> float | None:
    if target_year > anchor.year or (target_year == anchor.year and month > anchor.month):
        return None
    start, end = _month_bounds(target_year - 1, month)
    if target_year == anchor.year and month == anchor.month:
        comparable_day = min(anchor.day, int((start + pd.offsets.MonthEnd(0)).day))
        end = pd.Timestamp(year=target_year - 1, month=month, day=comparable_day)
    return _sales_between(df, start, end)


def _complete_targets_for_year(targets: pd.DataFrame, target_year: int) -> pd.DataFrame:
    year_targets = targets[targets["Year"].eq(target_year)].copy()
    records = []
    for month in range(1, 13):
        month_rows = year_targets[year_targets["Month"].eq(month)]
        if month_rows.empty:
            original = 0.0
            revised = 0.0
            notes = ""
        else:
            row = month_rows.iloc[-1]
            original = _safe_float(row.get("Original Target", 0.0))
            revised = _safe_float(row.get("Revised Target", original), original)
            notes = "" if pd.isna(row.get("Notes", "")) else str(row.get("Notes", ""))
        records.append(
            {
                "Year": target_year,
                "Month": month,
                "月份": MONTH_LABELS[month],
                "原始目标": original,
                "调整后目标": revised,
                "备注": notes,
            }
        )
    return pd.DataFrame.from_records(records)


def build_monthly_tracking_table(
    sales_df: pd.DataFrame,
    targets: pd.DataFrame,
    target_year: int,
) -> tuple[pd.DataFrame, TrackingSummary]:
    anchor = analysis_date(sales_df)
    if anchor is None:
        raise ValueError("当前销售数据没有有效 Performance Date，无法生成经营追踪。")

    target_table = _complete_targets_for_year(targets, target_year)
    rows = []
    for _, target_row in target_table.iterrows():
        month = int(target_row["Month"])
        revised_target = float(target_row["调整后目标"])
        actual = _month_actual(sales_df, target_year, month, anchor)
        previous = _previous_year_month_sales(sales_df, target_year, month, anchor)
        gap = actual - revised_target
        status = _month_status(target_year, month, anchor, actual, revised_target)
        rows.append(
            {
                "Year": target_year,
                "Month": month,
                "月份": target_row["月份"],
                "原始目标": float(target_row["原始目标"]),
                "调整后目标": revised_target,
                "实际销售": actual,
                "完成率": _safe_ratio(actual, revised_target),
                "去年同期": previous,
                "同比": None if previous is None else _safe_ratio(actual - previous, previous),
                "Gap": gap,
                "目标缺口": max(revised_target - actual, 0.0),
                "状态": status,
            }
        )

    table = pd.DataFrame.from_records(rows)
    table["累计实际"] = table["实际销售"].cumsum()
    table["累计目标"] = table["调整后目标"].cumsum()
    table["累计完成率"] = table.apply(lambda row: _safe_ratio(float(row["累计实际"]), float(row["累计目标"])), axis=1)

    annual_target = float(table["调整后目标"].sum())
    annual_actual = float(table["实际销售"].sum())
    previous_year_actual = _annual_comparable_sales(sales_df, target_year, anchor)
    annual_shortfall = max(annual_target - annual_actual, 0.0)
    ended_shortfall = float(table.loc[table["状态"].isin(["未达成", "接近目标"]), "目标缺口"].sum())
    future_month_count = int(table["状态"].eq("尚未开始").sum())
    average_allocation = ended_shortfall / future_month_count if future_month_count else 0.0

    summary = TrackingSummary(
        target_year=target_year,
        analysis_date=anchor,
        annual_target=annual_target,
        annual_actual=annual_actual,
        annual_completion=_safe_ratio(annual_actual, annual_target),
        previous_year_actual=previous_year_actual,
        annual_yoy=_safe_ratio(annual_actual - previous_year_actual, previous_year_actual),
        annual_target_shortfall=annual_shortfall,
        formal_shortfall=ended_shortfall,
        future_month_count=future_month_count,
        average_shortfall_allocation=average_allocation,
    )
    return table, summary


def build_product_group_amount_tracking(
    sales_df: pd.DataFrame,
    amount_targets: pd.DataFrame,
    target_year: int,
) -> pd.DataFrame:
    anchor = analysis_date(sales_df)
    if anchor is None or amount_targets is None or amount_targets.empty:
        return pd.DataFrame()
    if "Product Group" not in sales_df.columns:
        return pd.DataFrame()

    target_rows = amount_targets[
        amount_targets["Year"].astype("Int64").eq(target_year)
        & ~amount_targets["Product Group"].astype(str).eq("公司整体")
    ].copy()
    if target_rows.empty:
        return pd.DataFrame()

    rows = []
    for _, target_row in target_rows.iterrows():
        product_group = str(target_row["Product Group"])
        month = int(target_row["Month"])
        product_sales = sales_df[sales_df["Product Group"].astype(str).eq(product_group)]
        actual = _month_actual(product_sales, target_year, month, anchor)
        previous = _previous_year_month_sales(product_sales, target_year, month, anchor)
        original = _safe_float(target_row.get("Original Target", 0.0))
        revised = _safe_float(target_row.get("Revised Target", original), original)
        rows.append(
            {
                "Product Group": product_group,
                "Month": month,
                "Month Label": MONTH_LABELS[month],
                "Original Amount Target": original,
                "Revised Amount Target": revised,
                "Actual Sales Amount": actual,
                "Amount Completion Rate": _safe_ratio(actual, revised),
                "Amount Gap": actual - revised,
                "Previous Year Actual": previous,
                "YoY": None if previous is None else _safe_ratio(actual - previous, previous),
            }
        )
    return pd.DataFrame.from_records(rows).sort_values(["Product Group", "Month"]).reset_index(drop=True)


def build_product_group_case_tracking(case_targets: pd.DataFrame, target_year: int) -> pd.DataFrame:
    if case_targets is None or case_targets.empty:
        return pd.DataFrame()
    target_rows = case_targets[
        case_targets["Year"].astype("Int64").eq(target_year)
        & ~case_targets["Product Group"].astype(str).eq("公司整体")
    ].copy()
    if target_rows.empty:
        return pd.DataFrame()
    target_rows = target_rows.sort_values(["Product Group", "Month"]).reset_index(drop=True)
    target_rows["Actual Cases"] = pd.NA
    target_rows["Case Completion Rate"] = pd.NA
    target_rows["Case Gap"] = pd.NA
    return target_rows[
        [
            "Product Group",
            "Month",
            "Month Label",
            "Case Target",
            "Actual Cases",
            "Case Completion Rate",
            "Case Gap",
        ]
    ]


def _annual_comparable_sales(sales_df: pd.DataFrame, target_year: int, anchor: pd.Timestamp) -> float:
    if target_year > anchor.year:
        return 0.0
    if target_year == anchor.year:
        start = pd.Timestamp(year=target_year - 1, month=1, day=1)
        end = anchor - pd.DateOffset(years=1)
    else:
        start = pd.Timestamp(year=target_year - 1, month=1, day=1)
        end = pd.Timestamp(year=target_year - 1, month=12, day=31)
    return _sales_between(sales_df, start, end)


def average_allocation_table(tracking_table: pd.DataFrame, average_allocation: float) -> pd.DataFrame:
    future = tracking_table[tracking_table["状态"].eq("尚未开始")].copy()
    if future.empty:
        return pd.DataFrame(columns=["月份", "当前调整后目标", "平均追加目标", "建议调整后目标"])
    future["当前调整后目标"] = future["调整后目标"]
    future["平均追加目标"] = float(average_allocation)
    future["建议调整后目标"] = future["当前调整后目标"] + future["平均追加目标"]
    return future[["月份", "当前调整后目标", "平均追加目标", "建议调整后目标"]]
