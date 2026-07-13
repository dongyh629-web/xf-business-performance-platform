from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from app.config import DATE_BASIS_LABELS, NEW_CUSTOMER_START_DATE
from app.customer_health import PRIORITY_ORDER, analysis_cutoff, build_customer_health
from app.data import apply_date_basis, load_processed_data
from app.ui import money, percent, show_code_warning, show_context_summary, show_filters


DATA_PATH = Path("data/processed/latest_sales.parquet")


def _format_percent(value: float | None) -> str:
    return "暂无可比数据" if value is None or pd.isna(value) else percent(float(value))


def _format_date(value) -> str:
    if pd.isna(value):
        return ""
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _format_money(value) -> str:
    if pd.isna(value):
        return ""
    return money(float(value))


def _csv_bytes(df: pd.DataFrame) -> bytes:
    export = df.copy()
    for column in export.columns:
        if pd.api.types.is_datetime64_any_dtype(export[column]):
            export[column] = export[column].dt.strftime("%Y-%m-%d")
    return export.to_csv(index=False).encode("utf-8-sig")


def _history_scope(raw_df: pd.DataFrame, filtered: pd.DataFrame) -> pd.DataFrame:
    basis = filtered.attrs.get("date_basis", st.session_state.get("date_basis", "Completed Date"))
    history = apply_date_basis(raw_df, basis)
    customer_types = filtered.attrs.get("customer_types", [])
    product_groups = filtered.attrs.get("product_groups", [])
    all_customer_count = filtered.attrs.get("all_customer_type_count")
    all_product_count = filtered.attrs.get("all_product_group_count")
    if all_customer_count is not None and len(customer_types) != all_customer_count:
        history = history[history["Customer Type"].fillna("未分类").astype(str).isin(customer_types)]
    if all_product_count is not None and len(product_groups) != all_product_count:
        history = history[history["Product Group"].fillna("未分类").astype(str).isin(product_groups)]
    return history.copy()


def _scope_text(filtered: pd.DataFrame) -> str:
    customer_types = filtered.attrs.get("customer_types", [])
    product_groups = filtered.attrs.get("product_groups", [])
    all_customer_count = filtered.attrs.get("all_customer_type_count")
    all_product_count = filtered.attrs.get("all_product_group_count")
    customer_text = "全部客户类型" if all_customer_count is None or len(customer_types) == all_customer_count else f"客户类型 {len(customer_types)} 项"
    product_text = "全部产品组" if all_product_count is None or len(product_groups) == all_product_count else f"产品组 {len(product_groups)} 项"
    return f"{customer_text}；{product_text}"


def _display_risk_table(df: pd.DataFrame) -> pd.DataFrame:
    display = df.copy()
    for column in ["最近下单日期"]:
        if column in display.columns:
            display[column] = display[column].map(_format_date)
    for column in ["最近4周销售额", "前4周销售额", "历史累计销售额", "最近一次订单金额", "历史平均订单金额"]:
        if column in display.columns:
            display[column] = display[column].map(_format_money)
    for column in ["金额变化率", "频次变化率"]:
        if column in display.columns:
            display[column] = display[column].map(_format_percent)
    return display


def _display_new_table(df: pd.DataFrame) -> pd.DataFrame:
    display = df.copy()
    if "首次下单日期" in display.columns:
        display["首次下单日期"] = display["首次下单日期"].map(_format_date)
    for column in ["首单金额", "当月累计销售额"]:
        if column in display.columns:
            display[column] = display[column].map(_format_money)
    return display


def _impact_text(row: pd.Series) -> str:
    if pd.notna(row.get("未下单天数")):
        return f"{int(row['未下单天数'])} 天未下单"
    if pd.notna(row.get("金额变化率")):
        return f"最近4周采购下降 {abs(float(row['金额变化率'])):.1%}"
    if pd.notna(row.get("频次变化率")):
        return f"最近8周下单频次下降 {abs(float(row['频次变化率'])):.1%}"
    return str(row.get("主要风险原因", ""))


st.set_page_config(page_title="客户健康", layout="wide")
st.title("客户健康")
st.caption("Customer Health")
st.caption("哪些客户正在下滑、沉睡或需要销售跟进？")

df = st.session_state.get("clean_data")
if df is None:
    df = load_processed_data(DATA_PATH)
    if df is not None:
        st.session_state["data_source"] = "local_processed"

if df is None:
    st.info("当前暂无销售数据，请回到首页上传 Unleashed 销售明细。")
    st.stop()

filtered = show_filters(df, "customer_health")
show_code_warning(filtered)
show_context_summary(filtered)

anchor = analysis_cutoff(filtered)
if anchor is None:
    st.info("当前筛选范围内没有带 Customer Code 和有效 Performance Date 的销售数据。")
    st.stop()

history = _history_scope(df, filtered)
history_dates = pd.to_datetime(history["Performance Date"], errors="coerce").dt.normalize()
history = history[history_dates.le(anchor)].copy()
result = build_customer_health(history, history)

basis = filtered.attrs.get("date_basis", st.session_state.get("date_basis", "Completed Date"))
basis_label = DATE_BASIS_LABELS.get(basis, basis)
st.caption(f"分析截止日期：{anchor.date()} | Date Basis：{basis_label} | 当前筛选范围：{_scope_text(filtered)}")
st.caption("新客户当前以首次下单日期代替开户日期，自 2026 年 7 月起统计。")
if result.excluded_missing_customer_code:
    st.warning(f"当前筛选范围内有 {result.excluded_missing_customer_code:,} 行缺少 Customer Code，已排除出客户健康主键分析。")
