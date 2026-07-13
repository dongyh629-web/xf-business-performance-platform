from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from app.config import DATE_BASIS_LABELS


def money(value: float) -> str:
    return f"£{value:,.0f}"


def percent(value: float) -> str:
    return f"{value:.1%}"


PACE_NORMAL_BAND = 0.05


@dataclass(frozen=True)
class BusinessDashboardMetrics:
    anchor_date: date | None
    month_start: pd.Timestamp | None
    month_end: pd.Timestamp | None
    year_start: pd.Timestamp | None
    year_end: pd.Timestamp | None
    monthly_sales: float
    annual_sales: float
    monthly_target: float
    annual_target: float
    monthly_completion: float | None
    annual_completion: float | None
    previous_year_month_sales: float
    monthly_yoy: float | None
    previous_year_ytd_sales: float
    annual_ytd_yoy: float | None
    elapsed_workdays: int
    total_workdays: int
    remaining_workdays: int
    workday_progress: float | None
    pace_ratio: float | None
    pace_gap: float | None
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


def _calendar_year_start(anchor: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(year=anchor.year, month=1, day=1)


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
            year_start=None,
            year_end=None,
            monthly_sales=0.0,
            annual_sales=0.0,
            monthly_target=float(monthly_target),
            annual_target=float(annual_target),
            monthly_completion=None,
            annual_completion=None,
            previous_year_month_sales=0.0,
            monthly_yoy=None,
            previous_year_ytd_sales=0.0,
            annual_ytd_yoy=None,
            elapsed_workdays=0,
            total_workdays=0,
            remaining_workdays=0,
            workday_progress=None,
            pace_ratio=None,
            pace_gap=None,
            monthly_remaining_target=max(float(monthly_target), 0.0),
            annual_remaining_target=max(float(annual_target), 0.0),
            required_daily_sales=None,
        )

    anchor = valid["Performance Date"].max().normalize()
    month_start = anchor.replace(day=1)
    month_end = month_start + pd.offsets.MonthEnd(0)
    year_start = _calendar_year_start(anchor)
    year_end = pd.Timestamp(year=anchor.year, month=12, day=31)

    month_mask = valid["Performance Date"].between(month_start, month_end, inclusive="both")
    annual_ytd_mask = valid["Performance Date"].between(year_start, anchor, inclusive="both")

    previous_month_start = month_start - pd.DateOffset(years=1)
    previous_month_end = month_end - pd.DateOffset(years=1)
    previous_year_start = year_start - pd.DateOffset(years=1)
    previous_year_anchor = anchor - pd.DateOffset(years=1)

    previous_month_mask = valid["Performance Date"].between(previous_month_start, previous_month_end, inclusive="both")
    previous_year_ytd_mask = valid["Performance Date"].between(previous_year_start, previous_year_anchor, inclusive="both")

    monthly_sales = float(valid.loc[month_mask, "Sales Amount"].sum())
    annual_sales = float(valid.loc[annual_ytd_mask, "Sales Amount"].sum())
    previous_month_sales = float(valid.loc[previous_month_mask, "Sales Amount"].sum())
    previous_year_ytd_sales = float(valid.loc[previous_year_ytd_mask, "Sales Amount"].sum())

    elapsed_workdays = _business_days(month_start, anchor)
    total_workdays = _business_days(month_start, month_end)
    remaining_workdays = _business_days(anchor + pd.Timedelta(days=1), month_end)

    monthly_completion = _safe_ratio(monthly_sales, float(monthly_target))
    annual_completion = _safe_ratio(annual_sales, float(annual_target))
    workday_progress = _safe_ratio(float(elapsed_workdays), float(total_workdays))
    pace_ratio = _safe_ratio(monthly_completion or 0.0, workday_progress or 0.0) if monthly_completion is not None else None
    pace_gap = monthly_completion - workday_progress if monthly_completion is not None and workday_progress is not None else None
    monthly_remaining = max(float(monthly_target) - monthly_sales, 0.0)

    return BusinessDashboardMetrics(
        anchor_date=anchor.date(),
        month_start=month_start,
        month_end=month_end,
        year_start=year_start,
        year_end=year_end,
        monthly_sales=monthly_sales,
        annual_sales=annual_sales,
        monthly_target=float(monthly_target),
        annual_target=float(annual_target),
        monthly_completion=monthly_completion,
        annual_completion=annual_completion,
        previous_year_month_sales=previous_month_sales,
        monthly_yoy=_safe_ratio(monthly_sales - previous_month_sales, previous_month_sales),
        previous_year_ytd_sales=previous_year_ytd_sales,
        annual_ytd_yoy=_safe_ratio(annual_sales - previous_year_ytd_sales, previous_year_ytd_sales),
        elapsed_workdays=elapsed_workdays,
        total_workdays=total_workdays,
        remaining_workdays=remaining_workdays,
        workday_progress=workday_progress,
        pace_ratio=pace_ratio,
        pace_gap=pace_gap,
        monthly_remaining_target=monthly_remaining,
        annual_remaining_target=max(float(annual_target) - annual_sales, 0.0),
        required_daily_sales=_safe_ratio(monthly_remaining, float(remaining_workdays)),
    )


