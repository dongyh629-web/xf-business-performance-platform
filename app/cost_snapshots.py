from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
import re
from typing import Any, Iterable

import pandas as pd


COST_FILE_PATTERN = re.compile(r"^XF_Product_Cost_(20\d{2}-\d{2}-\d{2})\.xlsx$")
COST_REQUIRED_COLUMNS = ["Product Code", "Unit Cost"]
COST_RECOMMENDED_COLUMNS = [
    "Product Description",
    "Product Group",
    "Status",
    "Effective From",
    "Effective To",
    "Notes",
]
COST_MATCH_DATE_COLUMN = "Completed Date"


@dataclass(frozen=True)
class CostFileMetadata:
    file_id: str
    name: str
    modified_time: str | None = None
    size: str | None = None


@dataclass
class CostSnapshotRegistryEntry:
    cost_version_date: pd.Timestamp | None
    file_name: str
    file_id: str | None = None
    modified_time: str | None = None
    size: str | None = None
    row_count: int | None = None
    valid_sku_count: int | None = None
    duplicate_sku_count: int | None = None
    validation_status: str = "Pending"
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    participates_in_matching: bool = False


@dataclass
class CostSnapshotRegistry:
    entries: list[CostSnapshotRegistryEntry]

    @property
    def valid_entries(self) -> list[CostSnapshotRegistryEntry]:
        return [entry for entry in self.entries if entry.participates_in_matching]


@dataclass
class CostSnapshot:
    version_date: pd.Timestamp
    file_name: str
    file_id: str | None
    modified_time: str | None
    data: pd.DataFrame
    registry_entry: CostSnapshotRegistryEntry


def _as_metadata(item: Any) -> CostFileMetadata:
    metadata = getattr(item, "metadata", item)
    if isinstance(metadata, dict):
        return CostFileMetadata(
            file_id=str(metadata.get("file_id") or metadata.get("id") or ""),
            name=str(metadata.get("name") or ""),
            modified_time=metadata.get("modified_time") or metadata.get("modifiedTime"),
            size=metadata.get("size"),
        )
    return CostFileMetadata(
        file_id=str(getattr(metadata, "file_id", getattr(metadata, "id", "")) or ""),
        name=str(getattr(metadata, "name", "") or ""),
        modified_time=getattr(metadata, "modified_time", getattr(metadata, "modifiedTime", None)),
        size=getattr(metadata, "size", None),
    )


def _is_ignored_excel_name(name: str) -> bool:
    stripped = str(name).strip()
    return not stripped or stripped.startswith(".") or stripped.startswith("~$")


def parse_cost_version_date_from_filename(file_name: str) -> pd.Timestamp | None:
    if _is_ignored_excel_name(file_name):
        return None
    match = COST_FILE_PATTERN.match(str(file_name).strip())
    if not match:
        return None
    parsed = pd.to_datetime(match.group(1), errors="coerce")
    return None if pd.isna(parsed) else pd.Timestamp(parsed).normalize()


def list_cost_snapshot_candidates(items: Iterable[Any]) -> list[CostSnapshotRegistryEntry]:
    entries: list[CostSnapshotRegistryEntry] = []
    for item in items:
        metadata = _as_metadata(item)
        name = metadata.name.strip()
        if _is_ignored_excel_name(name):
            continue
        version_date = parse_cost_version_date_from_filename(name)
        entry = CostSnapshotRegistryEntry(
            cost_version_date=version_date,
            file_name=name,
            file_id=metadata.file_id,
            modified_time=metadata.modified_time,
            size=metadata.size,
        )
        if version_date is None:
            entry.validation_status = "Invalid File Name"
            entry.warnings.append("File name does not match XF_Product_Cost_YYYY-MM-DD.xlsx")
        entries.append(entry)
    return entries


def build_cost_snapshot_registry(items: Iterable[Any]) -> CostSnapshotRegistry:
    entries = list_cost_snapshot_candidates(items)
    version_counts: dict[pd.Timestamp, int] = {}
    for entry in entries:
        if entry.cost_version_date is not None:
            version_counts[entry.cost_version_date] = version_counts.get(entry.cost_version_date, 0) + 1

    for entry in entries:
        if entry.cost_version_date is None:
            entry.participates_in_matching = False
            continue
        if version_counts.get(entry.cost_version_date, 0) > 1:
            entry.validation_status = "Conflict"
            entry.errors.append("Multiple cost files share the same Cost Version Date")
            entry.participates_in_matching = False
        elif not entry.errors:
            entry.validation_status = "Discovered"
            entry.participates_in_matching = True
    entries.sort(key=lambda item: (item.cost_version_date is not None, item.cost_version_date or pd.Timestamp.min), reverse=True)
    return CostSnapshotRegistry(entries=entries)


