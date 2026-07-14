from __future__ import annotations

import pandas as pd
import streamlit as st

from app import config as app_config
from app.customer_health import (
    NEW_CUSTOMER_START_DATE,
    PRIORITY_ORDER,
    analysis_cutoff,
    build_customer_health,
    get_customer_status_style,
)
from app.data import apply_date_basis
from app.google_drive import ensure_drive_data_loaded, render_data_source_sidebar
from app.ui import money, percent, show_code_warning, show_context_summary, show_filters


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


def _format_days(value) -> str:
    if pd.isna(value):
        return "暂无稳定周期"
    return f"{float(value):.0f} 天"


def _format_ratio(value) -> str:
    if pd.isna(value):
        return "暂无基准"
    return f"{float(value):.1f}x"


def _format_score(value) -> str:
    if pd.isna(value):
        return "历史不足"
    return f"{float(value):.2f}"


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
    for column in ["最近下单日期", "Expected Next Order Date"]:
        if column in display.columns:
            display[column] = display[column].map(_format_date)
    for column in ["最近4周销售额", "前4周销售额", "绝对下降金额", "历史累计销售额", "最近一次订单金额", "历史平均订单金额"]:
        if column in display.columns:
            display[column] = display[column].map(_format_money)
    for column in ["Typical Order Interval Days"]:
        if column in display.columns:
            display[column] = display[column].map(_format_days)
    for column in ["Average Order Interval Days", "Median Order Interval Days", "Recent Median Interval Days"]:
        if column in display.columns:
            display[column] = display[column].map(_format_days)
    for column in ["Interval Stability Score"]:
        if column in display.columns:
            display[column] = display[column].map(_format_score)
    for column in ["Interval Ratio"]:
        if column in display.columns:
            display[column] = display[column].map(_format_ratio)
    for column in ["金额变化率", "频次变化率"]:
        if column in display.columns:
            display[column] = display[column].map(_format_percent)
    display = display.rename(
        columns={
            "Typical Order Interval Days": "正常采购周期",
            "Typical Interval Source": "周期基准来源",
            "Average Order Interval Days": "平均下单间隔",
            "Median Order Interval Days": "中位下单间隔",
            "Recent Median Interval Days": "近6个月中位间隔",
            "Interval Stability Score": "稳定性CV",
            "Interval Stability Status": "采购节奏稳定性",
            "Expected Next Order Date": "预计下次采购日期",
            "Days Since Last Order": "未下单天数",
            "Days Overdue": "已延期天数",
            "Interval Ratio": "偏离正常节奏倍数",
            "Interval Status": "节奏状态",
            "Historical Order Count": "历史订单数",
            "Recent 6 Months Order Count": "近6个月订单数",
        }
    )
    return display.loc[:, ~display.columns.duplicated()]


def _style_status_columns(df: pd.DataFrame):
    status_columns = [
        column
        for column in ["风险等级", "Health Status", "Trend Direction", "Improvement Type", "Interval Status", "节奏状态", "沉睡状态"]
        if column in df.columns
    ]
    if not status_columns:
        return df
    return df.style.map(get_customer_status_style, subset=status_columns)


def _display_new_table(df: pd.DataFrame) -> pd.DataFrame:
    display = df.copy()
    if "首次下单日期" in display.columns:
        display["首次下单日期"] = display["首次下单日期"].map(_format_date)
    for column in ["首单金额", "当月累计销售额"]:
        if column in display.columns:
            display[column] = display[column].map(_format_money)
    return display


def _impact_text(row: pd.Series) -> str:
    if pd.notna(row.get("Interval Ratio")):
        return f"偏离正常节奏 {_format_ratio(row.get('Interval Ratio'))}"
    if pd.notna(row.get("金额变化率")):
        return f"最近4周采购下降 {abs(float(row['金额变化率'])):.1%}"
    if pd.notna(row.get("频次变化率")):
        return f"最近8周下单频次下降 {abs(float(row['频次变化率'])):.1%}"
    return str(row.get("主要风险原因", ""))


