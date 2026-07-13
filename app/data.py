from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd
import logging

from app.config import (
    CUSTOMER_CODE_CANDIDATES,
    DATE_BASIS_OPTIONS,
    FALLBACK_SALES_DATE,
    GROSS_PROFIT_CANDIDATES,
    LINE_ID_CANDIDATES,
    METHODOLOGY_VERSION,
    OPTIONAL_STANDARD_COLUMNS,
    PRIMARY_SALES_DATE,
    PRODUCT_CODE_CANDIDATES,
    REQUIRED_COLUMNS,
    UNIT_CANDIDATES,
)


logger = logging.getLogger(__name__)


@dataclass
class ImportResult:
    raw: pd.DataFrame
    clean: pd.DataFrame
    quality: dict[str, int | float | str]
    sheet_name: str
    comparison: dict[str, object]


def find_sales_sheet(excel_file) -> tuple[str, int]:
    try:
        workbook = pd.ExcelFile(excel_file)
    except Exception as exc:
        raise ValueError("新版 Excel 无法读取，请确认文件没有损坏，并且是 .xlsx 格式。") from exc
    for sheet_name in workbook.sheet_names:
        try:
            preview = pd.read_excel(excel_file, sheet_name=sheet_name, header=None, nrows=12)
        except Exception as exc:
            raise ValueError(f"读取工作表 `{sheet_name}` 失败，请检查该工作表格式。") from exc
        for row_index in range(len(preview)):
            values = [str(v).strip() for v in preview.iloc[row_index].dropna().tolist()]
            if {"Order No.", "Required Date", "Customer", "Product", "Sub Total"}.issubset(values):
                return sheet_name, row_index
    raise ValueError("没有找到 Unleashed 销售明细表。请确认文件中包含 Order No., Required Date, Customer, Product, Sub Total 等字段。")


def read_unleashed_excel(excel_file) -> tuple[pd.DataFrame, str]:
    sheet_name, header_row = find_sales_sheet(excel_file)
    try:
        raw = pd.read_excel(excel_file, sheet_name=sheet_name, header=header_row)
    except Exception as exc:
        raise ValueError(f"读取销售明细工作表 `{sheet_name}` 失败。") from exc
    raw = raw.dropna(how="all").copy()
    raw.columns = [str(col).strip() for col in raw.columns]
    ignored_empty_columns = []
    for col in list(raw.columns):
        if col.startswith("Unnamed") and raw[col].isna().all():
            ignored_empty_columns.append(col)
            raw = raw.drop(columns=[col])
    if ignored_empty_columns:
        logger.info("Ignored empty unnamed columns: %s", ignored_empty_columns)
    raw.attrs["ignored_empty_columns"] = ignored_empty_columns
    return raw, sheet_name


def validate_columns(raw: pd.DataFrame) -> list[str]:
    return [col for col in REQUIRED_COLUMNS if col not in raw.columns]


def _first_existing(columns: Iterable[str], candidates: list[str]) -> str | None:
    normalized = {str(col).strip().lower(): str(col).strip() for col in columns}
    for candidate in candidates:
        match = normalized.get(candidate.lower())
        if match:
            return match
    return None


def _prepare_base(raw: pd.DataFrame) -> pd.DataFrame:
    optional_columns = [
        col
        for col in [
            _first_existing(raw.columns, CUSTOMER_CODE_CANDIDATES),
            _first_existing(raw.columns, PRODUCT_CODE_CANDIDATES),
            _first_existing(raw.columns, GROSS_PROFIT_CANDIDATES),
            _first_existing(raw.columns, LINE_ID_CANDIDATES),
            _first_existing(raw.columns, UNIT_CANDIDATES),
        ]
        if col and col not in REQUIRED_COLUMNS
    ]
    standard_columns = REQUIRED_COLUMNS + [col for col in OPTIONAL_STANDARD_COLUMNS if col in raw.columns]
    df = raw[standard_columns + optional_columns].copy()
    for col in OPTIONAL_STANDARD_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA

    for col in ["Order Date", "Required Date", "Completed Date"]:
        df[col] = pd.to_datetime(df[col], errors="coerce")

    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce")
    df["Sub Total"] = pd.to_numeric(df["Sub Total"], errors="coerce")
    gross_profit_col = _first_existing(df.columns, GROSS_PROFIT_CANDIDATES)
    if gross_profit_col:
        df[gross_profit_col] = pd.to_numeric(df[gross_profit_col], errors="coerce")

    numeric_optional_columns = [col for col in [_first_existing(df.columns, GROSS_PROFIT_CANDIDATES)] if col]
    text_optional_columns = [col for col in optional_columns if col not in numeric_optional_columns]
    text_columns = ["Order No.", "Warehouse", "Customer", "Customer Type", "Product", "Product Group", "Status"] + text_optional_columns
    for col in text_columns:
        df[col] = df[col].astype("string").str.replace("\n", " ", regex=False).str.strip()

    return df


