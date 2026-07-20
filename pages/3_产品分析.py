import pandas as pd
import plotly.express as px
import streamlit as st

from app.auth import require_login
from app.data import monthly_sales, top_entity_table, top_table
from app.google_drive import ensure_drive_data_loaded, render_data_source_sidebar
from app.ui import bar_chart, inject_global_styles, line_chart, money, percent, metric_row, section_header, show_code_warning, show_context_summary, show_filters, style_plotly


def _product_group_monthly_trend(data):
    if data.empty or "Product Group" not in data.columns:
        return pd.DataFrame()
    trend = data.copy()
    trend["产品系列"] = trend["Product Group"].fillna("未分类").astype(str)
    trend["月份"] = pd.to_datetime(trend["Performance Date"], errors="coerce").dt.to_period("M").astype(str)
    trend["Sales Amount"] = pd.to_numeric(trend["Sales Amount"], errors="coerce").fillna(0)
    return trend.groupby(["月份", "产品系列"], dropna=False)["Sales Amount"].sum().reset_index()


def _same_period_last_year(data):
    dates = pd.to_datetime(data["Performance Date"], errors="coerce").dropna()
    if dates.empty:
        return data.iloc[0:0]
    start = dates.min().normalize() - pd.DateOffset(years=1)
    end = dates.max().normalize() - pd.DateOffset(years=1)
    all_dates = pd.to_datetime(data["Performance Date"], errors="coerce").dt.normalize()
    return data.loc[all_dates.between(start, end, inclusive="both")]


def _product_group_snapshot(data):
    columns = ["产品系列", "当前销售额", "去年同期", "同比增长额", "同比增长率", "销售贡献率"]
    if data.empty or "Product Group" not in data.columns:
        return pd.DataFrame(columns=columns)
    current = data.copy()
    current["产品系列"] = current["Product Group"].fillna("未分类").astype(str)
    current["Sales Amount"] = pd.to_numeric(current["Sales Amount"], errors="coerce").fillna(0)
    previous = _same_period_last_year(data).copy()
    previous["产品系列"] = previous["Product Group"].fillna("未分类").astype(str)
    previous["Sales Amount"] = pd.to_numeric(previous["Sales Amount"], errors="coerce").fillna(0)
    table = current.groupby("产品系列", dropna=False)["Sales Amount"].sum().rename("当前销售额").reset_index()
    previous_table = previous.groupby("产品系列", dropna=False)["Sales Amount"].sum().rename("去年同期").reset_index()
    table = table.merge(previous_table, on="产品系列", how="left").fillna({"去年同期": 0})
    total = float(table["当前销售额"].sum())
    table["同比增长额"] = table["当前销售额"] - table["去年同期"]
    table["同比增长率"] = table.apply(lambda row: None if row["去年同期"] == 0 else row["同比增长额"] / row["去年同期"], axis=1)
    table["销售贡献率"] = table["当前销售额"] / total if total else 0.0
    return table[columns].sort_values("当前销售额", ascending=False).reset_index(drop=True)


def _format_group_snapshot(table):
    display = table.copy()
    for column in ["当前销售额", "去年同期", "同比增长额"]:
        display[column] = display[column].map(money)
    display["同比增长率"] = display["同比增长率"].map(lambda value: "无基数" if value is None or pd.isna(value) else percent(float(value)))
    display["销售贡献率"] = display["销售贡献率"].map(percent)
    return display


def _yoy_cell_style(value):
    if pd.isna(value) or value == "无基数":
        return "color: #6b7280;"
    text = str(value).replace("%", "").replace("+", "")
    try:
        number = float(text)
    except ValueError:
        return "color: #6b7280;"
    return "color: #166534; font-weight: 600;" if number >= 0 else "color: #b91c1c; font-weight: 600;"


def _group_trend_chart(trend, selected_groups):
    plot = trend[trend["产品系列"].isin(selected_groups)].copy()
    fig = px.line(plot, x="月份", y="Sales Amount", color="产品系列", markers=True, title="产品系列月度销售趋势")
    fig.update_yaxes(tickprefix="£", separatethousands=True)
    fig.update_layout(height=380, legend_title_text="产品系列")
    return style_plotly(fig)