def _format_percent(value: float | None) -> str:
    return "无基准" if value is None else percent(value)


def _format_pace(value: float | None) -> str:
    return "无基准" if value is None else f"{value:.2f}x"


def _format_signed_points(value: float | None) -> str:
    if value is None:
        return "无基准"
    return f"{value * 100:+.1f} 个百分点"


def _format_abs_points(value: float) -> str:
    return f"{abs(value) * 100:.1f} 个百分点"


def _progress(value: float | None) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(float(value), 1.0))


def _session_targets_for_anchor(df: pd.DataFrame) -> tuple[float, float, str | None]:
    targets = None
    try:
        import streamlit as st

        targets = st.session_state.get("target_data")
    except Exception:
        targets = None
    if targets is None or targets.empty or "Performance Date" not in df.columns:
        return 0.0, 0.0, None
    dates = pd.to_datetime(df["Performance Date"], errors="coerce").dropna()
    if dates.empty:
        return 0.0, 0.0, None
    anchor = dates.max()
    year_targets = targets[targets["Year"].astype("Int64").eq(int(anchor.year))].copy()
    if year_targets.empty:
        return 0.0, 0.0, None
    revised = pd.to_numeric(year_targets["Revised Target"], errors="coerce").fillna(
        pd.to_numeric(year_targets["Original Target"], errors="coerce")
    )
    year_targets = year_targets.assign(_revised=revised.fillna(0.0))
    month_rows = year_targets[year_targets["Month"].astype(int).eq(int(anchor.month))]
    monthly_target = float(month_rows["_revised"].iloc[-1]) if not month_rows.empty else 0.0
    annual_target = float(year_targets["_revised"].sum())
    return monthly_target, annual_target, "经营追踪目标"


def business_status(metrics: BusinessDashboardMetrics) -> tuple[str, str]:
    if metrics.monthly_target <= 0 or metrics.monthly_completion is None or metrics.pace_gap is None:
        return "尚未设置目标", "info"
    if metrics.monthly_completion >= 1:
        return "领先目标节奏", "success"
    if metrics.pace_gap >= PACE_NORMAL_BAND:
        return "领先目标节奏", "success"
    if metrics.pace_gap >= -PACE_NORMAL_BAND:
        return "基本符合目标节奏", "info"
    return "落后目标节奏", "warning"


def generate_business_summary(metrics: BusinessDashboardMetrics) -> str:
    if metrics.monthly_target <= 0 or metrics.monthly_completion is None or metrics.pace_gap is None:
        return "请先设置月度目标，以查看完成率和 Pace。"

    if metrics.monthly_completion >= 1:
        over_target = max(metrics.monthly_sales - metrics.monthly_target, 0.0)
        return f"本月目标已完成，目前超目标 {money(over_target)}。"

    if metrics.pace_gap < -PACE_NORMAL_BAND:
        required = money(metrics.required_daily_sales or 0.0)
        return f"当前销售进度落后工作日进度 {_format_abs_points(metrics.pace_gap)}，剩余工作日需日均销售 {required} 才能完成目标。"

    if metrics.monthly_yoy is None:
        if metrics.pace_gap >= PACE_NORMAL_BAND:
            return f"本月销售进度领先工作日进度 {_format_abs_points(metrics.pace_gap)}，同比暂无可比基准。"
        return f"本月销售完成 {_format_percent(metrics.monthly_completion)}，与工作日进度基本一致，同比暂无可比基准。"

    if metrics.pace_gap >= PACE_NORMAL_BAND and metrics.monthly_yoy >= 0:
        return f"本月销售进度领先工作日进度 {_format_abs_points(metrics.pace_gap)}，同比增加 {_format_percent(metrics.monthly_yoy)}，当前经营节奏良好。"

    if metrics.pace_gap >= PACE_NORMAL_BAND and metrics.monthly_yoy < 0:
        return f"本月销售进度领先工作日进度 {_format_abs_points(metrics.pace_gap)}，但同比下降 {_format_percent(abs(metrics.monthly_yoy))}，建议关注去年同期订单基数和大客户采购节奏。"

    if metrics.monthly_yoy >= 0:
        return f"本月销售完成 {_format_percent(metrics.monthly_completion)}，工作日进度基本匹配，同比增加 {_format_percent(metrics.monthly_yoy)}。"

    return f"本月销售完成 {_format_percent(metrics.monthly_completion)}，工作日进度基本匹配，同比下降 {_format_percent(abs(metrics.monthly_yoy))}。"


