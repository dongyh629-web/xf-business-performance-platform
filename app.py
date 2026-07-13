from datetime import datetime

import streamlit as st

from app.config import DATA_PATH, PERSIST_UPLOADED_DATA
from app.business_dashboard import render_business_dashboard
from app.data import import_excel, load_processed_data, monthly_sales, save_processed_data, top_entity_table, top_table
from app.ui import bar_chart, donut_chart, line_chart, metric_row, show_code_warning, show_context_summary, show_filters


st.set_page_config(page_title="XF Business Dashboard", page_icon="📊", layout="wide")

st.title("XF 内部商业分析系统")
st.caption("查看销售总览、核心客户和产品组表现")


def data_updated_at() -> str | None:
    if not DATA_PATH.exists():
        return None
    return datetime.fromtimestamp(DATA_PATH.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


with st.sidebar:
    st.header("数据导入")
    if st.button("更新数据", use_container_width=True):
        st.session_state["show_data_uploader"] = True
    show_uploader = st.session_state.get("show_data_uploader", not DATA_PATH.exists())
    uploaded = None
    if show_uploader:
        uploaded = st.file_uploader("上传 Unleashed Excel 文件", type=["xlsx"])
    st.caption("默认口径：完成日期（Completed Date）。可在下方“分析日期口径”中切换。")

if uploaded is not None:
    try:
        with st.spinner("正在读取和清洗 Excel..."):
            result = import_excel(uploaded)
            if PERSIST_UPLOADED_DATA:
                save_processed_data(result.clean, DATA_PATH)
            st.session_state["quality"] = result.quality
            st.session_state["comparison"] = result.comparison
            st.session_state["sheet_name"] = result.sheet_name
            st.session_state["clean_data"] = result.clean
            st.session_state["current_file_name"] = "latest_sales.parquet"
            st.session_state["data_source"] = "uploaded" if PERSIST_UPLOADED_DATA else "session_upload"
            st.session_state["data_last_updated"] = data_updated_at()
            st.session_state["source_columns"] = list(result.raw.columns)
            st.session_state["show_data_uploader"] = False
        st.success(f"导入完成：已识别工作表 `{result.sheet_name}`")
    except ValueError as exc:
        st.error(str(exc))
        st.stop()
    except Exception:
        st.error("导入失败：请确认文件是 Unleashed 导出的 Excel，且字段结构未发生变化。详细错误已记录在开发日志中。")
        st.stop()

df = st.session_state.get("clean_data")
if df is None:
    df = load_processed_data(DATA_PATH)
    if df is not None:
        st.session_state["data_source"] = "persistent"
        st.session_state["current_file_name"] = "latest_sales.parquet"
        st.session_state["data_last_updated"] = data_updated_at()

if df is None:
    with st.sidebar:
        st.markdown("### 数据状态")
        st.caption("当前暂无数据")
        st.caption("请上传 Unleashed Excel。")
    st.info("当前暂无销售数据，请上传 Unleashed 导出的 Excel 文件开始分析。")
    st.stop()

filtered = show_filters(df, "home")

show_code_warning(filtered)
show_context_summary(filtered)
quality = st.session_state.get("quality", {})
if quality:
    st.caption(f"当前主业绩日期：{quality.get('主业绩日期', '未知')}。{quality.get('主业绩日期说明', '')}")
    if quality.get("日期质量警告"):
        st.warning(str(quality["日期质量警告"]))

render_business_dashboard(filtered)
st.divider()

metric_row(filtered)

monthly = monthly_sales(filtered)
left, right = st.columns([1.4, 1])
with left:
    st.plotly_chart(line_chart(monthly, "Month", "Sales Amount", "月度销售趋势"), width="stretch")
with right:
    if "Customer Key" in filtered.columns and "Customer Label" in filtered.columns:
        top_customers = top_entity_table(filtered, "Customer Key", "Customer Label", 10)
        st.plotly_chart(bar_chart(top_customers.sort_values("Sales Amount"), "Sales Amount", "Customer Label", "Top 10 客户", "h"), width="stretch")
    else:
        top_customers = top_table(filtered, "Customer", 10)
        st.plotly_chart(bar_chart(top_customers.sort_values("Sales Amount"), "Sales Amount", "Customer", "Top 10 客户", "h"), width="stretch")

left, right = st.columns(2)
with left:
    top_groups = top_table(filtered, "Product Group", 10)
    st.plotly_chart(bar_chart(top_groups.sort_values("Sales Amount"), "Sales Amount", "Product Group", "Top 产品组", "h"), width="stretch")
with right:
    customer_types = top_table(filtered, "Customer Type", 20)
    st.plotly_chart(donut_chart(customer_types, "Customer Type", "Sales Amount", "客户类型销售占比"), width="stretch")

with st.expander("查看清洗后数据预览"):
    st.download_button(
        "下载当前筛选结果 CSV",
        filtered.to_csv(index=False).encode("utf-8-sig"),
        file_name="filtered_sales.csv",
        mime="text/csv",
    )
    st.dataframe(filtered.head(200), width="stretch")

with st.expander("查看数据质量摘要"):
    if quality:
        st.json(quality)
    else:
        st.caption("当前数据来自已保存的处理结果；重新上传文件后会显示本次导入质量摘要。")

with st.expander("查看旧口径与新口径对比"):
    comparison = st.session_state.get("comparison")
    if comparison:
        summary_keys = ["原始行数", "旧清洗后行数", "新清洗后行数", "旧销售额", "新销售额", "差额", "差异比例", "因停止删除疑似重复而恢复的金额", "因日期口径变化而移动月份的订单数量", "因日期口径变化而移动月份的金额"]
        st.json({key: comparison[key] for key in summary_keys})
        st.dataframe(comparison["每月销售额差异"], width="stretch")
    else:
        st.caption("重新上传文件后会生成新旧口径对比。")
