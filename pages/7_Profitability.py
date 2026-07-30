from __future__ import annotations

from html import escape
import logging

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.auth import require_login
from app.business_metrics import (
    aggregate_customer_profitability,
    aggregate_product_group_profitability,
    aggregate_product_profitability,
    cost_coverage_report,
    get_cached_business_metrics,
    invalid_unit_cost_rows,
    monthly_profitability,
    negative_gross_profit_transactions,
    product_margin_reconciliation,
    profitability_kpis,
    suspicious_unit_comparison,
)
from app.google_drive import DriveUserError, ensure_drive_data_loaded, load_drive_cost_snapshots, render_data_source_sidebar
from app.profitability_table_styles import (
    cost_to_sales_style,
    coverage_cell_style,
    margin_cell_style,
    profit_cell_style,
    status_cell_style,
    zero_value_cost_style,
)
from app.ui import inject_global_styles, money, percent, section_header, style_plotly


LOGGER = logging.getLogger(__name__)


def _money_or_na(value: object) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return money(float(value))


def _percent_or_na(value: object) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return percent(float(value))


def _money_compact(value: object) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    number = float(value)
    sign = "-" if number < 0 else ""
    absolute = abs(number)
    if absolute >= 1_000_000:
        return f"{sign}£{absolute / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{sign}£{absolute / 1_000:.1f}k"
    return money(number)


def _bilingual_label(chinese: str, english: str) -> str:
    return f"{chinese}\n{english}"


COLUMN_LABELS = {
    "Completed Date": _bilingual_label("完成日期", "Completed Date"),
    "Customer": _bilingual_label("客户", "Customer"),
    "Product Code": _bilingual_label("产品编码", "Product Code"),
    "Product Name": _bilingual_label("产品名称", "Product Name"),
    "Product Description": _bilingual_label("产品名称", "Product Name"),
    "Product Group": _bilingual_label("产品系列", "Product Group"),
    "Quantity": _bilingual_label("数量", "Quantity"),
    "Sales Amount": _bilingual_label("销售额", "Sales"),
    "Costed Sales": _bilingual_label("已核算销售额", "Costed Sales"),
    "Normal Sales": _bilingual_label("正常销售额", "Normal Sales"),
    "Costed Normal Sales": _bilingual_label("已核算销售额", "Costed Sales"),
    "Uncosted Sales": _bilingual_label("未核算销售额", "Uncosted Sales"),
    "Normal Cost": _bilingual_label("成本", "Cost"),
    "Unit Selling Price / ASP": _bilingual_label("平均销售单价", "ASP"),
    "Unit Selling Price": _bilingual_label("销售单价", "Unit Selling Price"),
    "Unit Cost": _bilingual_label("单位成本", "Unit Cost"),
    "Total Cost": _bilingual_label("总成本", "Total Cost"),
    "Gross Profit": _bilingual_label("毛利", "Gross Profit"),
    "Weighted Margin": _bilingual_label("毛利率", "Weighted Margin"),
    "Commercial Sales": _bilingual_label("正常销售额", "Normal Sales"),
    "Commercial Cost": _bilingual_label("成本", "Cost"),
    "Commercial Gross Profit": _bilingual_label("毛利", "Gross Profit"),
    "Commercial Gross Margin": _bilingual_label("毛利率", "Gross Margin"),
    "Zero-value Outbound Cost": _bilingual_label("赠品/零价成本", "Zero-value Cost"),
    "Business Profit": _bilingual_label("经营利润", "Business Profit"),
    "Contribution After Zero-value Cost": _bilingual_label("经营利润", "Business Profit"),
    "Margin %": _bilingual_label("毛利率", "Margin %"),
    "Cost Coverage": _bilingual_label("成本覆盖率", "Cost Coverage"),
    "Cost to Sales Ratio": _bilingual_label("成本销售比", "Cost to Sales"),
    "Gross Profit Contribution": _bilingual_label("毛利贡献", "Profit Contribution"),
    "Sales Contribution": _bilingual_label("销售贡献", "Sales Contribution"),
    "Cost Contribution": _bilingual_label("成本贡献", "Cost Contribution"),
    "Sales Rows": _bilingual_label("销售行数", "Sales Rows"),
    "Costed Rows": _bilingual_label("已匹配行数", "Costed Rows"),
    "Cost File Name": _bilingual_label("成本文件", "Cost File"),
    "Cost Match Status": _bilingual_label("成本匹配状态", "Cost Match Status"),
    "Cost Match Status summary": _bilingual_label("成本匹配摘要", "Cost Match Summary"),
    "Coverage Status": _bilingual_label("覆盖状态", "Coverage Status"),
    "Matched Sales Amount": _bilingual_label("已匹配销售额", "Matched Sales"),
}