def _date_quality(df: pd.DataFrame) -> dict[str, object]:
    completed_exists = "Completed Date" in df.columns
    total = len(df)
    completed_missing = int(df["Completed Date"].isna().sum()) if completed_exists else total
    completed_valid = total - completed_missing if completed_exists else 0
    completed_before_order = int((df["Completed Date"] < df["Order Date"]).sum()) if completed_exists else 0
    diff_days = (df["Completed Date"].dt.normalize() - df["Required Date"].dt.normalize()).dt.days if completed_exists else pd.Series(dtype="float")
    moved_months = int((df["Completed Date"].dt.to_period("M") != df["Required Date"].dt.to_period("M")).sum()) if completed_exists else 0
    return {
        "Completed Date 存在": bool(completed_exists),
        "Completed Date 缺失行": completed_missing,
        "Completed Date 缺失率": float(completed_missing / total) if total else 0.0,
        "Completed Date 有效率": float(completed_valid / total) if total else 0.0,
        "Completed Date 早于 Order Date 行": completed_before_order,
        "Completed Date 与 Required Date 月份不同订单行": moved_months,
        "Completed Date-Required Date 天数 最小值": float(diff_days.min()) if not diff_days.dropna().empty else 0.0,
        "Completed Date-Required Date 天数 中位数": float(diff_days.median()) if not diff_days.dropna().empty else 0.0,
        "Completed Date-Required Date 天数 最大值": float(diff_days.max()) if not diff_days.dropna().empty else 0.0,
    }


def _choose_sales_date(df: pd.DataFrame) -> tuple[str, dict[str, object]]:
    quality = _date_quality(df)
    can_use_completed = (
        quality["Completed Date 存在"]
        and quality["Completed Date 缺失率"] <= 0.01
        and quality["Completed Date 有效率"] >= 0.99
    )
    selected = PRIMARY_SALES_DATE if can_use_completed else FALLBACK_SALES_DATE
    quality["当前主业绩日期"] = selected
    quality["日期口径说明"] = (
        "Completed Date：订单在 Unleashed 中完成的日期/时间。"
        if selected == "Completed Date"
        else "Required Date：订单需求日期。Completed Date 质量不足时临时回退。"
    )
    if quality["Completed Date 早于 Order Date 行"]:
        quality["日期质量警告"] = "存在 Completed Date 早于 Order Date 的异常行，需人工复核。"
    return selected, quality


def apply_date_basis(df: pd.DataFrame, date_basis: str) -> pd.DataFrame:
    if date_basis not in DATE_BASIS_OPTIONS:
        raise ValueError(f"不支持的日期口径：{date_basis}")
    if date_basis not in df.columns:
        raise ValueError(f"当前数据缺少日期字段：{date_basis}")
    result = df.copy()
    result["Performance Date"] = pd.to_datetime(result[date_basis], errors="coerce")
    result["Sales Date"] = result["Performance Date"]
    result["Performance Month"] = result["Performance Date"].dt.to_period("M").astype("string")
    result["Month"] = result["Performance Month"]
    result["Year"] = result["Performance Date"].dt.year
    result.attrs["date_basis"] = date_basis
    return result


def _legacy_clean_sales_data(raw: pd.DataFrame) -> pd.DataFrame:
    df = _prepare_base(raw)
    totals_rows = df["Status"].str.lower().eq("totals").fillna(False) | df["Order No."].str.lower().eq("totals").fillna(False)
    blank_key_rows = df["Order No."].isna() | df["Customer"].isna() | df["Product"].isna()
    non_completed_rows = ~df["Status"].eq("Completed").fillna(False)
    clean = df.loc[~totals_rows & ~blank_key_rows & ~non_completed_rows].copy()
    clean = clean.drop_duplicates(subset=["Order No.", "Customer", "Product", "Quantity", "Sub Total"], keep="first")
    clean["Sales Date"] = clean["Required Date"]
    clean["Month"] = clean["Sales Date"].dt.to_period("M").astype("string")
    clean["Sales Amount"] = clean["Sub Total"].fillna(0)
    return clean


