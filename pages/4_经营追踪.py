from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st

from app.data import load_processed_data
from app.target_metrics import (
    MONTH_LABELS,
    analyze_target_workbook,
    manual_candidate,
    normalize_targets,
    read_sheet_columns,
    workbook_looks_like_sales_data,
)
from app.tracking_metrics import (
    analysis_year,
    average_allocation_table,
    build_monthly_tracking_table,
)
from app.ui import money, percent, show_code_warning, show_context_summary, show_filters


DATA_PATH = Path("data/processed/latest_sales.parquet")


def _format_percent(value: float | None) -> str:
    return "无基准" if value is None or pd.isna(value) else percent(float(value))


def _format_money_or_blank(value: float | None) -> str:
    if value is None or pd.isna(value):
        return ""
    return money(float(value))


def _complete_target_editor_rows(targets: pd.DataFrame, target_year: int) -> pd.DataFrame:
    year_targets = targets[targets["Year"].eq(target_year)].copy()
    records = []
    for month in range(1, 13):
        rows = year_targets[year_targets["Month"].eq(month)]
        if rows.empty:
            original = 0.0
            revised = 0.0
            notes = ""
        else:
            row = rows.iloc[-1]
            original = float(row["Original Target"])
            revised = float(row["Revised Target"])
            notes = "" if pd.isna(row.get("Notes", "")) else str(row.get("Notes", ""))
        records.append(
            {
                "Month": month,
                "月份": MONTH_LABELS[month],
                "Original Target": original,
                "Revised Target": revised,
                "Notes": notes,
            }
        )
    return pd.DataFrame.from_records(records)


def _apply_revised_targets(targets: pd.DataFrame, target_year: int, edited: pd.DataFrame) -> pd.DataFrame:
    result = targets.copy()
    existing = result[~result["Year"].eq(target_year)].copy()
    revised_rows = []
    for _, row in edited.iterrows():
        original = float(row["Original Target"])
        revised = pd.to_numeric(row["Revised Target"], errors="coerce")
        revised_rows.append(
            {
                "Year": target_year,
                "Month": int(row["Month"]),
                "Month Label": row["月份"],
                "Original Target": original,
                "Revised Target": float(revised) if pd.notna(revised) else original,
                "Notes": "" if pd.isna(row.get("Notes", "")) else str(row.get("Notes", "")),
            }
        )
    return pd.concat([existing, pd.DataFrame.from_records(revised_rows)], ignore_index=True).sort_values(["Year", "Month"])


def _store_targets(target_df: pd.DataFrame, annual_targets: dict[int, float], structure_label: str) -> None:
    st.session_state["target_data"] = target_df
    st.session_state["target_annual_targets"] = annual_targets
    st.session_state["target_structure_label"] = structure_label


def _sales_cutoff_text(sales_df: pd.DataFrame | None) -> str:
    if sales_df is None or "Performance Date" not in sales_df.columns:
        return "无"
    dates = pd.to_datetime(sales_df["Performance Date"], errors="coerce").dropna()
    if dates.empty:
        return "无"
    return str(dates.max().date())


def _target_years_text(targets: pd.DataFrame | None) -> str:
    if targets is None or targets.empty or "Year" not in targets.columns:
        return "无"
    years = sorted(targets["Year"].dropna().astype(int).unique().tolist())
    return ", ".join(str(year) for year in years) if years else "无"


def _show_data_status(sales_df: pd.DataFrame | None) -> None:
    target_df = st.session_state.get("target_data")
    status_cols = st.columns(2)
    with status_cols[0]:
        st.markdown("**销售数据**")
        if sales_df is None:
            st.caption("状态：未加载")
            st.caption("当前文件名：无")
            st.caption("数据截止日期：无")
        else:
            st.caption("状态：已加载")
            st.caption(f"当前文件名：{st.session_state.get('source_file_name') or st.session_state.get('current_file_name') or 'latest_sales.parquet'}")
            st.caption(f"数据截止日期：{_sales_cutoff_text(sales_df)}")
    with status_cols[1]:
        st.markdown("**目标数据**")
        if target_df is None:
            st.caption("状态：未加载")
            st.caption("当前文件名：无")
            st.caption("已识别年份：无")
            st.caption("已识别工作表：无")
        else:
            st.caption("状态：已加载")
            st.caption(f"当前文件名：{st.session_state.get('target_excel_name', '目标 Excel')}")
            st.caption(f"已识别年份：{_target_years_text(target_df)}")
            st.caption(f"已识别工作表：{st.session_state.get('target_structure_label', '无')}")