if result.abc_fallback_used:
    st.info("当前销售数据没有可直接复用的 ABC Class 字段，客户健康按历史销售额贡献临时计算 ABC 优先级。")

st.subheader("今日待跟进")
st.caption("Customers to Follow Up")
if result.follow_up.empty:
    st.info("当前筛选范围内暂无达到规则阈值的风险客户。")
else:
    for _, row in result.follow_up.iterrows():
        with st.container(border=True):
            cols = st.columns([1.1, 2.2, 1.2, 1.2])
            cols[0].markdown(f"**{row['风险等级']}**")
            cols[1].markdown(f"**{row.get('Customer Name', '')}**")
            cols[1].caption(str(row.get("Customer Code", "")))
            cols[2].caption("主要风险")
            cols[2].markdown(_impact_text(row))
            cols[3].caption("最近下单")
            cols[3].markdown(_format_date(row.get("最近下单日期")))
            st.caption(f"风险类型：{row.get('风险类型', '')} | 历史销售额：{_format_money(row.get('历史累计销售额'))}")

st.subheader("客户健康 KPI")
active = result.active_metrics
metric_cols = st.columns(4)
metric_cols[0].metric("当月活跃客户", f"{active.current_active:,}")
metric_cols[1].metric("活跃客户同比", _format_percent(active.yoy))
metric_cols[2].metric("活跃客户环比", _format_percent(active.mom))
if anchor.to_period("M") < pd.Timestamp(NEW_CUSTOMER_START_DATE).to_period("M"):
    new_customer_text = "自 2026-07 起统计"
else:
    new_customer_text = f"{len(result.new_customers):,}"
metric_cols[3].metric("本月新客户", new_customer_text)

metric_cols = st.columns(4)
metric_cols[0].metric("30天未下单客户", f"{len(result.dormant_30):,}")
metric_cols[1].metric("90天未下单客户", f"{len(result.dormant_90):,}")
metric_cols[2].metric("金额下降客户", f"{len(result.value_decline):,}")
metric_cols[3].metric("频次下降客户", f"{len(result.frequency_decline):,}")

st.subheader("风险客户名单")
risk_table = result.risk_customers.copy()
if risk_table.empty:
    st.info("当前没有风险客户。")
else:
    filter_cols = st.columns(4)
    priority_options = sorted(risk_table["风险等级"].dropna().unique().tolist(), key=lambda item: PRIORITY_ORDER.get(item, 9))
    risk_type_options = sorted(set("；".join(risk_table["风险类型"].dropna().astype(str)).split("；")))
    type_options = sorted(risk_table["Customer Type"].fillna("未分类").astype(str).unique().tolist())
    abc_options = sorted(risk_table["ABC Class"].fillna("未分类").astype(str).unique().tolist())
    selected_priority = filter_cols[0].multiselect("风险等级", priority_options, default=priority_options)
    selected_risk_types = filter_cols[1].multiselect("风险类型", risk_type_options, default=risk_type_options)
    selected_types = filter_cols[2].multiselect("Customer Type", type_options, default=type_options)
    selected_abc = filter_cols[3].multiselect("ABC Class", abc_options, default=abc_options)

    sort_options = {
        "风险等级": ("_priority_order", True),
        "未下单天数": ("未下单天数", False),
        "金额下降": ("金额变化率", True),
        "频次下降": ("频次变化率", True),
        "历史销售额": ("历史累计销售额", False),
    }
    sort_label = st.selectbox("排序", list(sort_options.keys()))

    filtered_risk = risk_table.copy()
    filtered_risk["_priority_order"] = filtered_risk["风险等级"].map(PRIORITY_ORDER).fillna(9)
    filtered_risk = filtered_risk[filtered_risk["风险等级"].isin(selected_priority)]
    filtered_risk = filtered_risk[filtered_risk["Customer Type"].fillna("未分类").astype(str).isin(selected_types)]
    filtered_risk = filtered_risk[filtered_risk["ABC Class"].fillna("未分类").astype(str).isin(selected_abc)]
    if selected_risk_types:
        pattern = "|".join(selected_risk_types)
        filtered_risk = filtered_risk[filtered_risk["风险类型"].str.contains(pattern, regex=True, na=False)]
    sort_column, ascending = sort_options[sort_label]
    filtered_risk = filtered_risk.sort_values(sort_column, ascending=ascending).drop(columns=["_priority_order"], errors="ignore")

    st.dataframe(_display_risk_table(filtered_risk), width="stretch", hide_index=True)
    st.download_button(
        "下载风险客户名单 / Download Customer Risk List",
        _csv_bytes(filtered_risk),
        file_name=f"XF_Customer_Health_{anchor.date()}.csv",
        mime="text/csv",
    )

st.subheader("新客户名单")
if anchor.to_period("M") < pd.Timestamp(NEW_CUSTOMER_START_DATE).to_period("M"):
    st.info("新客户自 2026-07 起统计。")
elif result.new_customers.empty:
    st.info("当前分析月份暂无新客户。")
else:
    st.dataframe(_display_new_table(result.new_customers), width="stretch", hide_index=True)
    st.download_button(
        "下载新客户名单 / Download New Customers",
        _csv_bytes(result.new_customers),
        file_name=f"XF_New_Customers_{anchor.date()}.csv",
        mime="text/csv",
    )
