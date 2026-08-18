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
        <div class="xf-credits-header">
            <div class="xf-credits-title-cn">退货与退款经营分析</div>
            <div class="xf-credits-title-en">Returns & Credits</div>
            <div class="xf-credits-subtitle-cn">快速判断退款规模、主要问题来源，以及需要关注的客户与产品。</div>
            <div class="xf-credits-subtitle-en">Review credit exposure, key drivers, and customer/product risk in one view.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _risk_class(rate: object) -> str:
    try:
        value = float(rate)
    except (TypeError, ValueError):
        return "neutral"
    if pd.isna(value):
        return "neutral"
    if value >= 0.10:
        return "high"
    if value >= 0.03:
        return "watch"
    return "normal"


def _metric_card_html(chinese: str, english: str, value: str, *, emphasis: bool = False, risk: str = "neutral") -> str:
    emphasis_class = " emphasis" if emphasis else ""
    return (
        f'<div class="xf-credit-kpi {risk}{emphasis_class}">'
        f'<div class="xf-credit-kpi-label">{chinese} / <span>{english}</span></div>'
        f'<div class="xf-credit-kpi-value">{value}</div>'
        "</div>"
    )


def _render_primary_kpis(kpis: dict[str, float | int | None]) -> None:
    cards = [
        _metric_card_html("销售额", "Gross Sales", _money_or_na(kpis.get("Gross Sales"))),
        _metric_card_html("退款金额", "Credit Notes", _money_or_na(kpis.get("Credit Amount"))),
        _metric_card_html("调整后销售额", "Net Sales", _money_or_na(kpis.get("Net Sales"))),
        _metric_card_html(
            "退款率",
            "Credit Rate",
            _percent_or_na(kpis.get("Credit Rate")),
            emphasis=True,
            risk=_risk_class(kpis.get("Credit Rate")),
        ),
    ]
    st.markdown(f'<div class="xf-credit-primary-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def _render_secondary_kpis(kpis: dict[str, float | int | None], top_reason: str) -> None:
    cards = [
        _metric_card_html("退款单数", "Credit Note Count", _number(kpis.get("Credit Note Count"))),
        _metric_card_html("涉及客户", "Affected Customers", _number(kpis.get("Affected Customers"))),
        _metric_card_html("涉及产品", "Affected Products", _number(kpis.get("Affected Products"))),
        _metric_card_html("主要退款原因", "Top Credit Reason", top_reason or "Unknown / 未知"),
    ]
    st.markdown(f'<div class="xf-credit-secondary-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


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


def _unclassified_style(value: object) -> str:
    text = "" if pd.isna(value) else str(value).casefold()
    if "unclassified" in text or "未分类" in text:
        return "color: #9a3412; background-color: #fff7ed; font-weight: 600;"
    return ""


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
    if table.empty:
        return table
    styled = table.style
    for column in ["Credit", "Credit Amount", "退款金额 / Credit", "退款金额 / Credit Amount"]:
        if column in table.columns:
            styled = styled.map(_credit_amount_style, subset=[column])
    for column in ["Credit Rate", "Credit %", "退款率 / Credit %", "占比 / Credit %"]:
        if column in table.columns:
            styled = styled.map(_rate_style, subset=[column])
    for column in ["Product Group", "产品系列 / Product Group"]:
        if column in table.columns:
            styled = styled.map(_unclassified_style, subset=[column])
    return styled.format({column: formatter for column, formatter in formatters.items() if column in table.columns})


def _add_low_sales_base_flag(table: pd.DataFrame) -> pd.DataFrame:
    if table.empty or "Gross Sales" not in table.columns:
        return table
    result = table.copy()
    gross_sales = pd.to_numeric(result["Gross Sales"], errors="coerce").fillna(0)
    credit = pd.to_numeric(result.get("Credit", 0), errors="coerce").fillna(0)
    result["Note"] = ""
    result.loc[gross_sales.lt(200) & credit.gt(0), "Note"] = "低销售基数 / Low sales base"
    return result


def _rename_customer_table(table: pd.DataFrame) -> pd.DataFrame:
    columns = ["Customer", "Gross Sales", "Credit", "Net Sales", "Credit Rate", "Credit Note Count", "Affected Products", "Latest Credit Date", "Note"]
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
            "Note": "提示 / Note",
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


def _top_value(table: pd.DataFrame, label_column: str, value_column: str = "Credit") -> str:
    if table is None or table.empty or label_column not in table.columns or value_column not in table.columns:
        return "无 / N/A"
    ranked = table.sort_values(value_column, ascending=False)
    value = ranked.iloc[0].get(label_column)
    text = "" if pd.isna(value) else str(value)
    return text or "无 / N/A"


def _render_executive_summary(
    kpis: dict[str, float | int | None],
    group_table: pd.DataFrame,
    customer_table: pd.DataFrame,
    reason_table: pd.DataFrame,
) -> None:
    top_group = _top_value(group_table, "Product Group")
    top_customer = _top_value(customer_table, "Customer")
    top_reason = _top_value(reason_table, "Reason", "Credit Amount")
    st.markdown(
        f"""
        <div class="xf-credit-summary">
            <div class="xf-credit-summary-title">经营摘要 / Executive Summary</div>
            <div class="xf-credit-summary-body">
                <div>本期退款金额 <strong>{_money_or_na(kpis.get("Credit Amount"))}</strong>，占销售额 <strong>{_percent_or_na(kpis.get("Credit Rate"))}</strong>。</div>
                <div>退款主要来自 <strong>{top_group}</strong>，退款金额最高客户为 <strong>{top_customer}</strong>。</div>
                <div>主要退款原因为 <strong>{top_reason}</strong>。</div>
                <div class="xf-credit-summary-en">Credit exposure is concentrated in {top_group}; top customer is {top_customer}, with {top_reason} as the leading reason.</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _line_chart(data: pd.DataFrame, title: str):
    chart_data = data.copy()
    chart_data["Month"] = chart_data["Month"].astype(str)
    fig = px.line(chart_data, x="Month", y="Credit Amount", markers=True, title=title, labels={"Month": "Month", "Credit Amount": "Credit Amount"})
    fig.update_traces(line=dict(color="#FFC72C", width=2.6), marker=dict(size=6, color="#FFC72C"))
    fig.update_yaxes(tickprefix="£", separatethousands=True)
    fig.update_xaxes(type="category")
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


def _reason_enrichment(reason_table: pd.DataFrame, credit_df: pd.DataFrame) -> pd.DataFrame:
    if reason_table.empty or credit_df.empty or "Credit Reason" not in credit_df.columns:
        return reason_table
    rows = []
    for reason in reason_table["Reason"].tolist():
        subset = credit_df[credit_df["Credit Reason"].fillna("Unknown / 未知").eq(reason)]
        top_customer = "无 / N/A"
        top_product = "无 / N/A"
        if not subset.empty:
            if {"Customer Label", "Credit Amount"}.issubset(subset.columns):
                customer = subset.groupby("Customer Label", dropna=False)["Credit Amount"].sum().sort_values(ascending=False)
                if not customer.empty:
                    top_customer = str(customer.index[0])
            if {"Product Label", "Credit Amount"}.issubset(subset.columns):
                product = subset.groupby("Product Label", dropna=False)["Credit Amount"].sum().sort_values(ascending=False)
                if not product.empty:
                    top_product = str(product.index[0])
        rows.append({"Reason": reason, "Top Customer for Reason": top_customer, "Top Product for Reason": top_product})
    return reason_table.merge(pd.DataFrame(rows), on="Reason", how="left")


def _inject_credit_page_styles() -> None:
    st.markdown(
        """
        <style>
        .xf-credits-header {margin-bottom: 14px;}
        .xf-credits-title-cn {font-size: 30px; line-height: 1.12; font-weight: 750; color: var(--xf-text-primary);}
        .xf-credits-title-en {font-size: 15px; color: var(--xf-text-secondary); margin-top: 3px;}
        .xf-credits-subtitle-cn {font-size: 14px; color: var(--xf-text-secondary); margin-top: 10px;}
        .xf-credits-subtitle-en {font-size: 12px; color: var(--xf-text-muted); margin-top: 2px;}
        .xf-credit-summary {
            background: #fff;
            border: 1px solid var(--xf-border);
            border-left: 4px solid var(--xf-brand-primary);
            border-radius: var(--xf-radius-md);
            box-shadow: var(--xf-shadow-card);
            padding: 14px 16px;
            margin: 10px 0 14px 0;
        }
        .xf-credit-summary-title {font-size: 16px; font-weight: 700; margin-bottom: 8px;}
        .xf-credit-summary-body {display: grid; gap: 4px; color: var(--xf-text-primary); font-size: 14px; line-height: 1.45;}
        .xf-credit-summary-en {color: var(--xf-text-secondary); font-size: 12.5px;}
        .xf-credit-primary-grid,
        .xf-credit-secondary-grid {
            display: grid;
            gap: 10px;
            margin: 10px 0;
        }
        .xf-credit-primary-grid {grid-template-columns: repeat(4, minmax(0, 1fr));}
        .xf-credit-secondary-grid {grid-template-columns: repeat(4, minmax(0, 1fr));}
        .xf-credit-kpi {
            background: #fff;
            border: 1px solid var(--xf-border);
            border-radius: var(--xf-radius-md);
            padding: 13px 14px;
            min-height: 86px;
            box-shadow: var(--xf-shadow-card);
        }
        .xf-credit-kpi.emphasis {border-color: var(--xf-brand-primary); box-shadow: inset 3px 0 0 var(--xf-brand-primary), var(--xf-shadow-card);}
        .xf-credit-kpi.normal.emphasis {background: #f8fff9;}
        .xf-credit-kpi.watch.emphasis {background: #fffaf0;}
        .xf-credit-kpi.high.emphasis {background: #fff5f5;}
        .xf-credit-kpi-label {font-size: 13px; color: var(--xf-text-primary); font-weight: 650; line-height: 1.25;}
        .xf-credit-kpi-label span {color: var(--xf-text-secondary); font-weight: 500;}
        .xf-credit-kpi-value {font-size: 25px; font-weight: 760; margin-top: 13px; letter-spacing: 0;}
        .xf-credit-secondary-grid .xf-credit-kpi {min-height: 68px; padding: 10px 12px;}
        .xf-credit-secondary-grid .xf-credit-kpi-value {font-size: 18px; margin-top: 8px;}
        .xf-credit-date-panel {
            background: #fff;
            border: 1px solid var(--xf-border);
            border-radius: var(--xf-radius-md);
            padding: 10px 12px 2px 12px;
            margin: 12px 0 18px 0;
        }
        .xf-credit-note {color: var(--xf-text-secondary); font-size: 12.5px;}
        @media (max-width: 900px) {
            .xf-credit-primary-grid,
            .xf-credit-secondary-grid {grid-template-columns: repeat(2, minmax(0, 1fr));}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


inject_global_styles()
_inject_credit_page_styles()
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

summary_slot = st.container()
primary_kpi_slot = st.container()
secondary_kpi_slot = st.container()

with st.container():
    st.markdown('<div class="xf-credit-date-panel">', unsafe_allow_html=True)
    selected_range = render_date_range_inputs(
        "Credit Date",
        "returns_credits",
        dates.min().date(),
        dates.max().date(),
        legacy_key="returns_credits_date_range",
    )
    st.markdown("</div>", unsafe_allow_html=True)
if selected_range is None:
    st.stop()
start_date, end_date = selected_range
st.caption(f"当前 Credit Date 范围：{start_date} 至 {end_date}")

filtered_credit = filter_credit_by_date(credit_df, start_date, end_date)
period_sales = sales_for_credit_period(sales_df, start_date, end_date)
kpis = credit_kpis(filtered_credit, period_sales)

customer_table = aggregate_customer_credits(filtered_credit, period_sales)
product_table = aggregate_product_credits(filtered_credit, period_sales)
group_table = aggregate_product_group_credits(product_table)
reason_table = aggregate_credit_reasons(filtered_credit)
top_reason = _top_value(reason_table, "Reason", "Credit Amount")

with summary_slot:
    _render_executive_summary(kpis, group_table, customer_table, reason_table)

with primary_kpi_slot:
    _render_primary_kpis(kpis)

with secondary_kpi_slot:
    _render_secondary_kpis(kpis, top_reason)

trend = monthly_credit_trend(filtered_credit)
section_header("Monthly Credit Trend / 月度退款趋势")
if len(trend) >= 2:
    st.plotly_chart(_line_chart(trend, "Monthly Credit Trend / 月度退款趋势"), use_container_width=True)
else:
    st.info("暂无足够趋势数据 / Not enough monthly history to show a trend.")

section_header("Customer Credit Analysis / 客户退货分析")
customer_tabs = st.tabs(["Top Credit / 退款金额最高", "Highest Credit Rate / 高退款率", "Repeat Credits / 重复退款"])
with customer_tabs[0]:
    st.dataframe(_format_credit_table(_rename_customer_table(_add_low_sales_base_flag(customer_table.head(50)))), use_container_width=True, hide_index=True)
with customer_tabs[1]:
    high_rate = customer_table[customer_table["Gross Sales"].gt(0)].sort_values("Credit Rate", ascending=False).head(50)
    st.dataframe(_format_credit_table(_rename_customer_table(_add_low_sales_base_flag(high_rate))), use_container_width=True, hide_index=True)
with customer_tabs[2]:
    repeat_customers = customer_table[customer_table["Credit Note Count"].gt(1)].head(50)
    st.dataframe(_format_credit_table(_rename_customer_table(_add_low_sales_base_flag(repeat_customers))), use_container_width=True, hide_index=True)

section_header("Product Credit Analysis / 产品退货分析")
product_tabs = st.tabs(["Top Credit / 退款金额最高", "Highest Credit Rate / 高退款率"])
with product_tabs[0]:
    st.dataframe(_format_credit_table(_rename_product_table(product_table.head(50))), use_container_width=True, hide_index=True)
with product_tabs[1]:
    high_product_rate = product_table[product_table["Gross Sales"].gt(0)].sort_values("Credit Rate", ascending=False).head(50)
    st.dataframe(_format_credit_table(_rename_product_table(high_product_rate)), use_container_width=True, hide_index=True)

section_header("Product Group Credit Summary / 产品系列退款汇总")
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
reason_display = _reason_enrichment(reason_table, filtered_credit).rename(
    columns={
        "Reason": "原因 / Reason",
        "Credit Amount": "退款金额 / Credit Amount",
        "Credit %": "占比 / Credit %",
        "Credit Note Count": "退款单数 / Credit Count",
        "Top Customer for Reason": "主要客户 / Top Customer",
        "Top Product for Reason": "主要产品 / Top Product",
    }
)
st.dataframe(_format_credit_table(reason_display), use_container_width=True, hide_index=True)

with st.expander("Data Source / 数据来源", expanded=False):
    _render_credit_data_source()

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