def _normalize_header_value(value: object) -> str:
    return str(value).strip().casefold()


def _find_cost_header_row(excel_file) -> tuple[str, int]:
    workbook = pd.ExcelFile(excel_file)
    required = {_normalize_header_value(column) for column in COST_REQUIRED_COLUMNS}
    for sheet_name in workbook.sheet_names:
        preview = pd.read_excel(excel_file, sheet_name=sheet_name, header=None, nrows=25)
        for row_index in range(len(preview)):
            values = {_normalize_header_value(value) for value in preview.iloc[row_index].dropna().tolist()}
            if required.issubset(values):
                return str(sheet_name), int(row_index)
    raise ValueError("No cost snapshot header row found with Product Code and Unit Cost.")


def read_cost_snapshot_workbook(excel_file) -> tuple[pd.DataFrame, str]:
    sheet_name, header_row = _find_cost_header_row(excel_file)
    raw = pd.read_excel(excel_file, sheet_name=sheet_name, header=header_row)
    raw = raw.dropna(how="all").copy()
    raw.columns = [str(column).strip() for column in raw.columns]
    raw = raw.loc[:, ~pd.Index(raw.columns).str.startswith("Unnamed")]
    return raw, sheet_name


def normalize_product_code(value: object) -> pd.NA | str:
    if pd.isna(value):
        return pd.NA
    text = str(value).replace("\n", " ").strip()
    if not text:
        return pd.NA
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return text


def _standardize_status(value: object) -> str:
    if pd.isna(value):
        return ""
    return " ".join(str(value).strip().split())


def normalize_cost_snapshot(raw: pd.DataFrame, version_date: pd.Timestamp, file_name: str) -> pd.DataFrame:
    normalized = pd.DataFrame(index=raw.index)
    for column in [*COST_REQUIRED_COLUMNS, *COST_RECOMMENDED_COLUMNS]:
        normalized[column] = raw[column] if column in raw.columns else pd.NA
    normalized["Product Code"] = normalized["Product Code"].map(normalize_product_code).astype("string")
    normalized["Unit Cost"] = pd.to_numeric(normalized["Unit Cost"], errors="coerce")
    normalized["Effective From"] = pd.to_datetime(normalized["Effective From"], errors="coerce").dt.normalize()
    normalized["Effective To"] = pd.to_datetime(normalized["Effective To"], errors="coerce").dt.normalize()
    normalized["Status"] = normalized["Status"].map(_standardize_status).astype("string")
    normalized["Cost Version Date"] = pd.Timestamp(version_date).normalize()
    normalized["Cost File Name"] = file_name
    return normalized.reset_index(drop=True)


def validate_cost_snapshot(df: pd.DataFrame, version_date: pd.Timestamp, file_name: str) -> CostSnapshotRegistryEntry:
    entry = CostSnapshotRegistryEntry(
        cost_version_date=pd.Timestamp(version_date).normalize(),
        file_name=file_name,
        row_count=int(len(df)),
        validation_status="Valid",
        participates_in_matching=True,
    )
    if df.empty:
        entry.validation_status = "Invalid"
        entry.errors.append("Cost snapshot worksheet is empty")
        entry.participates_in_matching = False
        return entry

    missing_required = [column for column in COST_REQUIRED_COLUMNS if column not in df.columns]
    if missing_required:
        entry.validation_status = "Invalid"
        entry.errors.append(f"Missing required columns: {', '.join(missing_required)}")
        entry.participates_in_matching = False
        return entry

    product_code = df["Product Code"].astype("string")
    valid_product_code = product_code.notna() & product_code.str.strip().ne("")
    duplicate_rows = product_code[valid_product_code].duplicated(keep=False)
    unit_cost = pd.to_numeric(df["Unit Cost"], errors="coerce")

    entry.valid_sku_count = int(valid_product_code.sum())
    entry.duplicate_sku_count = int(duplicate_rows.sum())
    empty_code_count = int((~valid_product_code).sum())
    invalid_unit_cost_count = int(unit_cost.isna().sum() + unit_cost.le(0).fillna(False).sum())

    if empty_code_count:
        entry.warnings.append(f"Empty Product Code rows: {empty_code_count}")
    if entry.duplicate_sku_count:
        entry.warnings.append(f"Duplicate Product Code rows: {entry.duplicate_sku_count}")
    if invalid_unit_cost_count:
        entry.warnings.append(f"Invalid Unit Cost rows: {invalid_unit_cost_count}")

    if "Effective From" in df.columns:
        effective = pd.to_datetime(df["Effective From"], errors="coerce").dropna().dt.normalize()
        mismatches = int(effective.ne(pd.Timestamp(version_date).normalize()).sum())
        if mismatches:
            entry.warnings.append(f"Effective From differs from file version date in {mismatches} rows")

    if entry.warnings:
        entry.validation_status = "Warning"
        entry.participates_in_matching = True
    return entry


