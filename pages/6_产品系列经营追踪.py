from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app.google_drive import ensure_drive_data_loaded, render_data_source_sidebar
from app.product_range_metrics import (
    RANGE_COLUMN,
    build_core_kpis,
    build_monthly_trend,
    build_range_overview,
    build_week_progress,
    top_contributors,
)
from app.ui import (
    inject_global_styles,
    kpi_grid,
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


def _fmt_percent(value) -> str:
    if value is None or pd.isna(value):
        return "无基准"
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
        "距离目标差额",
    ]
    percent_cols = ["同比增长率", "环比增长率", "年累计同比", "目标完成率"]
    for col in money_cols:
        if col in display.columns:
            display[col] = display[col].map(_fmt_money)
    for col in percent_cols:
        if col in display.columns:
            display[col] = display[col].map(_fmt_percent)
    return display


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

section_header("核心 KPI", "本月与去年同期、上月同期和系列目标的对比。")
kpis = build_core_kpis(overview if selected_range == "全部" else overview[overview[RANGE_COLUMN].eq(selected_range)])
kpi_grid(
    [
        {"label": "本月系列总销售额", "value": _fmt_money(kpis.get("current_sales")), "caption": "当前筛选范围"},
        {"label": "去年同期销售额", "value": _fmt_money(kpis.get("previous_year_sales")), "delta": _fmt_percent(kpis.get("yoy")), "caption": "同月同日进度"},
        {"label": "上月同期销售额", "value": _fmt_money(kpis.get("previous_month_sales")), "delta": _fmt_percent(kpis.get("mom")), "caption": "环比为上月同日进度"},
        {"label": "本月目标", "value": _fmt_money(kpis.get("monthly_target")), "delta": _fmt_percent(kpis.get("completion")), "caption": "未配置则不拆分公司目标"},
        {"label": "距离目标差额", "value": _fmt_money(kpis.get("target_gap")), "caption": "实际销售 - 系列目标"},
    ],
    columns=5,
)

section_header("系列经营总览表", "按系列查看销售、同比、环比、目标完成率和当前状态。")
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
        "Target Completion": "目标完成率",
        "Target Gap": "距离目标差额",
        "Status": "当前状态",
    }
)
if table.empty:
    st.info("当前筛选范围内没有产品系列销售。")
else:
    status_options = sorted(table["当前状态"].dropna().unique().tolist())
    selected_status = st.multiselect("状态筛选", status_options, default=status_options)
    visible = table[table["当前状态"].isin(selected_status)].copy()
    if selected_range != "全部":
        visible = visible[visible["产品系列"].eq(selected_range)]
    sort_label = st.selectbox("排序", ["本月销售额", "同比增长率", "目标完成率", "当前状态"])
    ascending = sort_label == "当前状态"
    visible = visible.sort_values(sort_label, ascending=ascending, na_position="last")
    st.dataframe(
        _display_table(visible).style.map(status_style, subset=["当前状态"]),
        width="stretch",
        hide_index=True,
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
detail_range = st.selectbox("选择单系列", [item for item in range_options if item != "全部"], index=0 if len(range_options) > 1 else None)
if detail_range:
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