def _comparison(raw: pd.DataFrame, clean: pd.DataFrame, restored_duplicate_rows: pd.Series, selected_date: str) -> dict[str, object]:
    old = _legacy_clean_sales_data(raw)
    new = clean
    old_sales = float(old["Sales Amount"].sum())
    new_sales = float(new["Sales Amount"].sum())
    diff = new_sales - old_sales
    monthly_old = old.groupby("Month", dropna=False)["Sales Amount"].sum()
    monthly_new = new.groupby("Month", dropna=False)["Sales Amount"].sum()
    monthly = (
        pd.concat([monthly_old.rename("旧销售额"), monthly_new.rename("新销售额")], axis=1)
        .fillna(0)
        .reset_index()
        .rename(columns={"index": "月份", "Month": "月份"})
    )
    monthly["差额"] = monthly["新销售额"] - monthly["旧销售额"]

    restored_amount = float(clean.loc[restored_duplicate_rows.reindex(clean.index, fill_value=False), "Sales Amount"].sum())
    moved = new[new["Required Date"].dt.to_period("M") != new[selected_date].dt.to_period("M")]
    return {
        "原始行数": int(len(raw)),
        "旧清洗后行数": int(len(old)),
        "新清洗后行数": int(len(new)),
        "旧销售额": old_sales,
        "新销售额": new_sales,
        "差额": diff,
        "差异比例": float(diff / old_sales) if old_sales else 0.0,
        "每月销售额差异": monthly.to_dict("records"),
        "因停止删除疑似重复而恢复的金额": restored_amount,
        "因日期口径变化而移动月份的订单数量": int(moved["Order No."].nunique()),
        "因日期口径变化而移动月份的金额": float(moved["Sales Amount"].sum()),
    }


