import pandas as pd
import streamlit as st

from app.auth import require_login
from app.business_metrics import build_business_metrics_dataframe
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


@st.cache_data(show_spinner=False)
def _cached_metrics(sales_data: pd.DataFrame, snapshots: list) -> pd.DataFrame:
    return build_business_metrics_dataframe(sales_data, snapshots)


def _format_table(data: pd.DataFrame) -> pd.DataFrame:
    display = data.copy()
    for column in [
        "Sales Amount",
        "Sales",
        "Gift Sales",
        "Gift Cost",
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
    return display


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
    ]
    available = [column for column in columns if column in data.columns]
    display = data[available].copy()
    if "Completed Date" in display.columns:
        display["Completed Date"] = pd.to_datetime(display["Completed Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return _format_table(display)


st.set_page_config(page_title="Data Validation", layout="wide")
inject_global_styles()
require_login("data_validation")

st.title("Data Validation")
st.caption("Business Validation & Sign-off for Profitability")

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

metrics = _cached_metrics(sales_df, cost_snapshots)
metrics = add_business_validation_status(metrics)

score = profitability_readiness_score(metrics)
summary = coverage_summary(metrics)
gift_summary = gift_free_of_charge_summary(metrics)

st.warning("此页面仅供 Admin / Finance 内部验证使用。Profitability 未通过 Business Sign-off 前，不建议进入老板视图。")

cols = st.columns(5)
cols[0].metric("Readiness Score", f"{score.score:.1f}/100")
cols[1].metric("Status", score.grade)
cols[2].metric("Sales Coverage", _percent_or_na(summary["Sales Coverage"]))
cols[3].metric("Gift Rows", f"{gift_summary['Gift Rows']:,}")
cols[4].metric("Invalid / Unit Risk", f"{len(unit_validation_rows(metrics)):,}")

with st.expander("Business Validation Status 设计", expanded=False):
    st.write(pd.DataFrame({"Business Validation Status": VALIDATION_STATUS_OPTIONS}))
    st.caption("本阶段仅设计和自动标记状态，不开发人工编辑功能。")

section_header("Gift / Free of Charge Validation")
gift_rows = gift_free_of_charge_rows(metrics)
gift_cols = st.columns(3)
gift_cols[0].metric("Gift Rows", f"{gift_summary['Gift Rows']:,}")
gift_cols[1].metric("Gift Sales", _money_or_na(gift_summary["Gift Sales"]))
gift_cols[2].metric("Gift Cost", _money_or_na(gift_summary["Gift Cost"]))
if gift_rows.empty:
    st.caption("没有识别到 Sales Amount = 0 且 Quantity > 0 的记录。")
else:
    st.dataframe(_validation_columns(gift_rows.head(300)), width="stretch", hide_index=True)

section_header("Unit Validation")
unit_rows = unit_validation_rows(metrics)
if unit_rows.empty:
    st.caption("没有识别到单位/价格/数量风险记录。")
else:
    st.dataframe(_validation_columns(unit_rows.head(500)), width="stretch", hide_index=True)

section_header("Coverage Analysis")
coverage_cols = st.columns(3)
coverage_cols[0].metric("Rows Coverage", _percent_or_na(summary["Rows Coverage"]))
coverage_cols[1].metric("Sales Coverage", _percent_or_na(summary["Sales Coverage"]))
coverage_cols[2].metric("SKU Coverage", _percent_or_na(summary["SKU Coverage"]))

tabs = st.tabs(["By Month", "By Product Group", "By Customer"])
with tabs[0]:
    st.dataframe(_format_table(coverage_by_month(metrics)), width="stretch", hide_index=True)
with tabs[1]:
    st.dataframe(_format_table(coverage_by_dimension(metrics, "Product Group")), width="stretch", hide_index=True)
with tabs[2]:
    customer_dimension = "Customer Label" if "Customer Label" in metrics.columns else "Customer"
    st.dataframe(_format_table(coverage_by_dimension(metrics, customer_dimension).head(200)), width="stretch", hide_index=True)

section_header("Margin Outlier Validation")
st.dataframe(_format_table(margin_band_analysis(metrics)), width="stretch", hide_index=True)

section_header("Top Exceptions")
exceptions = top_exceptions(metrics, limit=20)
exception_tabs = st.tabs(list(exceptions.keys()))
for tab, (name, table) in zip(exception_tabs, exceptions.items()):
    with tab:
        if table.empty:
            st.caption(f"{name}: 无记录")
        else:
            if "Validation Reason" in table.columns:
                st.dataframe(_validation_columns(table), width="stretch", hide_index=True)
            else:
                st.dataframe(_format_table(table), width="stretch", hide_index=True)

section_header("Profitability Readiness Score")
st.json(
    {
        "Score": score.score,
        "Grade": score.grade,
        "Details": score.details,
        "Suggested Thresholds": {
            "Target Sales Coverage": "80%",
            "Target Unit Risk Rate": "<=5%",
            "Target Invalid Cost Rate": "<=1%",
            "Target Gift Row Rate": "<=3%",
            "Target Missing Cost Sales Rate": "<=5%",
        },
    }
)

section_header("Business Sign-off Checklist")
for item in SIGN_OFF_CHECKLIST:
    st.checkbox(item, value=False, disabled=True)