def _scope_text(df: pd.DataFrame) -> str:
    customer_types = df.attrs.get("customer_types", [])
    product_groups = df.attrs.get("product_groups", [])
    all_customer_count = df.attrs.get("all_customer_type_count")
    all_product_count = df.attrs.get("all_product_group_count")
    customer_filtered = all_customer_count is not None and len(customer_types) != all_customer_count
    product_filtered = all_product_count is not None and len(product_groups) != all_product_count
    if customer_filtered or product_filtered:
        return "当前为筛选范围内经营表现"
    return "当前为全部可见数据经营表现"


def render_business_dashboard(df: pd.DataFrame) -> None:
    import streamlit as st

    session_monthly_target, session_annual_target, target_source = _session_targets_for_anchor(df)
    with st.sidebar:
        with st.expander("经营目标", expanded=True):
            if target_source:
                monthly_target = session_monthly_target
                annual_target = session_annual_target
                st.caption(f"当前使用：{target_source}")
                st.caption(f"月度目标：{money(monthly_target)}")
                st.caption(f"年度目标：{money(annual_target)}")
                st.page_link("pages/4_经营追踪.py", label="前往经营追踪调整目标")
            else:
                monthly_target = st.number_input(
                    "月度目标",
                    min_value=0.0,
                    value=float(st.session_state.get("home_monthly_target", 0.0)),
                    step=10000.0,
                    format="%.0f",
                    key="home_monthly_target",
                )
                annual_target = st.number_input(
                    "年度目标（自然年）",
                    min_value=0.0,
                    value=float(st.session_state.get("home_annual_target", 0.0)),
                    step=50000.0,
                    format="%.0f",
                    key="home_annual_target",
                )
                st.page_link("pages/4_经营追踪.py", label="前往经营追踪设置目标")
            st.caption("年度范围为1月1日至12月31日。")

    metrics = calculate_business_dashboard_metrics(df, monthly_target, annual_target)
    if metrics.anchor_date is None:
        st.info("当前筛选结果没有有效日期，无法计算经营驾驶舱指标。")
        return

    basis = df.attrs.get("date_basis", "Completed Date")
    basis_label = DATE_BASIS_LABELS.get(basis, basis)

    st.caption(
        f"分析截止日期：{metrics.anchor_date} | "
        f"当前年度：{metrics.year_start.date()} 至 {metrics.year_end.date()} | "
        f"Date Basis：{basis_label}"
    )
    st.caption(_scope_text(df))

    status_text, status_level = business_status(metrics)
    status_message = f"经营状态：{status_text}"
    if status_level == "success":
        st.success(status_message)
    elif status_level == "warning":
        st.warning(status_message)
    else:
        st.info(status_message)
        if metrics.monthly_target <= 0:
            st.page_link("pages/4_经营追踪.py", label="前往经营追踪设置目标")

    st.markdown("#### 本月核心指标")
    month_cols = st.columns(4)
    month_cols[0].metric("本月销售额", money(metrics.monthly_sales))
    month_cols[1].metric("本月完成率", _format_percent(metrics.monthly_completion))
    month_cols[2].metric("本月同比", _format_percent(metrics.monthly_yoy))
    month_cols[3].metric("Pace 差值", _format_signed_points(metrics.pace_gap), help="销售完成进度 - 工作日进度")

    st.info(generate_business_summary(metrics))

    st.divider()
    st.markdown("#### 年度经营")
    annual_cols = st.columns(4)
    annual_cols[0].metric("年度累计销售额", money(metrics.annual_sales))
    annual_cols[1].metric("年度完成率", _format_percent(metrics.annual_completion))
    annual_cols[2].metric("年度累计同比", _format_percent(metrics.annual_ytd_yoy))
    annual_cols[3].metric("年度剩余目标", money(metrics.annual_remaining_target))

    st.divider()
    st.markdown("#### 行动指标")
    action_cols = st.columns(3)
    action_cols[0].metric("剩余目标", money(metrics.monthly_remaining_target))
    action_cols[1].metric("剩余工作日", f"{metrics.remaining_workdays} 天")
    action_cols[2].metric(
        "达标所需日均销售额",
        "已达标" if metrics.monthly_remaining_target == 0 and metrics.monthly_target > 0 else money(metrics.required_daily_sales or 0.0),
    )

    progress_cols = st.columns(2)
    with progress_cols[0]:
        st.caption("本月销售进度")
        st.progress(_progress(metrics.monthly_completion), text=_format_percent(metrics.monthly_completion))
    with progress_cols[1]:
        st.caption("工作日进度")
        st.progress(_progress(metrics.workday_progress), text=_format_percent(metrics.workday_progress))
