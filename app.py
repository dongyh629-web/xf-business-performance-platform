from io import BytesIO
from textwrap import dedent

import streamlit as st

from app.auth import local_preview_login, redirect_to_post_login_page, render_logout_button, render_user_sidebar, require_login, role_allows
from app.business_dashboard import render_business_dashboard
from app.data import import_excel, monthly_sales, top_entity_table, top_table
from app.google_drive import (
    MANUAL_SOURCE_LABEL,
    ensure_drive_data_loaded,
    render_data_source_sidebar,
    store_sales_import_in_session,
    store_target_workbook_in_session,
)
from app.target_metrics import analyze_target_workbook, parse_xf_target_workbook, workbook_looks_like_sales_data
from app.ui import bar_chart, donut_chart, inject_global_styles, line_chart, metric_row, safe_page_link, section_header, show_code_warning, show_filters


st.set_page_config(page_title="XF Business Dashboard", page_icon="📊", layout="wide")
inject_global_styles()

if st.query_params.get("auth_preview"):
    local_preview_login(str(st.query_params.get("auth_preview")))

auth_user = require_login("overview")
redirect_to_post_login_page()

def render_home_page() -> None:
    st.title("首页概况")
    st.caption("鲜锋经营驾驶舱 · XF Business Dashboard")

    drive_status = ensure_drive_data_loaded()
    uploaded, uploaded_target = render_data_source_sidebar(show_uploaders=True)

    if uploaded is not None:
        try:
            with st.spinner("正在读取和清洗 Excel..."):
                result = import_excel(uploaded)
                store_sales_import_in_session(result, getattr(uploaded, "name", "销售明细 Excel"), MANUAL_SOURCE_LABEL, "manual")
            st.success(f"导入完成：已识别工作表 `{result.sheet_name}`")
        except ValueError as exc:
            looks_like_target = False
            try:
                parse_xf_target_workbook(BytesIO(uploaded.getvalue()))
                looks_like_target = True
            except Exception:
                looks_like_target = False
            try:
                target_analysis = analyze_target_workbook(BytesIO(uploaded.getvalue()))
            except Exception:
                target_analysis = None
            if looks_like_target or (target_analysis and target_analysis.candidates):
                st.error("该文件看起来不像 Unleashed 销售明细，可能是目标表。请前往‘经营追踪’页面上传目标 Excel。")
            else:
                st.error(str(exc))
            st.stop()
        except Exception:
            st.error("导入失败：请确认文件是 Unleashed 导出的 Excel，且字段结构未发生变化。详细错误已记录在开发日志中。")
            st.stop()

    if uploaded_target is not None:
        target_bytes = uploaded_target.getvalue()
        try:
            parsed_target = parse_xf_target_workbook(BytesIO(target_bytes))
        except ValueError as exc:
            if workbook_looks_like_sales_data(BytesIO(target_bytes)):
                st.error("该文件看起来像销售明细，不像目标表。请使用左侧‘上传销售明细’入口。")
            else:
                st.error(str(exc))
        except Exception:
            st.error("目标 Excel 导入失败，请确认文件是 XF 销售目标模板。")
        else:
            store_target_workbook_in_session(parsed_target, uploaded_target.name, MANUAL_SOURCE_LABEL, "manual")
            st.success("目标数据已导入当前会话。")
            st.rerun()

    df = st.session_state.get("clean_data")

    if df is None:
        if not drive_status.configured:
            st.info("Google Drive 尚未配置。请配置 Streamlit Secrets，或使用左侧手动上传销售明细。")
        elif drive_status.sales.status == "failed":
            st.warning(drive_status.sales.message)
            st.info("当前暂无销售数据，可使用左侧手动上传销售明细作为备用。")
        else:
            st.info("当前暂无销售数据，请使用左侧手动上传销售明细，或点击“刷新 Google Drive 数据”。")
        st.stop()

    filtered = show_filters(df, "home")

    show_code_warning(filtered)
    quality = st.session_state.get("quality", {})
    if quality:
        if quality.get("日期质量警告"):
            st.warning(str(quality["日期质量警告"]))

    render_business_dashboard(filtered)
    st.divider()

    section_header("趋势和结构")

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

    with st.expander("查看当前筛选数据概览"):
        metric_row(filtered)

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


