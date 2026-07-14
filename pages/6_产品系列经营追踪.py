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
        "Annual Target",
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


def _summary_table(data: pd.DataFrame) -> pd.DataFrame:
    columns = ["产品系列", "本月销售额", "同比增长率", "目标完成率", "距离目标差额", "当前状态"]
    if data.empty:
        return pd.DataFrame(columns=columns)
    return _display_table(data[columns].copy())


def _render_summary_block(title: str, data: pd.DataFrame) -> None:
    st.caption(title)
    if data.empty:
        st.write("暂无数据")
        return
    display = _summary_table(data.head(5)).copy()
    display["产品系列"] = display["产品系列"].map(_compact_name)
    st.dataframe(display.style.map(status_style, subset=["当前状态"]), width="stretch", hide_index=True, height=220)


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
selected_overview = overview if selected_range == "全部" else overview[overview[RANGE_COLUMN].eq(selected_range)]
configured_targets = selected_overview["Monthly Target"].notna() if "Monthly Target" in selected_overview.columns else pd.Series(dtype=bool)
kpi_grid(
    [
        {"label": "当前筛选范围销售额", "value": _fmt_money(selected_overview["Current Month Sales"].sum() if not selected_overview.empty else 0), "caption": "本月 1 日至分析截止日"},
        {"label": "增长系列数量", "value": f"{int(selected_overview['YoY Rate'].gt(0).sum()):,}", "caption": "同比为正"},
        {"label": "下降系列数量", "value": f"{int(selected_overview['YoY Rate'].lt(0).sum()):,}", "caption": "同比为负"},
        {"label": "达标系列数量", "value": f"{int(selected_overview['Target Completion'].ge(1).sum()):,}", "caption": "完成率 >= 100%"},
        {"label": "需要关注系列数量", "value": f"{int(selected_overview['Status'].isin(['明显落后', '需要关注']).sum()):,}", "caption": "按状态规则"},
        {"label": "未配置目标系列数量", "value": f"{int((~configured_targets).sum()) if len(configured_targets) else 0:,}", "caption": "未拆分公司目标"},
    ],
    columns=3,
)

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

    section_header("Top / Risk 摘要", "先看规模、增长、下降和目标进度落后系列。")
    top_sales = table.sort_values("本月销售额", ascending=False).head(5)
    top_growth = table[pd.to_numeric(table["同比增长率"], errors="coerce").notna()].sort_values("同比增长率", ascending=False).head(5)
    top_decline = table[pd.to_numeric(table["同比增长率"], errors="coerce").notna()].sort_values("同比增长率").head(5)
    target_lag = table[pd.to_numeric(table["目标完成率"], errors="coerce").notna()].sort_values(["目标完成率", "距离目标差额"], ascending=[True, True]).head(5)
    summary_cols = st.columns(4)
    with summary_cols[0]:
        _render_summary_block("销售额 Top 5", top_sales)
    with summary_cols[1]:
        _render_summary_block("同比增长 Top 5", top_growth)
    with summary_cols[2]:
        _render_summary_block("同比下降 Top 5", top_decline)
    with summary_cols[3]:
        _render_summary_block("目标进度落后 Top 5", target_lag)

    section_header("精简系列经营总览表", "默认只显示最重要的 9 个字段，状态放在第二列。")
    status_options = sorted(table["当前状态"].dropna().unique().tolist())
    control_cols = st.columns([2, 1])
    selected_status = control_cols[0].multiselect("状态筛选", status_options, default=status_options)
    sort_label = control_cols[1].selectbox("排序", ["当前状态", "本月销售额", "同比增长率", "目标完成率", "距离目标差额"])
    visible = table[table["当前状态"].isin(selected_status)].copy()
    visible = _sort_overview(visible, sort_label)
    compact_columns = [
        "产品系列",
        "当前状态",
        "本月销售额",
        "本月目标",
        "目标完成率",
        "距离目标差额",
        "同比增长率",
        "环比增长率",
        "年累计同比",
    ]
    st.dataframe(
        _display_table(visible[compact_columns]).style.map(status_style, subset=["当前状态"]),
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

if "table" in locals() and not table.empty:
    with st.expander("查看完整指标明细", expanded=False):
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
            _display_table(table[detail_columns]).style.map(status_style, subset=["当前状态"]),
            width="stretch",
            hide_index=True,
        )
