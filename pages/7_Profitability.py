import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.auth import require_login
from app.business_metrics import (
    aggregate_customer_profitability,
    aggregate_product_profitability,
    build_business_metrics_dataframe,
    cost_coverage_report,
    invalid_unit_cost_rows,
    monthly_profitability,
    negative_gross_profit_transactions,
    product_margin_reconciliation,
    profitability_kpis,
    suspicious_unit_comparison,
)
from app.google_drive import DriveUserError, ensure_drive_data_loaded, load_drive_cost_snapshots, render_data_source_sidebar
from app.ui import inject_global_styles, money, percent, section_header, style_plotly


def _money_or_na(value: object) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return money(float(value))


def _percent_or_na(value: object) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return percent(float(value))


@st.cache_data(show_spinner=False)
def _cached_business_metrics(sales_data: pd.DataFrame, snapshots: list) -> pd.DataFrame:
    return build_business_metrics_dataframe(sales_data, snapshots)


def _completed_date_filter(data: pd.DataFrame) -> pd.DataFrame:
    filtered = data.copy()
    dates = pd.to_datetime(filtered["Completed Date"], errors="coerce")
    min_date = dates.dropna().min()
    max_date = dates.dropna().max()
    with st.sidebar:
        st.markdown("### Profitability Filters")
        if pd.notna(min_date) and pd.notna(max_date):
            selected = st.date_input(
                "Date Range (Completed Date)",
                value=(min_date.date(), max_date.date()),
                min_value=min_date.date(),
                max_value=max_date.date(),
                key="profitability_completed_date_range",
            )
            if isinstance(selected, tuple) and len(selected) == 2:
                start, end = pd.Timestamp(selected[0]), pd.Timestamp(selected[1])
                normalized = dates.dt.normalize()
                filtered = filtered[normalized.between(start, end, inclusive="both")]

        product_groups = sorted(filtered["Product Group"].fillna("未分类").astype(str).unique().tolist()) if "Product Group" in filtered.columns else []
        with st.expander("Product Group", expanded=False):
            selected_groups = st.multiselect("Product Group", product_groups, default=product_groups, key="profitability_product_group")
        if selected_groups and len(selected_groups) < len(product_groups):
            filtered = filtered[filtered["Product Group"].fillna("未分类").astype(str).isin(selected_groups)]

        product_label_col = "Product Label" if "Product Label" in filtered.columns else "Product"
        product_options = sorted(filtered[product_label_col].fillna("Unknown").astype(str).unique().tolist()) if product_label_col in filtered.columns else []
        with st.expander("Product Code / Product", expanded=False):
            selected_products = st.multiselect("Product", product_options, default=[], key="profitability_products")
            st.caption("留空表示全部产品。")
        if selected_products:
            filtered = filtered[filtered[product_label_col].fillna("Unknown").astype(str).isin(selected_products)]

        customer_label_col = "Customer Label" if "Customer Label" in filtered.columns else "Customer"
        customer_options = sorted(filtered[customer_label_col].fillna("Unknown").astype(str).unique().tolist()) if customer_label_col in filtered.columns else []
        with st.expander("Customer", expanded=False):
            selected_customers = st.multiselect("Customer", customer_options, default=[], key="profitability_customers")
            st.caption("留空表示全部客户。")
        if selected_customers:
            filtered = filtered[filtered[customer_label_col].fillna("Unknown").astype(str).isin(selected_customers)]

        salesperson_candidates = [column for column in ["Salesperson", "Sales Person", "Sales Rep", "Account Manager"] if column in filtered.columns]
        if salesperson_candidates:
            sales_col = salesperson_candidates[0]
            salesperson_options = sorted(filtered[sales_col].fillna("Unknown").astype(str).unique().tolist())
            with st.expander("Salesperson", expanded=False):
                selected_salespeople = st.multiselect("Salesperson", salesperson_options, default=salesperson_options, key="profitability_salesperson")
            if selected_salespeople and len(selected_salespeople) < len(salesperson_options):
                filtered = filtered[filtered[sales_col].fillna("Unknown").astype(str).isin(selected_salespeople)]

        statuses = sorted(filtered["Cost Match Status"].fillna("Unknown").astype(str).unique().tolist())
        with st.expander("Cost Match Status", expanded=False):
            selected_statuses = st.multiselect("Cost Match Status", statuses, default=statuses, key="profitability_cost_status")
        if selected_statuses and len(selected_statuses) < len(statuses):
            filtered = filtered[filtered["Cost Match Status"].fillna("Unknown").astype(str).isin(selected_statuses)]
    return filtered