st.set_page_config(page_title="经营追踪", layout="wide")
st.title("经营追踪")
st.caption("Business Tracking")

df = st.session_state.get("clean_data")
if df is None:
    df = load_processed_data(DATA_PATH)
    if df is not None:
        st.session_state["data_source"] = "local_processed"

status_placeholder = st.empty()
with status_placeholder.container():
    st.subheader("数据状态")
    _show_data_status(df)

st.subheader("目标数据上传")
uploaded_target = st.file_uploader("上传目标表 / Upload Targets Excel", type=["xlsx"], key="target_excel_upload")
if uploaded_target is not None:
    uploaded_target_bytes = uploaded_target.getvalue()
    target_analysis_preview = analyze_target_workbook(BytesIO(uploaded_target_bytes))
    if not target_analysis_preview.candidates and workbook_looks_like_sales_data(BytesIO(uploaded_target_bytes)):
        st.error("该文件看起来像销售明细，不像目标表。请使用左侧‘上传销售明细’入口。")
        st.stop()
    else:
        st.session_state["target_excel_bytes"] = uploaded_target_bytes
        st.session_state["target_excel_name"] = uploaded_target.name

target_bytes = st.session_state.get("target_excel_bytes")
if not target_bytes:
    st.info("请上传目标 Excel，以查看月度目标、完成率、Gap 和年度追踪。")
    st.stop()

target_name = st.session_state.get("target_excel_name", "目标 Excel")
st.caption(f"当前目标文件：{target_name}")

analysis = analyze_target_workbook(BytesIO(target_bytes))
candidate = None
if analysis.candidates:
    labels = [item.label for item in analysis.candidates]
    selected_label = st.selectbox("选择识别到的目标工作表", labels, index=0)
    candidate = analysis.candidates[labels.index(selected_label)]
    st.caption(
        f"识别结构：{candidate.layout}；年份字段：{candidate.year_column}；"
        f"月份字段：{candidate.month_column or '横向月份列'}；"
        f"目标字段：{candidate.original_target_column or ', '.join(candidate.month_columns.values())}"
    )
else:
    st.warning("未能自动识别目标表结构，请手动映射字段。")
    manual_sheet = st.selectbox("目标工作表", analysis.sheet_names)
    manual_header = int(st.number_input("表头所在行", min_value=1, max_value=20, value=1, step=1)) - 1
    columns = read_sheet_columns(BytesIO(target_bytes), manual_sheet, manual_header)
    if not columns:
        st.info("当前工作表和表头行没有可识别字段，请调整表头所在行。")
        st.stop()
    year_col = st.selectbox("年份字段", columns)
    month_col = st.selectbox("月份字段", columns)
    target_col = st.selectbox("原始目标字段", columns)
    annual_options = ["无"] + columns
    annual_col = st.selectbox("年度总目标字段（可选）", annual_options)
    candidate = manual_candidate(
        sheet_name=manual_sheet,
        header_row=manual_header,
        layout="long",
        year_column=year_col,
        month_column=month_col,
        original_target_column=target_col,
        annual_target_column=None if annual_col == "无" else annual_col,
    )

try:
    target_df, annual_targets = normalize_targets(BytesIO(target_bytes), candidate)
except ValueError as exc:
    st.error(str(exc))
    st.stop()

_store_targets(target_df, annual_targets, candidate.label)

status_placeholder.empty()
with status_placeholder.container():
    st.subheader("数据状态")
    _show_data_status(df)

if df is None:
    st.info("目标表已加载。请使用左侧‘上传销售明细 / Upload Unleashed Sales Data’入口上传销售明细，以生成实际销售、完成率、Gap 和年度追踪。")
    st.stop()

filtered = show_filters(df, "tracking")
show_code_warning(filtered)
show_context_summary(filtered)

current_year = analysis_year(filtered)
if current_year is None:
    st.info("当前筛选结果没有有效 Performance Date，无法生成经营追踪。")
    st.stop()

