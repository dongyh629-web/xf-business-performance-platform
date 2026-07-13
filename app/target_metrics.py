from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from app.config import (
    REQUIRED_COLUMNS,
    TARGET_ANNUAL_CANDIDATES,
    TARGET_MONTH_CANDIDATES,
    TARGET_NOTES_CANDIDATES,
    TARGET_ORIGINAL_CANDIDATES,
    TARGET_REVISED_CANDIDATES,
    TARGET_YEAR_CANDIDATES,
)


MONTH_NAME_MAP = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

MONTH_LABELS = {month: f"{month}月" for month in range(1, 13)}


@dataclass(frozen=True)
class TargetSheetCandidate:
    sheet_name: str
    header_row: int
    layout: str
    score: int
    year_column: str | None
    month_column: str | None
    original_target_column: str | None
    revised_target_column: str | None
    annual_target_column: str | None
    notes_column: str | None
    month_columns: dict[int, str]

    @property
    def label(self) -> str:
        layout_label = "长表" if self.layout == "long" else "横向月份"
        return f"{self.sheet_name}（{layout_label}，第 {self.header_row + 1} 行为表头）"


@dataclass(frozen=True)
class TargetWorkbookAnalysis:
    candidates: list[TargetSheetCandidate]
    sheet_names: list[str]


def _normalize_name(value: object) -> str:
    text = str(value).strip().lower()
    for token in [" ", "_", "-", ".", "/", "\\", "（", "）", "(", ")"]:
        text = text.replace(token, "")
    return text


def _first_existing(columns: Iterable[object], candidates: list[str]) -> str | None:
    normalized = {_normalize_name(col): str(col).strip() for col in columns}
    for candidate in candidates:
        match = normalized.get(_normalize_name(candidate))
        if match:
            return match
    return None


def parse_month(value: object) -> int | None:
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return int(value.month)
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower().replace("月", "").replace(".", "").strip()
    if lowered in MONTH_NAME_MAP:
        return MONTH_NAME_MAP[lowered]
    try:
        month = int(float(lowered))
    except ValueError:
        return None
    return month if 1 <= month <= 12 else None


def _month_column_number(column: object) -> int | None:
    text = str(column).strip()
    normalized = _normalize_name(text).replace("target", "").replace("目标", "")
    if normalized in MONTH_NAME_MAP:
        return MONTH_NAME_MAP[normalized]
    if text.endswith("月"):
        return parse_month(text)
    if normalized.startswith("m") and normalized[1:].isdigit():
        return parse_month(normalized[1:])
    return parse_month(normalized)


def _read_sheet(excel_file, sheet_name: str, header_row: int) -> pd.DataFrame | None:
    try:
        df = pd.read_excel(excel_file, sheet_name=sheet_name, header=header_row)
    except Exception:
        return None
    df = df.dropna(how="all").copy()
    if df.empty:
        return None
    df.columns = [str(col).strip() for col in df.columns]
    df = df.loc[:, ~pd.Index(df.columns).astype(str).str.startswith("Unnamed")]
    return df if not df.empty else None


def _score_candidate(df: pd.DataFrame, sheet_name: str, header_row: int) -> TargetSheetCandidate | None:
    year_col = _first_existing(df.columns, TARGET_YEAR_CANDIDATES)
    month_col = _first_existing(df.columns, TARGET_MONTH_CANDIDATES)
    original_col = _first_existing(df.columns, TARGET_ORIGINAL_CANDIDATES)
    revised_col = _first_existing(df.columns, TARGET_REVISED_CANDIDATES)
    annual_col = _first_existing(df.columns, TARGET_ANNUAL_CANDIDATES)
    notes_col = _first_existing(df.columns, TARGET_NOTES_CANDIDATES)
    month_cols = {month: col for col in df.columns if (month := _month_column_number(col))}

    if year_col and month_col and original_col:
        score = 80
        score += 10 if revised_col else 0
        score += 5 if annual_col else 0
        return TargetSheetCandidate(sheet_name, header_row, "long", score, year_col, month_col, original_col, revised_col, annual_col, notes_col, {})

    if year_col and len(month_cols) >= 3:
        score = 70 + min(len(month_cols), 12)
        score += 5 if annual_col else 0
        return TargetSheetCandidate(sheet_name, header_row, "wide", score, year_col, None, None, revised_col, annual_col, notes_col, dict(sorted(month_cols.items())))

    return None


def analyze_target_workbook(excel_file) -> TargetWorkbookAnalysis:
    workbook = pd.ExcelFile(excel_file)
    candidates: list[TargetSheetCandidate] = []
    for sheet_name in workbook.sheet_names:
        for header_row in range(0, 10):
            df = _read_sheet(excel_file, sheet_name, header_row)
            if df is None:
                continue
            candidate = _score_candidate(df, sheet_name, header_row)
            if candidate:
                candidates.append(candidate)
    candidates = sorted(candidates, key=lambda item: item.score, reverse=True)
    return TargetWorkbookAnalysis(candidates=candidates, sheet_names=workbook.sheet_names)


