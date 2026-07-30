import pandas as pd
import streamlit as st

st.set_page_config(page_title="数据验证", layout="wide")

from app.auth import require_login
from app.business_metrics import get_cached_business_metrics
from app.data_validation import (
    SIGN_OFF_CHECKLIST,
    VALIDATION_STATUS_OPTIONS,
    add_business_validation_status,
    coverage_by_dimension,
    coverage_by_month,
    coverage_summary,
    gift_free_of_charge_rows,
    gift_free_of_charge_summary,
    margin_band_analysis,
    profitability_readiness_score,
    top_exceptions,
    unit_validation_rows,
    zero_value_outbound_rows,
    zero_value_outbound_summary,
)
from app.google_drive import DriveUserError, ensure_drive_data_loaded, load_drive_cost_snapshots, render_data_source_sidebar
from app.ui import inject_global_styles, money, percent, section_header


def _money_or_na(value: object) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return money(float(value))


def _percent_or_na(value: object) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return percent(float(value))


def _bilingual(chinese: str, english: str) -> str:
    return f"{chinese}\n{english}"


COLUMN_LABELS = {
    "Completed Date": _bilingual("完成日期", "Completed Date"),
    "Customer": _bilingual("客户", "Customer"),
    "Customer Label": _bilingual("客户", "Customer"),
    "Product Code": _bilingual("产品编码", "Product Code"),
    "Product Name": _bilingual("产品名称", "Product Name"),
    "Product Group": _bilingual("产品系列", "Product Group"),
    "Quantity": _bilingual("数量", "Quantity"),
    "Sales Amount": _bilingual("销售额", "Sales Amount"),
    "Unit Selling Price": _bilingual("销售单价", "Unit Selling Price"),
    "Unit Cost": _bilingual("单位成本", "Unit Cost"),
    "Total Cost": _bilingual("出库成本", "Outbound Cost"),
    "Gross Profit": _bilingual("毛利", "Gross Profit"),
    "Margin %": _bilingual("毛利率", "Margin %"),
    "Cost Match Status": _bilingual("成本匹配状态", "Cost Match Status"),
    "Business Validation Status": _bilingual("业务验证状态", "Business Validation Status"),
    "Validation Reason": _bilingual("验证原因", "Validation Reason"),
    "Zero-value Reason": _bilingual("零价原因", "Zero-value Reason"),
    "Zero-value Validation Status": _bilingual("验证状态", "Validation Status"),
    "Zero-value Recommended Action": _bilingual("建议处理", "Recommended Action"),
    "Rows Coverage": _bilingual("行数覆盖率", "Rows Coverage"),
    "Sales Coverage": _bilingual("销售成本覆盖率", "Sales Coverage"),
    "SKU Coverage": _bilingual("SKU 覆盖率", "SKU Coverage"),
    "Total Sales": _bilingual("销售额", "Total Sales"),
    "Costed Sales": _bilingual("已匹配成本销售额", "Costed Sales"),
    "Total Rows": _bilingual("总行数", "Total Rows"),
    "Costed Rows": _bilingual("已匹配行数", "Costed Rows"),
    "Margin Band": _bilingual("毛利率区间", "Margin Band"),
    "Rows": _bilingual("记录数", "Rows"),
    "Sales": _bilingual("销售额", "Sales"),
    "Product Count": _bilingual("产品数", "Product Count"),
}


GRADE_LABELS = {
    "Ready": "已就绪\nReady",
    "Needs Review": "需要复核\nNeeds Review",
    "Not Ready": "暂未就绪\nNot Ready",
}

ZERO_VALUE_REASON_LABELS = {
    "Marketing / Sample": "营销赠品 / 样品\nMarketing / Sample",
    "Customer Compensation": "客户补偿\nCustomer Compensation",
    "Warehouse Loan": "仓库借用 / 待归还\nWarehouse Loan",
    "Internal Use": "内部领用\nInternal Use",
    "Stock Write-off": "库存报损\nStock Write-off",
    "Data Error": "数据错误\nData Error",
    "Unclassified": "待分类\nUnclassified",
}

ZERO_VALUE_STATUS_LABELS = {
    "Pending Business Review": "待业务确认\nPending Business Review",
    "Verified": "已确认\nVerified",
}