def clean_sales_data(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int | float | str]]:
    missing = validate_columns(raw)
    if missing:
        raise ValueError(f"文件缺少必要字段：{', '.join(missing)}")
    if all(col not in raw.columns for col in ["Order Date", "Required Date", "Completed Date"]):
        raise ValueError("文件缺少 Order Date、Required Date、Completed Date，无法建立日期口径。")

    df = _prepare_base(raw)
    original_rows = len(df)
    customer_code_col = _first_existing(df.columns, CUSTOMER_CODE_CANDIDATES)
    product_code_col = _first_existing(df.columns, PRODUCT_CODE_CANDIDATES)
    line_id_col = _first_existing(df.columns, LINE_ID_CANDIDATES)
    unit_col = _first_existing(df.columns, UNIT_CANDIDATES)
    gross_profit_col = _first_existing(df.columns, GROSS_PROFIT_CANDIDATES)

    totals_rows = df["Status"].str.lower().eq("totals").fillna(False) | df["Order No."].str.lower().eq("totals").fillna(False)
    blank_key_rows = df["Order No."].isna() | df["Customer"].isna() | df["Product"].isna()
    non_completed_rows = ~df["Status"].eq("Completed").fillna(False)
    eligible = ~totals_rows & ~blank_key_rows & ~non_completed_rows
    selected_date, date_quality = _choose_sales_date(df.loc[eligible].copy())

    customer_dup_key = customer_code_col or "Customer"
    product_dup_key = product_code_col or "Product"
    if line_id_col:
        duplicate_subset = ["Order No.", line_id_col]
        duplicate_basis = f"Order No. + {line_id_col}"
    else:
        duplicate_subset = ["Order No.", customer_dup_key, product_dup_key, "Quantity", "Sub Total"]
        duplicate_basis = f"Order No. + {customer_dup_key} + {product_dup_key} + Quantity + Sub Total（无行唯一标识，仅疑似）"
    duplicate_group_rows = df.loc[eligible].duplicated(subset=duplicate_subset, keep=False)
    old_auto_drop_rows_in_eligible = df.loc[eligible].duplicated(subset=["Order No.", "Customer", "Product", "Quantity", "Sub Total"], keep="first")
    duplicate_rows = pd.Series(False, index=df.index)
    old_auto_drop_rows = pd.Series(False, index=df.index)
    duplicate_rows.loc[df.loc[eligible].index] = duplicate_group_rows.to_numpy()
    old_auto_drop_rows.loc[df.loc[eligible].index] = old_auto_drop_rows_in_eligible.to_numpy()
    duplicate_group_count = int(df.loc[duplicate_rows, duplicate_subset].drop_duplicates().shape[0]) if duplicate_rows.any() else 0
    zero_value_rows = df["Sub Total"].fillna(0).eq(0)

    clean = df.loc[eligible].copy()

    clean["Performance Date"] = pd.NaT
    clean["Sales Date"] = pd.NaT
    clean["Performance Month"] = pd.NA
    clean["Year"] = pd.NA
    clean["Month"] = pd.NA
    clean["Sales Amount"] = clean["Sub Total"].fillna(0)
    clean["Gross Profit"] = clean[gross_profit_col] if gross_profit_col else pd.NA
    clean["Quantity"] = clean["Quantity"]
    clean["Customer Name"] = clean["Customer"]
    clean["Product Name"] = clean["Product"]
    clean["Customer Code"] = clean[customer_code_col] if customer_code_col else pd.NA
    clean["Product Code"] = clean[product_code_col] if product_code_col else pd.NA
    clean["SKU"] = clean["Product Code"]
    clean["Quantity Unit"] = clean[unit_col] if unit_col else pd.NA
    clean["Customer Key"] = clean["Customer Code"].fillna(clean["Customer Name"])
    clean["Product Key"] = clean["Product Code"].fillna(clean["Product Name"])
    clean["Customer Label"] = clean["Customer Name"]
    clean.loc[clean["Customer Code"].notna(), "Customer Label"] = clean.loc[clean["Customer Code"].notna(), "Customer Code"].astype("string") + " — " + clean.loc[clean["Customer Code"].notna(), "Customer Name"].astype("string")
    clean["Product Label"] = clean["Product Name"]
    clean.loc[clean["Product Code"].notna(), "Product Label"] = clean.loc[clean["Product Code"].notna(), "Product Code"].astype("string") + " — " + clean.loc[clean["Product Code"].notna(), "Product Name"].astype("string")
    clean["Has Customer Code"] = clean["Customer Code"].notna()
    clean["Has Product Code"] = clean["Product Code"].notna()
    clean["Suspicious Duplicate"] = duplicate_rows.loc[clean.index].to_numpy()
    clean["Would Drop Under Old Dedup"] = old_auto_drop_rows.loc[clean.index].to_numpy()
    clean["Area"] = pd.NA

    clean = apply_date_basis(clean, selected_date)

    for date_col in ["Order Date", "Required Date", "Completed Date"]:
        total = len(clean)
        missing = int(clean[date_col].isna().sum()) if date_col in clean.columns else total
        quality_prefix = date_col
        date_stats = {
            f"{quality_prefix} 缺失行": missing,
            f"{quality_prefix} 缺失率": float(missing / total) if total else 0.0,
            f"{quality_prefix} 有效率": float((total - missing) / total) if total else 0.0,
        }
        if date_col in clean.columns:
            date_stats[f"{quality_prefix} 最小值"] = str(clean[date_col].min())
            date_stats[f"{quality_prefix} 最大值"] = str(clean[date_col].max())
        date_quality.update(date_stats)

    date_logic_anomalies = clean["Completed Date"].notna() & clean["Order Date"].notna() & clean["Completed Date"].lt(clean["Order Date"])
    clean["Date Logic Anomaly"] = date_logic_anomalies
    order_required_different = clean["Order Date"].ne(clean["Required Date"])
    order_required_month_different = clean["Order Date"].dt.to_period("M").ne(clean["Required Date"].dt.to_period("M"))

    quality = {
        "口径版本": METHODOLOGY_VERSION,
        "原始行数": int(original_rows),
        "清洗后行数": int(len(clean)),
        "排除汇总行": int(totals_rows.sum()),
        "排除关键字段空白行": int(blank_key_rows.sum()),
        "排除非 Completed 行": int(non_completed_rows.sum()),
        "疑似重复行": int(duplicate_rows.sum()),
        "疑似重复组": duplicate_group_count,
        "疑似重复影响金额": float(clean.loc[clean["Suspicious Duplicate"], "Sales Amount"].sum()),
        "疑似重复判断依据": duplicate_basis,
        "疑似重复处理方式": "仅标记与展示，当前系统未自动删除这些行。",
        "日期逻辑异常行": int(date_logic_anomalies.sum()),
        "Order Date 与 Required Date 不相等行": int(order_required_different.sum()),
        "Order Date 与 Required Date 月份不同订单行": int(order_required_month_different.sum()),
        "Order Date 与 Required Date 月销售额一致原因": "Order Date 与 Required Date 在当前清洗后数据中完全一致。" if int(order_required_different.sum()) == 0 else "存在日期不一致；若月份差异为 0，则月度销售额仍会一致。",
        "忽略的空白 Unnamed 列": ", ".join(raw.attrs.get("ignored_empty_columns", [])) or "无",
        "0 金额行": int(zero_value_rows.sum()),
        "缺客户类型行": int(df["Customer Type"].isna().sum()),
        "缺产品组行": int(df["Product Group"].isna().sum()),
        "日期缺失行": int(df[selected_date].isna().sum()),
        "缺 Customer Code 行": int(clean["Customer Code"].isna().sum()),
        "缺 Product Code 行": int(clean["Product Code"].isna().sum()),
        "客户代码字段": customer_code_col or "源文件缺少",
        "产品代码字段": product_code_col or "源文件缺少",
        "行唯一标识字段": line_id_col or "源文件缺少",
        "数量单位字段": unit_col or "源文件缺少",
        "毛利字段": gross_profit_col or "源文件缺少",
        "数量单位检查": "源文件缺少数量单位字段，首页不展示公司级总销量。" if not unit_col else f"检测到数量单位字段：{unit_col}",
        "主业绩日期": selected_date,
        "主业绩日期说明": date_quality["日期口径说明"],
        "销售额合计": float(clean["Sales Amount"].sum()),
        "订单数": int(clean["Order No."].nunique()),
        "客户数": int(clean["Customer Key"].nunique()),
        "产品数": int(clean["Product Key"].nunique()),
    }
    quality.update(date_quality)
    return clean, quality


