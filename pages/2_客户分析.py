from datetime import date

import pandas as pd
import streamlit as st

from app.auth import require_login
from app.config import ABC_A_THRESHOLD, ABC_B_THRESHOLD
from app.customer_metrics import abc_distribution, build_customer_summary, concentration_metrics
from app.data import monthly_sales, top_entity_table, top_table
from app.google_drive import ensure_drive_data_loaded, render_data_source_sidebar
from app.ui import bar_chart, date_text, days, donut_chart, inject_global_styles, line_chart, metric_cards, metric_row, money, percent, section_header, show_code_warning, show_context_summary, show_filters


def money_or_na(value) -> str:
    if pd.isna(value):
        return "暂无毛利字段"
    return money(float(value))


def percent_or_na(value) -> str:
    if pd.isna(value):
        return "暂无可比数据"
    return f"{float(value):.1%}"


def _order_amount_metrics(data: pd.DataFrame, customer_summary: pd.DataFrame) -> dict[str, object]:
    if data.empty or "Order No." not in data.columns:
        return {
            "median_order_value": None,
            "trimmed_average_order_value": None,
            "non_top10_aov": None,
            "median_customer_aov": None,
            "order_value_iqr": None,
        }

    orders = (
        data.groupby("Order No.", dropna=False)["Sales Amount"]
        .sum()
        .dropna()
        .sort_values()
    )
    median_order_value = float(orders.median()) if not orders.empty else None
    if len(orders) < 20:
        trimmed_average_order_value = None
    else:
        trim_count = int(len(orders) * 0.05)
        trimmed_orders = orders.iloc[trim_count : len(orders) - trim_count] if trim_count else orders
        trimmed_average_order_value = float(trimmed_orders.mean()) if not trimmed_orders.empty else None
    if orders.empty:
        order_value_iqr = None
    else:
        order_value_iqr = (float(orders.quantile(0.25)), float(orders.quantile(0.75)))

    customer_key = "Customer Key" if "Customer Key" in data.columns else "Customer"
    top10_keys = set(customer_summary.head(10)["Customer Key"].astype(str)) if not customer_summary.empty and "Customer Key" in customer_summary.columns else set()
    non_top10 = data[~data[customer_key].astype(str).isin(top10_keys)] if top10_keys else data.iloc[0:0]
    non_top10_orders = int(non_top10["Order No."].nunique()) if not non_top10.empty else 0
    non_top10_aov = float(non_top10["Sales Amount"].sum() / non_top10_orders) if non_top10_orders else None

    if customer_summary.empty:
        median_customer_aov = None
    else:
        customer_aovs = pd.to_numeric(customer_summary["Average Order Value"], errors="coerce").dropna()
        median_customer_aov = float(customer_aovs.median()) if not customer_aovs.empty else None

    return {
        "median_order_value": median_order_value,
        "trimmed_average_order_value": trimmed_average_order_value,
        "non_top10_aov": non_top10_aov,
        "median_customer_aov": median_customer_aov,
        "order_value_iqr": order_value_iqr,
    }


def _money_or_sample_text(value: object) -> str:
    if value is None or pd.isna(value):
        return "样本不足"
    return money(float(value))


def _iqr_text(value: object) -> str:
    if value is None:
        return "样本不足"
    low, high = value
    return f"{money(low)} ~ {money(high)}"


st.set_page_config(page_title="客户分析", layout="wide")
inject_global_styles()
require_login("customer_analysis")
st.title("客户分析")
st.caption("Customer Intelligence")
st.caption("了解客户贡献、结构和购买表现")

ensure_drive_data_loaded()
render_data_source_sidebar(show_uploaders=False)

df = st.session_state.get("clean_data")

if df is None:
    st.info("当前暂无销售数据，请回到首页使用 Google Drive 刷新或手动上传 Unleashed 销售明细。")
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
order_metrics = _order_amount_metrics(filtered, customer_summary)

