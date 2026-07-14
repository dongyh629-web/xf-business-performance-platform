from __future__ import annotations

import os
from pathlib import Path


DATA_PATH = Path("data/processed/latest_sales.parquet")

# Deployment storage mode:
# - "persistent" saves the latest processed data to DATA_PATH after upload, so the
#   next app session can load the latest dataset without requiring another upload.
# - "session" remains available for temporary demos, but is no longer the default.
STORAGE_MODE = os.getenv("XF_STORAGE_MODE", "persistent").strip().lower()
PERSIST_UPLOADED_DATA = STORAGE_MODE == "persistent"

# Main performance date for sales reporting.
# Definition: Completed Date is the date/time when the order was completed in Unleashed.
# The system keeps Order Date, Required Date, and Completed Date separately.
PRIMARY_SALES_DATE = "Completed Date"
FALLBACK_SALES_DATE = "Required Date"
METHODOLOGY_VERSION = "sales_date_completed_no_auto_dedup_v1"

DATE_BASIS_OPTIONS = ["Completed Date", "Order Date", "Required Date"]
DATE_BASIS_LABELS = {
    "Completed Date": "完成日期（Completed Date）",
    "Order Date": "下单日期（Order Date）",
    "Required Date": "要求交货日期（Required Date）",
}
DATE_BASIS_DESCRIPTIONS = {
    "Order Date": "订单创建日期，适合分析订单产生和销售需求趋势",
    "Required Date": "客户要求交货日期，适合分析交付计划和需求排期",
    "Completed Date": "订单实际完成日期，适合分析已完成销售表现",
}

ABC_A_THRESHOLD = 0.70
ABC_B_THRESHOLD = 0.90

TRACKING_CLOSE_TO_TARGET_THRESHOLD = 0.90

# Customer Health settings.
NEW_CUSTOMER_START_DATE = "2026-07-01"
CUSTOMER_VALUE_DECLINE_THRESHOLD = 0.30
CUSTOMER_VALUE_DECLINE_MIN_AMOUNT = 500
CUSTOMER_FREQUENCY_DECLINE_THRESHOLD = 0.30
CUSTOMER_FREQUENCY_MIN_BASE_ORDERS = 4
CUSTOMER_MIN_ORDERS_FOR_INTERVAL = 4
CUSTOMER_INTERVAL_NORMAL_RATIO = 1.2
CUSTOMER_INTERVAL_WARNING_RATIO = 1.8
CUSTOMER_INTERVAL_HIGH_RISK_RATIO = 2.5
CUSTOMER_INTERVAL_STABILITY_VERY_STABLE_CV = 0.25
CUSTOMER_INTERVAL_STABILITY_STABLE_CV = 0.50
CUSTOMER_VALUE_IMPROVEMENT_THRESHOLD = 0.20
CUSTOMER_VALUE_IMPROVEMENT_MIN_AMOUNT = 500
CUSTOMER_FREQUENCY_IMPROVEMENT_THRESHOLD = 0.20
TARGET_YEAR_CANDIDATES = ["Year", "年份", "年度", "目标年度", "Calendar Year"]
TARGET_MONTH_CANDIDATES = ["Month", "月份", "月", "月份序号"]
TARGET_ORIGINAL_CANDIDATES = [
    "Target",
    "Sales Target",
    "Original Target",
    "Original Sales Target",
    "目标",
    "销售目标",
    "原始目标",
    "原始销售目标",
    "月度目标",
]
TARGET_REVISED_CANDIDATES = [
    "Revised Target",
    "Revised Sales Target",
    "Adjusted Target",
    "调整后目标",
    "调整销售目标",
    "修订目标",
]
TARGET_ANNUAL_CANDIDATES = [
    "Annual Target",
    "Year Target",
    "Annual Sales Target",
    "年度目标",
    "年度合计",
    "全年目标",
    "全年合计",
]
TARGET_NOTES_CANDIDATES = ["Notes", "Note", "备注", "说明"]

UI_TEXT = {
    "customer_count": "客户数",
    "total_sales": "总销售额",
    "order_count": "总订单数",
    "average_order_value": "平均订单金额",
    "top_10_customer_contribution": "Top 10 客户贡献率",
    "customer_code": "客户代码",
    "customer_name": "客户名称",
    "customer_type": "客户类型",
    "abc_class": "ABC 等级",
    "sales": "销售额",
    "sales_contribution": "销售贡献率",
    "first_order_date": "首次下单日期",
    "last_order_date": "最近下单日期",
    "average_order_gap": "平均订单间隔",
    "orders_per_month": "月均订单数",
    "product_count": "产品数",
    "product_group_count": "产品组数",
}

REQUIRED_COLUMNS = [
    "Order No.",
    "Order Date",
    "Required Date",
    "Completed Date",
    "Customer",
    "Customer Type",
    "Product",
    "Product Group",
    "Status",
    "Quantity",
    "Sub Total",
]

OPTIONAL_STANDARD_COLUMNS = ["Warehouse"]

CUSTOMER_CODE_CANDIDATES = [
    "Customer Code",
    "CustomerCode",
    "Customer ID",
    "CustomerID",
    "Account Code",
    "AccountCode",
]

PRODUCT_CODE_CANDIDATES = [
    "Product Code",
    "ProductCode",
    "SKU",
    "Product SKU",
    "Item Code",
    "ItemCode",
]

GROSS_PROFIT_CANDIDATES = [
    "Gross Profit",
    "GrossProfit",
    "Profit",
    "Gross Margin",
    "Margin",
]

LINE_ID_CANDIDATES = [
    "Line ID",
    "LineID",
    "Line No.",
    "Line Number",
    "LineNumber",
    "GUID",
    "Line GUID",
    "Sales Order Line ID",
]

UNIT_CANDIDATES = [
    "Unit",
    "UOM",
    "Unit of Measure",
    "Quantity Unit",
    "Sales Unit",
]
