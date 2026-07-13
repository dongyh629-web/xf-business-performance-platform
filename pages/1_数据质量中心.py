from pathlib import Path

import pandas as pd
import streamlit as st

from app.data import apply_date_basis, load_processed_data
from app.ui import show_code_warning, show_context_summary, show_filters


DATA_PATH = Path("data/processed/latest_sales.parquet")

st.set_page_config(page_title="数据质量中心", layout="wide")
st.title("数据质量中心")

df = st.session_state.get("clean_data")
if df is None:
    df = load_processed_data(DATA_PATH)
    if df is not None:
        st.session_state["data_source"] = "local_processed"

if df is None:
    st.info("当前暂无销售数据，请回到首页上传 Unleashed 导出的 Excel 文件开始分析。")
    st.stop()

filtered = show_filters(df, "quality")
show_code_warning(filtered)
show_context_summary(filtered)

st.subheader("当前已清洗数据概览")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("清洗后行数", f"{len(filtered):,}")
c2.metric("订单数", f"{filtered['Order No.'].nunique():,}")
c3.metric("客户数", f"{filtered['Customer Key'].nunique() if 'Customer Key' in filtered.columns else filtered['Customer'].nunique():,}")
c4.metric("产品数", f"{filtered['Product Key'].nunique() if 'Product Key' in filtered.columns else filtered['Product'].nunique():,}")
c5.metric("销售额", f"£{filtered['Sales Amount'].sum():,.0f}")

st.subheader("异常提醒")
suspicious_duplicates = df[df["Suspicious Duplicate"].eq(True)] if "Suspicious Duplicate" in df.columns else pd.DataFrame()
date_logic_anomalies = df[df["Date Logic Anomaly"].eq(True)] if "Date Logic Anomaly" in df.columns else pd.DataFrame()
missing_customer_code = df[df["Customer Code"].isna()] if "Customer Code" in df.columns else df
missing_product_code = df[df["Product Code"].isna()] if "Product Code" in df.columns else df
checks = pd.DataFrame(
    [
        {"异常类型": "缺客户类型", "数量": int(df["Customer Type"].isna().sum())},
        {"异常类型": "缺产品组", "数量": int(df["Product Group"].isna().sum())},
        {"异常类型": "0 金额行（赠品/样品）", "数量": int(df["Sales Amount"].eq(0).sum())},
        {"异常类型": "缺 Order Date", "数量": int(df["Order Date"].isna().sum())},
        {"异常类型": "缺 Required Date", "数量": int(df["Required Date"].isna().sum())},
        {"异常类型": "缺 Completed Date", "数量": int(df["Completed Date"].isna().sum())},
        {"异常类型": "缺 Customer Code", "数量": int(len(missing_customer_code)), "影响金额": float(missing_customer_code["Sales Amount"].sum())},
        {"异常类型": "缺 Product Code / SKU", "数量": int(len(missing_product_code)), "影响金额": float(missing_product_code["Sales Amount"].sum())},
        {"异常类型": "负金额", "数量": int(df["Sales Amount"].lt(0).sum())},
        {"异常类型": "负数量", "数量": int(df["Quantity"].lt(0).sum())},
        {"异常类型": "缺 Quantity Unit（无法确认统一单位）", "数量": int(df["Quantity Unit"].isna().sum()) if "Quantity Unit" in df.columns else len(df)},
        {"异常类型": "疑似重复行（未自动删除）", "数量": int(len(suspicious_duplicates))},
        {"异常类型": "日期逻辑异常：Completed Date 早于 Order Date", "数量": int(len(date_logic_anomalies))},
        {"异常类型": "缺 Area（后续补充）", "数量": int(df["Area"].isna().sum()) if "Area" in df.columns else len(df)},
    ]
)
st.dataframe(checks, width="stretch", hide_index=True)

