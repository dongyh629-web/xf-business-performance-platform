from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app.google_drive import ensure_drive_data_loaded, render_data_source_sidebar
from app.product_range_metrics import (
    RANGE_COLUMN,
    build_monthly_trend,
    build_range_overview,
    build_week_progress,
    top_contributors,
)
from app.ui import (
    inject_global_styles,
    money,
    percent,
    section_header,
    show_code_warning,
    show_context_summary,
    show_filters,
    status_style,
    style_plotly,
)


def _fmt_money(value) -> str:
    if value is None or pd.isna(value):
        return "未配置"
    return money(float(value))


def _fmt_signed_money(value) -> str:
    if value is None or pd.isna(value):
        return "未配置"
    amount = float(value)
    if amount > 0:
        return f"+£{amount:,.0f}"
    if amount < 0:
        return f"-£{abs(amount):,.0f}"
    return "£0"


def _fmt_percent(value) -> str:
    if value is None or pd.isna(value):
        return "无基数"
    return f"{float(value):+.1%}"


def _fmt_percent_plain(value) -> str:
    if value is None or pd.isna(value):
        return "无基数"
    return percent(float(value))


def _display_table(df: pd.DataFrame) -> pd.DataFrame:
    display = df.copy()
    money_cols = [
        "本月销售额",
        "去年同期销售额",
        "同比增长额",
        "上月同期销售额",
        "本年累计销售额",
        "去年同期累计销售额",
        "本月目标",
        "Annual Target",
        "距离目标差额",
    ]
    signed_percent_cols = ["同比增长率"]
    plain_percent_cols = ["环比增长率", "年累计同比"]
    for col in money_cols:
        if col in display.columns:
            display[col] = display[col].map(_fmt_money)
    for col in signed_percent_cols:
        if col in display.columns:
            display[col] = display[col].map(_fmt_percent)
    for col in plain_percent_cols:
        if col in display.columns:
            display[col] = display[col].map(_fmt_percent_plain)
    if "目标完成率" in display.columns:
        display["目标完成率"] = display["目标完成率"].map(lambda value: "未配置" if value is None or pd.isna(value) else percent(float(value)))
    return display


def _status_sort_value(value: object) -> int:
    order = {"明显落后": 0, "需要关注": 1, "表现良好": 2, "稳定": 3, "数据不足": 4}
    return order.get(str(value), 5)


def _sort_overview(table: pd.DataFrame, sort_label: str) -> pd.DataFrame:
    visible = table.copy()
    if sort_label == "当前状态":
        visible["_status_order"] = visible["当前状态"].map(_status_sort_value)
        return visible.sort_values(["_status_order", "本月销售额"], ascending=[True, False], na_position="last").drop(columns=["_status_order"])
    ascending = sort_label in {"目标完成率"}
    return visible.sort_values(sort_label, ascending=ascending, na_position="last")


def _compact_name(value: object, limit: int = 26) -> str:
    text = "未分类" if pd.isna(value) else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _completion_style(value: object) -> str:
    if pd.isna(value) or value == "未配置":
        return "background-color: #f3f4f6; color: #4b5563;"
    text = str(value).replace("%", "").replace("+", "")
    try:
        number = float(text) / 100
    except ValueError:
        return "background-color: #f3f4f6; color: #4b5563;"
    if number >= 1:
        return "background-color: #e8f5ee; color: #166534; font-weight: 600;"
    if number >= 0.8:
        return "background-color: #f0fdf4; color: #166534;"
    if number >= 0.6:
        return "background-color: #fff9db; color: #854d0e;"
    return "background-color: #fde8e8; color: #991b1b;"


def _yoy_style(value: object) -> str:
    if pd.isna(value) or value == "无基数":
        return "background-color: #f3f4f6; color: #4b5563;"
    text = str(value).replace("%", "").replace("+", "")
    try:
        number = float(text) / 100
    except ValueError:
        return "background-color: #f3f4f6; color: #4b5563;"
    if number >= 0.10:
        return "background-color: #e8f5ee; color: #166534; font-weight: 600;"
    if number >= 0:
        return "background-color: #f0fdf4; color: #166534;"
    if number >= -0.10:
        return "background-color: #fff9db; color: #854d0e;"
    return "background-color: #fde8e8; color: #991b1b;"