def _rename_display_columns(table: pd.DataFrame) -> pd.DataFrame:
    return table.rename(columns={column: label for column, label in COLUMN_LABELS.items() if column in table.columns})


def _inject_profitability_styles() -> None:
    st.markdown(
        """
        <style>
        .xf-profit-header {margin-bottom: 1.25rem;}
        .xf-profit-title-cn {font-size: 2rem; font-weight: 720; color: #111827; line-height: 1.15;}
        .xf-profit-title-en {font-size: 1rem; color: #8b93a1; margin-top: 0.2rem;}
        .xf-profit-subtitle-cn {font-size: 1rem; color: #374151; margin-top: 0.8rem;}
        .xf-profit-subtitle-en {font-size: 0.9rem; color: #8b93a1; margin-top: 0.15rem;}
        .xf-profit-notice,
        .xf-profit-interpretation {
            border: 1px solid #e5e7eb;
            border-left: 4px solid #ffc72c;
            border-radius: 10px;
            background: #fffdf5;
            padding: 1rem 1.1rem;
            margin: 1rem 0 1.2rem;
        }
        .xf-profit-interpretation {background: #ffffff; border-left-color: #64748b;}
        .xf-profit-card-title {font-weight: 700; color: #111827; margin-bottom: 0.35rem;}
        .xf-profit-card-subtitle {color: #8b93a1; font-size: 0.86rem; margin-bottom: 0.75rem;}
        .xf-profit-card-body {color: #374151; line-height: 1.7;}
        .xf-profit-card-body-en {color: #8b93a1; line-height: 1.6; margin-top: 0.8rem; font-size: 0.9rem;}
        .xf-profit-kpi-grid {
            display: grid;
            grid-template-columns: repeat(6, minmax(0, 1fr));
            gap: 0.9rem;
            margin: 1rem 0 1.2rem;
        }
        .xf-profit-kpi {
            border: 1px solid #e5e7eb;
            border-radius: 10px;
            background: #ffffff;
            padding: 0.95rem 1rem;
            min-height: 126px;
        }
        .xf-profit-kpi.featured {border-color: #f2c94c; box-shadow: inset 0 3px 0 #ffc72c;}
        .xf-profit-kpi-cn {font-size: 0.96rem; font-weight: 700; color: #111827;}
        .xf-profit-kpi-en {font-size: 0.78rem; color: #9ca3af; margin-top: 0.1rem;}
        .xf-profit-kpi-value {font-size: 1.55rem; font-weight: 760; color: #111827; margin-top: 0.8rem; line-height: 1.1;}
        .xf-profit-section-caption {color: #8b93a1; font-size: 0.9rem; margin-top: -0.35rem; margin-bottom: 0.9rem;}
        @media (max-width: 1200px) {
            .xf-profit-kpi-grid {grid-template-columns: repeat(3, minmax(0, 1fr));}
        }
        @media (max-width: 760px) {
            .xf-profit-kpi-grid {grid-template-columns: repeat(2, minmax(0, 1fr));}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_page_header() -> None:
    st.markdown(
        """
        <div class="xf-profit-header">
            <div class="xf-profit-title-cn">利润分析</div>
            <div class="xf-profit-title-en">Profitability</div>
            <div class="xf-profit-subtitle-cn">查看销售毛利、成本覆盖率及利润趋势</div>
            <div class="xf-profit-subtitle-en">Review gross profit, cost coverage and profitability trends.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_notice(kpis: dict[str, float | None]) -> None:
    coverage_text = _percent_or_na(kpis.get("Cost Coverage"))
    st.markdown(
        f"""
        <div class="xf-profit-notice">
            <div class="xf-profit-card-title">经营提醒</div>
            <div class="xf-profit-card-subtitle">Business Notice</div>
            <div class="xf-profit-card-body">
                当前成本覆盖率：<strong>{escape(coverage_text)}</strong><br>
                由于历史成本尚未全部补齐，当前利润仅代表已匹配成本销售，不能作为整体经营利润。<br>
                赠品/零价出库不计入毛利率，但相关商品成本不会被忽略。营销赠品、客户补偿、仓库借用及其他零价出库将在数据验证页面中单独核查。
            </div>
            <div class="xf-profit-card-body-en">
                Current profitability is calculated only for sales matched with available cost snapshots.
                Zero-value outbound transactions are excluded from gross margin, while their product costs remain visible for validation and operating profit analysis.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_kpis(kpis: dict[str, float | None]) -> None:
    cards = [
        ("销售额", "Total Sales", _money_compact(kpis["Total Sales"]), False),
        ("已核算正常销售额", "Costed Normal Sales", _money_compact(kpis["Costed Normal Sales"]), False),
        ("毛利", "Gross Profit", _money_compact(kpis["Commercial Gross Profit"]), True),
        ("毛利率", "Gross Margin", _percent_or_na(kpis["Commercial Gross Margin"]), True),
        ("赠品/零价成本", "Zero-value Cost", _money_compact(kpis["Zero-value Outbound Cost"]), True),
        (
            "经营利润",
            "Operating Profit",
            _money_compact(kpis["Contribution After Zero-value Cost"]),
            True,
        ),
        ("成本覆盖率", "Cost Coverage", _percent_or_na(kpis["Cost Coverage"]), True),
        ("未核算销售额", "Uncosted Sales", _money_compact(kpis["Uncosted Sales"]), False),
    ]
    html = []
    for chinese, english, value, featured in cards:
        feature_class = " featured" if featured else ""
        html.append(
            f'<div class="xf-profit-kpi{feature_class}">'
            f'<div class="xf-profit-kpi-cn">{escape(chinese)}</div>'
            f'<div class="xf-profit-kpi-en">{escape(english)}</div>'
            f'<div class="xf-profit-kpi-value">{escape(value)}</div>'
            "</div>"
        )
    st.markdown(f'<div class="xf-profit-kpi-grid">{"".join(html)}</div>', unsafe_allow_html=True)


def _render_interpretation(kpis: dict[str, float | None]) -> None:
    coverage_text = _percent_or_na(kpis.get("Cost Coverage"))
    costed_sales = _money_or_na(kpis.get("Costed Normal Sales"))
    uncosted_sales = _money_or_na(kpis.get("Uncosted Sales"))
    margin_text = _percent_or_na(kpis.get("Commercial Gross Margin"))
    zero_cost = _money_or_na(kpis.get("Zero-value Outbound Cost"))
    contribution = _money_or_na(kpis.get("Contribution After Zero-value Cost"))
    st.markdown(
        f"""
        <div class="xf-profit-interpretation">
            <div class="xf-profit-card-title">经营解读</div>
            <div class="xf-profit-card-subtitle">Business Interpretation</div>
            <div class="xf-profit-card-body">
                当前成本覆盖率为 <strong>{escape(coverage_text)}</strong>，目前利润只能覆盖 <strong>{escape(coverage_text)}</strong> 的销售额。<br>
                已核算正常销售额为 <strong>{escape(costed_sales)}</strong>，未核算销售额为 <strong>{escape(uncosted_sales)}</strong>。<br>
                当前毛利率为 <strong>{escape(margin_text)}</strong>。<br>
                赠品/零价成本为 <strong>{escape(zero_cost)}</strong>，扣除后的经营利润为 <strong>{escape(contribution)}</strong>。<br>
                建议先补齐历史成本快照，再进行经营决策。
            </div>
            <div class="xf-profit-card-body-en">
                Current profitability covers only normal sales matched with cost snapshots.
                Gross margin excludes zero-value outbound transactions; their costs remain visible and are deducted from operating profit.
                Please complete historical cost snapshots before using profitability for business decisions.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _completed_date_filter(data: pd.DataFrame) -> pd.DataFrame:
    filtered = data
    dates = pd.to_datetime(data["Completed Date"], errors="coerce")
    min_date = dates.dropna().min()
    max_date = dates.dropna().max()
    with st.sidebar:
        st.markdown("### 利润筛选")
        st.caption("Profitability Filters")
        if pd.notna(min_date) and pd.notna(max_date):
            selected = st.date_input(
                "完成日期范围 / Completed Date",
                value=(min_date.date(), max_date.date()),
                min_value=min_date.date(),
                max_value=max_date.date(),
                key="profitability_completed_date_range",
            )
            if isinstance(selected, tuple) and len(selected) == 2:
                start, end = pd.Timestamp(selected[0]), pd.Timestamp(selected[1])
                normalized = dates.dt.normalize()
                filtered = data.loc[normalized.between(start, end, inclusive="both")]

        product_groups = sorted(filtered["Product Group"].fillna("未分类").astype(str).unique().tolist()) if "Product Group" in filtered.columns else []
        with st.expander("产品系列 / Product Group", expanded=False):
            selected_groups = st.multiselect("产品系列", product_groups, default=product_groups, key="profitability_product_group")
        if selected_groups and len(selected_groups) < len(product_groups):
            filtered = filtered[filtered["Product Group"].fillna("未分类").astype(str).isin(selected_groups)]

        product_label_col = "Product Label" if "Product Label" in filtered.columns else "Product"
        product_options = sorted(filtered[product_label_col].fillna("Unknown").astype(str).unique().tolist()) if product_label_col in filtered.columns else []
        with st.expander("产品 / Product", expanded=False):
            selected_products = st.multiselect("产品", product_options, default=[], key="profitability_products")
            st.caption("留空表示全部产品。")
        if selected_products:
            filtered = filtered[filtered[product_label_col].fillna("Unknown").astype(str).isin(selected_products)]

        customer_label_col = "Customer Label" if "Customer Label" in filtered.columns else "Customer"
        customer_options = sorted(filtered[customer_label_col].fillna("Unknown").astype(str).unique().tolist()) if customer_label_col in filtered.columns else []
        with st.expander("客户 / Customer", expanded=False):
            selected_customers = st.multiselect("客户", customer_options, default=[], key="profitability_customers")
            st.caption("留空表示全部客户。")
        if selected_customers:
            filtered = filtered[filtered[customer_label_col].fillna("Unknown").astype(str).isin(selected_customers)]

        salesperson_candidates = [column for column in ["Salesperson", "Sales Person", "Sales Rep", "Account Manager"] if column in filtered.columns]
        if salesperson_candidates:
            sales_col = salesperson_candidates[0]
            salesperson_options = sorted(filtered[sales_col].fillna("Unknown").astype(str).unique().tolist())
            with st.expander("销售负责人 / Salesperson", expanded=False):
                selected_salespeople = st.multiselect("销售负责人", salesperson_options, default=salesperson_options, key="profitability_salesperson")
            if selected_salespeople and len(selected_salespeople) < len(salesperson_options):
                filtered = filtered[filtered[sales_col].fillna("Unknown").astype(str).isin(selected_salespeople)]

        statuses = sorted(filtered["Cost Match Status"].fillna("Unknown").astype(str).unique().tolist())
        with st.expander("成本匹配状态 / Cost Match Status", expanded=False):
            selected_statuses = st.multiselect("成本匹配状态", statuses, default=statuses, key="profitability_cost_status")
        if selected_statuses and len(selected_statuses) < len(statuses):
            filtered = filtered[filtered["Cost Match Status"].fillna("Unknown").astype(str).isin(selected_statuses)]
    return filtered


MONEY_COLUMNS = {
    "Sales Amount",
    "Costed Sales",
    "Normal Sales",
    "Costed Normal Sales",
    "Uncosted Sales",
    "Unit Selling Price / ASP",
    "Unit Selling Price",
    "Unit Cost",
    "Total Cost",
    "Gross Profit",
    "Normal Cost",
    "Commercial Sales",
    "Commercial Cost",
    "Commercial Gross Profit",
    "Zero-value Outbound Cost",
    "Business Profit",
    "Contribution After Zero-value Cost",
    "Matched Sales Amount",
}

PERCENT_COLUMNS = {
    "Weighted Margin",
    "Commercial Gross Margin",
    "Margin %",
    "Cost Coverage",
    "Cost to Sales Ratio",
    "Gross Profit Contribution",
    "Sales Contribution",
    "Cost Contribution",
}

PRODUCT_PROFITABILITY_COLUMN_ORDER = [
    "Product Code",
    "Product Description",
    "Product Group",
    "Sales Amount",
    "Costed Normal Sales",
    "Commercial Gross Profit",
    "Commercial Gross Margin",
    "Cost Coverage",
    "Quantity",
    "Unit Selling Price / ASP",
    "Unit Cost",
    "Total Cost",
    "Zero-value Outbound Cost",
    "Business Profit",
]

TABLE_DROP_PREFERENCES = [
    ("Commercial Sales", "Normal Sales"),
    ("Commercial Cost", "Normal Cost"),
    ("Gross Profit", "Commercial Gross Profit"),
    ("Weighted Margin", "Commercial Gross Margin"),
    ("Contribution After Zero-value Cost", "Business Profit"),
    ("Costed Sales", "Costed Normal Sales"),
]


def _column_labels(columns: pd.Index) -> dict[str, str]:
    labels: dict[str, str] = {}
    used: set[str] = set()
    for column in columns:
        label = COLUMN_LABELS.get(column, column)
        if label in used:
            label = f"{label}\n{column}"
        used.add(label)
        labels[column] = label
    return labels


def _prepare_display_table(table: pd.DataFrame, column_order: list[str] | None = None) -> pd.DataFrame:
    display = table.copy()
    for drop_column, preferred_column in TABLE_DROP_PREFERENCES:
        if drop_column in display.columns and preferred_column in display.columns:
            display = display.drop(columns=[drop_column])
    if column_order:
        ordered = [column for column in column_order if column in display.columns]
        remaining = [column for column in display.columns if column not in ordered]
        display = display.loc[:, ordered + remaining]
    return display.reset_index(drop=True)


def _date_or_blank(value: object) -> str:
    if pd.isna(value):
        return ""
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%Y-%m-%d")


def _format_table(table: pd.DataFrame, column_order: list[str] | None = None) -> pd.DataFrame:
    display = _prepare_display_table(table, column_order)
    for column in [column for column in MONEY_COLUMNS if column in display.columns]:
        display[column] = display[column].map(_money_or_na)
    for column in [column for column in PERCENT_COLUMNS if column in display.columns]:
        display[column] = display[column].map(_percent_or_na)
    if "Quantity" in display.columns:
        display["Quantity"] = pd.to_numeric(display["Quantity"], errors="coerce").map(lambda value: "" if pd.isna(value) else f"{value:,.2f}")
    if "Completed Date" in display.columns:
        display["Completed Date"] = pd.to_datetime(display["Completed Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return display.rename(columns=_column_labels(display.columns))


def _style_profitability_table(table: pd.DataFrame, column_order: list[str] | None = None):
    raw_display = _prepare_display_table(table, column_order)
    labels = _column_labels(raw_display.columns)
    display = raw_display.rename(columns=labels)
    styled = display.style

    def labels_for(columns: set[str]) -> list[str]:
        return [labels[column] for column in columns if column in raw_display.columns and labels[column] in display.columns]

    money_columns = labels_for(MONEY_COLUMNS)
    percent_columns = labels_for(PERCENT_COLUMNS)
    quantity_columns = labels_for({"Quantity"})
    date_columns = labels_for({"Completed Date"})
    formatters = {column: _money_or_na for column in money_columns}
    formatters.update({column: _percent_or_na for column in percent_columns})
    formatters.update({column: (lambda value: "" if pd.isna(value) else f"{float(value):,.2f}") for column in quantity_columns})
    formatters.update({column: _date_or_blank for column in date_columns})
    if formatters:
        styled = styled.format(formatters, na_rep="N/A")

    profit_columns = labels_for({"Gross Profit", "Commercial Gross Profit"})
    margin_columns = labels_for({"Weighted Margin", "Commercial Gross Margin", "Margin %"})
    coverage_columns = labels_for({"Cost Coverage"})
    cost_to_sales_columns = labels_for({"Cost to Sales Ratio"})
    zero_cost_columns = labels_for({"Zero-value Outbound Cost"})
    status_columns = labels_for({"Cost Match Status"})
    if profit_columns:
        styled = styled.map(profit_cell_style, subset=profit_columns)
    if margin_columns:
        styled = styled.map(margin_cell_style, subset=margin_columns)
    if coverage_columns:
        styled = styled.map(coverage_cell_style, subset=coverage_columns)
    if cost_to_sales_columns:
        styled = styled.map(cost_to_sales_style, subset=cost_to_sales_columns)
    if zero_cost_columns:
        styled = styled.map(zero_value_cost_style, subset=zero_cost_columns)
    if status_columns:
        styled = styled.map(status_cell_style, subset=status_columns)
    return styled


def _render_profitability_table(table: pd.DataFrame, column_order: list[str] | None = None, **kwargs) -> None:
    try:
        st.dataframe(_style_profitability_table(table, column_order), use_container_width=True, hide_index=True, **kwargs)
    except Exception:
        LOGGER.warning("Profitability table styling failed; falling back to plain table", exc_info=True)
        st.dataframe(_format_table(table, column_order), use_container_width=True, hide_index=True, **kwargs)


def _profitability_summary(table: pd.DataFrame, entity_column: str, label: str) -> None:
    if table.empty:
        return
    if "Commercial Gross Profit" in table.columns:
        gp = pd.to_numeric(table["Commercial Gross Profit"], errors="coerce")
    else:
        gp = pd.Series(pd.NA, index=table.index, dtype="Float64")
    positive_count = int((gp > 0).sum())
    negative_count = int((gp < 0).sum())
    entity = table.get(entity_column, pd.Series(["N/A"] * len(table), index=table.index)).fillna("N/A").astype(str)
    highest = "N/A"
    lowest = "N/A"
    if gp.notna().any():
        highest = entity.loc[gp.idxmax()]
        lowest = entity.loc[gp.idxmin()]
    st.caption(
        f"{label}摘要：共 {len(table):,} 项｜正毛利 {positive_count:,}｜负毛利 {negative_count:,}｜"
        f"毛利最高：{highest}｜毛利最低：{lowest}"
    )


def _amount_trend_chart(trend: pd.DataFrame):
    fig = go.Figure()
    series = [
        ("Costed Normal Sales", "已核算正常销售额 / Costed Normal Sales", "#FFC72C"),
        ("Commercial Gross Profit", "毛利 / Gross Profit", "#2E8B57"),
        ("Zero-value Outbound Cost", "赠品/零价成本 / Zero-value Cost", "#8B93A1"),
        ("Business Profit", "经营利润 / Business Profit", "#374151"),
    ]
    for column, label, color in series:
        fig.add_trace(go.Scatter(x=trend["Month"], y=trend[column], mode="lines+markers", name=label, line=dict(color=color)))
    fig.update_yaxes(tickprefix="£", separatethousands=True)
    fig.update_layout(title="利润金额趋势 / Profitability Trend", height=340)
    return style_plotly(fig)


def _ratio_trend_chart(trend: pd.DataFrame):
    fig = go.Figure()
    series = [
        ("Commercial Gross Margin", "毛利率 / Gross Margin", "#2E8B57"),
        ("Cost Coverage", "成本覆盖率 / Cost Coverage", "#8B93A1"),
    ]
    for column, label, color in series:
        fig.add_trace(go.Scatter(x=trend["Month"], y=trend[column], mode="lines+markers", name=label, line=dict(color=color)))
    fig.update_yaxes(tickformat=".1%")
    fig.update_layout(title="毛利率与成本覆盖率趋势 / Margin & Cost Coverage", height=340)
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
        "Commercial Gross Profit",
        "Commercial Gross Margin",
        "Zero-value Outbound Cost",
        "Margin %",
        "Cost File Name",
        "Cost Match Status",
    ]
    available = [column for column in columns if column in data.columns]
    display = data[available].copy()
    return display.reset_index(drop=True)


def _product_profitability_filters(data: pd.DataFrame) -> pd.DataFrame:
    if data.empty:
        return data
    filtered_data = data
    group_col = "Product Group"
    product_col = "Product Label" if "Product Label" in filtered_data.columns else "Product"
    customer_col = "Customer Label" if "Customer Label" in filtered_data.columns else "Customer"

    st.markdown("**产品利润筛选 / Product Profitability Filters**")
    group_options = (
        sorted(filtered_data[group_col].fillna("未分类").astype(str).unique().tolist())
        if group_col in filtered_data.columns
        else []
    )
    selected_groups: list[str] = []
    group_box, product_box, customer_box = st.columns(3)
    with group_box:
        if group_options:
            selected_groups = st.multiselect(
                "产品系列 / Product Group",
                group_options,
                default=group_options,
                key="product_profitability_group_filter",
            )
            if selected_groups and len(selected_groups) < len(group_options):
                filtered_data = filtered_data[filtered_data[group_col].fillna("未分类").astype(str).isin(selected_groups)]
        else:
            st.caption("暂无产品系列字段。")

    product_options = (
        sorted(filtered_data[product_col].fillna("Unknown").astype(str).unique().tolist())
        if product_col in filtered_data.columns
        else []
    )
    selected_products: list[str] = []
    with product_box:
        if product_options:
            selected_products = st.multiselect(
                "产品 / Product",
                product_options,
                default=[],
                key="product_profitability_product_filter",
                placeholder="全部产品",
            )
            if selected_products:
                filtered_data = filtered_data[filtered_data[product_col].fillna("Unknown").astype(str).isin(selected_products)]
        else:
            st.caption("暂无产品字段。")

    customer_options = (
        sorted(filtered_data[customer_col].fillna("Unknown").astype(str).unique().tolist())
        if customer_col in filtered_data.columns
        else []
    )
    selected_customers: list[str] = []
    with customer_box:
        if customer_options:
            selected_customers = st.multiselect(
                "客户 / Customer",
                customer_options,
                default=[],
                key="product_profitability_customer_filter",
                placeholder="全部客户",
            )
            if selected_customers:
                filtered_data = filtered_data[filtered_data[customer_col].fillna("Unknown").astype(str).isin(selected_customers)]
        else:
            st.caption("暂无客户字段。")
    return filtered_data


inject_global_styles()
_inject_profitability_styles()
require_login("margin")

_render_page_header()

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
    st.info("当前没有可用成本快照。页面会保留销售数据，但无法计算毛利。")

metrics_df = get_cached_business_metrics(sales_df, cost_snapshots)
filtered = _completed_date_filter(metrics_df)

with st.expander("数据口径说明 / Calculation Method", expanded=False):
    st.markdown(
        """
        销售额来自已完成订单。

        成本按订单完成日期匹配历史成本版本。

        未匹配成本的销售不会参与利润计算。

        毛利率仅基于 Sales Amount > 0 的正常销售计算。

        赠品/零价出库不会稀释毛利率，但其成本会单独反映，并从经营利润中扣除。

        ---

        Sales amount comes from completed orders.

        Costs are matched to historical cost snapshots by Completed Date.

        Sales without matched costs are retained, but excluded from profit calculation.

        Gross margin is calculated only from Sales Amount > 0 transactions.

        Zero-value outbound transactions do not dilute gross margin, but their costs remain visible and are deducted from operating profit.
        """
    )
    if registry is not None:
        st.caption(f"成本快照数：{len(cost_snapshots)}")
        for entry in registry.entries:
            st.caption(f"{entry.file_name}: {entry.validation_status}; warnings={len(entry.warnings)}; errors={len(entry.errors)}")

kpis = profitability_kpis(filtered)
coverage = cost_coverage_report(filtered)
_render_notice(kpis)
_render_kpis(kpis)
_render_interpretation(kpis)

section_header("利润趋势")
st.markdown('<div class="xf-profit-section-caption">Profit Trend</div>', unsafe_allow_html=True)
trend = monthly_profitability(filtered)
if trend.empty:
    st.info("当前筛选范围内没有趋势数据。")
else:
    left, right = st.columns(2)
    with left:
        st.plotly_chart(_amount_trend_chart(trend), width="stretch")
    with right:
        st.plotly_chart(_ratio_trend_chart(trend), width="stretch")

section_header("产品利润")
st.markdown('<div class="xf-profit-section-caption">Product Profitability</div>', unsafe_allow_html=True)
product_profitability_data = _product_profitability_filters(filtered)
product_table = aggregate_product_profitability(product_profitability_data)
if product_table.empty:
    st.info("当前筛选范围内没有产品利润数据。")
else:
    sort_options = {
        "销售额 / Sales": "Sales Amount",
        "毛利 / Gross Profit": "Commercial Gross Profit",
        "毛利率 / Gross Margin": "Commercial Gross Margin",
        "成本覆盖率 / Cost Coverage": "Cost Coverage",
    }
    sort_option = st.selectbox(
        "产品表排序 / Product table sort",
        list(sort_options.keys()),
        index=0,
    )
    sort_column = sort_options[sort_option]
    ascending = sort_column in ["Commercial Gross Margin", "Cost Coverage"]
    sorted_product_table = product_table.sort_values(sort_column, ascending=ascending, na_position="last")
    _profitability_summary(product_table, "Product Code", "产品利润")
    _render_profitability_table(sorted_product_table.head(200), PRODUCT_PROFITABILITY_COLUMN_ORDER)

section_header("客户利润")
st.markdown('<div class="xf-profit-section-caption">Customer Profitability</div>', unsafe_allow_html=True)
customer_table = aggregate_customer_profitability(filtered)
if customer_table.empty:
    st.info("当前筛选范围内没有客户利润数据。")
else:
    _profitability_summary(customer_table, "Customer", "客户利润")
    _render_profitability_table(customer_table.head(200))

section_header("产品系列利润")
st.markdown('<div class="xf-profit-section-caption">Product Group Profitability</div>', unsafe_allow_html=True)
group_table = aggregate_product_group_profitability(filtered)
if group_table.empty:
    st.info("当前筛选范围内没有产品系列利润数据。")
else:
    _profitability_summary(group_table, "Product Group", "产品系列利润")
    _render_profitability_table(group_table.head(200))

section_header("毛利核对")
st.markdown('<div class="xf-profit-section-caption">Margin Reconciliation</div>', unsafe_allow_html=True)
if not product_table.empty:
    with st.expander("A. 低毛利产品 / Lowest Margin Products", expanded=True):
        lowest = product_table[product_table["Commercial Gross Margin"].notna()].sort_values("Commercial Gross Margin", ascending=True).head(30)
        _render_profitability_table(lowest, PRODUCT_PROFITABILITY_COLUMN_ORDER)

    with st.expander("B. 高成本销售比产品 / Highest Cost-to-Sales Products", expanded=False):
        cost_to_sales = product_table.copy()
        cost_to_sales["Cost to Sales Ratio"] = pd.to_numeric(cost_to_sales["Total Cost"], errors="coerce") / pd.to_numeric(
            cost_to_sales["Sales Amount"], errors="coerce"
        )
        _render_profitability_table(cost_to_sales.sort_values("Cost to Sales Ratio", ascending=False).head(30), PRODUCT_PROFITABILITY_COLUMN_ORDER)

section_header("异常交易")
st.markdown('<div class="xf-profit-section-caption">Exception Transactions</div>', unsafe_allow_html=True)
negative = negative_gross_profit_transactions(filtered)
with st.expander("C. 负毛利交易 / Negative Gross Profit Transactions", expanded=True):
    if negative.empty:
        st.caption("当前筛选范围内没有负毛利交易。")
    else:
        _render_profitability_table(_transaction_columns(negative.sort_values("Gross Profit").head(300)))

suspicious = suspicious_unit_comparison(filtered)
with st.expander("D. 单位与价格疑点 / Suspicious Unit Comparison", expanded=True):
    if suspicious.empty:
        st.caption("当前筛选范围内没有命中单位/售价诊断规则的交易。")
    else:
        display = _transaction_columns(suspicious.head(500))
        if "Suspicion Reason" in suspicious.columns:
            display[_bilingual_label("异常原因", "Exception Reason")] = suspicious["Suspicion Reason"].head(500).to_list()
        _render_profitability_table(display)

invalid_cost = invalid_unit_cost_rows(filtered)
with st.expander("E. 无效单位成本 / Invalid Unit Cost", expanded=True):
    if invalid_cost.empty:
        st.caption("当前筛选范围内没有 Invalid Unit Cost 行。")
    else:
        _render_profitability_table(_transaction_columns(invalid_cost.head(300)))

with st.expander("F. 产品毛利贡献核对 / Product Margin Reconciliation", expanded=True):
    reconciliation = product_margin_reconciliation(filtered)
    if reconciliation.empty:
        st.caption("当前筛选范围内没有产品贡献拆解。")
    else:
        _render_profitability_table(reconciliation.head(50))

with st.expander("成本覆盖报告 / Coverage Report", expanded=False):
    st.json(coverage)