def _priority_text(priority: str) -> str:
    if priority == "高优先级":
        return ":red[**高优先级**]"
    if priority == "中优先级":
        return ":orange[**中优先级**]"
    return ":gray[**低优先级**]"


def _actionable_reason(row: pd.Series) -> str:
    typical = row.get("Typical Order Interval Days")
    days_since = row.get("Days Since Last Order")
    days_overdue = row.get("Days Overdue")
    value_decline = row.get("金额变化率")
    frequency_decline = row.get("频次变化率")
    if pd.notna(typical) and pd.notna(days_since) and float(days_since) > float(typical):
        return f"原本约每 {float(typical):.0f} 天采购，当前已 {float(days_since):.0f} 天未下单"
    if pd.notna(value_decline):
        return f"最近4周金额下降 {abs(float(value_decline)):.1%}"
    if pd.notna(frequency_decline):
        return f"最近8周订单频次下降 {abs(float(frequency_decline)):.1%}"
    if pd.notna(days_overdue) and float(days_overdue) > 0:
        return f"已超过正常节奏 {float(days_overdue):.0f} 天"
    return str(row.get("主要风险原因", "近期经营异常"))


def _days_overdue_text(row: pd.Series) -> str:
    days = row.get("Days Overdue")
    if pd.isna(days):
        return "暂无稳定周期"
    return f"{float(days):.0f} 天"


st.set_page_config(page_title="客户健康", layout="wide")
st.title("客户健康")
st.caption("Customer Health")
st.caption("哪些客户正在下滑、沉睡或需要销售跟进？")

ensure_drive_data_loaded()
render_data_source_sidebar(show_uploaders=False)

df = st.session_state.get("clean_data")

if df is None:
    st.info("当前暂无销售数据，请回到首页使用 Google Drive 刷新或手动上传 Unleashed 销售明细。")
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
basis_labels = getattr(app_config, "DATE_BASIS_LABELS", {})
basis_label = basis_labels.get(basis, basis)
st.caption(f"分析截止日期：{anchor.date()} | Date Basis：{basis_label} | 当前筛选范围：{_scope_text(filtered)}")
st.caption("新客户当前以首次下单日期代替开户日期，自 2026 年 7 月起统计。")
if result.excluded_missing_customer_code:
    st.warning(f"当前筛选范围内有 {result.excluded_missing_customer_code:,} 行缺少 Customer Code，已排除出客户健康主键分析。")
if result.abc_fallback_used:
    st.info("当前销售数据没有可直接复用的 ABC Class 字段，客户健康按历史销售额贡献临时计算 ABC 优先级。")

st.subheader("今日待跟进")
st.caption("Customers to Follow Up")
if result.follow_up.empty:
    st.info("当前筛选范围内暂无近期异常且仍适合今天跟进的客户。长期未下单客户已归入“长期沉睡或可能流失”。")
else:
    for _, row in result.follow_up.iterrows():
        with st.container(border=True):
            cols = st.columns([1.0, 2.0, 1.3, 1.2, 1.2, 1.2])
            cols[0].markdown(_priority_text(row["风险等级"]))
            cols[1].markdown(f"**{row.get('Customer Name', '')}**")
            cols[1].caption(str(row.get("Customer Code", "")))
            cols[2].caption("正常采购周期")
            cols[2].markdown(_format_days(row.get("Typical Order Interval Days")))
            cols[3].caption("当前未下单")
            cols[3].markdown(f"{int(row.get('Days Since Last Order', 0) or 0)} 天")
            cols[4].caption("超过正常节奏")
            cols[4].markdown(_days_overdue_text(row))
            cols[5].caption("偏离倍数")
            cols[5].markdown(_format_ratio(row.get("Interval Ratio")))
            if pd.notna(row.get("金额变化率")):
                st.caption(f"最近4周金额下降：{abs(float(row['金额变化率'])):.1%}")
            st.caption(
                f"主要原因：{_actionable_reason(row)} | "
                f"最近下单：{_format_date(row.get('最近下单日期'))} | "
                f"预计采购：{_format_date(row.get('Expected Next Order Date')) or '暂无稳定周期'} | "
                f"采购节奏：{row.get('Interval Stability Status', '历史不足')} | "
                f"风险类型：{row.get('风险类型', '')} | "
                f"历史销售额：{_format_money(row.get('历史累计销售额'))}"
            )

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