home_page = st.Page(render_home_page, title="首页", icon="🏠", default=True)
sales_tracking_page = st.Page("pages/4_经营追踪.py", title="销售经营")
product_range_page = st.Page("pages/6_产品系列经营追踪.py", title="产品系列")
customer_analysis_page = st.Page("pages/2_客户分析.py", title="客户分析")
customer_health_page = st.Page("pages/5_客户健康.py", title="客户健康")
product_analysis_page = st.Page("pages/3_产品分析.py", title="产品分析")
data_quality_page = st.Page("pages/1_数据质量中心.py", title="数据质量")

pages = {"": [home_page]}
if role_allows(auth_user.role, "sales"):
    pages["📈 销售"] = [sales_tracking_page, product_range_page]
if role_allows(auth_user.role, "customers"):
    pages["👥 客户"] = [customer_analysis_page, customer_health_page]
if role_allows(auth_user.role, "products"):
    pages["📦 产品"] = [product_analysis_page]
if role_allows(auth_user.role, "system"):
    pages["⚙️ 系统"] = [data_quality_page]

current_page = st.navigation(pages, position="hidden")


NAV_GROUPS = [
    {
        "key": "sales",
        "area": "sales",
        "label": "📈 销售",
        "english": "Sales",
        "items": [
            {"title": "销售经营", "english": "Sales Performance", "page": "pages/4_经营追踪.py"},
            {"title": "产品系列", "english": "Product Range", "page": "pages/6_产品系列经营追踪.py"},
        ],
    },
    {
        "key": "customers",
        "area": "customers",
        "label": "👥 客户",
        "english": "Customers",
        "items": [
            {"title": "客户分析", "english": "Customer Analysis", "page": "pages/2_客户分析.py"},
            {"title": "客户健康", "english": "Customer Health", "page": "pages/5_客户健康.py"},
        ],
    },
    {
        "key": "products",
        "area": "products",
        "label": "📦 产品",
        "english": "Products",
        "items": [
            {"title": "产品分析", "english": "Product Analysis", "page": "pages/3_产品分析.py"},
        ],
    },
    {
        "key": "system",
        "area": "system",
        "label": "⚙️ 系统",
        "english": "System",
        "items": [
            {"title": "数据质量", "english": "Data Quality", "page": "pages/1_数据质量中心.py"},
        ],
    },
]


def render_sidebar_navigation() -> None:
    current_title = getattr(current_page, "title", "首页")
    visible_groups = [group for group in NAV_GROUPS if role_allows(auth_user.role, str(group["area"]))]

    with st.sidebar:
        brand_html = dedent(
            """
            <div class="xf-sidebar-brand">
                <div class="xf-sidebar-brand-title">鲜锋经营驾驶舱</div>
                <div class="xf-sidebar-brand-subtitle">XF Business Dashboard</div>
            </div>
            """
        ).strip()
        st.markdown(brand_html, unsafe_allow_html=True)
        render_user_sidebar()
        with st.container(key="sidebar_home_link"):
            st.page_link(home_page, label="首页 · Home", icon="🏠")
        st.markdown('<div class="xf-nav-divider"></div>', unsafe_allow_html=True)
        for group in visible_groups:
            group_key = str(group["key"])
            state_key = f"sidebar_group_{group_key}_open"
            is_current_group = any(item["title"] == current_title for item in group["items"])
            if state_key not in st.session_state:
                st.session_state[state_key] = is_current_group
            elif is_current_group:
                st.session_state[state_key] = True

            is_open = bool(st.session_state[state_key])
            arrow = "⌄" if is_open else "›"
            with st.container(key=f"sidebar_group_row_{group_key}"):
                toggle_clicked = st.button(
                    f"{group['label']}  {group['english']}  {arrow}",
                    key=f"sidebar_group_toggle_{group_key}",
                    use_container_width=True,
                )
            if toggle_clicked:
                st.session_state[state_key] = not is_open
                st.rerun()

            if st.session_state[state_key]:
                with st.container():
                    for item in group["items"]:
                        safe_page_link(
                            str(item["page"]),
                            label=f"{item['title']} · {item['english']}",
                        )
            st.markdown('<div class="xf-nav-group-gap"></div>', unsafe_allow_html=True)
        render_logout_button()


render_sidebar_navigation()
current_page.run()