if not suspicious_duplicates.empty:
    duplicate_group_cols = ["Order No.", "Customer", "Product", "Quantity", "Sales Amount"]
    if "Customer Code" in suspicious_duplicates.columns:
        duplicate_group_cols[1] = "Customer Code"
    if "Product Code" in suspicious_duplicates.columns:
        duplicate_group_cols[2] = "Product Code"
    duplicate_groups = (
        suspicious_duplicates.groupby(duplicate_group_cols, dropna=False)
        .agg(出现次数=("Order No.", "size"), 涉及销售额=("Sales Amount", "sum"))
        .reset_index()
        .sort_values(["出现次数", "涉及销售额"], ascending=False)
    )
    group_count = suspicious_duplicates[duplicate_group_cols].drop_duplicates().shape[0]
    impact_amount = suspicious_duplicates["Sales Amount"].sum()
    st.warning(f"发现疑似重复组 {group_count:,} 组，影响金额 £{impact_amount:,.2f}。当前系统未自动删除这些行。")
    st.dataframe(duplicate_groups, width="stretch", hide_index=True)
    st.download_button(
        "下载疑似重复明细 CSV",
        suspicious_duplicates.to_csv(index=False).encode("utf-8-sig"),
        file_name="suspicious_duplicates.csv",
        mime="text/csv",
    )

with st.expander("查看 0 金额行"):
    st.dataframe(df[df["Sales Amount"].eq(0)].head(300), width="stretch")

with st.expander("查看缺客户类型行"):
    st.dataframe(df[df["Customer Type"].isna()].head(300), width="stretch")

with st.expander("查看缺产品组行"):
    st.dataframe(df[df["Product Group"].isna()].head(300), width="stretch")

with st.expander("查看疑似重复明细（当前未自动删除）"):
    if suspicious_duplicates.empty:
        st.caption("当前筛选范围内没有疑似重复行。")
    else:
        st.dataframe(suspicious_duplicates.head(500), width="stretch")

with st.expander("查看日期逻辑异常（Completed Date 早于 Order Date）"):
    if date_logic_anomalies.empty:
        st.caption("当前没有日期逻辑异常。")
    else:
        cols = [
            "Order No.",
            "Customer Code",
            "Customer Name",
            "Product Code",
            "Product Name",
            "Order Date",
            "Required Date",
            "Completed Date",
            "Sub Total",
        ]
        existing_cols = [col for col in cols if col in date_logic_anomalies.columns]
        st.dataframe(date_logic_anomalies[existing_cols], width="stretch")
        st.download_button(
            "下载日期逻辑异常 CSV",
            date_logic_anomalies[existing_cols].to_csv(index=False).encode("utf-8-sig"),
            file_name="date_logic_anomalies.csv",
            mime="text/csv",
        )

with st.expander("查看三种日期口径月度销售额对比"):
    basis_frames = []
    for basis in ["Order Date", "Required Date", "Completed Date"]:
        basis_df = apply_date_basis(df, basis)
        monthly = basis_df.groupby("Performance Month", dropna=False).agg(
            销售额=("Sales Amount", "sum"),
            订单数=("Order No.", "nunique"),
            明细行数=("Order No.", "size"),
        )
        monthly.columns = [f"{basis} {col}" for col in monthly.columns]
        basis_frames.append(monthly)
    comparison = pd.concat(basis_frames, axis=1).fillna(0).reset_index().rename(columns={"Performance Month": "月份"})
    st.dataframe(comparison, width="stretch", hide_index=True)

with st.expander("查看月份移动明细"):
    moved = df[
        (df["Order Date"].dt.to_period("M") != df["Completed Date"].dt.to_period("M"))
        | (df["Required Date"].dt.to_period("M") != df["Completed Date"].dt.to_period("M"))
    ].copy()
    if moved.empty:
        st.caption("当前没有跨月份移动记录。")
    else:
        moved["Order Month"] = moved["Order Date"].dt.to_period("M").astype("string")
        moved["Required Month"] = moved["Required Date"].dt.to_period("M").astype("string")
        moved["Completed Month"] = moved["Completed Date"].dt.to_period("M").astype("string")
        movement = (
            moved.groupby(["Order Month", "Required Month", "Completed Month"], dropna=False)
            .agg(涉及销售额=("Sales Amount", "sum"), 移动订单数=("Order No.", "nunique"), 明细行数=("Order No.", "size"))
            .reset_index()
            .sort_values("涉及销售额", ascending=False)
        )
        st.dataframe(movement, width="stretch", hide_index=True)
