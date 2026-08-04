from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from html import escape

import pandas as pd
import streamlit as st

from app.business_metrics import build_business_metrics_dataframe
from app.cost_snapshots import CostSnapshot
from app.product_range_metrics import RANGE_COLUMN, safe_ratio
from app.ui import section_header


MONTHLY_SUMMARY_WINDOW_DAYS = 7
POST_MONTH_SUMMARY_DAYS = 7
UPCOMING_MONTH_WINDOW_DAYS = 7


@dataclass(frozen=True)
class MonthlySummaryContext:
    period_start: pd.Timestamp
    period_end: pd.Timestamp
    data_cutoff: pd.Timestamp
    is_previous_month_summary: bool
    is_partial_month: bool
    expanded_by_default: bool


@dataclass(frozen=True)
class MonthlyBusinessSummary:
    context: MonthlySummaryContext
    sales: float
    target: float | None
    completion: float | None
    previous_year_sales: float
    yoy: float | None
    comparison_label: str
    comparison_sales: float
    mom: float | None
    gross_profit: float | None
    gross_margin: float | None
    orders: int
    active_customers: int
    top_growth_group: str | None
    top_growth_amount: float | None
    top_decline_group: str | None
    top_decline_amount: float | None
    top_customer_names: list[str]
    top_customer_share: float | None
    focus_items: list[str]


@dataclass(frozen=True)
class UpcomingMonthTargetSummary:
    month_start: pd.Timestamp
    month_end: pd.Timestamp
    target: float | None
    current_month_target: float | None
    change_amount: float | None
    change_percent: float | None
    calendar_week_count: int
    business_day_count: int
    weekly_target: float | None
    business_day_target: float | None
    top_product_groups: pd.DataFrame
    highest_increase_groups: list[str]
    low_completion_groups: list[str]
    missing_target: bool


def _money(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"£{float(value):,.0f}"


def _percent(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):.1%}"


def _signed_money(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    prefix = "+" if float(value) >= 0 else "-"
    return f"{prefix}£{abs(float(value)):,.0f}"


def _signed_percent(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):+.1%}"