def load_cost_snapshot(
    excel_file,
    version_date: pd.Timestamp,
    file_name: str,
    file_id: str | None = None,
    modified_time: str | None = None,
) -> CostSnapshot:
    raw, _sheet_name = read_cost_snapshot_workbook(excel_file)
    normalized = normalize_cost_snapshot(raw, version_date, file_name)
    entry = validate_cost_snapshot(normalized, version_date, file_name)
    entry.file_id = file_id
    entry.modified_time = modified_time
    return CostSnapshot(
        version_date=pd.Timestamp(version_date).normalize(),
        file_name=file_name,
        file_id=file_id,
        modified_time=modified_time,
        data=normalized,
        registry_entry=entry,
    )


def _snapshot_lookup(snapshot: CostSnapshot) -> pd.DataFrame:
    data = snapshot.data.copy()
    data["Product Code"] = data["Product Code"].map(normalize_product_code).astype("string")
    return data.set_index("Product Code", drop=False)


def assign_cost_version(sales_df: pd.DataFrame, snapshots: list[CostSnapshot]) -> pd.Series:
    if sales_df.empty:
        return pd.Series(dtype="datetime64[ns]", index=sales_df.index)
    sale_dates = pd.to_datetime(sales_df.get(COST_MATCH_DATE_COLUMN), errors="coerce").dt.normalize()
    valid_versions = sorted(
        [snapshot.version_date for snapshot in snapshots if snapshot.registry_entry.participates_in_matching],
    )
    assigned = pd.Series(pd.NaT, index=sales_df.index, dtype="datetime64[ns]")
    for version in valid_versions:
        assigned = assigned.mask(sale_dates.ge(version), version)
    return assigned


def match_unit_cost(sales_df: pd.DataFrame, snapshots: list[CostSnapshot]) -> pd.DataFrame:
    result = pd.DataFrame(index=sales_df.index)
    result["Cost Version Date"] = assign_cost_version(sales_df, snapshots)
    result["Unit Cost"] = pd.NA
    result["Cost Match Status"] = "No Cost Version"
    result["Cost File Name"] = pd.NA
    result["Cost Product Status"] = pd.NA
    result["Cost Product Group"] = pd.NA

    sale_dates = pd.to_datetime(sales_df.get(COST_MATCH_DATE_COLUMN), errors="coerce")
    invalid_date = sale_dates.isna()
    result.loc[invalid_date, "Cost Match Status"] = "Invalid Sale Date"

    product_codes = (
        sales_df.get("Product Code", pd.Series(pd.NA, index=sales_df.index))
        .map(normalize_product_code)
        .astype("string")
    )
    missing_product_code = product_codes.isna() | product_codes.str.strip().eq("")
    result.loc[missing_product_code & ~invalid_date, "Cost Match Status"] = "Missing Product Cost"

    for snapshot in snapshots:
        if not snapshot.registry_entry.participates_in_matching:
            continue
        version_mask = result["Cost Version Date"].eq(snapshot.version_date)
        if not version_mask.any():
            continue
        lookup = _snapshot_lookup(snapshot)
        duplicate_codes = set(lookup.index[lookup.index.duplicated(keep=False)].dropna().tolist())
        for row_index in result.index[version_mask]:
            if invalid_date.loc[row_index]:
                continue
            product_code = product_codes.loc[row_index]
            if pd.isna(product_code) or not str(product_code).strip():
                result.loc[row_index, "Cost Match Status"] = "Missing Product Cost"
                continue
            if product_code in duplicate_codes:
                result.loc[row_index, "Cost Match Status"] = "Duplicate Cost Record"
                result.loc[row_index, "Cost File Name"] = snapshot.file_name
                continue
            if product_code not in lookup.index:
                result.loc[row_index, "Cost Match Status"] = "Missing Product Cost"
                result.loc[row_index, "Cost File Name"] = snapshot.file_name
                continue
            cost_row = lookup.loc[product_code]
            unit_cost = pd.to_numeric(cost_row.get("Unit Cost"), errors="coerce")
            result.loc[row_index, "Cost File Name"] = snapshot.file_name
            result.loc[row_index, "Cost Product Status"] = cost_row.get("Status")
            result.loc[row_index, "Cost Product Group"] = cost_row.get("Product Group")
            if pd.isna(unit_cost) or float(unit_cost) <= 0:
                result.loc[row_index, "Cost Match Status"] = "Invalid Unit Cost"
                continue
            if str(cost_row.get("Status", "")).strip().casefold() == "non-sale":
                result.loc[row_index, "Unit Cost"] = float(unit_cost)
                result.loc[row_index, "Cost Match Status"] = "Non-sale Product"
                continue
            result.loc[row_index, "Unit Cost"] = float(unit_cost)
            result.loc[row_index, "Cost Match Status"] = "Matched"
    return result