available_years = sorted(target_df["Year"].dropna().astype(int).unique().tolist())
default_year_index = available_years.index(current_year) if current_year in available_years else 0
target_year = st.selectbox("目标年度", available_years, index=default_year_index)

st.caption(f"当前分析年度来自所选 Date Basis 下最大 Performance Date：{current_year}。")

editor_rows = _complete_target_editor_rows(target_df, target_year)
edited_targets = st.data_editor(
    editor_rows,
    width="stretch",
    hide_index=True,
    disabled=["Month", "月份", "Original Target"],
    column_config={
        "Month": st.column_config.NumberColumn("月份序号", format="%d"),
        "月份": st.column_config.TextColumn("月份"),
        "Original Target": st.column_config.NumberColumn("Original Target", format="£%.0f"),
        "Revised Target": st.column_config.NumberColumn("Revised Target", format="£%.0f", min_value=0),
        "Notes": st.column_config.TextColumn("Notes"),
    },
    key=f"target_editor_{target_year}",
)

target_df = _apply_revised_targets(target_df, target_year, edited_targets)
st.session_state["target_data"] = target_df

monthly_table, summary = build_monthly_tracking_table(filtered, target_df, target_year)
annual_target_from_file = annual_targets.get(target_year)
if annual_target_from_file is not None:
    diff = summary.annual_target - annual_target_from_file
    if abs(diff) > 0.01:
        st.warning(f"目标表年度总目标为 {money(annual_target_from_file)}，12个月调整后目标合计为 {money(summary.annual_target)}，差额 {money(diff)}。")

st.subheader("年度追踪")
kpi_cols = st.columns(5)
kpi_cols[0].metric("年度目标", money(summary.annual_target))
kpi_cols[1].metric("年度累计实际", money(summary.annual_actual))
kpi_cols[2].metric("年度累计完成率", _format_percent(summary.annual_completion))
kpi_cols[3].metric("年度累计同比", _format_percent(summary.annual_yoy))
kpi_cols[4].metric("年度目标缺口", money(summary.annual_target_shortfall))

st.subheader("月度目标追踪")
display_table = monthly_table.copy()
for column in ["原始目标", "调整后目标", "实际销售", "去年同期", "Gap", "目标缺口", "累计实际", "累计目标"]:
    display_table[column] = display_table[column].map(_format_money_or_blank)
for column in ["完成率", "同比", "累计完成率"]:
    display_table[column] = display_table[column].map(_format_percent)
st.dataframe(
    display_table[
        [
            "月份",
            "原始目标",
            "调整后目标",
            "实际销售",
            "完成率",
            "去年同期",
            "同比",
            "Gap",
            "累计实际",
            "累计目标",
            "累计完成率",
            "状态",
        ]
    ],
    width="stretch",
    hide_index=True,
)

st.subheader("后续月份平均分摊建议")
st.caption("正式缺口只来自已结束月份的未完成部分；当前进行中月份暂不计入再分配，未来月份作为缺口承接月份。")
st.metric("待补正式缺口", money(summary.formal_shortfall))
allocation = average_allocation_table(monthly_table, summary.average_shortfall_allocation)
if allocation.empty:
    st.info("当前没有未来月份可承接缺口。")
else:
    allocation_display = allocation.copy()
    for column in ["当前调整后目标", "平均追加目标", "建议调整后目标"]:
        allocation_display[column] = allocation_display[column].map(_format_money_or_blank)
    st.dataframe(allocation_display, width="stretch", hide_index=True)

with st.expander("查看目标识别详情"):
    st.caption(f"使用结构：{st.session_state.get('target_structure_label')}")
    if analysis.candidates:
        candidate_rows = [
            {
                "工作表": item.sheet_name,
                "表头行": item.header_row + 1,
                "结构": item.layout,
                "评分": item.score,
                "年份字段": item.year_column,
                "月份字段": item.month_column or "横向月份列",
                "目标字段": item.original_target_column or ", ".join(item.month_columns.values()),
                "年度目标字段": item.annual_target_column or "",
            }
            for item in analysis.candidates
        ]
        st.dataframe(pd.DataFrame(candidate_rows), width="stretch", hide_index=True)
