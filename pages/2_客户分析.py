from pathlib import Path
from datetime import date

import pandas as pd
import streamlit as st

from app.config import ABC_A_THRESHOLD, ABC_B_THRESHOLD
from app.customer_metrics import abc_distribution, build_customer_summary, concentration_metrics
from app.data import load_processed_data, monthly_sales, top_entity_table, top_table
from app.ui import bar_chart, date_text, days, donut_chart, line_chart, metric_cards, metric_row, money, percent, show_code_warning, show_context_summary, show_filters


DATA_PATH = Path("data/processed/latest_sales.parquet")

st.set_page_config(page_title="客户分析", layout="wide")
st.title("客户分析")
st.caption("Customer Intelligence")
st.caption("了解客户贡献、结构和购买表现")

df = st.session_state.get("clean_data")
if df is None:
    df = load_processed_data(DATA_PATH)
    if df is not None:
        st.session_state["data_source"] = "local_processed"

if df is None:
    st.info("当前暂无销售数据，请回到首页上传 Unleashed 导出的 Excel 文件开始分析。")
    st.stop()

filtered = show_filters(df, "customers")
show_code_warning(filtered)
show_context_summary(filtered)

st.caption("以下客户等级和购买行为基于当前所选日期口径、日期范围、客户类型及产品组计算。筛选某个产品组后，销售额、产品覆盖和 ABC 均按筛选后口径动态计算。")

customer_summary, conflicts = build_customer_summary(filtered)
concentration = concentration_metrics(customer_summary)
total_sales = float(filtered["Sales Amount"].sum())
order_count = int(filtered["Order No."].nunique())
avg_order = total_sales / order_count if order_count else 0
customer_count = int(customer_summary["Customer Key"].nunique()) if not customer_summary.empty else 0

metric_cards(
    [
        ("总销售额", money(total_sales)),
        ("总订单数", f"{order_count:,}"),
        ("客户数", f"{customer_count:,}"),
        ("平均订单金额", money(avg_order)),
        ("Top 10 客户贡献率", f"{concentration['Top 10 Contribution']:.1%}"),
    ]
)

left, right = st.columns([1.2, 1])
with left:
    if "Customer Key" in filtered.columns and "Customer Label" in filtered.columns:
        top_customers = top_entity_table(filtered, "Customer Key", "Customer Label", 20)
        st.plotly_chart(bar_chart(top_customers.sort_values("Sales Amount"), "Sales Amount", "Customer Label", "客户销售排行榜", "h"), width="stretch")
    else:
        top_customers = top_table(filtered, "Customer", 20)
        st.plotly_chart(bar_chart(top_customers.sort_values("Sales Amount"), "Sales Amount", "Customer", "客户销售排行榜", "h"), width="stretch")
with right:
    customer_types = top_table(filtered, "Customer Type", 20)
    st.plotly_chart(donut_chart(customer_types, "Customer Type", "Sales Amount", "客户类型销售占比"), width="stretch")

st.subheader("客户集中度与 ABC 分类")
st.caption(f"ABC 分类：A 类累计贡献至 {ABC_A_THRESHOLD:.0%}，B 类超过 {ABC_A_THRESHOLD:.0%} 至 {ABC_B_THRESHOLD:.0%}，C 类超过 {ABC_B_THRESHOLD:.0%}。该等级基于当前筛选动态计算，不代表永久客户等级。")

abc = abc_distribution(customer_summary)
left, right = st.columns([1, 1])
with left:
    st.plotly_chart(donut_chart(abc.rename(columns={"ABC Class": "客户等级", "Sales": "销售额"}), "客户等级", "销售额", "ABC 销售额分布"), width="stretch")
with right:
    metric_cards(
        [
            ("Top 5", f"{concentration['Top 5 Contribution']:.1%}"),
            ("Top 10", f"{concentration['Top 10 Contribution']:.1%}"),
            ("Top 20", f"{concentration['Top 20 Contribution']:.1%}"),
            (f"Top 20%（{concentration['Top 20 Percent Customer Count']}户）", f"{concentration['Top 20 Percent Contribution']:.1%}"),
        ]
    )
    st.dataframe(abc, width="stretch", hide_index=True)

st.subheader("客户汇总表")
st.caption("Customer Summary Table。表格筛选仅影响本表，不改变页面顶部 KPI、ABC 分布或全局筛选。")

if customer_summary.empty:
    st.info("当前筛选范围内没有客户汇总数据。")
