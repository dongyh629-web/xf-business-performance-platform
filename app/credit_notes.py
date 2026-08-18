from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


CREDIT_SAMPLE_PATH = Path.home() / "Downloads" / "CreditEnquiryList.xlsx"
CREDIT_FOLDER_NAMES = ("credit notes", "credits", "credit data", "returns credits")
CREDIT_FILE_PATTERN = "XF_Credit_YYYY-MM-DD.xlsx"
UNKNOWN_REASON = "Unknown / 未知"

REQUIRED_HEADER_FIELDS = {"Credit Date", "Credit Number", "Customer", "Sub Total"}

COLUMN_ALIASES = {
    "Credit Date": ["Credit Date", "Date", "Credit Note Date"],
    "Receipt Date": ["Receipt Date", "Received Date"],
    "Credit Number": ["Credit Number", "Credit No.", "Credit Note", "Credit Note Number"],
    "Customer Code": ["Customer Code", "Customer Ref", "Customer ID"],
    "Customer": ["Customer", "Customer Name"],
    "Product": ["Product", "Product Description", "Product Name"],
    "Product Group": ["Product Group", "Product Category", "Product Type"],
    "Product Code": ["Product Code", "SKU", "Item Code"],
    "Credit Reason": ["Credit Reason", "Reason", "Return Reason"],
    "Status": ["Status"],
    "Quantity": ["Quantity", "Qty"],
    "Sub Total": ["Sub Total", "Subtotal", "Credit Amount", "Amount", "Total"],
}


@dataclass(frozen=True)
class CreditImportResult:
    raw: pd.DataFrame
    clean: pd.DataFrame
    quality: dict[str, int | float | str]
    sheet_name: str
    source_name: str


def _normalized_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).replace("\n", " ").strip()


def _first_existing(columns: Iterable[object], candidates: list[str]) -> str | None:
    lookup = {str(column).strip().casefold(): str(column).strip() for column in columns}
    for candidate in candidates:
        match = lookup.get(candidate.casefold())
        if match:
            return match
    return None


def find_credit_sheet(excel_file) -> tuple[str, int]:
    try:
        workbook = pd.ExcelFile(excel_file)
    except Exception as exc:
        raise ValueError("Credit Excel 无法读取，请确认文件没有损坏，并且是 .xlsx 格式。") from exc

    for sheet_name in workbook.sheet_names:
        try:
            preview = pd.read_excel(excel_file, sheet_name=sheet_name, header=None, nrows=20)
        except Exception as exc:
            raise ValueError(f"读取工作表 `{sheet_name}` 失败，请检查该工作表格式。") from exc
        for row_index in range(len(preview)):
            values = {_normalized_text(value) for value in preview.iloc[row_index].dropna().tolist()}
            if REQUIRED_HEADER_FIELDS.issubset(values):
                return sheet_name, row_index
    raise ValueError("没有找到 Credit Enquiry 表头，请确认文件包含 Credit Date, Credit Number, Customer, Sub Total。")


def read_credit_enquiry_excel(excel_file) -> tuple[pd.DataFrame, str]:
    sheet_name, header_row = find_credit_sheet(excel_file)
    try:
        raw = pd.read_excel(excel_file, sheet_name=sheet_name, header=header_row)
    except Exception as exc:
        raise ValueError(f"读取 Credit Enquiry 工作表 `{sheet_name}` 失败。") from exc
    raw = raw.dropna(how="all").copy()
    raw.columns = [_normalized_text(column) for column in raw.columns]
    for column in list(raw.columns):
        if str(column).startswith("Unnamed") and raw[column].isna().all():
            raw = raw.drop(columns=[column])
    return raw, sheet_name


