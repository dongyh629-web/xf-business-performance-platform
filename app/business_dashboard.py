from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd


def money(value: float) -> str:
    return f"£{value:,.0f}"


def percent(value: float) -> str:
    return f"{value:.1%}"


FISCAL_YEAR_START_MONTH = 4


@dataclass(frozen=True)
class BusinessDashboardMetrics:
    anchor_date: date | None
    month_start: pd.Timestamp | None
    month_end: pd.Timestamp | None
    fiscal_year_start: pd.Timestamp | None
    monthly_sales: float
    annual_sales: float
    monthly_target: float
    annual_target: float
    monthly_completion: float | None
    annual_completion: float | None
    previous_year_month_sales: float
    monthly_yoy: float | None
    previous_fiscal_ytd_sales: float
    fiscal_ytd_yoy: float | None
    elapsed_workdays: int
    total_workdays: int
    remaining_workdays: int
    workday_progress: float | None
    pace_ratio: float | None
    monthly_remaining_target: float
    annual_remaining_target: float
    required_daily_sales: float | None


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _business_days(start: pd.Timestamp, end: pd.Timestamp) -> int:
    if end < start:
        return 0
    return len(pd.bdate_range(start.normalize(), end.normalize()))


def _fiscal_year_start(anchor: pd.Timestamp) -> pd.Timestamp:
    year = anchor.year if anchor.month >= FISCAL_YEAR_START_MONTH else anchor.year - 1
    return pd.Timestamp(year=year, month=FISCAL_YEAR_START_MONTH, day=1)


def calculate_business_dashboard_metrics(
    df: pd.DataFrame,
    monthly_target: float,
    annual_target: float,
) -> BusinessDashboardMetrics:
    valid = df.copy()
    valid["Performance Date"] = pd.to_datetime(valid.get("Performance Date"), errors="coerce")
    valid = valid.dropna(subset=["Performance Date"])

    if valid.empty:
        return BusinessDashboardMetrics(
            anchor_date=None,
            month_start=None,
            month_end=None,
            fiscal_year_start=None,
            monthly_sales=0.0,
            annual_sales=0.0,
            monthly_target=float(monthly_target),
            annual_target=float(annual_target),
            monthly_completion=None,
            annual_completion=None,
            previous_year_month_sales=0.0,
            monthly_yoy=None,
            previous_fiscal_ytd_sales=0.0,
            fiscal_ytd_yoy=None,
            elapsed_workdays=0,
            total_workdays=0,
            remaining_workdays=0,
            workday_progress=None,
            pace_ratio=None,
            monthly_remaining_target=max(float(monthly_target), 0.0),
            annual_remaining_target=max(float(annual_target), 0.0),
            required_daily_sales=None,
        )

    anchor = valid["Performance Date"].max().normalize()
    month_start = anchor.replace(day=1)
    month_end = month_start + pd.offsets.MonthEnd(0)
    fiscal_start = _fiscal_year_start(anchor)

    month_mask = valid["Performance Date"].between(month_start, month_end, inclusive="both")
    fiscal_ytd_mask = valid["Performance Date"].between(fiscal_start, anchor, inclusive="both")

    previous_month_start = month_start - pd.DateOffset(years=1)
    previous_month_end = month_end - pd.DateOffset(years=1)
    previous_fiscal_start = fiscal_start - pd.DateOffset(years=1)
    previous_fiscal_anchor = anchor - pd.DateOffset(years=1)

    previous_month_mask = valid["Performance Date"].between(previous_month_start, previous_month_end, inclusive="both")
    previous_fiscal_ytd_mask = valid["Performance Date"].between(previous_fiscal_start, previous_fiscal_anchor, inclusive="both")

    monthly_sales = float(valid.loc[month_mask, "Sales Amount"].sum())
    annual_sales = float(valid.loc[fiscal_ytd_mask, "Sales Amount"].sum())
    previous_month_sales = float(valid.loc[previous_month_mask, "Sales Amount"].sum())
    previous_fiscal_ytd_sales = float(valid.loc[previous_fiscal_ytd_mask, "Sales Amount"].sum())

    elapsed_workdays = _business_days(month_start, anchor)
    total_workdays = _business_days(month_start, month_end)
    remaining_workdays = _business_days(anchor + pd.Timedelta(days=1), month_end)

    monthly_completion = _safe_ratio(monthly_sales, float(monthly_target))
    annual_completion = _safe_ratio(annual_sales, float(annual_target))
    workday_progress = _safe_ratio(float(elapsed_workdays), float(total_workdays))
    pace_ratio = _safe_ratio(monthly_completion or 0.0, workday_progress or 0.0) if monthly_completion is not None else None
    monthly_remaining = max(float(monthly_target) - monthly_sales, 0.0)

    return BusinessDashboardMetrics(
        anchor_date=anchor.date(),
        month_start=month_start,
        month_end=month_end,
        fiscal_year_start=fiscal_start,
        monthly_sales=monthly_sales,
        annual_sales=annual_sales,
        monthly_target=float(monthly_target),
        annual_target=float(annual_target),
        monthly_completion=monthly_completion,
        annual_completion=annual_completion,
        previous_year_month_sales=previous_month_sales,
        monthly_yoy=_safe_ratio(monthly_sales - previous_month_sales, previous_month_sales),
        previous_fiscal_ytd_sales=previous_fiscal_ytd_sales,
        fiscal_ytd_yoy=_safe_ratio(annual_sales - previous_fiscal_ytd_sales, previous_fiscal_ytd_sales),
        elapsed_workdays=elapsed_workdays,
        total_workdays=total_workdays,
        remaining_workdays=remaining_workdays,
        workday_progress=workday_progress,
        pace_ratio=pace_ratio,
        monthly_remaining_target=monthly_remaining,
        annual_remaining_target=max(float(annual_target) - annual_sales, 0.0),
        required_daily_sales=_safe_ratio(monthly_remaining, float(remaining_workdays)),
    )