else:
    table = customer_summary.copy()
    display = pd.DataFrame(
        {
            "客户代码": table["Customer Code"].fillna(table["Customer Key"]),
            "客户名称": table["Customer Name"].fillna(""),
            "客户类型": table["Customer Type"].fillna("未分类"),
            "ABC 等级": table["ABC Class"],
            "销售额": table["Total Sales"],
            "销售贡献率": table["Sales Contribution"],
            "订单数": table["Order Count"],
            "平均订单金额": table["Average Order Value"],
            "最近下单日期": table["Last Order Date"].map(date_text),
            "平均订单间隔": table["Average Order Gap Days"].map(days),
            "月均订单数": table["Orders per Month"],
            "产品数": table["Product Count"],
            "首次下单日期": table["First Order Date"].map(date_text),
            "产品组数": table["Product Group Count"],
        }
    )

    st.markdown("**表格筛选**")
    search = st.text_input("搜索客户代码或客户名称", placeholder="输入客户代码或名称", key="customer_summary_search")

    abc_options = sorted(display["ABC 等级"].dropna().unique().tolist())
    selected_abc = st.multiselect("ABC 等级筛选", abc_options, default=abc_options, key="customer_summary_abc")

    type_options = sorted(display["客户类型"].dropna().unique().tolist())
    selected_types = st.multiselect("客户类型筛选", type_options, default=type_options, key="customer_summary_type")

    sort_options = {
        "销售额（高到低）": ("销售额", False),
        "销售贡献率（高到低）": ("销售贡献率", False),
        "订单数（高到低）": ("订单数", False),
        "平均订单金额（高到低）": ("平均订单金额", False),
        "最近下单日期（新到旧）": ("最近下单日期", False),
        "月均订单数（高到低）": ("月均订单数", False),
        "产品数（高到低）": ("产品数", False),
        "客户名称（A-Z）": ("客户名称", True),
    }
    sort_label = st.selectbox("排序", list(sort_options.keys()), key="customer_summary_sort")

    filtered_table = display.copy()
    if search:
        search_text = search.strip().lower()
        filtered_table = filtered_table[
            filtered_table["客户代码"].astype(str).str.lower().str.contains(search_text, na=False)
            | filtered_table["客户名称"].astype(str).str.lower().str.contains(search_text, na=False)
        ]
    filtered_table = filtered_table[filtered_table["ABC 等级"].isin(selected_abc)]
    filtered_table = filtered_table[filtered_table["客户类型"].isin(selected_types)]

    sort_col, ascending = sort_options[sort_label]
    filtered_table = filtered_table.sort_values(sort_col, ascending=ascending)

    result_customers = len(filtered_table)
    result_sales = float(filtered_table["销售额"].sum()) if not filtered_table.empty else 0.0
    metric_cards(
        [
            ("当前结果客户数", f"{result_customers:,}"),
            ("当前结果销售额", money(result_sales)),
        ]
    )

    if filtered_table.empty:
        st.info("当前表格筛选条件下没有客户。")
    else:
        formatted_table = filtered_table.copy()
        formatted_table["销售额"] = formatted_table["销售额"].map(money)
        formatted_table["销售贡献率"] = formatted_table["销售贡献率"].map(percent)
        formatted_table["平均订单金额"] = formatted_table["平均订单金额"].map(money)
        formatted_table["月均订单数"] = formatted_table["月均订单数"].map(lambda v: f"{v:.1f}")
        st.download_button(
            "下载当前筛选客户汇总 CSV",
            filtered_table.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"customer_summary_{date.today():%Y%m%d}.csv",
            mime="text/csv",
        )
        st.dataframe(
            formatted_table[
                [
                    "客户代码",
                    "客户名称",
                    "客户类型",
                    "ABC 等级",
                    "销售额",
                    "销售贡献率",
                    "订单数",
                    "平均订单金额",
                    "最近下单日期",
                    "平均订单间隔",
                    "月均订单数",
                    "产品数",
                    "首次下单日期",
                    "产品组数",
                ]
            ],
            width="stretch",
            hide_index=True,
        )

st.subheader("单客户详情")
customer_dimension = "Customer Key" if "Customer Key" in filtered.columns else "Customer"
label_dimension = "Customer Label" if "Customer Label" in filtered.columns else customer_dimension
customer_options = filtered[[customer_dimension, label_dimension]].dropna().drop_duplicates().sort_values(label_dimension)
customers = customer_options[label_dimension].astype(str).tolist()
if not customers:
    st.info("当前筛选范围内没有客户数据。")
    st.stop()
selected_customer = st.selectbox("选择客户", customers)
selected_key = customer_options.loc[customer_options[label_dimension].astype(str).eq(selected_customer), customer_dimension].iloc[0]
customer_df = filtered[filtered[customer_dimension].eq(selected_key)]

metric_row(customer_df)

left, right = st.columns(2)
with left:
    st.plotly_chart(line_chart(monthly_sales(customer_df), "Month", "Sales Amount", "客户月度销售趋势"), width="stretch")
with right:
    customer_products = top_table(customer_df, "Product Group", 10)
    st.plotly_chart(bar_chart(customer_products.sort_values("Sales Amount"), "Sales Amount", "Product Group", "客户购买产品组", "h"), width="stretch")

with st.expander("查看客户订单明细"):
    st.dataframe(customer_df.sort_values("Sales Date", ascending=False).head(500), width="stretch")
