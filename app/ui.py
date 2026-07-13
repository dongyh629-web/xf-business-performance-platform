from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from datetime import date, datetime

from app.config import DATA_PATH, DATE_BASIS_DESCRIPTIONS, DATE_BASIS_LABELS, DATE_BASIS_OPTIONS
from app.data import apply_date_basis


def money(value: float) -> str:
    return f"£{value:,.0f}"


def number(value: float) -> str:
    return f"{value:,.0f}"


def percent(value: float) -> str:
    return f"{value:.1%}"


def days(value: float | None) -> str:
    if pd.isna(value):
        return "仅1单"
    return f"约 {value:.1f} 天"


def date_text(value) -> str:
    if pd.isna(value):
        return ""
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def compact_name(value: object, max_len: int = 34) -> str:
    text = "未分类" if pd.isna(value) else str(value)
    if " — " in text:
        text = text.split(" — ", 1)[1]
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def _first_valid_date(df: pd.DataFrame, col: str):
    if col not in df.columns:
        return None
    values = pd.to_datetime(df[col], errors="coerce").dropna()
    if values.empty:
        return None
    return values.min().date(), values.max().date()


def _reset_filter_state(key_prefix: str, min_date, max_date) -> None:
    st.session_state["date_basis"] = "Completed Date"
    st.session_state[f"{key_prefix}_date_range"] = (min_date, max_date) if min_date and max_date else None
    st.session_state[f"{key_prefix}_all_customer_types"] = True
    st.session_state[f"{key_prefix}_all_product_groups"] = True
    st.session_state[f"{key_prefix}_selected_customer_types"] = []
    st.session_state[f"{key_prefix}_selected_product_groups"] = []