def _format_table(data: pd.DataFrame) -> pd.DataFrame:
    display = data.copy()
    for column in [
        "Sales Amount",
        "Sales",
        "Zero-value Sales",
        "Zero-value Outbound Cost",
        "Unit Cost",
        "Unit Selling Price",
        "Total Cost",
        "Gross Profit",
        "Costed Sales",
        "Total Sales",
    ]:
        if column in display.columns:
            display[column] = pd.to_numeric(display[column], errors="coerce").map(_money_or_na)
    for column in ["Rows Coverage", "Sales Coverage", "SKU Coverage", "Margin %", "Cost Coverage"]:
        if column in display.columns:
            display[column] = pd.to_numeric(display[column], errors="coerce").map(_percent_or_na)
    if "Quantity" in display.columns:
        display["Quantity"] = pd.to_numeric(display["Quantity"], errors="coerce").map(lambda value: "" if pd.isna(value) else f"{value:,.2f}")
    if "Zero-value Reason" in display.columns:
        display["Zero-value Reason"] = display["Zero-value Reason"].map(lambda value: ZERO_VALUE_REASON_LABELS.get(str(value), str(value)))
    if "Zero-value Validation Status" in display.columns:
        display["Zero-value Validation Status"] = display["Zero-value Validation Status"].map(
            lambda value: ZERO_VALUE_STATUS_LABELS.get(str(value), str(value))
        )
    return display.rename(columns={column: label for column, label in COLUMN_LABELS.items() if column in display.columns})