def _format_money_columns(table: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    display = table.copy()
    for column in columns:
        if column in display.columns:
            display[column] = display[column].map(_money_or_na)
    return display


def _format_percent_columns(table: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    display = table.copy()
    for column in columns:
        if column in display.columns:
            display[column] = display[column].map(_percent_or_na)
    return display


def _format_table(table: pd.DataFrame) -> pd.DataFrame:
    display = table.copy()
    money_columns = [
        "Sales Amount",
        "Costed Sales",
        "Unit Selling Price / ASP",
        "Unit Selling Price",
        "Unit Cost",
        "Total Cost",
        "Gross Profit",
        "Matched Sales Amount",
    ]
    percent_columns = [
        "Weighted Margin",
        "Margin %",
        "Cost Coverage",
        "Cost to Sales Ratio",
        "Gross Profit Contribution",
        "Sales Contribution",
        "Cost Contribution",
    ]
    display = _format_money_columns(display, money_columns)
    display = _format_percent_columns(display, percent_columns)
    if "Quantity" in display.columns:
        display["Quantity"] = pd.to_numeric(table["Quantity"], errors="coerce").map(lambda value: "" if pd.isna(value) else f"{value:,.2f}")
    return display


def _amount_trend_chart(trend: pd.DataFrame):
    fig = go.Figure()
    for column, color in [("Total Sales", "#FFC72C"), ("Costed Sales", "#374151"), ("Gross Profit", "#2E8B57")]:
        fig.add_trace(go.Scatter(x=trend["Month"], y=trend[column], mode="lines+markers", name=column, line=dict(color=color)))
    fig.update_yaxes(tickprefix="£", separatethousands=True)
    fig.update_layout(title="Profitability Amount Trend", height=340)
    return style_plotly(fig)


def _ratio_trend_chart(trend: pd.DataFrame):
    fig = go.Figure()
    for column, color in [("Weighted Margin", "#2E8B57"), ("Cost Coverage", "#8B93A1")]:
        fig.add_trace(go.Scatter(x=trend["Month"], y=trend[column], mode="lines+markers", name=column, line=dict(color=color)))
    fig.update_yaxes(tickformat=".1%")
    fig.update_layout(title="Margin and Coverage Trend", height=340)
    return style_plotly(fig)


def _transaction_columns(data: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Completed Date",
        "Customer",
        "Product Code",
        "Product Name",
        "Quantity",
        "Sales Amount",
        "Unit Selling Price",
        "Unit Cost",
        "Total Cost",
        "Gross Profit",
        "Margin %",
        "Cost File Name",
        "Cost Match Status",
    ]
    available = [column for column in columns if column in data.columns]
    display = data[available].copy()
    if "Completed Date" in display.columns:
        display["Completed Date"] = pd.to_datetime(display["Completed Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return _format_table(display)


st.set_page_config(page_title="Profitability", layout="wide")
inject_global_styles()
require_login("margin")

st.title("Profitability")
st.caption("Profitability Dashboard MVP & Margin Reconciliation")

ensure_drive_data_loaded()
render_data_source_sidebar(show_uploaders=False)

sales_df = st.session_state.get("clean_data")
if sales_df is None:
    st.info("当前暂无销售数据，请回到首页使用 Google Drive 刷新或手动上传 Unleashed 销售明细。")
    st.stop()

try:
    registry, cost_snapshots = load_drive_cost_snapshots(force=False)
except DriveUserError as exc:
    st.warning(str(exc))
    registry, cost_snapshots = None, []

if not cost_snapshots:
    st.info("当前没有可用成本快照，Profitability 页面会保留销售数据，但无法计算毛利。")

metrics_df = _cached_business_metrics(sales_df, cost_snapshots)
filtered = _completed_date_filter(metrics_df)

st.caption("Profitability is calculated using Completed Date.")
with st.expander("数据口径说明", expanded=False):
    st.markdown(
        """
        - Profitability uses Completed Date.
        - Gross Profit is calculated only where a valid cost is available.
        - Total Sales includes both costed and uncosted sales.
        - Current cost history begins on 2026-07-01.
        - Quantity Unit is not populated in the source sales data.
        - Product Code is currently used as the unit-matching key.
        """
    )
    if registry is not None:
        st.caption(f"成本快照数：{len(cost_snapshots)}")
        for entry in registry.entries:
            st.caption(f"{entry.file_name}: {entry.validation_status}; warnings={len(entry.warnings)}; errors={len(entry.errors)}")

kpis = profitability_kpis(filtered)
coverage = cost_coverage_report(filtered)
if kpis["Cost Coverage"] is not None and float(kpis["Cost Coverage"]) < 0.5:
    st.warning(f"当前成本覆盖率较低：{_percent_or_na(kpis['Cost Coverage'])}。毛利和 Weighted Margin 仅代表已核算成本的销售。")

cols = st.columns(6)
cols[0].metric("Total Sales", _money_or_na(kpis["Total Sales"]))
cols[1].metric("Costed Sales", _money_or_na(kpis["Costed Sales"]))
cols[2].metric("Gross Profit", _money_or_na(kpis["Gross Profit"]))
cols[3].metric("Weighted Margin", _percent_or_na(kpis["Weighted Margin"]))
cols[4].metric("Cost Coverage", _percent_or_na(kpis["Cost Coverage"]))
cols[5].metric("Uncosted Sales", _money_or_na(kpis["Uncosted Sales"]))

section_header("Profit Trend")
trend = monthly_profitability(filtered)
if trend.empty:
    st.info("当前筛选范围内没有趋势数据。")
else:
    left, right = st.columns(2)
    with left:
        st.plotly_chart(_amount_trend_chart(trend), width="stretch")
    with right:
        st.plotly_chart(_ratio_trend_chart(trend), width="stretch")

section_header("Product Profitability")
product_table = aggregate_product_profitability(filtered)
if product_table.empty:
    st.info("当前筛选范围内没有产品利润数据。")
else:
    sort_option = st.selectbox(
        "Product table sort",
        ["Sales Amount", "Gross Profit", "Weighted Margin", "Cost Coverage"],
        index=0,
    )
    ascending = sort_option in ["Weighted Margin", "Cost Coverage"]
    sorted_product_table = product_table.sort_values(sort_option, ascending=ascending, na_position="last")
    st.dataframe(_format_table(sorted_product_table.head(200)), width="stretch", hide_index=True)

section_header("Customer Profitability")
customer_table = aggregate_customer_profitability(filtered)
if customer_table.empty:
    st.info("当前筛选范围内没有客户利润数据。")
else:
    st.dataframe(_format_table(customer_table.head(200)), width="stretch", hide_index=True)

section_header("Margin Reconciliation")
if not product_table.empty:
    with st.expander("A. Lowest Margin Products", expanded=True):
        lowest = product_table[product_table["Weighted Margin"].notna()].sort_values("Weighted Margin", ascending=True).head(30)
        st.dataframe(_format_table(lowest), width="stretch", hide_index=True)

    with st.expander("B. Highest Cost-to-Sales Products", expanded=False):
        cost_to_sales = product_table.copy()
        cost_to_sales["Cost to Sales Ratio"] = pd.to_numeric(cost_to_sales["Total Cost"], errors="coerce") / pd.to_numeric(
            cost_to_sales["Sales Amount"], errors="coerce"
        )
        st.dataframe(_format_table(cost_to_sales.sort_values("Cost to Sales Ratio", ascending=False).head(30)), width="stretch", hide_index=True)

negative = negative_gross_profit_transactions(filtered)
with st.expander("C. Negative Gross Profit Transactions", expanded=True):
    if negative.empty:
        st.caption("当前筛选范围内没有负毛利交易。")
    else:
        st.dataframe(_transaction_columns(negative.sort_values("Gross Profit").head(300)), width="stretch", hide_index=True)

suspicious = suspicious_unit_comparison(filtered)
with st.expander("D. Suspicious Unit Comparison", expanded=True):
    if suspicious.empty:
        st.caption("当前筛选范围内没有命中单位/售价诊断规则的交易。")
    else:
        display = _transaction_columns(suspicious.head(500))
        if "Suspicion Reason" in suspicious.columns:
            display["Suspicion Reason"] = suspicious["Suspicion Reason"].head(500).to_list()
        st.dataframe(display, width="stretch", hide_index=True)

invalid_cost = invalid_unit_cost_rows(filtered)
with st.expander("E. Invalid Unit Cost", expanded=True):
    if invalid_cost.empty:
        st.caption("当前筛选范围内没有 Invalid Unit Cost 行。")
    else:
        st.dataframe(_transaction_columns(invalid_cost.head(300)), width="stretch", hide_index=True)

with st.expander("F. Product Margin Reconciliation", expanded=True):
    reconciliation = product_margin_reconciliation(filtered)
    if reconciliation.empty:
        st.caption("当前筛选范围内没有产品贡献拆解。")
    else:
        st.dataframe(_format_table(reconciliation.head(50)), width="stretch", hide_index=True)

with st.expander("Coverage Report", expanded=False):
    st.json(coverage)