def show_filters(df: pd.DataFrame, key_prefix: str = "main") -> pd.DataFrame:
    completed_range = _first_valid_date(df, "Completed Date")
    data_range = completed_range or _first_valid_date(df, "Order Date") or _first_valid_date(df, "Required Date")
    range_text = f"{data_range[0]} 至 {data_range[1]}" if data_range else "无有效日期"
    current_file = st.session_state.get("current_file_name")
    data_source = st.session_state.get("data_source")
    last_updated = st.session_state.get("data_last_updated")
    if not last_updated and DATA_PATH.exists():
        last_updated = datetime.fromtimestamp(DATA_PATH.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    if data_source == "uploaded" and current_file:
        source_text = "来源：latest_sales.parquet"
        data_label = "已加载"
    elif data_source == "persistent" or data_source == "local_processed" or not current_file:
        source_text = "来源：latest_sales.parquet"
        data_label = "已加载"
    else:
        source_text = "当前暂无数据，请上传 Unleashed Excel 文件。"
        data_label = "暂无数据"

    with st.sidebar:
        st.markdown("### 数据状态")
        st.caption(f"当前数据：{data_label}")
        st.caption(source_text)
        if last_updated:
            st.caption(f"最后更新时间：{last_updated}")
        st.caption(f"数据日期范围：{range_text}")

        st.markdown("### 日期")
        current_basis = st.session_state.get("date_basis", "Completed Date")
        if current_basis not in DATE_BASIS_OPTIONS:
            current_basis = "Completed Date"
        selected_basis = st.selectbox(
            "分析日期口径\nDate Basis",
            DATE_BASIS_OPTIONS,
            index=DATE_BASIS_OPTIONS.index(current_basis),
            key="date_basis",
            format_func=lambda value: DATE_BASIS_LABELS.get(value, value),
        )
        st.caption(DATE_BASIS_DESCRIPTIONS[selected_basis])

    basis_df = apply_date_basis(df, selected_basis)
    if basis_df["Performance Date"].isna().any():
        st.warning(f"当前选择的日期字段 `{selected_basis}` 存在缺失或无效值，请到数据质量中心查看明细。")

    min_date = basis_df["Performance Date"].dropna().min()
    max_date = basis_df["Performance Date"].dropna().max()
    month_values = basis_df["Month"].fillna("未分类").astype(str)
    customer_type_values = basis_df["Customer Type"].fillna("未分类").astype(str)
    product_group_values = basis_df["Product Group"].fillna("未分类").astype(str)

    with st.sidebar:
        if pd.notna(min_date) and pd.notna(max_date):
            selected_range = st.date_input(
                "日期范围",
                value=(min_date.date(), max_date.date()),
                min_value=min_date.date(),
                max_value=max_date.date(),
                key=f"{key_prefix}_date_range",
            )
        else:
            selected_range = None
        customer_types = sorted(customer_type_values.unique().tolist())
        product_groups = sorted(product_group_values.unique().tolist())

        st.markdown("### 客户")
        all_customer_types = st.checkbox("全部客户类型", value=True, key=f"{key_prefix}_all_customer_types")
        if all_customer_types:
            selected_customer_types = customer_types
            st.caption("客户类型：全部")
        else:
            with st.expander("选择客户类型", expanded=True):
                selected_customer_types = st.multiselect(
                    "客户类型",
                    customer_types,
                    default=st.session_state.get(f"{key_prefix}_selected_customer_types", []),
                    key=f"{key_prefix}_selected_customer_types",
                )
                st.caption(f"已选 {len(selected_customer_types)} 项")

        st.markdown("### 产品")
        all_product_groups = st.checkbox("全部产品组", value=True, key=f"{key_prefix}_all_product_groups")
        if all_product_groups:
            selected_product_groups = product_groups
            st.caption("产品组：全部")
        else:
            with st.expander("选择产品组", expanded=True):
                selected_product_groups = st.multiselect(
                    "产品组",
                    product_groups,
                    default=st.session_state.get(f"{key_prefix}_selected_product_groups", []),
                    key=f"{key_prefix}_selected_product_groups",
                )
                st.caption(f"已选 {len(selected_product_groups)} 项")

        st.divider()
        if st.button("重置全部筛选", key=f"{key_prefix}_reset_filters", use_container_width=True):
            _reset_filter_state(key_prefix, min_date.date() if pd.notna(min_date) else None, max_date.date() if pd.notna(max_date) else None)
            st.rerun()

    date_mask = pd.Series(True, index=basis_df.index)
    if isinstance(selected_range, tuple) and len(selected_range) == 2:
        start, end = selected_range
        date_values = basis_df["Performance Date"].dt.date
        date_mask = date_values.ge(start) & date_values.le(end)

    filtered = basis_df[date_mask & customer_type_values.isin(selected_customer_types) & product_group_values.isin(selected_product_groups)].copy()
    filtered.attrs["date_basis"] = selected_basis
    if isinstance(selected_range, tuple) and len(selected_range) == 2:
        filtered.attrs["date_range"] = (str(selected_range[0]), str(selected_range[1]))
    else:
        filtered.attrs["date_range"] = None
    filtered.attrs["customer_types"] = selected_customer_types
    filtered.attrs["product_groups"] = selected_product_groups
    filtered.attrs["all_customer_type_count"] = len(customer_types)
    filtered.attrs["all_product_group_count"] = len(product_groups)
    return filtered


def show_code_warning(df: pd.DataFrame) -> None:
    missing_customer_code = "Customer Code" not in df.columns or df["Customer Code"].isna().any()
    missing_product_code = "Product Code" not in df.columns or df["Product Code"].isna().any()
    if missing_customer_code or missing_product_code:
        st.warning("当前源文件缺少唯一代码，分析暂按名称统计，可能存在重名、改名或空格导致的误差。")


def show_context_summary(df: pd.DataFrame) -> None:
    basis = df.attrs.get("date_basis", st.session_state.get("date_basis", "Completed Date"))
    basis_label = DATE_BASIS_LABELS.get(basis, basis)
    desc = DATE_BASIS_DESCRIPTIONS.get(basis, "")
    valid_dates = df["Performance Date"].dropna() if "Performance Date" in df.columns else pd.Series(dtype="datetime64[ns]")
    if valid_dates.empty:
        date_text = "无有效日期"
    else:
        date_text = f"{valid_dates.min().date()} 至 {valid_dates.max().date()}"
    customer_types = df.attrs.get("customer_types", [])
    product_groups = df.attrs.get("product_groups", [])
    all_customer_count = df.attrs.get("all_customer_type_count")
    all_product_count = df.attrs.get("all_product_group_count")
    customer_text = "全部" if all_customer_count is None or len(customer_types) == all_customer_count else f"已选 {len(customer_types)} 项"
    product_text = "全部" if all_product_count is None or len(product_groups) == all_product_count else f"已选 {len(product_groups)} 项"
    cols = st.columns(4)
    cols[0].caption(f"日期范围\n\n**{date_text}**")
    cols[1].caption(f"日期口径\n\n**{basis_label}**")
    cols[2].caption(f"客户类型\n\n**{customer_text}**")
    cols[3].caption(f"产品组\n\n**{product_text}**")
    st.caption(desc)
    if not valid_dates.empty:
        latest_date = valid_dates.max().date()
        today = date.today()
        if latest_date.year == today.year and latest_date.month == today.month:
            st.warning(f"{latest_date:%Y-%m} 为截至 {today:%Y-%m-%d} 的部分月份数据，避免与完整月份直接比较。")


def metric_row(df: pd.DataFrame) -> None:
    sales = df["Sales Amount"].sum()
    orders = df["Order No."].nunique()
    customer_dim = "Customer Key" if "Customer Key" in df.columns else "Customer"
    product_dim = "Product Key" if "Product Key" in df.columns else "Product"
    customers = df[customer_dim].nunique()
    products = df[product_dim].nunique()
    avg_order = sales / orders if orders else 0

    metrics = [
        ("销售额", money(sales)),
        ("订单数", number(orders)),
        ("客户数", number(customers)),
        ("产品数", number(products)),
        ("平均订单金额", money(avg_order)),
    ]
    cols = st.columns(len(metrics))
    for col, (label, value) in zip(cols, metrics):
        col.metric(label, value)


def metric_cards(metrics: list[tuple[str, str]]) -> None:
    if not metrics:
        return
    cols = st.columns(len(metrics))
    for col, (label, value) in zip(cols, metrics):
        col.metric(label, value)


def style_plotly(fig):
    fig.update_layout(
        font=dict(size=12, color="#374151"),
        paper_bgcolor="white",
        plot_bgcolor="white",
        margin=dict(l=12, r=12, t=48, b=18),
        title=dict(font=dict(size=16, color="#111827")),
        legend=dict(font=dict(size=11)),
    )
    fig.update_xaxes(showgrid=False, zeroline=False, title_font=dict(size=11), tickfont=dict(size=11))
    fig.update_yaxes(showgrid=False, zeroline=False, title_font=dict(size=11), tickfont=dict(size=11))
    return fig


def line_chart(data: pd.DataFrame, x: str, y: str, title: str):
    fig = px.line(data, x=x, y=y, markers=True, title=title)
    fig.update_traces(line=dict(color="#2563EB", width=2.5), marker=dict(size=6))
    fig.update_yaxes(tickprefix="£", separatethousands=True)
    fig.update_layout(height=340)
    return style_plotly(fig)


def bar_chart(data: pd.DataFrame, x: str, y: str, title: str, orientation: str = "v"):
    plot_data = data.copy()
    if orientation == "h":
        label_col = y
        if label_col in plot_data.columns:
            plot_data["_Display Label"] = plot_data[label_col].map(compact_name)
            plot_data = plot_data.sort_values(x, ascending=True)
            fig = px.bar(
                plot_data,
                x=x,
                y="_Display Label",
                title=title,
                orientation="h",
                hover_data={label_col: True, x: ":,.2f", "_Display Label": False},
            )
            fig.update_yaxes(title="")
        else:
            fig = px.bar(plot_data, x=x, y=y, title=title, orientation=orientation)
    else:
        fig = px.bar(plot_data, x=x, y=y, title=title, orientation=orientation)
    fig.update_traces(marker_color="#2563EB", hovertemplate="%{y}<br>销售额：£%{x:,.2f}<extra></extra>" if orientation == "h" else None)
    fig.update_xaxes(tickprefix="£", separatethousands=True, title="销售额" if orientation == "h" else None)
    fig.update_layout(height=max(320, min(560, 26 * len(plot_data) + 120)))
    return style_plotly(fig)


def donut_chart(data: pd.DataFrame, names: str, values: str, title: str):
    plot_data = data.copy()
    plot_data[names] = plot_data[names].fillna("未分类").astype(str).replace({"<NA>": "未分类", "nan": "未分类"})
    fig = px.pie(plot_data, names=names, values=values, title=title, hole=0.55)
    fig.update_traces(textposition="inside", textinfo="percent", hovertemplate="%{label}<br>销售额：£%{value:,.2f}<br>占比：%{percent}<extra></extra>")
    fig.update_layout(height=360)
    return style_plotly(fig)