def _validation_columns(data: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Completed Date",
        "Customer",
        "Product Code",
        "Product Name",
        "Product Group",
        "Quantity",
        "Sales Amount",
        "Unit Selling Price",
        "Unit Cost",
        "Total Cost",
        "Gross Profit",
        "Margin %",
        "Cost Match Status",
        "Business Validation Status",
        "Validation Reason",
        "Zero-value Reason",
        "Zero-value Validation Status",
        "Zero-value Recommended Action",
    ]
    available = [column for column in columns if column in data.columns]
    display = data[available].copy()
    if "Completed Date" in display.columns:
        display["Completed Date"] = pd.to_datetime(display["Completed Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return _format_table(display)


inject_global_styles()
require_login("data_validation")

st.title("数据验证")
st.caption("Data Validation")
st.markdown("**利润数据业务验证与签核**  \nBusiness Validation & Sign-off for Profitability")

ensure_drive_data_loaded()
render_data_source_sidebar(show_uploaders=False)

sales_df = st.session_state.get("clean_data")
if sales_df is None:
    st.info("当前暂无销售数据，请先加载销售数据。")
    st.stop()

try:
    registry, cost_snapshots = load_drive_cost_snapshots(force=False)
except DriveUserError as exc:
    st.warning(str(exc))
    registry, cost_snapshots = None, []

metrics = get_cached_business_metrics(sales_df, cost_snapshots)
metrics = add_business_validation_status(metrics)

score = profitability_readiness_score(metrics)
summary = coverage_summary(metrics)
zero_summary = zero_value_outbound_summary(metrics)
unit_rows = unit_validation_rows(metrics)

st.info(
    "此页面仅供 Admin / Finance 内部验证使用。在完成业务签核前，利润数据不建议作为老板视图或正式经营结论。\n\n"
    "This page is intended for internal Admin / Finance validation. Profitability should not be treated as an executive business result before business sign-off."
)

cols = st.columns(5)
cols[0].metric("利润就绪评分\nReadiness Score", f"{score.score:.1f}/100")
cols[1].metric("验证状态\nValidation Status", GRADE_LABELS.get(score.grade, score.grade))
cols[2].metric("销售成本覆盖率\nSales Cost Coverage", _percent_or_na(summary["Sales Coverage"]))
cols[3].metric("零价出库记录\nZero-value Rows", f"{zero_summary['Zero-value Rows']:,}")
cols[4].metric("无效成本 / 单位风险\nInvalid Cost / Unit Risk", f"{len(unit_rows):,}")

with st.expander("业务验证状态 / Business Validation Status", expanded=False):
    st.write(pd.DataFrame({_bilingual("业务验证状态", "Business Validation Status"): VALIDATION_STATUS_OPTIONS}))
    st.caption("本阶段仅设计和自动标记状态，不开发人工编辑功能。")
    st.caption("This phase only designs and auto-tags validation status. Manual editing is not included.")

section_header("零价出库验证", "Zero-value Outbound Validation")
zero_rows = zero_value_outbound_rows(metrics)
zero_cols = st.columns(4)
zero_cols[0].metric("零价出库记录\nZero-value Rows", f"{zero_summary['Zero-value Rows']:,}")
zero_cols[1].metric("零价销售额\nZero-value Sales", _money_or_na(zero_summary["Zero-value Sales"]))
zero_cols[2].metric("零价出库成本\nZero-value Outbound Cost", _money_or_na(zero_summary["Zero-value Outbound Cost"]))
zero_cols[3].metric("待分类记录\nUnclassified Rows", f"{zero_summary['Unclassified Rows']:,}")
if zero_rows.empty:
    st.caption("没有识别到零价出库记录。")
    st.caption("No zero-value outbound rows identified.")
else:
    st.dataframe(_validation_columns(zero_rows.head(300)), width="stretch", hide_index=True)

section_header("单位验证", "Unit Validation")
if unit_rows.empty:
    st.caption("没有识别到单位/价格/数量风险记录。")
    st.caption("No unit, price or quantity risk rows identified.")
else:
    st.dataframe(_validation_columns(unit_rows.head(500)), width="stretch", hide_index=True)

section_header("成本覆盖分析", "Cost Coverage Analysis")
coverage_cols = st.columns(3)
coverage_cols[0].metric("行数覆盖率\nRows Coverage", _percent_or_na(summary["Rows Coverage"]))
coverage_cols[1].metric("销售成本覆盖率\nSales Coverage", _percent_or_na(summary["Sales Coverage"]))
coverage_cols[2].metric("SKU 覆盖率\nSKU Coverage", _percent_or_na(summary["SKU Coverage"]))

tabs = st.tabs(["按月份 / By Month", "按产品系列 / By Product Group", "按客户 / By Customer"])
with tabs[0]:
    st.dataframe(_format_table(coverage_by_month(metrics)), width="stretch", hide_index=True)
with tabs[1]:
    st.dataframe(_format_table(coverage_by_dimension(metrics, "Product Group")), width="stretch", hide_index=True)
with tabs[2]:
    customer_dimension = "Customer Label" if "Customer Label" in metrics.columns else "Customer"
    st.dataframe(_format_table(coverage_by_dimension(metrics, customer_dimension).head(200)), width="stretch", hide_index=True)

section_header("毛利率区间", "Margin Bands")
st.dataframe(_format_table(margin_band_analysis(metrics)), width="stretch", hide_index=True)

section_header("重点异常", "Top Exceptions")
exceptions = top_exceptions(metrics, limit=20)
exception_labels = {
    "Top Negative Margin Products": "负毛利异常 / Negative Margin Exceptions",
    "Top Invalid Unit Cost": "无效单位成本 / Invalid Unit Cost",
    "Top Missing Cost": "缺失成本 / Missing Cost",
    "Top Suspicious Unit Price": "单位匹配异常 / Unit Matching Exceptions",
    "Top Zero Sales Amount": "零价出库 / Zero-value Outbound",
}
exception_tabs = st.tabs([exception_labels.get(name, name) for name in exceptions.keys()])
for tab, (name, table) in zip(exception_tabs, exceptions.items()):
    with tab:
        if table.empty:
            st.caption(f"{name}: 无记录")
        else:
            if "Validation Reason" in table.columns:
                st.dataframe(_validation_columns(table), width="stretch", hide_index=True)
            else:
                st.dataframe(_format_table(table), width="stretch", hide_index=True)

section_header("利润就绪评分", "Profitability Readiness Score")
st.json(
    {
        "Score": score.score,
        "Grade": GRADE_LABELS.get(score.grade, score.grade),
        "Details": score.details,
        "Suggested Thresholds": {
            "Target Sales Coverage": "80%",
            "Target Unit Risk Rate": "<=5%",
            "Target Invalid Cost Rate": "<=1%",
            "Target Unclassified Zero-value Row Rate": "<=3%",
            "Target Missing Cost Sales Rate": "<=5%",
        },
    }
)

section_header("业务签核清单", "Business Sign-off Checklist")
for item in SIGN_OFF_CHECKLIST:
    st.checkbox(item, value=False, disabled=True)
