from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from html import escape

import pandas as pd
import streamlit as st

from app.config import DATE_BASIS_LABELS
from app.customer_health import active_customer_metrics
from app.date_periods import WeekContext, week_context
from app.product_range_metrics import RANGE_COLUMN, build_range_overview, safe_ratio
from app.ui import safe_page_link, section_header


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
    today_sales: float
    week_sales: float
    previous_business_day_sales: float
    previous_week_same_progress_sales: float
    week_mom: float | None
    today_orders: int
    week_orders: int
    month_orders: int
    monthly_active_customers: int
    week_active_customers: int
    previous_month_sales: float
    monthly_mom: float | None
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
    week: WeekContext | None


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _business_days(start: pd.Timestamp, end: pd.Timestamp) -> int:
    if end < start:
        return 0
    return len(pd.bdate_range(start.normalize(), end.normalize()))


def _sum_between(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float:
    dates = pd.to_datetime(df["Performance Date"], errors="coerce").dt.normalize()
    mask = dates.between(start.normalize(), end.normalize(), inclusive="both")
    return float(df.loc[mask, "Sales Amount"].sum())


def _orders_between(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> int:
    if "Order No." not in df.columns:
        return 0
    dates = pd.to_datetime(df["Performance Date"], errors="coerce").dt.normalize()
    mask = dates.between(start.normalize(), end.normalize(), inclusive="both")
    return int(df.loc[mask, "Order No."].nunique())


def _customers_between(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> int:
    dates = pd.to_datetime(df["Performance Date"], errors="coerce").dt.normalize()
    mask = dates.between(start.normalize(), end.normalize(), inclusive="both")
    customer_col = "Customer Code" if "Customer Code" in df.columns else "Customer"
    if customer_col not in df.columns:
        return 0
    return int(df.loc[mask, customer_col].nunique())


def _previous_business_day(anchor: pd.Timestamp) -> pd.Timestamp:
    previous = anchor - pd.Timedelta(days=1)
    while previous.weekday() >= 5:
        previous -= pd.Timedelta(days=1)
    return previous.normalize()


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
            today_sales=0.0,
            week_sales=0.0,
            previous_business_day_sales=0.0,
            previous_week_same_progress_sales=0.0,
            week_mom=None,
            today_orders=0,
            week_orders=0,
            month_orders=0,
            monthly_active_customers=0,
            week_active_customers=0,
            previous_month_sales=0.0,
            monthly_mom=None,
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
            week=None,
        )

    anchor = valid["Performance Date"].max().normalize()
    month_start = anchor.replace(day=1)
    month_end = month_start + pd.offsets.MonthEnd(0)
    year_start = _calendar_year_start(anchor)
    year_end = pd.Timestamp(year=anchor.year, month=12, day=31)

    previous_month_start = month_start - pd.DateOffset(years=1)
    previous_month_end_full = previous_month_start + pd.offsets.MonthEnd(0)
    previous_month_end = pd.Timestamp(
        year=previous_month_start.year,
        month=previous_month_start.month,
        day=min(anchor.day, int(previous_month_end_full.day)),
    )
    previous_year_start = year_start - pd.DateOffset(years=1)
    previous_year_anchor = anchor - pd.DateOffset(years=1)
    previous_calendar_month = month_start - pd.DateOffset(months=1)
    previous_calendar_month_start = pd.Timestamp(year=previous_calendar_month.year, month=previous_calendar_month.month, day=1)
    previous_calendar_month_end_full = previous_calendar_month_start + pd.offsets.MonthEnd(0)
    previous_calendar_month_end = pd.Timestamp(
        year=previous_calendar_month_start.year,
        month=previous_calendar_month_start.month,
        day=min(anchor.day, int(previous_calendar_month_end_full.day)),
    )
    week = week_context(anchor)
    previous_day = _previous_business_day(anchor)

    today_sales = _sum_between(valid, anchor, anchor)
    week_sales = _sum_between(valid, week.week_start, week.week_cutoff)
    previous_business_day_sales = _sum_between(valid, previous_day, previous_day)
    previous_week_same_progress_sales = _sum_between(valid, week.previous_week_start, week.previous_week_cutoff)
    monthly_sales = _sum_between(valid, month_start, anchor)
    annual_sales = _sum_between(valid, year_start, anchor)
    previous_month_sales = _sum_between(valid, previous_month_start, previous_month_end)
    previous_calendar_month_sales = _sum_between(valid, previous_calendar_month_start, previous_calendar_month_end)
    previous_year_ytd_sales = _sum_between(valid, previous_year_start, previous_year_anchor)

    today_orders = _orders_between(valid, anchor, anchor)
    week_orders = _orders_between(valid, week.week_start, week.week_cutoff)
    month_orders = _orders_between(valid, month_start, anchor)
    week_active_customers = _customers_between(valid, week.week_start, week.week_cutoff)
    try:
        monthly_active_customers = int(active_customer_metrics(valid, anchor).current_active)
    except Exception:
        monthly_active_customers = _customers_between(valid, month_start, anchor)

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
        today_sales=today_sales,
        week_sales=week_sales,
        previous_business_day_sales=previous_business_day_sales,
        previous_week_same_progress_sales=previous_week_same_progress_sales,
        week_mom=_safe_ratio(week_sales - previous_week_same_progress_sales, previous_week_same_progress_sales),
        today_orders=today_orders,
        week_orders=week_orders,
        month_orders=month_orders,
        monthly_active_customers=monthly_active_customers,
        week_active_customers=week_active_customers,
        previous_month_sales=previous_calendar_month_sales,
        monthly_mom=_safe_ratio(monthly_sales - previous_calendar_month_sales, previous_calendar_month_sales),
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
        week=week,
    )


@st.cache_data(show_spinner=False)
def cached_business_dashboard_metrics(
    df: pd.DataFrame,
    monthly_target: float,
    annual_target: float,
) -> BusinessDashboardMetrics:
    return calculate_business_dashboard_metrics(df, monthly_target, annual_target)


@st.cache_data(show_spinner=False)
def cached_monthly_range_overview(df: pd.DataFrame, amount_targets: pd.DataFrame | None):
    return build_range_overview(df, amount_targets)


@st.cache_data(show_spinner=False)
def cached_range_week_table(df: pd.DataFrame, week: WeekContext) -> pd.DataFrame:
    return _range_week_table(df, week)


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


def _metric_status(value: float | None, good_at_zero: bool = False) -> str:
    if value is None:
        return "无可比基数"
    if good_at_zero and value >= -0.05:
        return "基本正常"
    if value >= 0.10:
        return "明显改善"
    if value >= -0.05:
        return "基本正常"
    return "需要关注"


def _format_delta(value: float | None) -> str | None:
    if value is None:
        return None
    return _format_percent(value)


def _trend_class(value: float | None) -> str:
    if value is None:
        return "neutral"
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "neutral"


def _trend_text(value: float | None, label: str = "") -> str:
    if value is None:
        return f"{label}无基准" if label else "无基准"
    arrow = "▲" if value >= 0 else "▼"
    prefix = f"{label}" if label else ""
    return f"{prefix}{arrow} {value:+.1%}"


def _points_trend_text(value: float | None, label: str = "") -> str:
    if value is None:
        return f"{label}无基准" if label else "无基准"
    arrow = "▲" if value >= 0 else "▼"
    prefix = f"{label}" if label else ""
    return f"{prefix}{arrow} {value * 100:+.1f} 点"


def _time_text(value: object) -> str:
    if not value:
        return "无"
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        today = pd.Timestamp.today().date()
        day_text = "Today" if parsed.date() == today else parsed.strftime("%Y-%m-%d")
        return f"{day_text} {parsed:%H:%M}"
    except ValueError:
        try:
            parsed = datetime.strptime(text.replace(" UTC", ""), "%Y-%m-%d %H:%M:%S")
            today = pd.Timestamp.today().date()
            day_text = "Today" if parsed.date() == today else parsed.strftime("%Y-%m-%d")
            return f"{day_text} {parsed:%H:%M}"
        except ValueError:
            return text


def _render_dashboard_header(metrics: BusinessDashboardMetrics, basis_label: str, scope_text: str) -> None:
    quality = st.session_state.get("quality", {})
    has_date_warning = bool(quality.get("日期质量警告")) if isinstance(quality, dict) else False
    status_label = "数据待检查" if has_date_warning else "数据正常"
    status_class = "warning" if has_date_warning else "success"
    loaded_at = st.session_state.get("drive_sales_loaded_at") or st.session_state.get("drive_target_loaded_at")
    sync_text = _time_text(loaded_at)
    cutoff = metrics.anchor_date.isoformat() if metrics.anchor_date else "无"
    st.markdown(
        f"""
        <div class="xf-dashboard-header">
            <div class="xf-dashboard-title">
                <h1>经营概览</h1>
                <p>Business Overview</p>
                <div class="xf-dashboard-meta">
                    <div class="xf-dashboard-meta-item">截至：<strong>{escape(cutoff)}</strong></div>
                    <div class="xf-dashboard-meta-item">Date Basis：<strong>{escape(basis_label)}</strong></div>
                    <div class="xf-dashboard-meta-item">最近同步：<strong>{escape(sync_text)}</strong></div>
                    <div class="xf-dashboard-meta-item xf-dashboard-meta-status {status_class}">
                        <span class="xf-dashboard-status-dot"></span>
                        <strong>{escape(status_label)}</strong>
                    </div>
                </div>
            </div>
        </div>
        <div class="xf-dashboard-scope">{escape(scope_text)}</div>
        """,
        unsafe_allow_html=True,
    )


def _render_business_alert(title: str, message: str, tone: str = "info") -> None:
    icon = {"success": "✓", "warning": "!", "error": "!", "info": "i"}.get(tone, "i")
    st.markdown(
        f"""
        <div class="xf-alert {escape(tone)}">
            <div class="xf-alert-icon">{escape(icon)}</div>
            <div class="xf-alert-content">
                <div class="xf-alert-title">{escape(title)}</div>
                <p class="xf-alert-body">{escape(message)}</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_trend(value: float | None, text: str) -> str:
    return f'<div class="xf-trend {_trend_class(value)}">{escape(text)}</div>'


def _render_executive_kpis(metrics: BusinessDashboardMetrics) -> None:
    kpis = [
        {
            "label": "本周销售额",
            "value": money(metrics.week_sales),
            "trend_value": metrics.week_mom,
            "trend_text": _trend_text(metrics.week_mom, "较上周 "),
            "caption": _format_date_range(metrics.week.week_start, metrics.week.week_end) if metrics.week else "",
            "featured": False,
        },
        {
            "label": "本月销售额",
            "value": money(metrics.monthly_sales),
            "trend_value": metrics.monthly_yoy,
            "trend_text": _trend_text(metrics.monthly_yoy, "同比 "),
            "caption": f"目标：{money(metrics.monthly_target)}",
            "featured": False,
        },
        {
            "label": "本月目标完成率",
            "value": _format_percent(metrics.monthly_completion),
            "trend_value": metrics.pace_gap,
            "trend_text": _points_trend_text(metrics.pace_gap, "Pace "),
            "caption": f"剩余目标：{money(metrics.monthly_remaining_target)}",
            "featured": True,
        },
        {
            "label": "Pace 差值",
            "value": _format_signed_points(metrics.pace_gap),
            "trend_value": metrics.pace_gap,
            "trend_text": f"工作日进度 {_format_percent(metrics.workday_progress)}",
            "caption": "目标完成率 - 工作日进度",
            "featured": False,
        },
    ]
    cards = []
    for item in kpis:
        featured = " featured" if item["featured"] else ""
        cards.append(
            (
                f'<div class="xf-executive-kpi{featured}">'
                '<div class="xf-executive-kpi-top">'
                f'<div class="xf-executive-kpi-label">{escape(str(item["label"]))}</div>'
                '</div>'
                f'<div class="xf-executive-kpi-value">{escape(str(item["value"]))}</div>'
                f'{_render_trend(item["trend_value"], str(item["trend_text"]))}'
                f'<div class="xf-executive-kpi-caption">{escape(str(item["caption"]))}</div>'
                '</div>'
            )
        )
    st.markdown(f'<div class="xf-executive-kpi-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def _render_summary_cards(cards: list[dict[str, str | bool]]) -> None:
    html_cards = []
    for card in cards:
        featured = " featured" if card.get("featured") else ""
        html_cards.append(
            (
                f'<div class="xf-summary-card{featured}">'
                f'<div class="xf-summary-card-label">{escape(str(card["label"]))}</div>'
                f'<div class="xf-summary-card-value">{escape(str(card["value"]))}</div>'
                '</div>'
            )
        )
    st.markdown(f'<div class="xf-summary-card-grid">{"".join(html_cards)}</div>', unsafe_allow_html=True)


def _format_date_range(start: pd.Timestamp, end: pd.Timestamp) -> str:
    return f"{start.date()} 至 {end.date()}"


def _monthly_range_overview(df: pd.DataFrame) -> tuple[pd.DataFrame, object | None]:
    try:
        import streamlit as st

        amount_targets = st.session_state.get("target_amount_data")
    except Exception:
        amount_targets = None
    if RANGE_COLUMN not in df.columns:
        return pd.DataFrame(), None
    return build_range_overview(df, amount_targets)


def _range_week_table(df: pd.DataFrame, week: WeekContext) -> pd.DataFrame:
    if RANGE_COLUMN not in df.columns:
        return pd.DataFrame()
    work = df.copy()
    work[RANGE_COLUMN] = work[RANGE_COLUMN].fillna("未分类").astype(str)
    dates = pd.to_datetime(work["Performance Date"], errors="coerce").dt.normalize()
    current_mask = dates.between(week.week_start, week.week_cutoff, inclusive="both")
    previous_mask = dates.between(week.previous_week_start, week.previous_week_cutoff, inclusive="both")
    current = work.loc[current_mask].groupby(RANGE_COLUMN, dropna=False)["Sales Amount"].sum().rename("Current Week")
    previous = work.loc[previous_mask].groupby(RANGE_COLUMN, dropna=False)["Sales Amount"].sum().rename("Previous Week Same Progress")
    table = pd.concat([current, previous], axis=1).fillna(0).reset_index()
    table["Change"] = table["Current Week"] - table["Previous Week Same Progress"]
    table["Rate"] = table.apply(lambda row: safe_ratio(row["Change"], row["Previous Week Same Progress"]), axis=1)
    return table


def _render_weekly_summary(df: pd.DataFrame, metrics: BusinessDashboardMetrics) -> None:
    import streamlit as st

    if metrics.week is None:
        return
    week = metrics.week
    section_header("本周经营摘要", "Weekly Business Summary")
    range_week = cached_range_week_table(df, week)
    top_growth = range_week[range_week["Change"].gt(0)].sort_values("Change", ascending=False).head(1)
    top_decline = range_week[range_week["Change"].lt(0)].sort_values("Change").head(1)
    focus = range_week[
        (range_week["Change"].lt(0)) & (range_week["Current Week"].gt(0) | range_week["Previous Week Same Progress"].gt(0))
    ].sort_values("Change").head(2)

    growth_text = "暂无增长贡献"
    if not top_growth.empty:
        row = top_growth.iloc[0]
        growth_text = f"{row[RANGE_COLUMN]} {money(row['Change'])}"
    decline_text = "暂无下降拖累"
    if not top_decline.empty:
        row = top_decline.iloc[0]
        decline_text = f"{row[RANGE_COLUMN]} {money(row['Change'])}"
    focus_text = "暂无明显下降系列" if focus.empty else "、".join(focus[RANGE_COLUMN].astype(str).tolist())
    change_text = "无可比基数" if metrics.week_mom is None else _format_percent(metrics.week_mom)
    st.info(
        f"**Week {week.iso_week}，截至 {week.anchor:%-m 月 %-d 日}**\n\n"
        f"本周销售 {money(metrics.week_sales)}，较上周同期 {change_text}。"
        f"本周订单 {metrics.week_orders:,} 单，活跃客户 {metrics.week_active_customers:,} 个。\n\n"
        f"增长贡献最高：{growth_text}。\n\n"
        f"下降拖累最大：{decline_text}。\n\n"
        f"需要关注：{focus_text}。"
    )


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
    session_monthly_target, session_annual_target, target_source = _session_targets_for_anchor(df)
    with st.sidebar:
        with st.expander("经营目标", expanded=True):
            if target_source:
                monthly_target = session_monthly_target
                annual_target = session_annual_target
                st.caption(f"当前使用：{target_source}")
                st.caption(f"月度目标：{money(monthly_target)}")
                st.caption(f"年度目标：{money(annual_target)}")
                safe_page_link("pages/4_经营追踪.py", label="前往销售经营调整目标")
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
                safe_page_link("pages/4_经营追踪.py", label="前往销售经营设置目标")
            st.caption("年度范围为1月1日至12月31日。")

    metrics = cached_business_dashboard_metrics(df, monthly_target, annual_target)
    if metrics.anchor_date is None:
        st.info("当前筛选结果没有有效日期，无法计算经营驾驶舱指标。")
        return

    basis = df.attrs.get("date_basis", "Completed Date")
    basis_label = DATE_BASIS_LABELS.get(basis, basis)

    _render_dashboard_header(metrics, basis_label, _scope_text(df))

    status_text, status_level = business_status(metrics)
    _render_business_alert("当前经营状态", f"{status_text}。{generate_business_summary(metrics)}", status_level)
    if status_level == "info" and metrics.monthly_target <= 0:
        safe_page_link("pages/4_经营追踪.py", label="前往销售经营设置目标")

    section_header("销售与目标进度")
    _render_executive_kpis(metrics)

    st.divider()
    st.markdown("#### 年度经营")
    _render_summary_cards(
        [
            {"label": "年度累计销售额", "value": money(metrics.annual_sales)},
            {"label": "年度完成率", "value": _format_percent(metrics.annual_completion), "featured": True},
            {"label": "年度累计同比", "value": _format_percent(metrics.annual_ytd_yoy)},
            {"label": "年度剩余目标", "value": money(metrics.annual_remaining_target)},
        ]
    )

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

    st.caption(
        f"本周订单 {metrics.week_orders:,} | "
        f"本月订单 {metrics.month_orders:,} | "
        f"活跃客户 {metrics.monthly_active_customers:,}"
    )

    _render_weekly_summary(df, metrics)
    safe_page_link("pages/6_产品系列经营追踪.py", label="查看产品系列")
