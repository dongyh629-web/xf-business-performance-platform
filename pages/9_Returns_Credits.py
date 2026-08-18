from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from app.auth import require_login
from app.credit_metrics import (
    aggregate_credit_reasons,
    aggregate_customer_credits,
    aggregate_product_credits,
    aggregate_product_group_credits,
    credit_kpis,
    monthly_credit_trend,
    sales_for_credit_period,
)
from app.credit_notes import CREDIT_FILE_PATTERN, filter_credit_by_date
from app import google_drive as drive_data
from app.ui import inject_global_styles, money, percent, render_date_range_inputs, section_header, style_plotly


def _money_or_na(value: object) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return money(float(value))


def _percent_or_na(value: object) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return percent(float(value))


def _number(value: object) -> str:
    if value is None or pd.isna(value):
        return "0"
    return f"{float(value):,.0f}"


def _render_header() -> None:
    st.markdown(
        """
        <div class="xf-profit-header">
            <div class="xf-profit-title-cn">退货与退款综合看板</div>
            <div class="xf-profit-title-en">Returns & Credits Executive Dashboard</div>
            <div class="xf-profit-subtitle-cn">查看 Credit Notes、退款率、调整后销售额及客户/产品退货风险。</div>
            <div class="xf-profit-subtitle-en">Review credit notes, credit rate, adjusted net sales, and customer/product return exposure.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_kpis(kpis: dict[str, float | int | None]) -> None:
    cards = [
        ("退款金额", "Credit Amount", _money_or_na(kpis.get("Credit Amount")), True),
        ("退款率", "Credit Rate", _percent_or_na(kpis.get("Credit Rate")), True),
        ("退款单数", "Credit Note Count", _number(kpis.get("Credit Note Count")), False),
        ("涉及客户", "Affected Customers", _number(kpis.get("Affected Customers")), False),
        ("涉及产品", "Affected Products", _number(kpis.get("Affected Products")), False),
        ("调整后销售额", "Net Sales", _money_or_na(kpis.get("Net Sales")), True),
    ]
    html = []
    for chinese, english, value, featured in cards:
        feature_class = " featured" if featured else ""
        html.append(
            f'<div class="xf-profit-kpi{feature_class}">'
            f'<div class="xf-profit-kpi-cn">{chinese}</div>'
            f'<div class="xf-profit-kpi-en">{english}</div>'
            f'<div class="xf-profit-kpi-value">{value}</div>'
            "</div>"
        )
    st.markdown(f'<div class="xf-profit-kpi-grid">{"".join(html)}</div>', unsafe_allow_html=True)


def _credit_amount_style(value: object) -> str:
    number = None
    try:
        if not pd.isna(value):
            number = float(value)
    except (TypeError, ValueError):
        number = None
    if number is None:
        return "color: #6b7280; background-color: #f9fafb;"
    if number > 0:
        return "color: #9a3412; background-color: #fff7ed;"
    return ""


def _rate_style(value: object) -> str:
    number = None
    try:
        if not pd.isna(value):
            number = float(value)
    except (TypeError, ValueError):
        number = None
    if number is None:
        return "color: #6b7280; background-color: #f9fafb;"
    if number >= 0.10:
        return "color: #991b1b; background-color: #fff1f2;"
    if number >= 0.03:
        return "color: #9a3412; background-color: #fff7ed;"
    return "color: #166534; background-color: #f0fdf4;"


def _format_credit_table(table: pd.DataFrame):
    formatters = {
        "Gross Sales": lambda value: _money_or_na(value),
        "Credit": lambda value: _money_or_na(value),
        "Credit Amount": lambda value: _money_or_na(value),
        "Net Sales": lambda value: _money_or_na(value),
        "Credit Rate": lambda value: _percent_or_na(value),
        "Credit %": lambda value: _percent_or_na(value),
        "Credit Quantity": lambda value: _number(value),
        "Credit Note Count": lambda value: _number(value),
        "Affected Products": lambda value: _number(value),
        "Latest Credit Date": lambda value: "" if pd.isna(value) else pd.Timestamp(value).strftime("%Y-%m-%d"),
        "销售额 / Gross Sales": lambda value: _money_or_na(value),
        "退款金额 / Credit": lambda value: _money_or_na(value),
        "退款金额 / Credit Amount": lambda value: _money_or_na(value),
        "调整后销售额 / Net Sales": lambda value: _money_or_na(value),
        "退款率 / Credit %": lambda value: _percent_or_na(value),
        "占比 / Credit %": lambda value: _percent_or_na(value),
        "退款数量 / Credit Quantity": lambda value: _number(value),
        "退款单数 / Credit Count": lambda value: _number(value),
        "涉及产品 / Affected Products": lambda value: _number(value),
        "最近退款日期 / Latest Credit Date": lambda value: "" if pd.isna(value) else pd.Timestamp(value).strftime("%Y-%m-%d"),
    }
    styled = table.style
    for column in ["Credit", "Credit Amount", "退款金额 / Credit", "退款金额 / Credit Amount"]:
        if column in table.columns:
            styled = styled.map(_credit_amount_style, subset=[column])
    for column in ["Credit Rate", "Credit %", "退款率 / Credit %", "占比 / Credit %"]:
        if column in table.columns:
            styled = styled.map(_rate_style, subset=[column])
    return styled.format({column: formatter for column, formatter in formatters.items() if column in table.columns})


def _rename_customer_table(table: pd.DataFrame) -> pd.DataFrame:
    columns = ["Customer", "Gross Sales", "Credit", "Net Sales", "Credit Rate", "Credit Note Count", "Affected Products", "Latest Credit Date"]
    return table.loc[:, [column for column in columns if column in table.columns]].rename(
        columns={
            "Customer": "客户 / Customer",
            "Gross Sales": "销售额 / Gross Sales",
            "Credit": "退款金额 / Credit",
            "Net Sales": "调整后销售额 / Net Sales",
            "Credit Rate": "退款率 / Credit %",
            "Credit Note Count": "退款单数 / Credit Count",
            "Affected Products": "涉及产品 / Affected Products",
            "Latest Credit Date": "最近退款日期 / Latest Credit Date",
        }
    )


def _rename_product_table(table: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Product Code",
        "Product",
        "Product Group",
        "Gross Sales",
        "Credit",
        "Net Sales",
        "Credit Rate",
        "Credit Quantity",
        "Credit Note Count",
    ]
    return table.loc[:, [column for column in columns if column in table.columns]].rename(
        columns={
            "Product Code": "产品编码 / Product Code",
            "Product": "产品名称 / Product",
            "Product Group": "产品系列 / Product Group",
            "Gross Sales": "销售额 / Gross Sales",
            "Credit": "退款金额 / Credit",
            "Net Sales": "调整后销售额 / Net Sales",
            "Credit Rate": "退款率 / Credit %",
            "Credit Quantity": "退款数量 / Credit Quantity",
            "Credit Note Count": "退款单数 / Credit Count",
        }
    )


def _line_chart(data: pd.DataFrame, title: str):
    fig = px.line(data, x="Month", y="Credit Amount", markers=True, title=title)
    fig.update_traces(line=dict(color="#FFC72C", width=2.6), marker=dict(size=6, color="#FFC72C"))
    fig.update_yaxes(tickprefix="£", separatethousands=True)
    fig.update_layout(height=320)
    return style_plotly(fig)


def _render_credit_data_source() -> None:
    quality = st.session_state.get("credit_quality") or {}
    rows = st.session_state.get("drive_credit_row_count", quality.get("Rows", 0))
    credit_notes = st.session_state.get("drive_credit_note_count", quality.get("Credit Note Count", 0))
    date_range = st.session_state.get("drive_credit_date_range") or "无"
    last_refresh = st.session_state.get("drive_credit_loaded_at") or "无"
    latest_snapshot = st.session_state.get("drive_credit_file_name") or st.session_state.get("drive_credit_latest_snapshot") or "无"
    cols = st.columns(5)
    cols[0].metric("Latest Snapshot / 最新快照", latest_snapshot)
    cols[1].metric("Rows / 行数", _number(rows))
    cols[2].metric("Credit Notes / 退款单数", _number(credit_notes))
    cols[3].metric("Date Range / 日期范围", date_range)
    cols[4].metric("Last Refresh / 最近刷新", last_refresh)
    if st.session_state.get("drive_credit_message"):
        st.caption(st.session_state["drive_credit_message"])


inject_global_styles()
require_login("returns")
drive_status = drive_data.ensure_drive_data_loaded()
drive_data.render_data_source_sidebar(show_uploaders=False)

sales_df = st.session_state.get("clean_data")
if sales_df is None:
    st.markdown("## 退货与退款 / Returns & Credits")
    if drive_status and drive_status.sales.status == "failed":
        st.warning(drive_status.sales.message)
    drive_data.render_drive_data_load_prompt("尚未加载销售数据", "Returns & Credits 需要先读取销售数据，用于计算 Gross Sales 与 Net Sales。")
    st.stop()

_render_header()

_registry, _snapshot = drive_data.load_drive_credit_snapshot(force=False)

section_header("Credit Data Source / Credit 数据来源")
_render_credit_data_source()
if _snapshot is None:
    st.info(f"尚未加载 Credit Notes。请在左侧数据同步中点击 Refresh Credit Notes。文件命名规则：{CREDIT_FILE_PATTERN}")

credit_df = st.session_state.get("credit_data")
if credit_df is None:
    credit_df = pd.DataFrame()

date_source = credit_df if not credit_df.empty else sales_df
date_column = "Credit Date" if not credit_df.empty else ("Performance Date" if "Performance Date" in sales_df.columns else "Completed Date")
dates = pd.to_datetime(date_source.get(date_column), errors="coerce").dropna()
if dates.empty:
    st.warning("当前没有有效日期，无法生成 Returns & Credits 分析。")
    st.stop()

selected_range = render_date_range_inputs(
    "Credit Date",
    "returns_credits",
    dates.min().date(),
    dates.max().date(),
    legacy_key="returns_credits_date_range",
)
if selected_range is None:
    st.stop()
start_date, end_date = selected_range
st.caption(f"当前 Credit Date 范围：{start_date} 至 {end_date}")

filtered_credit = filter_credit_by_date(credit_df, start_date, end_date)
period_sales = sales_for_credit_period(sales_df, start_date, end_date)
kpis = credit_kpis(filtered_credit, period_sales)
_render_kpis(kpis)

section_header("Adjusted Sales / 调整后销售额", "Gross Sales - Credit Notes = Net Sales；本阶段不修改 Profitability 利润公式。")
formula_cols = st.columns(3)
formula_cols[0].metric("Gross Sales / 销售额", _money_or_na(kpis["Gross Sales"]))
formula_cols[1].metric("Credit Notes / 退款金额", _money_or_na(kpis["Credit Amount"]))
formula_cols[2].metric("Net Sales / 调整后销售额", _money_or_na(kpis["Net Sales"]))

trend = monthly_credit_trend(filtered_credit)
if not trend.empty:
    st.plotly_chart(_line_chart(trend, "Credit 趋势 / Monthly Credit Trend"), use_container_width=True)

customer_table = aggregate_customer_credits(filtered_credit, period_sales)
product_table = aggregate_product_credits(filtered_credit, period_sales)
group_table = aggregate_product_group_credits(product_table)
reason_table = aggregate_credit_reasons(filtered_credit)

section_header("Customer Credit Analysis / 客户退货分析")
left, right = st.columns(2)
with left:
    st.markdown("**Top Credit Customers / 退款金额最高客户**")
    st.dataframe(_format_credit_table(_rename_customer_table(customer_table.head(20))), use_container_width=True, hide_index=True)
with right:
    st.markdown("**High Credit Rate Customers / 高退款率客户**")
    high_rate = customer_table[customer_table["Gross Sales"].gt(0)].sort_values("Credit Rate", ascending=False).head(20)
    st.dataframe(_format_credit_table(_rename_customer_table(high_rate)), use_container_width=True, hide_index=True)

section_header("Product Credit Analysis / 产品退货分析")
left, right = st.columns(2)
with left:
    st.markdown("**Top Credit Products / 退款金额最高产品**")
    st.dataframe(_format_credit_table(_rename_product_table(product_table.head(20))), use_container_width=True, hide_index=True)
with right:
    st.markdown("**Highest Credit Rate Products / 高退款率产品**")
    high_product_rate = product_table[product_table["Gross Sales"].gt(0)].sort_values("Credit Rate", ascending=False).head(20)
    st.dataframe(_format_credit_table(_rename_product_table(high_product_rate)), use_container_width=True, hide_index=True)

st.markdown("**Product Group Credit Summary / 产品系列退款汇总**")
st.dataframe(
    _format_credit_table(
        group_table.rename(
            columns={
                "Product Group": "产品系列 / Product Group",
                "Gross Sales": "销售额 / Gross Sales",
                "Credit": "退款金额 / Credit",
                "Net Sales": "调整后销售额 / Net Sales",
                "Credit Rate": "退款率 / Credit %",
                "Credit Quantity": "退款数量 / Credit Quantity",
                "Credit Note Count": "退款单数 / Credit Count",
            }
        )
    ),
    use_container_width=True,
    hide_index=True,
)

section_header("Credit Reason Analysis / 退货原因分析")
reason_display = reason_table.rename(
    columns={
        "Reason": "原因 / Reason",
        "Credit Amount": "退款金额 / Credit Amount",
        "Credit %": "占比 / Credit %",
        "Credit Note Count": "退款单数 / Credit Count",
    }
)
st.dataframe(_format_credit_table(reason_display), use_container_width=True, hide_index=True)

with st.expander("Data Quality / 数据质量", expanded=False):
    quality = st.session_state.get("credit_quality") or {}
    if quality:
        labels = {
            "Rows": "Rows / 行数",
            "Credit Note Count": "Credit Note Count / 退款单数",
            "Customers": "Customers / 客户数",
            "Products": "Products / 产品数",
            "Missing Customer": "Missing Customer / 缺客户",
            "Missing Product": "Missing Product / 缺产品",
            "Missing Product Group": "Missing Product Group / 缺产品系列",
            "Unknown Reason": "Unknown Reason / 未知原因",
            "Duplicate / Ambiguous Rows": "Duplicate / Ambiguous Rows / 重复或模糊行",
            "Exact Duplicate Rows Preserved": "Exact Duplicate Rows Preserved / 已保留完全相同行",
            "Ambiguous Rows Preserved": "Ambiguous Rows Preserved / 已保留模糊重复行",
        }
        quality_table = pd.DataFrame(
            [{"Metric": labels.get(key, key), "Value": value} for key, value in quality.items() if key in labels]
        )
        st.dataframe(quality_table, use_container_width=True, hide_index=True)
    else:
        st.caption("尚未读取 Credit 文件。")
    if st.session_state.get("credit_raw_columns"):
        st.caption("实际读取字段 / Raw Columns")
        st.write(st.session_state["credit_raw_columns"])