def match_sales_to_cost_versions(sales_df: pd.DataFrame, snapshots: list[CostSnapshot]) -> pd.DataFrame:
    matched = sales_df.copy()
    cost_columns = match_unit_cost(sales_df, snapshots)
    for column in cost_columns.columns:
        matched[column] = cost_columns[column]
    return matched


def build_cost_coverage_report(matched_sales: pd.DataFrame) -> dict[str, Any]:
    total_rows = int(len(matched_sales))
    status = matched_sales.get("Cost Match Status", pd.Series(index=matched_sales.index, dtype="string")).astype("string")
    sales_amount = pd.to_numeric(matched_sales.get("Sales Amount", pd.Series(0, index=matched_sales.index)), errors="coerce").fillna(0)
    matched_mask = status.eq("Matched")
    unmatched_mask = ~matched_mask
    product_codes = matched_sales.get("Product Code", pd.Series(pd.NA, index=matched_sales.index)).map(normalize_product_code).astype("string")
    missing_product_codes = sorted(product_codes[status.eq("Missing Product Cost")].dropna().unique().tolist())
    no_version_rows = int(status.eq("No Cost Version").sum())
    invalid_date_rows = int(status.eq("Invalid Sale Date").sum())
    return {
        "Total Sales Rows": total_rows,
        "Total Sales SKUs": int(product_codes.dropna().nunique()),
        "Matched Rows": int(matched_mask.sum()),
        "Matched SKUs": int(product_codes[matched_mask].dropna().nunique()),
        "No Cost Version Rows": no_version_rows,
        "Invalid Sale Date Rows": invalid_date_rows,
        "Missing Product Cost Rows": int(status.eq("Missing Product Cost").sum()),
        "Invalid Unit Cost Rows": int(status.eq("Invalid Unit Cost").sum()),
        "Non-sale Rows": int(status.eq("Non-sale Product").sum()),
        "Matched Sales Amount": float(sales_amount[matched_mask].sum()),
        "Unmatched Sales Amount": float(sales_amount[unmatched_mask].sum()),
        "Cost Coverage by Rows": float(matched_mask.sum() / total_rows) if total_rows else 0.0,
        "Cost Coverage by Sales Amount": float(sales_amount[matched_mask].sum() / sales_amount.sum()) if float(sales_amount.sum()) else 0.0,
        "Missing Product Codes": missing_product_codes,
        "No Cost Version Reason": f"{no_version_rows} rows have {COST_MATCH_DATE_COLUMN} earlier than the first available cost version or no valid version.",
        "Missing Product Cost Reason": "Rows have a valid cost version but no matching Product Code in that snapshot.",
    }


def load_cost_snapshot_from_bytes(content: bytes, entry: CostSnapshotRegistryEntry) -> CostSnapshot:
    if entry.cost_version_date is None:
        raise ValueError("Cost registry entry does not have a version date.")
    return load_cost_snapshot(
        BytesIO(content),
        entry.cost_version_date,
        entry.file_name,
        file_id=entry.file_id,
        modified_time=entry.modified_time,
    )