metric_cards(
    [
        ("总销售额", money(total_sales)),
        ("总订单数", f"{order_count:,}"),
        ("客户数", f"{customer_count:,}"),
        ("平均订单金额", money(avg_order)),
        ("订单金额中位数", _money_or_sample_text(order_metrics["median_order_value"])),
    ]
)
metric_cards(
    [
        ("去极值平均订单金额", _money_or_sample_text(order_metrics["trimmed_average_order_value"])),
        ("非Top10客户平均订单金额", _money_or_sample_text(order_metrics["non_top10_aov"])),
        ("典型客户平均订单金额", _money_or_sample_text(order_metrics["median_customer_aov"])),
        ("Top 10 客户贡献率", f"{concentration['Top 10 Contribution']:.1%}"),
        ("订单金额中间50%区间", _iqr_text(order_metrics["order_value_iqr"])),
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

section_header("客户集中度与 ABC 分类")
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

section_header("客户汇总表")
st.caption("Customer Summary Table。表格筛选仅影响本表，不改变页面顶部 KPI、ABC 分布或全局筛选。同比增长按当前筛选范围内最近年份与前一年销售额计算；月度趋势基于当前日期口径。")

if customer_summary.empty:
    st.info("当前筛选范围内没有客户汇总数据。")
else:
    table = customer_summary.copy()
    has_gross_profit = "Gross Profit" in table.columns and table["Gross Profit"].notna().any()
    if not has_gross_profit:
        st.caption("当前源文件未识别到 Gross Profit / 毛利字段，表格中毛利显示为“暂无毛利字段”。")
    display = pd.DataFrame(
        {
            "客户名称": table["Customer Name"].fillna(""),
            "客户代码": table["Customer Code"].fillna(table["Customer Key"]),
            "销售额": table["Total Sales"],
            "毛利": table["Gross Profit"] if "Gross Profit" in table.columns else pd.NA,
            "订单数": table["Order Count"],
            "平均订单金额": table["Average Order Value"],
            "ABC 等级": table["ABC Class"],
            "同比增长": table["YoY Growth"] if "YoY Growth" in table.columns else pd.NA,
            "月度趋势": table["Monthly Trend"] if "Monthly Trend" in table.columns else [[] for _ in range(len(table))],
            "客户类型": table["Customer Type"].fillna("未分类"),
            "销售贡献率": table["Sales Contribution"],
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
        "毛利（高到低）": ("毛利", False),
        "同比增长（高到低）": ("同比增长", False),
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
    if sort_col in ["毛利", "同比增长"]:
        filtered_table = filtered_table.assign(_sort_value=pd.to_numeric(filtered_table[sort_col], errors="coerce").fillna(float("-inf")))
        filtered_table = filtered_table.sort_values("_sort_value", ascending=ascending).drop(columns=["_sort_value"])
    else:
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
        formatted_table["毛利"] = formatted_table["毛利"].map(money_or_na)
        formatted_table["同比增长"] = formatted_table["同比增长"].map(percent_or_na)
        formatted_table["销售贡献率"] = formatted_table["销售贡献率"].map(percent)
        formatted_table["平均订单金额"] = formatted_table["平均订单金额"].map(money)
        formatted_table["月均订单数"] = formatted_table["月均订单数"].map(lambda v: f"{v:.1f}")
        download_table = filtered_table.copy()
        download_table["月度趋势"] = download_table["月度趋势"].map(lambda values: ", ".join(f"{v:.2f}" for v in values) if isinstance(values, list) else "")
        st.download_button(
            "下载当前筛选客户汇总 CSV",
            download_table.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"customer_summary_{date.today():%Y%m%d}.csv",
            mime="text/csv",
        )
        st.dataframe(
            formatted_table[
                [
                    "客户名称",
                    "客户代码",
                    "销售额",
                    "毛利",
                    "订单数",
                    "平均订单金额",
                    "ABC 等级",
                    "同比增长",
                    "月度趋势",
                    "客户类型",
                    "销售贡献率",
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
            column_config={
                "月度趋势": st.column_config.LineChartColumn("月度趋势", help="Monthly Trend：当前筛选范围内每月销售额变化。"),
            },
        )

section_header("单客户详情")
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