def _month_start(ts: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(year=int(ts.year), month=int(ts.month), day=1)


def _month_end(ts: pd.Timestamp) -> pd.Timestamp:
    return _month_start(ts) + pd.offsets.MonthEnd(0)


def _previous_month(ts: pd.Timestamp) -> pd.Timestamp:
    return _month_start(ts) - pd.DateOffset(months=1)


def _same_day_in_year(year: int, month: int, day: int) -> pd.Timestamp:
    last_day = calendar.monthrange(year, month)[1]
    return pd.Timestamp(year=year, month=month, day=min(day, last_day))


def _easter_sunday(year: int) -> date:
    """Return Gregorian Easter Sunday for England & Wales bank holiday calculation."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _first_monday(year: int, month: int) -> date:
    day = date(year, month, 1)
    offset = (7 - day.weekday()) % 7
    return date(year, month, 1 + offset)


def _last_monday(year: int, month: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    day = date(year, month, last_day)
    return date(year, month, last_day - day.weekday())


def _observed_single_day(day: date) -> date:
    if day.weekday() == 5:
        return day + timedelta(days=2)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def england_wales_bank_holidays(year: int) -> set[date]:
    easter = _easter_sunday(year)
    holidays: set[date] = {
        _observed_single_day(date(year, 1, 1)),
        easter - timedelta(days=2),
        easter + timedelta(days=1),
        _first_monday(year, 5),
        _last_monday(year, 5),
        _last_monday(year, 8),
    }
    christmas_days = [date(year, 12, 25), date(year, 12, 26)]
    for day in christmas_days:
        if day.weekday() < 5:
            holidays.add(day)
    missing = 2 - sum(1 for day in christmas_days if day.weekday() < 5)
    substitute = date(year, 12, 27)
    while missing > 0:
        if substitute.weekday() < 5 and substitute not in holidays:
            holidays.add(substitute)
            missing -= 1
        substitute = substitute + timedelta(days=1)
    return {pd.Timestamp(day).date() for day in holidays}


def england_wales_business_days(start: pd.Timestamp, end: pd.Timestamp) -> int:
    weekdays = pd.bdate_range(start.normalize(), end.normalize())
    if weekdays.empty:
        return 0
    years = range(int(start.year), int(end.year) + 1)
    holidays = set().union(*(england_wales_bank_holidays(year) for year in years))
    return int(sum(day.date() not in holidays for day in weekdays))


def _normalize_sales_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "Performance Date" not in df.columns:
        return pd.DataFrame(columns=list(df.columns) + ["_summary_date"])
    work = df.copy()
    work["_summary_date"] = pd.to_datetime(work["Performance Date"], errors="coerce").dt.normalize()
    work["Sales Amount"] = pd.to_numeric(work.get("Sales Amount"), errors="coerce").fillna(0)
    return work.dropna(subset=["_summary_date"])


def _period_mask(work: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    if work.empty or "_summary_date" not in work.columns:
        return pd.Series(False, index=work.index)
    return work["_summary_date"].between(start.normalize(), end.normalize(), inclusive="both")


def _sum_sales(work: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float:
    if work.empty:
        return 0.0
    return float(work.loc[_period_mask(work, start, end), "Sales Amount"].sum())


def _target_value(targets: pd.DataFrame | None, year: int, month: int) -> float | None:
    if targets is None or targets.empty:
        return None
    required = {"Year", "Month"}
    if not required.issubset(targets.columns):
        return None
    rows = targets[
        pd.to_numeric(targets["Year"], errors="coerce").eq(int(year))
        & pd.to_numeric(targets["Month"], errors="coerce").eq(int(month))
    ].copy()
    if rows.empty:
        return None
    revised = pd.to_numeric(rows.get("Revised Target"), errors="coerce")
    original = pd.to_numeric(rows.get("Original Target"), errors="coerce")
    values = revised.fillna(original).dropna()
    if values.empty:
        return None
    return float(values.iloc[-1])


def _product_group_targets(amount_targets: pd.DataFrame | None, year: int, month: int) -> pd.DataFrame:
    columns = [RANGE_COLUMN, "Target"]
    if amount_targets is None or amount_targets.empty:
        return pd.DataFrame(columns=columns)
    required = {"Year", "Month", RANGE_COLUMN}
    if not required.issubset(amount_targets.columns):
        return pd.DataFrame(columns=columns)
    rows = amount_targets[
        pd.to_numeric(amount_targets["Year"], errors="coerce").eq(int(year))
        & pd.to_numeric(amount_targets["Month"], errors="coerce").eq(int(month))
    ].copy()
    if rows.empty:
        return pd.DataFrame(columns=columns)
    rows = rows[~rows[RANGE_COLUMN].astype(str).str.strip().eq("公司整体")]
    revised = pd.to_numeric(rows.get("Revised Target"), errors="coerce")
    original = pd.to_numeric(rows.get("Original Target"), errors="coerce")
    rows["Target"] = revised.fillna(original)
    rows[RANGE_COLUMN] = rows[RANGE_COLUMN].fillna("未分类").astype(str)
    return rows[[RANGE_COLUMN, "Target"]].dropna(subset=["Target"])


def monthly_summary_context(anchor_date: date | pd.Timestamp) -> MonthlySummaryContext:
    anchor = pd.Timestamp(anchor_date).normalize()
    current_start = _month_start(anchor)
    current_end = _month_end(anchor)
    if int(anchor.day) <= POST_MONTH_SUMMARY_DAYS:
        period_start = _previous_month(anchor)
        period_end = _month_end(period_start)
        return MonthlySummaryContext(
            period_start=period_start,
            period_end=period_end,
            data_cutoff=period_end,
            is_previous_month_summary=True,
            is_partial_month=False,
            expanded_by_default=True,
        )
    days_to_month_end = int((current_end - anchor).days)
    expanded = days_to_month_end < MONTHLY_SUMMARY_WINDOW_DAYS
    return MonthlySummaryContext(
        period_start=current_start,
        period_end=current_end,
        data_cutoff=anchor,
        is_previous_month_summary=False,
        is_partial_month=anchor < current_end,
        expanded_by_default=expanded,
    )


def should_show_upcoming_month(anchor_date: date | pd.Timestamp) -> bool:
    anchor = pd.Timestamp(anchor_date).normalize()
    days_to_month_end = int((_month_end(anchor) - anchor).days)
    return 0 <= days_to_month_end < UPCOMING_MONTH_WINDOW_DAYS


def build_monthly_summary(
    sales_df: pd.DataFrame,
    targets: pd.DataFrame | None,
    cost_snapshots: list[CostSnapshot] | None = None,
    anchor_date: date | pd.Timestamp | None = None,
) -> MonthlyBusinessSummary | None:
    work = _normalize_sales_df(sales_df)
    if work.empty:
        return None
    anchor = pd.Timestamp(anchor_date).normalize() if anchor_date is not None else work["_summary_date"].max()
    context = monthly_summary_context(anchor)
    current_end = min(context.data_cutoff, context.period_end)
    current = work.loc[_period_mask(work, context.period_start, current_end)]

    year = int(context.period_start.year)
    month = int(context.period_start.month)
    previous_year_start = pd.Timestamp(year=year - 1, month=month, day=1)
    previous_year_end = _same_day_in_year(year - 1, month, int(current_end.day))
    previous_year_sales = _sum_sales(work, previous_year_start, previous_year_end)

    previous_month_start = context.period_start - pd.DateOffset(months=1)
    previous_month_end = _month_end(previous_month_start)
    if context.is_partial_month:
        previous_month_end = _same_day_in_year(int(previous_month_start.year), int(previous_month_start.month), int(current_end.day))
        comparison_label = "上月同期"
    else:
        comparison_label = "上月全月"
    comparison_sales = _sum_sales(work, previous_month_start, previous_month_end)

    target = _target_value(targets, year, month)
    sales = float(current["Sales Amount"].sum()) if not current.empty else 0.0
    gross_profit = None
    gross_margin = None
    if cost_snapshots:
        try:
            metrics_df = build_business_metrics_dataframe(current.drop(columns=["_summary_date"], errors="ignore"), cost_snapshots)
            gross_profit_series = pd.to_numeric(metrics_df.get("Gross Profit"), errors="coerce")
            costed_sales_mask = gross_profit_series.notna() & pd.to_numeric(metrics_df.get("Sales Amount"), errors="coerce").gt(0)
            costed_sales = float(pd.to_numeric(metrics_df.loc[costed_sales_mask, "Sales Amount"], errors="coerce").sum())
            gross_profit = float(gross_profit_series.sum()) if gross_profit_series.notna().any() else None
            gross_margin = safe_ratio(gross_profit or 0.0, costed_sales) if gross_profit is not None else None
        except Exception:
            gross_profit = None
            gross_margin = None

    orders = int(current["Order No."].nunique()) if "Order No." in current.columns else 0
    customer_col = "Customer Code" if "Customer Code" in current.columns else "Customer"
    active_customers = int(current[customer_col].nunique()) if customer_col in current.columns else 0

    top_growth_group, top_growth_amount, top_decline_group, top_decline_amount = _product_group_growth(work, context.period_start, current_end)
    top_customer_names, top_customer_share = _top_customer_contribution(current, sales)
    focus_items = _focus_items(work, context.period_start, current_end)

    return MonthlyBusinessSummary(
        context=context,
        sales=sales,
        target=target,
        completion=safe_ratio(sales, target or 0.0) if target is not None else None,
        previous_year_sales=previous_year_sales,
        yoy=safe_ratio(sales - previous_year_sales, previous_year_sales),
        comparison_label=comparison_label,
        comparison_sales=comparison_sales,
        mom=safe_ratio(sales - comparison_sales, comparison_sales),
        gross_profit=gross_profit,
        gross_margin=gross_margin,
        orders=orders,
        active_customers=active_customers,
        top_growth_group=top_growth_group,
        top_growth_amount=top_growth_amount,
        top_decline_group=top_decline_group,
        top_decline_amount=top_decline_amount,
        top_customer_names=top_customer_names,
        top_customer_share=top_customer_share,
        focus_items=focus_items,
    )


def _product_group_growth(
    work: pd.DataFrame,
    period_start: pd.Timestamp,
    period_end: pd.Timestamp,
) -> tuple[str | None, float | None, str | None, float | None]:
    if RANGE_COLUMN not in work.columns:
        return None, None, None, None
    current = work.loc[_period_mask(work, period_start, period_end)].copy()
    previous_start = pd.Timestamp(year=int(period_start.year) - 1, month=int(period_start.month), day=int(period_start.day))
    previous_end = _same_day_in_year(int(previous_start.year), int(previous_start.month), int(period_end.day))
    previous = work.loc[_period_mask(work, previous_start, previous_end)].copy()
    current_group = current.groupby(RANGE_COLUMN, dropna=False)["Sales Amount"].sum()
    previous_group = previous.groupby(RANGE_COLUMN, dropna=False)["Sales Amount"].sum()
    table = pd.concat([current_group.rename("current"), previous_group.rename("previous")], axis=1).fillna(0)
    if table.empty:
        return None, None, None, None
    table["change"] = table["current"] - table["previous"]
    growth = table[table["change"].gt(0)].sort_values("change", ascending=False)
    decline = table[table["change"].lt(0)].sort_values("change")
    growth_name = str(growth.index[0]) if not growth.empty else None
    growth_amount = float(growth["change"].iloc[0]) if not growth.empty else None
    decline_name = str(decline.index[0]) if not decline.empty else None
    decline_amount = float(decline["change"].iloc[0]) if not decline.empty else None
    return growth_name, growth_amount, decline_name, decline_amount


def _top_customer_contribution(current: pd.DataFrame, sales: float) -> tuple[list[str], float | None]:
    customer_col = "Customer Label" if "Customer Label" in current.columns else "Customer"
    if current.empty or customer_col not in current.columns:
        return [], None
    top = current.groupby(customer_col, dropna=False)["Sales Amount"].sum().sort_values(ascending=False).head(3)
    return [str(value) for value in top.index.tolist()], safe_ratio(float(top.sum()), sales)


def _focus_items(work: pd.DataFrame, period_start: pd.Timestamp, period_end: pd.Timestamp) -> list[str]:
    items: list[str] = []
    growth_name, growth_amount, decline_name, decline_amount = _product_group_growth(work, period_start, period_end)
    if decline_name and decline_amount is not None:
        items.append(f"{decline_name} 同比下降 {_money(abs(decline_amount))}")
    period = work.loc[_period_mask(work, period_start, period_end)]
    customer_col = "Customer Label" if "Customer Label" in period.columns else "Customer"
    if customer_col in period.columns and not period.empty:
        negative_customers = period.groupby(customer_col, dropna=False)["Sales Amount"].sum().sort_values().head(1)
        if not negative_customers.empty and float(negative_customers.iloc[0]) <= 0:
            items.append(f"{negative_customers.index[0]} 本月销售偏低")
    if not items and growth_name and growth_amount is not None:
        items.append(f"继续跟进 {growth_name} 的增长质量")
    return items[:2]


def build_upcoming_month_target_summary(
    sales_df: pd.DataFrame,
    targets: pd.DataFrame | None,
    amount_targets: pd.DataFrame | None,
    anchor_date: date | pd.Timestamp | None = None,
) -> UpcomingMonthTargetSummary | None:
    work = _normalize_sales_df(sales_df)
    if work.empty:
        return None
    anchor = pd.Timestamp(anchor_date).normalize() if anchor_date is not None else work["_summary_date"].max()
    if not should_show_upcoming_month(anchor):
        return None
    next_start = _month_start(anchor) + pd.DateOffset(months=1)
    next_end = _month_end(next_start)
    current_target = _target_value(targets, int(anchor.year), int(anchor.month))
    next_target = _target_value(targets, int(next_start.year), int(next_start.month))
    week_count = _calendar_week_count(next_start, next_end)
    business_day_count = england_wales_business_days(next_start, next_end)

    top_product_groups = _product_group_targets(amount_targets, int(next_start.year), int(next_start.month)).sort_values(
        "Target", ascending=False
    )
    current_group_targets = _product_group_targets(amount_targets, int(anchor.year), int(anchor.month))
    highest_increase_groups = _target_increases(top_product_groups, current_group_targets)
    low_completion_groups = _low_completion_groups(work, current_group_targets, anchor)

    return UpcomingMonthTargetSummary(
        month_start=next_start,
        month_end=next_end,
        target=next_target,
        current_month_target=current_target,
        change_amount=(next_target - current_target) if next_target is not None and current_target is not None else None,
        change_percent=safe_ratio((next_target or 0.0) - (current_target or 0.0), current_target or 0.0)
        if next_target is not None and current_target is not None
        else None,
        calendar_week_count=week_count,
        business_day_count=business_day_count,
        weekly_target=safe_ratio(next_target or 0.0, float(week_count)) if next_target is not None else None,
        business_day_target=safe_ratio(next_target or 0.0, float(business_day_count)) if next_target is not None else None,
        top_product_groups=top_product_groups.head(5).reset_index(drop=True),
        highest_increase_groups=highest_increase_groups,
        low_completion_groups=low_completion_groups,
        missing_target=next_target is None,
    )


def _calendar_week_count(start: pd.Timestamp, end: pd.Timestamp) -> int:
    weeks = pd.date_range(start.normalize(), end.normalize(), freq="D").to_series().dt.isocalendar().week
    return int(weeks.nunique()) if not weeks.empty else 0


def _target_increases(next_targets: pd.DataFrame, current_targets: pd.DataFrame) -> list[str]:
    if next_targets.empty or current_targets.empty:
        return []
    merged = next_targets.merge(current_targets, on=RANGE_COLUMN, how="left", suffixes=("_next", "_current"))
    merged["Increase"] = pd.to_numeric(merged["Target_next"], errors="coerce") - pd.to_numeric(merged["Target_current"], errors="coerce")
    merged = merged[merged["Increase"].gt(0)].sort_values("Increase", ascending=False)
    return merged[RANGE_COLUMN].astype(str).head(3).tolist()


def _low_completion_groups(work: pd.DataFrame, current_targets: pd.DataFrame, anchor: pd.Timestamp) -> list[str]:
    if work.empty or current_targets.empty or RANGE_COLUMN not in work.columns:
        return []
    current_start = _month_start(anchor)
    period = work.loc[_period_mask(work, current_start, anchor)]
    sales = period.groupby(RANGE_COLUMN, dropna=False)["Sales Amount"].sum().rename("Sales").reset_index()
    merged = current_targets.merge(sales, on=RANGE_COLUMN, how="left")
    merged["Sales"] = pd.to_numeric(merged["Sales"], errors="coerce").fillna(0)
    merged["Completion"] = merged.apply(lambda row: safe_ratio(row["Sales"], row["Target"]), axis=1)
    low = merged[merged["Completion"].fillna(0).lt(0.7)].sort_values("Completion")
    return low[RANGE_COLUMN].astype(str).head(3).tolist()


def render_monthly_business_summary(summary: MonthlyBusinessSummary | None) -> None:
    if summary is None:
        return
    title = "月度经营摘要"
    subtitle = "Monthly Business Summary"
    label = _summary_period_label(summary.context)
    with st.expander(f"{title} / {subtitle} · {label}", expanded=summary.context.expanded_by_default):
        if summary.context.is_partial_month:
            st.caption(f"截至 {summary.context.data_cutoff.date()} 的部分月份数据")
        st.markdown(_monthly_summary_html(summary), unsafe_allow_html=True)


def _summary_period_label(context: MonthlySummaryContext) -> str:
    return f"{context.period_start.year}年{context.period_start.month}月"


def _monthly_summary_html(summary: MonthlyBusinessSummary) -> str:
    completion = _percent(summary.completion) if summary.target is not None else "未读取到目标"
    top_customer_text = "、".join(summary.top_customer_names) if summary.top_customer_names else "暂无"
    focus_text = "；".join(summary.focus_items) if summary.focus_items else "暂无明显风险项"
    growth_text = (
        f"{summary.top_growth_group}（{_signed_money(summary.top_growth_amount)}）"
        if summary.top_growth_group and summary.top_growth_amount is not None
        else "暂无明显增长贡献"
    )
    decline_text = (
        f"{summary.top_decline_group}（{_signed_money(summary.top_decline_amount)}）"
        if summary.top_decline_group and summary.top_decline_amount is not None
        else "暂无明显下滑拖累"
    )
    return f"""
    <div class="xf-insight-card">
        <div class="xf-insight-kpis">
            <div><span>本月销售</span><strong>{escape(_money(summary.sales))}</strong></div>
            <div><span>本月目标</span><strong>{escape(_money(summary.target) if summary.target is not None else "未读取到目标")}</strong></div>
            <div><span>完成率</span><strong>{escape(completion)}</strong></div>
            <div><span>毛利率</span><strong>{escape(_percent(summary.gross_margin))}</strong></div>
        </div>
        <ul class="xf-insight-lines">
            <li>销售 {escape(_money(summary.sales))}，目标完成率 {escape(completion)}；去年同期 {escape(_money(summary.previous_year_sales))}，同比 {escape(_percent(summary.yoy))}。</li>
            <li>{escape(summary.comparison_label)}销售 {escape(_money(summary.comparison_sales))}，环比 {escape(_percent(summary.mom))}；订单 {summary.orders:,} 单，活跃客户 {summary.active_customers:,} 个。</li>
            <li>已匹配成本销售毛利 {escape(_money(summary.gross_profit))}，毛利率 {escape(_percent(summary.gross_margin))}。</li>
            <li>增长贡献最高：{escape(growth_text)}；下滑拖累最大：{escape(decline_text)}。</li>
            <li>Top 3 客户：{escape(top_customer_text)}，贡献 {escape(_percent(summary.top_customer_share))}；需要关注：{escape(focus_text)}。</li>
        </ul>
    </div>
    """


def render_upcoming_month_target_card(summary: UpcomingMonthTargetSummary | None) -> None:
    if summary is None:
        return
    month_name = f"{summary.month_start.month} 月"
    section_header(f"即将到来的 {month_name}", f"Upcoming {summary.month_start.strftime('%B')}")
    target_text = "尚未设置下月目标" if summary.missing_target else _money(summary.target)
    change_text = "N/A" if summary.change_amount is None else f"{_signed_money(summary.change_amount)} / {_signed_percent(summary.change_percent)}"
    st.markdown(
        f"""
        <div class="xf-insight-card">
            <div class="xf-insight-kpis">
                <div><span>下月销售总目标</span><strong>{escape(target_text)}</strong></div>
                <div><span>较本月变化</span><strong>{escape(change_text)}</strong></div>
                <div><span>周均目标</span><strong>{escape(_money(summary.weekly_target))}</strong></div>
                <div><span>工作日日均目标</span><strong>{escape(_money(summary.business_day_target))}</strong></div>
            </div>
            <ul class="xf-insight-lines">
                <li>周均目标按覆盖自然周（ISO 周）计算，共 {summary.calendar_week_count} 周。</li>
                <li>日均目标按 England & Wales 工作日计算，已排除 Bank Holidays，共 {summary.business_day_count} 个工作日。</li>
                <li>目标增幅最高系列：{escape("、".join(summary.highest_increase_groups) if summary.highest_increase_groups else "暂无")}。</li>
                <li>历史完成率较低需提前关注：{escape("、".join(summary.low_completion_groups) if summary.low_completion_groups else "暂无")}。</li>
            </ul>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if not summary.top_product_groups.empty:
        with st.expander("查看下月产品系列目标 Top 5", expanded=False):
            display = summary.top_product_groups.rename(columns={RANGE_COLUMN: "产品系列", "Target": "目标"})
            st.dataframe(
                display,
                hide_index=True,
                use_container_width=True,
                column_config={"目标": st.column_config.NumberColumn("目标", format="£%.0f")},
            )