def _canonical_columns(raw: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame(index=raw.index)
    for canonical, aliases in COLUMN_ALIASES.items():
        source = _first_existing(raw.columns, aliases)
        if source is None:
            result[canonical] = pd.NA
        else:
            result[canonical] = raw[source]
    return result


def normalize_credit_notes(raw: pd.DataFrame, source_name: str = "") -> pd.DataFrame:
    data = _canonical_columns(raw)
    data["Credit Date"] = pd.to_datetime(data["Credit Date"], errors="coerce")
    data["Receipt Date"] = pd.to_datetime(data["Receipt Date"], errors="coerce", dayfirst=True)
    data["Quantity"] = pd.to_numeric(data["Quantity"], errors="coerce")
    data["Credit Raw Amount"] = pd.to_numeric(data["Sub Total"], errors="coerce")
    data["Credit Amount"] = data["Credit Raw Amount"].abs()

    text_columns = [
        "Credit Number",
        "Customer Code",
        "Customer",
        "Product",
        "Product Group",
        "Product Code",
        "Credit Reason",
        "Status",
    ]
    for column in text_columns:
        data[column] = data[column].astype("string").map(_normalized_text).replace("", pd.NA)

    data["Credit Reason"] = data["Credit Reason"].fillna(UNKNOWN_REASON).replace("", UNKNOWN_REASON)
    data["Customer Key"] = data["Customer Code"].fillna(data["Customer"]).astype("string").map(_normalized_text)
    data["Product Key"] = data["Product Code"].fillna(data["Product"]).astype("string").map(_normalized_text)
    data["Customer Label"] = data["Customer"].fillna(data["Customer Code"]).astype("string").map(_normalized_text)
    data["Product Label"] = data["Product"].fillna(data["Product Code"]).astype("string").map(_normalized_text)
    data["Source File"] = source_name or "CreditEnquiryList.xlsx"
    return data


def _duplicate_audit(data: pd.DataFrame) -> tuple[int, int]:
    duplicate_columns = [
        column
        for column in [
            "Credit Number",
            "Customer Key",
            "Product Key",
            "Credit Date",
            "Quantity",
            "Credit Amount",
            "Source File",
        ]
        if column in data.columns
    ]
    exact_duplicates = int(data.duplicated(subset=duplicate_columns, keep=False).sum()) if duplicate_columns else 0

    weak_columns = [column for column in duplicate_columns if column != "Credit Amount"]
    ambiguous = 0
    if weak_columns and "Credit Amount" in data.columns:
        grouped = data.groupby(weak_columns, dropna=False)["Credit Amount"].nunique(dropna=True)
        ambiguous_keys = grouped[grouped.gt(1)]
        if not ambiguous_keys.empty:
            ambiguous = int(data.set_index(weak_columns).index.isin(ambiguous_keys.index).sum())
    return exact_duplicates, ambiguous


def validate_credit_notes(data: pd.DataFrame) -> dict[str, int | float | str]:
    rows = int(len(data))
    exact_duplicates, ambiguous = _duplicate_audit(data)
    return {
        "Rows": rows,
        "Credit Note Count": int(data["Credit Number"].nunique(dropna=True)) if "Credit Number" in data.columns else 0,
        "Customers": int(data["Customer Key"].replace("", pd.NA).nunique(dropna=True)) if "Customer Key" in data.columns else 0,
        "Products": int(data["Product Key"].replace("", pd.NA).nunique(dropna=True)) if "Product Key" in data.columns else 0,
        "Missing Customer": int(data["Customer Key"].replace("", pd.NA).isna().sum()) if "Customer Key" in data.columns else rows,
        "Missing Product": int(data["Product Key"].replace("", pd.NA).isna().sum()) if "Product Key" in data.columns else rows,
        "Missing Product Group": int(data["Product Group"].isna().sum()) if "Product Group" in data.columns else rows,
        "Unknown Reason": int(data["Credit Reason"].fillna(UNKNOWN_REASON).eq(UNKNOWN_REASON).sum()) if "Credit Reason" in data.columns else rows,
        "Duplicate / Ambiguous Rows": exact_duplicates + ambiguous,
        "Exact Duplicate Rows Preserved": exact_duplicates,
        "Ambiguous Rows Preserved": ambiguous,
        "Credit Amount": float(pd.to_numeric(data.get("Credit Amount"), errors="coerce").fillna(0).sum()) if rows else 0.0,
    }


def import_credit_enquiry(excel_file, source_name: str | None = None) -> CreditImportResult:
    raw, sheet_name = read_credit_enquiry_excel(excel_file)
    clean = normalize_credit_notes(raw, source_name or getattr(excel_file, "name", "") or "CreditEnquiryList.xlsx")
    quality = validate_credit_notes(clean)
    return CreditImportResult(raw=raw, clean=clean, quality=quality, sheet_name=sheet_name, source_name=source_name or getattr(excel_file, "name", "") or "CreditEnquiryList.xlsx")


def filter_credit_by_date(data: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
    if data is None or data.empty or "Credit Date" not in data.columns:
        return data.iloc[0:0].copy() if isinstance(data, pd.DataFrame) else pd.DataFrame()
    dates = pd.to_datetime(data["Credit Date"], errors="coerce").dt.date
    return data.loc[dates.ge(start_date) & dates.le(end_date)].copy()