def workbook_looks_like_sales_data(excel_file) -> bool:
    try:
        workbook = pd.ExcelFile(excel_file)
    except Exception:
        return False
    required = set(REQUIRED_COLUMNS)
    for sheet_name in workbook.sheet_names:
        try:
            preview = pd.read_excel(excel_file, sheet_name=sheet_name, header=None, nrows=12)
        except Exception:
            continue
        for row_index in range(len(preview)):
            values = {str(value).strip() for value in preview.iloc[row_index].dropna().tolist()}
            if required.issubset(values):
                return True
    return False


def _clean_year(value: object) -> int | None:
    if pd.isna(value):
        return None
    try:
        year = int(float(str(value).strip()))
    except ValueError:
        return None
    return year if 1900 <= year <= 2200 else None


def normalize_targets(excel_file, candidate: TargetSheetCandidate) -> tuple[pd.DataFrame, dict[int, float]]:
    df = _read_sheet(excel_file, candidate.sheet_name, candidate.header_row)
    if df is None:
        raise ValueError("目标工作表为空或无法读取。")

    annual_targets: dict[int, float] = {}
    records: list[dict[str, object]] = []
    if candidate.layout == "long":
        for _, row in df.iterrows():
            year = _clean_year(row.get(candidate.year_column))
            month = parse_month(row.get(candidate.month_column))
            if year is None or month is None:
                continue
            original_target = pd.to_numeric(str(row.get(candidate.original_target_column, "")).replace(",", "").replace("£", ""), errors="coerce")
            if pd.isna(original_target):
                continue
            revised_target = pd.NA
            if candidate.revised_target_column:
                revised_target = pd.to_numeric(str(row.get(candidate.revised_target_column, "")).replace(",", "").replace("£", ""), errors="coerce")
            notes = row.get(candidate.notes_column) if candidate.notes_column else ""
            records.append(
                {
                    "Year": year,
                    "Month": month,
                    "Month Label": MONTH_LABELS[month],
                    "Original Target": float(original_target),
                    "Revised Target": float(revised_target) if pd.notna(revised_target) else pd.NA,
                    "Notes": "" if pd.isna(notes) else str(notes),
                }
            )
            if candidate.annual_target_column:
                annual_value = pd.to_numeric(str(row.get(candidate.annual_target_column, "")).replace(",", "").replace("£", ""), errors="coerce")
                if pd.notna(annual_value):
                    annual_targets[year] = float(annual_value)
    else:
        for _, row in df.iterrows():
            year = _clean_year(row.get(candidate.year_column))
            if year is None:
                continue
            if candidate.annual_target_column:
                annual_value = pd.to_numeric(str(row.get(candidate.annual_target_column, "")).replace(",", "").replace("£", ""), errors="coerce")
                if pd.notna(annual_value):
                    annual_targets[year] = float(annual_value)
            notes = row.get(candidate.notes_column) if candidate.notes_column else ""
            for month, col in candidate.month_columns.items():
                original_target = pd.to_numeric(str(row.get(col, "")).replace(",", "").replace("£", ""), errors="coerce")
                if pd.isna(original_target):
                    continue
                records.append(
                    {
                        "Year": year,
                        "Month": month,
                        "Month Label": MONTH_LABELS[month],
                        "Original Target": float(original_target),
                        "Revised Target": pd.NA,
                        "Notes": "" if pd.isna(notes) else str(notes),
                    }
                )

    target_df = pd.DataFrame.from_records(records)
    if target_df.empty:
        raise ValueError("未能从目标工作表中提取有效月份目标。")
    target_df = (
        target_df.sort_values(["Year", "Month"])
        .drop_duplicates(subset=["Year", "Month"], keep="last")
        .reset_index(drop=True)
    )
    target_df["Revised Target"] = target_df["Revised Target"].fillna(target_df["Original Target"])
    return target_df, annual_targets


def manual_candidate(
    sheet_name: str,
    header_row: int,
    layout: str,
    year_column: str,
    month_column: str | None,
    original_target_column: str | None,
    annual_target_column: str | None = None,
) -> TargetSheetCandidate:
    return TargetSheetCandidate(
        sheet_name=sheet_name,
        header_row=header_row,
        layout=layout,
        score=0,
        year_column=year_column,
        month_column=month_column,
        original_target_column=original_target_column,
        revised_target_column=None,
        annual_target_column=annual_target_column,
        notes_column=None,
        month_columns={},
    )


def read_sheet_columns(excel_file, sheet_name: str, header_row: int) -> list[str]:
    df = _read_sheet(excel_file, sheet_name, header_row)
    return [] if df is None else list(df.columns)