st.set_page_config(page_title="产品分析", layout="wide")
inject_global_styles()
require_login("product_analysis")
st.title("产品分析")
st.caption("查看产品组、重点产品和购买客户表现")

ensure_drive_data_loaded()
render_data_source_sidebar(show_uploaders=False)

df = st.session_state.get("clean_data")

if df is None:
    st.info("当前暂无销售数据，请回到首页使用 Google Drive 刷新或手动上传 Unleashed 销售明细。")
    st.stop()

filtered = show_filters(df, "products")
show_code_warning(filtered)
show_context_summary(filtered)
metric_row(filtered)

section_header("产品系列趋势", "Product Group Trend")
group_trend = _product_group_monthly_trend(filtered)
group_snapshot = _product_group_snapshot(filtered)
if group_trend.empty or group_snapshot.empty:
    st.info("当前筛选范围内没有可用的产品系列数据。")
else:
    top_groups = group_snapshot.head(5)["产品系列"].tolist()
    group_options = group_snapshot["产品系列"].tolist()
    selected_groups = st.multiselect("选择产品系列", group_options, default=top_groups)
    dates = pd.to_datetime(filtered["Performance Date"], errors="coerce").dropna()
    if not dates.empty:
        latest_date = dates.max().normalize()
        if latest_date < latest_date + pd.offsets.MonthEnd(0):
            st.caption(f"{latest_date:%Y-%m} 为部分月份，当前数据截至 {latest_date:%Y-%m-%d}。")
    if selected_groups:
        st.plotly_chart(_group_trend_chart(group_trend, selected_groups), width="stretch")
    else:
        st.info("请选择至少一个产品系列。")

    st.dataframe(
        _format_group_snapshot(group_snapshot).style.map(_yoy_cell_style, subset=["同比增长率"]),
        width="stretch",
        hide_index=True,
    )

section_header("Top Products")
left, right = st.columns(2)
with left:
    top_groups = top_table(filtered, "Product Group", 20)
    st.plotly_chart(bar_chart(top_groups.sort_values("Sales Amount"), "Sales Amount", "Product Group", "产品组销售排行", "h"), width="stretch")
with right:
    if "Product Key" in filtered.columns and "Product Label" in filtered.columns:
        top_products = top_entity_table(filtered, "Product Key", "Product Label", 20)
        st.plotly_chart(bar_chart(top_products.sort_values("Sales Amount"), "Sales Amount", "Product Label", "Top Product Code", "h"), width="stretch")
    else:
        top_products = top_table(filtered, "Product", 20)
        st.plotly_chart(bar_chart(top_products.sort_values("Sales Amount"), "Sales Amount", "Product", "Top Product", "h"), width="stretch")

section_header("单产品趋势")
product_dimension = "Product Key" if "Product Key" in filtered.columns else "Product"
label_dimension = "Product Label" if "Product Label" in filtered.columns else product_dimension
product_options = filtered[[product_dimension, label_dimension]].dropna().drop_duplicates().sort_values(label_dimension)
products = product_options[label_dimension].astype(str).tolist()
if not products:
    st.info("当前筛选范围内没有产品数据。")
    st.stop()
selected_product = st.selectbox("选择产品", products)
selected_key = product_options.loc[product_options[label_dimension].astype(str).eq(selected_product), product_dimension].iloc[0]
product_df = filtered[filtered[product_dimension].eq(selected_key)]

left, right = st.columns([1.2, 1])
with left:
    st.plotly_chart(line_chart(monthly_sales(product_df), "Month", "Sales Amount", "产品月度销售趋势"), width="stretch")
with right:
    if "Customer Key" in product_df.columns and "Customer Label" in product_df.columns:
        buyers = top_entity_table(product_df, "Customer Key", "Customer Label", 15)
        st.plotly_chart(bar_chart(buyers.sort_values("Sales Amount"), "Sales Amount", "Customer Label", "购买客户排行", "h"), width="stretch")
    else:
        buyers = top_table(product_df, "Customer", 15)
        st.plotly_chart(bar_chart(buyers.sort_values("Sales Amount"), "Sales Amount", "Customer", "购买客户排行", "h"), width="stretch")

with st.expander("查看产品明细"):
    st.dataframe(product_df.sort_values("Sales Date", ascending=False).head(500), width="stretch")