def _style_overview(display: pd.DataFrame):
    styler = display.style.map(status_style, subset=["当前状态"])
    if "目标完成率" in display.columns:
        styler = styler.map(_completion_style, subset=["目标完成率"])
    if "同比增长率" in display.columns:
        styler = styler.map(_yoy_style, subset=["同比增长率"])
    return styler


def _metric_tone(value: float | None, kind: str) -> str:
    if value is None or pd.isna(value):
        return "gray"
    number = float(value)
    if kind == "completion":
        if number >= 1:
            return "green"
        if number >= 0.8:
            return "soft-green"
        if number >= 0.6:
            return "yellow"
        return "red"
    if kind == "gap":
        return "green" if number >= 0 else "red"
    if kind == "yoy":
        return "green" if number >= 0 else "red"
    return "gray"


def _tone_style(tone: str) -> str:
    styles = {
        "green": "color:#166534; background:#e8f5ee;",
        "soft-green": "color:#166534; background:#f0fdf4;",
        "yellow": "color:#854d0e; background:#fff9db;",
        "red": "color:#991b1b; background:#fde8e8;",
        "gray": "color:#4b5563; background:#f3f4f6;",
    }
    return styles.get(tone, styles["gray"])


def _delta_text_style(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "color:#6b7280;"
    return "color:#166534;" if float(value) >= 0 else "color:#b91c1c;"


def _total_sales_between(data: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, product_range: str | None) -> float:
    if data.empty:
        return 0.0
    work = data
    if product_range and product_range != "全部":
        range_values = work[RANGE_COLUMN].fillna("未分类").astype(str)
        work = work[range_values.eq(product_range)]
    dates = pd.to_datetime(work["Performance Date"], errors="coerce").dt.normalize()
    mask = dates.between(start.normalize(), end.normalize(), inclusive="both")
    return float(pd.to_numeric(work.loc[mask, "Sales Amount"], errors="coerce").fillna(0).sum())


def _prior_full_month_from_targets(amount_targets: pd.DataFrame | None, ctx, product_range: str | None) -> float | None:
    if amount_targets is None or amount_targets.empty or "Previous Year Actual" not in amount_targets.columns:
        return None
    required = {"Year", "Month", RANGE_COLUMN}
    if not required.issubset(amount_targets.columns):
        return None
    rows = amount_targets[
        amount_targets["Year"].astype("Int64").eq(int(ctx.year))
        & amount_targets["Month"].astype("Int64").eq(int(ctx.month))
    ].copy()
    if rows.empty:
        return None
    if product_range and product_range != "全部":
        rows = rows[rows[RANGE_COLUMN].astype(str).eq(str(product_range))]
    else:
        company_rows = rows[rows[RANGE_COLUMN].astype(str).eq("公司整体")]
        rows = company_rows if not company_rows.empty else rows[~rows[RANGE_COLUMN].astype(str).eq("公司整体")]
    values = pd.to_numeric(rows["Previous Year Actual"], errors="coerce").dropna()
    if values.empty:
        return None
    return float(values.sum())


def _render_total_summary(
    table: pd.DataFrame,
    filtered_data: pd.DataFrame,
    amount_targets: pd.DataFrame | None,
    ctx,
    product_range: str | None,
) -> None:
    current_sales = float(pd.to_numeric(table["本月销售额"], errors="coerce").fillna(0).sum())
    previous_sales = float(pd.to_numeric(table["去年同期销售额"], errors="coerce").fillna(0).sum())
    prior_full_start = pd.Timestamp(year=ctx.year - 1, month=ctx.month, day=1)
    prior_full_end = prior_full_start + pd.offsets.MonthEnd(0)
    prior_full_sales = _prior_full_month_from_targets(amount_targets, ctx, product_range)
    if prior_full_sales is None:
        prior_full_sales = _total_sales_between(filtered_data, prior_full_start, prior_full_end, product_range)
    prior_full_gap = None if prior_full_sales == 0 else current_sales - prior_full_sales
    targets = pd.to_numeric(table["本月目标"], errors="coerce").dropna()
    monthly_target = None if targets.empty else float(targets.sum())
    completion = None if not monthly_target else current_sales / monthly_target
    gap = None if monthly_target is None else current_sales - monthly_target
    yoy = None if previous_sales == 0 else current_sales / previous_sales - 1

    completion_style = _tone_style(_metric_tone(completion, "completion"))
    gap_style = _tone_style(_metric_tone(gap, "gap"))
    yoy_style = _tone_style(_metric_tone(yoy, "yoy"))
    target_text = _fmt_money(monthly_target)
    completion_text = "未配置" if completion is None else percent(completion)
    gap_text = _fmt_signed_money(gap)
    yoy_text = _fmt_percent(yoy)
    prior_full_gap_text = "无基数" if prior_full_gap is None else f"距去年全月 {_fmt_signed_money(prior_full_gap)}"
    prior_full_gap_style = _delta_text_style(prior_full_gap)

    st.markdown(
        f"""
        <div class="xf-total-row">
            <div class="xf-total-title">系列总计</div>
            <div class="xf-total-metrics">
                <div><span>本月销售</span><strong>{_fmt_money(current_sales)}</strong></div>
                <div><span>去年同期</span><strong>{_fmt_money(previous_sales)}</strong></div>
                <div><span>去年全月</span><strong>{_fmt_money(prior_full_sales)}</strong><small style="{prior_full_gap_style}">{prior_full_gap_text}</small></div>
                <div><span>本月目标</span><strong>{target_text}</strong></div>
                <div><span>目标完成率</span><strong class="xf-total-pill" style="{completion_style}">{completion_text}</strong></div>
                <div><span>距离目标</span><strong class="xf-total-pill" style="{gap_style}">{gap_text}</strong></div>
                <div><span>本月同比</span><strong class="xf-total-pill" style="{yoy_style}">{yoy_text}</strong></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _range_label_options(df: pd.DataFrame) -> list[str]:
    values = df[RANGE_COLUMN].fillna("未分类").astype(str).drop_duplicates().sort_values().tolist()
    return ["全部"] + values


@st.cache_data(show_spinner=False)
def _cached_range_overview(data: pd.DataFrame, targets: pd.DataFrame | None, year: int, month: int):
    return build_range_overview(data, targets, year, month)


@st.cache_data(show_spinner=False)
def _cached_monthly_trend(data: pd.DataFrame, targets: pd.DataFrame | None, year: int, product_range: str | None):
    return build_monthly_trend(data, targets, year, product_range)


@st.cache_data(show_spinner=False)
def _cached_week_progress(data: pd.DataFrame, targets: pd.DataFrame | None, context, product_range: str | None):
    return build_week_progress(data, targets, context, product_range)


@st.cache_data(show_spinner=False)
def _cached_top_contributors(data: pd.DataFrame, dimension: str, context, product_range: str | None):
    return top_contributors(data, dimension, context, product_range)


def _bar_line_chart(trend: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_bar(x=trend["Month Label"], y=trend["Sales"], name="本年销售额", marker_color="#2563EB")
    fig.add_scatter(x=trend["Month Label"], y=trend["Previous Year"], name="去年同期", mode="lines+markers", line=dict(color="#64748b"))
    if "Target" in trend.columns and trend["Target"].notna().any():
        fig.add_scatter(x=trend["Month Label"], y=trend["Target"], name="目标", mode="lines", line=dict(color="#f59e0b", dash="dash"))
    fig.update_yaxes(tickprefix="£", separatethousands=True)
    fig.update_layout(title="月度销售额与同期对比", height=360)
    return style_plotly(fig)


def _yoy_chart(trend: pd.DataFrame) -> go.Figure:
    plot = trend.copy()
    plot["YoY Display"] = pd.to_numeric(plot["YoY"], errors="coerce")
    fig = px.bar(plot, x="Month Label", y="YoY Display", title="月度同比")
    fig.update_yaxes(tickformat=".0%")
    fig.update_traces(marker_color=plot["YoY Display"].map(lambda value: "#16a34a" if pd.notna(value) and value >= 0 else "#dc2626"))
    fig.update_layout(height=260)
    return style_plotly(fig)


st.set_page_config(page_title="产品系列经营追踪", layout="wide")
inject_global_styles()
st.markdown(
    """
    <style>
    .xf-total-row {
        border: 1px solid #dbe3ef;
        border-radius: 8px;
        background: #ffffff;
        margin: 1rem 0 0.8rem 0;
        padding: 12px 14px;
    }
    .xf-total-title {
        color: #1f2937;
        font-size: 0.92rem;
        font-weight: 700;
        margin-bottom: 8px;
    }
    .xf-total-metrics {
        display: grid;
        grid-template-columns: repeat(7, minmax(100px, 1fr));
        gap: 10px;
        align-items: stretch;
    }
    .xf-total-metrics div {
        min-width: 0;
        border-left: 1px solid #eef2f7;
        padding-left: 10px;
    }
    .xf-total-metrics div:first-child {border-left: 0; padding-left: 0;}
    .xf-total-metrics span {
        display: block;
        color: #6b7280;
        font-size: 0.76rem;
        line-height: 1.2;
    }
    .xf-total-metrics strong {
        display: inline-block;
        margin-top: 4px;
        color: #111827;
        font-size: 1.05rem;
        line-height: 1.2;
        white-space: nowrap;
    }
    .xf-total-metrics small {
        display: block;
        margin-top: 4px;
        font-size: 0.72rem;
        line-height: 1.2;
        white-space: nowrap;
    }
    .xf-total-pill {
        border-radius: 6px;
        padding: 3px 7px;
    }
    @media (max-width: 1000px) {
        .xf-total-metrics {grid-template-columns: repeat(3, minmax(120px, 1fr));}
        .xf-total-metrics div:nth-child(4) {border-left: 0; padding-left: 0;}
    }
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("产品系列经营追踪")
st.caption("Product Range Performance")

ensure_drive_data_loaded()
render_data_source_sidebar(show_uploaders=False)

df = st.session_state.get("clean_data")
if df is None:
    st.info("当前暂无销售数据，请回到首页使用 Google Drive 刷新或手动上传 Unleashed 销售明细。")
    st.stop()
if RANGE_COLUMN not in df.columns:
    st.info("当前销售数据没有 Product Group 字段，暂无法生成产品系列经营追踪。")
    st.stop()

filtered = show_filters(df, "product_range")
show_code_warning(filtered)
show_context_summary(filtered)

dates = pd.to_datetime(filtered["Performance Date"], errors="coerce").dropna()
if dates.empty:
    st.info("当前筛选范围内没有有效日期。")
    st.stop()

default_year = int(dates.max().year)
default_month = int(dates.max().month)
years = sorted(pd.to_datetime(df["Performance Date"], errors="coerce").dropna().dt.year.unique().astype(int).tolist())
filter_cols = st.columns([1, 1, 2])
selected_year = filter_cols[0].selectbox("年度", years, index=years.index(default_year) if default_year in years else len(years) - 1)
selected_month = filter_cols[1].selectbox("月份", list(range(1, 13)), index=default_month - 1, format_func=lambda value: f"{value}月")

amount_targets = st.session_state.get("target_amount_data")
overview, ctx = _cached_range_overview(filtered, amount_targets, selected_year, selected_month)
range_options = _range_label_options(filtered)
selected_range = filter_cols[2].selectbox("产品系列", range_options)

table = overview.rename(
    columns={
        RANGE_COLUMN: "产品系列",
        "Current Month Sales": "本月销售额",
        "Previous Year Same Period": "去年同期销售额",
        "YoY Change": "同比增长额",
        "YoY Rate": "同比增长率",
        "Previous Month Same Period": "上月同期销售额",
        "MoM Rate": "环比增长率",
        "YTD Sales": "本年累计销售额",
        "Previous YTD Sales": "去年同期累计销售额",
        "YTD YoY": "年累计同比",
        "Monthly Target": "本月目标",
        "Annual Target": "Annual Target",
        "Target Completion": "目标完成率",
        "Target Gap": "距离目标差额",
        "Status": "当前状态",
    }
)
if table.empty:
    st.info("当前筛选范围内没有产品系列销售。")
else:
    if selected_range != "全部":
        table = table[table["产品系列"].eq(selected_range)].copy()

    _render_total_summary(table, filtered, amount_targets, ctx, selected_range)

    section_header("精简系列经营总览表")
    status_options = sorted(table["当前状态"].dropna().unique().tolist())
    control_cols = st.columns([2, 1])
    selected_status = control_cols[0].multiselect("状态筛选", status_options, default=status_options)
    sort_label = control_cols[1].selectbox("排序", ["当前状态", "本月销售额", "同比增长率", "目标完成率", "距离目标差额"])
    visible = table[table["当前状态"].isin(selected_status)].copy()
    visible = _sort_overview(visible, sort_label)
    compact_columns = [
        "当前状态",
        "产品系列",
        "本月销售额",
        "本月目标",
        "目标完成率",
        "同比增长率",
        "环比增长率",
        "距离目标差额",
        "年累计同比",
    ]
    st.dataframe(
        _style_overview(_display_table(visible[compact_columns])),
        width="stretch",
        hide_index=True,
        column_config={
            "当前状态": st.column_config.TextColumn("当前状态", width="small"),
            "产品系列": st.column_config.TextColumn("产品系列", width="medium"),
            "本月销售额": st.column_config.TextColumn("本月销售额", width="small"),
            "目标完成率": st.column_config.TextColumn("目标完成率", width="small"),
            "同比增长率": st.column_config.TextColumn("同比增长率", width="small"),
        },
    )

section_header("本月周进度", "区分时间进度和目标完成率，用于每周查看系列进展。")
weekly = _cached_week_progress(filtered, amount_targets, ctx, selected_range)
if weekly.empty:
    st.info("当前月份暂无周进度数据。")
else:
    weekly_display = weekly.copy()
    weekly_display["Weekly Sales"] = weekly_display["Weekly Sales"].map(_fmt_money)
    weekly_display["Month Cumulative Sales"] = weekly_display["Month Cumulative Sales"].map(_fmt_money)
    weekly_display["Previous Year Cumulative"] = weekly_display["Previous Year Cumulative"].map(_fmt_money)
    weekly_display["Cumulative YoY"] = weekly_display["Cumulative YoY"].map(_fmt_percent)
    weekly_display["Time Progress"] = weekly_display["Time Progress"].map(_fmt_percent)
    weekly_display["Target Completion"] = weekly_display["Target Completion"].map(_fmt_percent)
    st.dataframe(
        weekly_display[
            [
                "Week Label",
                "Weekly Sales",
                "Month Cumulative Sales",
                "Previous Year Cumulative",
                "Cumulative YoY",
                "Time Progress",
                "Target Completion",
            ]
        ].rename(
            columns={
                "Week Label": "周",
                "Weekly Sales": "每周销售额",
                "Month Cumulative Sales": "本月累计销售额",
                "Previous Year Cumulative": "去年同月同期累计",
                "Cumulative YoY": "当前累计同比",
                "Time Progress": "当前时间进度",
                "Target Completion": "目标完成率",
            }
        ),
        width="stretch",
        hide_index=True,
    )

section_header("月度和年度进度", "月度销售额、去年同期和目标线分开呈现，避免双 Y 轴误导。")
trend = _cached_monthly_trend(filtered, amount_targets, selected_year, selected_range)
left, right = st.columns([1.5, 1])
with left:
    st.plotly_chart(_bar_line_chart(trend), width="stretch")
with right:
    st.plotly_chart(_yoy_chart(trend), width="stretch")
    st.dataframe(
        trend[["Month Label", "YTD Sales", "Previous YTD", "YTD YoY"]].rename(
            columns={"Month Label": "月份", "YTD Sales": "年累计销售额", "Previous YTD": "去年同期累计", "YTD YoY": "年累计同比"}
        ).assign(
            年累计销售额=lambda data: data["年累计销售额"].map(_fmt_money),
            去年同期累计=lambda data: data["去年同期累计"].map(_fmt_money),
            年累计同比=lambda data: data["年累计同比"].map(_fmt_percent),
        ),
        width="stretch",
        hide_index=True,
    )

section_header("单系列详情", "查看 SKU、客户贡献变化，以及主要增长贡献和主要下降拖累。")
load_detail = st.toggle("加载单系列贡献分析", value=False)
if load_detail:
    detail_range = st.selectbox("选择单系列", [item for item in range_options if item != "全部"], index=0 if len(range_options) > 1 else None)
else:
    st.caption("默认不加载单系列贡献拆解，以加快页面切换。")
    detail_range = None
if load_detail and detail_range:
    detail_trend = _cached_monthly_trend(filtered, amount_targets, selected_year, detail_range)
    st.plotly_chart(_bar_line_chart(detail_trend), width="stretch")
    product_dimension = "Product Label" if "Product Label" in filtered.columns else "Product"
    customer_dimension = "Customer Label" if "Customer Label" in filtered.columns else "Customer"
    sku_growth, sku_decline = _cached_top_contributors(filtered, product_dimension, ctx, detail_range)
    customer_growth, customer_decline = _cached_top_contributors(filtered, customer_dimension, ctx, detail_range)
    col1, col2 = st.columns(2)
    with col1:
        section_header("主要增长贡献", "按本月较去年同期的销售额增量排序。")
        st.dataframe(sku_growth.rename(columns={product_dimension: "SKU", "Current": "本月", "Previous": "去年同期", "Change": "增量"}).assign(本月=lambda data: data["本月"].map(_fmt_money), 去年同期=lambda data: data["去年同期"].map(_fmt_money), 增量=lambda data: data["增量"].map(_fmt_money)), width="stretch", hide_index=True)
    with col2:
        section_header("主要下降拖累", "按本月较去年同期的销售额下降排序。")
        st.dataframe(sku_decline.rename(columns={product_dimension: "SKU", "Current": "本月", "Previous": "去年同期", "Change": "差额"}).assign(本月=lambda data: data["本月"].map(_fmt_money), 去年同期=lambda data: data["去年同期"].map(_fmt_money), 差额=lambda data: data["差额"].map(_fmt_money)), width="stretch", hide_index=True)
    col1, col2 = st.columns(2)
    with col1:
        st.caption("Top 10 贡献客户增长")
        st.dataframe(customer_growth.rename(columns={customer_dimension: "客户", "Current": "本月", "Previous": "去年同期", "Change": "增量"}).assign(本月=lambda data: data["本月"].map(_fmt_money), 去年同期=lambda data: data["去年同期"].map(_fmt_money), 增量=lambda data: data["增量"].map(_fmt_money)), width="stretch", hide_index=True)
    with col2:
        st.caption("Top 10 贡献客户下降")
        st.dataframe(customer_decline.rename(columns={customer_dimension: "客户", "Current": "本月", "Previous": "去年同期", "Change": "差额"}).assign(本月=lambda data: data["本月"].map(_fmt_money), 去年同期=lambda data: data["去年同期"].map(_fmt_money), 差额=lambda data: data["差额"].map(_fmt_money)), width="stretch", hide_index=True)

if "table" in locals() and not table.empty:
    load_full_detail = st.toggle("查看完整指标明细", value=False)
    if load_full_detail:
        detail_columns = [
            "产品系列",
            "当前状态",
            "本月销售额",
            "去年同期销售额",
            "上月同期销售额",
            "本年累计销售额",
            "去年同期累计销售额",
            "本月目标",
            "Annual Target",
            "同比增长额",
            "同比增长率",
            "环比增长率",
            "年累计同比",
            "目标完成率",
            "距离目标差额",
        ]
        st.dataframe(
            _style_overview(_display_table(table[detail_columns])),
            width="stretch",
            hide_index=True,
        )