def import_excel(excel_file) -> ImportResult:
    file_name = getattr(excel_file, "name", "unknown")
    try:
        logger.info("Import started file=%s stage=read_excel", file_name)
        raw, sheet_name = read_unleashed_excel(excel_file)
        logger.info("Import file=%s stage=clean_sales_data sheet=%s", file_name, sheet_name)
        clean, quality = clean_sales_data(raw)
        comparison = _comparison(raw, clean, clean.get("Would Drop Under Old Dedup", pd.Series(False, index=clean.index)), quality["主业绩日期"])
        return ImportResult(raw=raw, clean=clean, quality=quality, sheet_name=sheet_name, comparison=comparison)
    except Exception:
        logger.exception("Excel import failed file=%s", file_name)
        raise


def filter_data(
    df: pd.DataFrame,
    months: Iterable[str] | None = None,
    customer_types: Iterable[str] | None = None,
    product_groups: Iterable[str] | None = None,
) -> pd.DataFrame:
    filtered = df.copy()
    if months:
        filtered = filtered[filtered["Month"].isin(list(months))]
    if customer_types:
        filtered = filtered[filtered["Customer Type"].isin(list(customer_types))]
    if product_groups:
        filtered = filtered[filtered["Product Group"].isin(list(product_groups))]
    return filtered


def monthly_sales(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["Month", "Sales Amount", "Quantity", "Orders", "Customers", "Products"])
    customer_dim = "Customer Key" if "Customer Key" in df.columns else "Customer"
    product_dim = "Product Key" if "Product Key" in df.columns else "Product"
    grouped = (
        df.groupby("Month", dropna=False)
        .agg(
            **{
                "Sales Amount": ("Sales Amount", "sum"),
                "Quantity": ("Quantity", "sum"),
                "Orders": ("Order No.", "nunique"),
                "Customers": (customer_dim, "nunique"),
                "Products": (product_dim, "nunique"),
            }
        )
        .reset_index()
        .sort_values("Month")
    )
    return grouped


def top_table(df: pd.DataFrame, dimension: str, limit: int = 10) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[dimension, "Sales Amount", "Quantity", "Orders"])
    return (
        df.groupby(dimension, dropna=False)
        .agg(
            **{
                "Sales Amount": ("Sales Amount", "sum"),
                "Quantity": ("Quantity", "sum"),
                "Orders": ("Order No.", "nunique"),
            }
        )
        .reset_index()
        .sort_values("Sales Amount", ascending=False)
        .head(limit)
    )


def top_entity_table(df: pd.DataFrame, key_col: str, label_col: str, limit: int = 10) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[key_col, label_col, "Sales Amount", "Quantity", "Orders"])
    return (
        df.groupby(key_col, dropna=False)
        .agg(
            **{
                label_col: (label_col, "first"),
                "Sales Amount": ("Sales Amount", "sum"),
                "Quantity": ("Quantity", "sum"),
                "Orders": ("Order No.", "nunique"),
            }
        )
        .reset_index()
        .sort_values("Sales Amount", ascending=False)
        .head(limit)
    )


def save_processed_data(clean: pd.DataFrame, output_path: Path) -> None:
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        clean.to_parquet(output_path, index=False)
    except Exception as exc:
        logger.exception("Parquet save failed path=%s", output_path)
        raise ValueError("保存处理后的数据失败，请确认项目文件夹可写。") from exc


def load_processed_data(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    return pd.read_parquet(path)
