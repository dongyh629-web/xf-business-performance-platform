import streamlit as st

from app.data import monthly_sales, top_entity_table, top_table
from app.google_drive import ensure_drive_data_loaded, render_data_source_sidebar
from app.ui import bar_chart, line_chart, metric_row, show_code_warning, show_context_summary, show_filters


st.set_page_config(page_title="产品分析", layout="wide")
st.title("产品分析")
st.caption("查看产品组、重点产品和购买客户表现")

ensure_drive_data_loaded()
render_data_source_sidebar(show_uploaders=False)

df = st.session_state.get("clean_data")

if df is None:
    st.info("当前暂无销售数据，请回到首页使用 Google Drive 刷新或手动上传 Unleashed 销售明细。")
    st.stop()

filtered = show_filters(df, "products")
show_code_warning(filtered)
show_context_summary(filtered)
metric_row(filtered)

left, right = st.columns(2)
with left:
    top_groups = top_table(filtered, "Product Group", 20)
    st.plotly_chart(bar_chart(top_groups.sort_values("Sales Amount"), "Sales Amount", "Product Group", "产品组销售排行", "h"), width="stretch")
with right:
    if "Product Key" in filtered.columns and "Product Label" in filtered.columns:
        top_products = top_entity_table(filtered, "Product Key", "Product Label", 20)
        st.plotly_chart(bar_chart(top_products.sort_values("Sales Amount"), "Sales Amount", "Product Label", "Top Product Code", "h"), width="stretch")
    else:
        top_products = top_table(filtered, "Product", 20)
        st.plotly_chart(bar_chart(top_products.sort_values("Sales Amount"), "Sales Amount", "Product", "Top Product", "h"), width="stretch")

st.subheader("单产品趋势")
product_dimension = "Product Key" if "Product Key" in filtered.columns else "Product"
label_dimension = "Product Label" if "Product Label" in filtered.columns else product_dimension
product_options = filtered[[product_dimension, label_dimension]].dropna().drop_duplicates().sort_values(label_dimension)
products = product_options[label_dimension].astype(str).tolist()
if not products:
    st.info("当前筛选范围内没有产品数据。")
    st.stop()
selected_product = st.selectbox("选择产品", products)
selected_key = product_options.loc[product_options[label_dimension].astype(str).eq(selected_product), product_dimension].iloc[0]
product_df = filtered[filtered[product_dimension].eq(selected_key)]

left, right = st.columns([1.2, 1])
with left:
    st.plotly_chart(line_chart(monthly_sales(product_df), "Month", "Sales Amount", "产品月度销售趋势"), width="stretch")
with right:
    if "Customer Key" in product_df.columns and "Customer Label" in product_df.columns:
        buyers = top_entity_table(product_df, "Customer Key", "Customer Label", 15)
        st.plotly_chart(bar_chart(buyers.sort_values("Sales Amount"), "Sales Amount", "Customer Label", "购买客户排行", "h"), width="stretch")
    else:
        buyers = top_table(product_df, "Customer", 15)
        st.plotly_chart(bar_chart(buyers.sort_values("Sales Amount"), "Sales Amount", "Customer", "购买客户排行", "h"), width="stretch")

with st.expander("查看产品明细"):
    st.dataframe(product_df.sort_values("Sales Date", ascending=False).head(500), width="stretch")