metric_cols = st.columns(5)
metric_cols[0].metric("今日可跟进客户", f"{len(result.follow_up):,}")
metric_cols[1].metric("长期沉睡客户", f"{len(result.dormant_lost_customers):,}")
metric_cols[2].metric("近期改善客户", f"{len(result.improving_customers):,}")
metric_cols[3].metric("30天未下单客户", f"{len(result.dormant_30):,}")
metric_cols[4].metric("90天未下单客户", f"{len(result.dormant_90):,}")

metric_cols = st.columns(2)
metric_cols[0].metric("金额下降客户", f"{len(result.value_decline):,}")
metric_cols[1].metric("频次下降客户", f"{len(result.frequency_decline):,}")

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
        "偏离正常节奏": ("Interval Ratio", False),
        "已延期天数": ("Days Overdue", False),
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

    st.dataframe(_style_status_columns(_display_risk_table(filtered_risk)), width="stretch", hide_index=True)
    st.download_button(
        "下载风险客户名单 / Download Customer Risk List",
        _csv_bytes(filtered_risk),
        file_name=f"XF_Customer_Health_{anchor.date()}.csv",
        mime="text/csv",
    )

with st.expander("长期沉睡或可能流失 / Dormant or Lost", expanded=False):
    dormant_lost = result.dormant_lost_customers.copy()
    if dormant_lost.empty:
        st.info("当前没有识别到长期沉睡或可能流失客户。")
    else:
        st.caption("这些客户不进入今日待跟进前列，用于后续清理、重新激活或单独复盘。")
        st.dataframe(_style_status_columns(_display_risk_table(dormant_lost)), width="stretch", hide_index=True)
        st.download_button(
            "下载长期沉睡客户 / Download Dormant Customers",
            _csv_bytes(dormant_lost),
            file_name=f"XF_Dormant_Customers_{anchor.date()}.csv",
            mime="text/csv",
        )

st.subheader("近期改善客户")
st.caption("Improving Customers")
improving = result.improving_customers.head(10).copy()
if improving.empty:
    st.info("当前没有识别到明显改善或恢复正常的客户。")
else:
    for _, row in improving.iterrows():
        with st.container(border=True):
            cols = st.columns([2.2, 1.2, 1.4, 1.4, 1.2])
            cols[0].markdown(f"**{row.get('Customer Name', '')}**")
            cols[0].caption(str(row.get("Customer Code", "")))
            cols[1].markdown(f":green[**{row.get('Improvement Type', '')}**]")
            cols[2].caption("金额变化")
            cols[2].markdown(f"{_format_money(row.get('前4周销售额'))} → {_format_money(row.get('最近4周销售额'))}")
            if pd.notna(row.get("金额增长率")):
                cols[2].caption(f"+{float(row['金额增长率']):.1%}")
            cols[3].caption("订单频次")
            cols[3].markdown(f"{int(row.get('前8周订单数', 0))} 单 → {int(row.get('最近8周订单数', 0))} 单")
            cols[4].caption("当前节奏")
            cols[4].markdown(_format_ratio(row.get("Interval Ratio")))
            st.caption(
                f"正常周期：{_format_days(row.get('Typical Order Interval Days'))} | "
                f"采购节奏：{row.get('Interval Stability Status', '历史不足')} | "
                f"历史销售额：{_format_money(row.get('历史累计销售额'))}"
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
