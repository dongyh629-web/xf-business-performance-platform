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