def _format_percent(value: float | None) -> str:
    return "无基准" if value is None else percent(value)


def _format_pace(value: float | None) -> str:
    return "无基准" if value is None else f"{value:.2f}x"


def _progress(value: float | None) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(float(value), 1.0))


def render_business_dashboard(df: pd.DataFrame) -> None:
    import streamlit as st

    st.subheader("经营驾驶舱")

    target_cols = st.columns(2)
    with target_cols[0]:
        monthly_target = st.number_input(
            "月度目标",
            min_value=0.0,
            value=float(st.session_state.get("home_monthly_target", 100000.0)),
            step=10000.0,
            format="%.0f",
            key="home_monthly_target",
        )
    with target_cols[1]:
        annual_target = st.number_input(
            "年度目标（财年，4月开始）",
            min_value=0.0,
            value=float(st.session_state.get("home_annual_target", 1200000.0)),
            step=50000.0,
            format="%.0f",
            key="home_annual_target",
        )

    metrics = calculate_business_dashboard_metrics(df, monthly_target, annual_target)
    if metrics.anchor_date is None:
        st.info("当前筛选结果没有有效日期，无法计算经营驾驶舱指标。")
        return

    fiscal_year_end = metrics.fiscal_year_start + pd.DateOffset(years=1) - pd.Timedelta(days=1)
    st.caption(
        f"当前经营日期：{metrics.anchor_date}；"
        f"当前财年：{metrics.fiscal_year_start.date()} 至 {fiscal_year_end.date()}。"
    )

    row1 = st.columns(4)
    row1[0].metric("月度完成率", _format_percent(metrics.monthly_completion), help="当前月份销售额 / 月度目标")
    row1[1].metric("年度完成率", _format_percent(metrics.annual_completion), help="当前财年至今销售额 / 年度目标")
    row1[2].metric("月度同比", _format_percent(metrics.monthly_yoy), help="当前月份销售额 vs 去年同月销售额")
    row1[3].metric("财年累计同比", _format_percent(metrics.fiscal_ytd_yoy), help="当前财年至今 vs 上一财年同期")

    row2 = st.columns(4)
    row2[0].metric("月度销售额", money(metrics.monthly_sales), help="沿用当前首页销售额口径")
    row2[1].metric("财年累计销售额", money(metrics.annual_sales), help="财年从4月1日开始")
    row2[2].metric("月度剩余目标", money(metrics.monthly_remaining_target))
    row2[3].metric("年度剩余目标", money(metrics.annual_remaining_target))

    row3 = st.columns(4)
    row3[0].metric("Pace", _format_pace(metrics.pace_ratio), help="销售完成进度 / 工作日进度")
    row3[1].metric("工作日进度", _format_percent(metrics.workday_progress))
    row3[2].metric("剩余工作日", f"{metrics.remaining_workdays} 天")
    row3[3].metric(
        "达标所需日均销售额",
        "已达标" if metrics.monthly_remaining_target == 0 else money(metrics.required_daily_sales or 0.0),
    )

    progress_cols = st.columns(2)
    with progress_cols[0]:
        st.caption("销售进度")
        st.progress(_progress(metrics.monthly_completion), text=_format_percent(metrics.monthly_completion))
    with progress_cols[1]:
        st.caption("工作日进度")
        st.progress(_progress(metrics.workday_progress), text=_format_percent(metrics.workday_progress))
