from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import json
import logging
from pathlib import Path
import pickle
import re
import time
from typing import Any

import pandas as pd
import streamlit as st

from app.config import METHODOLOGY_VERSION
from app.data import ImportResult, import_excel
from app.target_metrics import XFTargetWorkbook, parse_xf_target_workbook


logger = logging.getLogger(__name__)

DRIVE_SOURCE_LABEL = "Google Drive"
MANUAL_SOURCE_LABEL = "本次会话手动上传"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
SALES_FOLDER_NAME = "sales data"
TARGETS_FOLDER_NAME = "targets"
TARGET_FILE_FALLBACK_NAMES = ["XF 2026销售目标_Target may.xlsx"]
EXCEL_EXTENSIONS = (".xlsx", ".xls")
DRIVE_CACHE_VERSION = f"drive_cache_v2_{METHODOLOGY_VERSION}"
CACHE_DIR = Path(".cache")
CACHE_METADATA_PATH = CACHE_DIR / "metadata.json"
CACHE_SALES_PATH = CACHE_DIR / "sales_clean.parquet"
CACHE_SALES_EXTRAS_PATH = CACHE_DIR / "sales_extras.pkl"
CACHE_TARGETS_PATH = CACHE_DIR / "targets_clean.pkl"


class DriveUserError(RuntimeError):
    """A user-safe Google Drive loading error."""


@dataclass(frozen=True)
class DriveConfig:
    client_id: str
    client_secret: str
    refresh_token: str
    token_uri: str
    folder_id: str
    sales_file_name: str
    target_file_name: str


@dataclass(frozen=True)
class DriveFileMetadata:
    file_id: str
    name: str
    modified_time: str | None
    mime_type: str | None = None
    size: str | None = None
    web_view_link: str | None = None


@dataclass(frozen=True)
class DriveLoadItemStatus:
    status: str
    message: str
    file_name: str | None = None
    modified_time: str | None = None
    file_id: str | None = None


@dataclass(frozen=True)
class DriveLoadStatus:
    configured: bool
    message: str
    sales: DriveLoadItemStatus
    targets: DriveLoadItemStatus


@dataclass(frozen=True)
class DriveFileCandidate:
    metadata: DriveFileMetadata
    filename_date: pd.Timestamp | None
    version: int | None
    year: int | None
    reason: str


class _NamedBytesIO(BytesIO):
    def __init__(self, data: bytes, name: str):
        super().__init__(data)
        self.name = name


def _timer() -> float:
    return time.perf_counter()


def _perf_log(step: str, start: float, rows: int | None = None, cache: str | None = None) -> None:
    details = ["perf_step=%s", "elapsed=%.3fs"]
    args: list[object] = [step, time.perf_counter() - start]
    if rows is not None:
        details.append("rows=%s")
        args.append(rows)
    if cache:
        details.append("cache=%s")
        args.append(cache)
    logger.info(" ".join(details), *args)


def _ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _read_cache_metadata() -> dict[str, Any]:
    try:
        if not CACHE_METADATA_PATH.exists():
            return {}
        return json.loads(CACHE_METADATA_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Local dashboard cache metadata is unreadable; rebuilding cache")
        return {}


def _write_cache_metadata(metadata: dict[str, Any]) -> None:
    _ensure_cache_dir()
    safe = {key: value for key, value in metadata.items() if "secret" not in key.lower() and "token" not in key.lower()}
    CACHE_METADATA_PATH.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")


def _cache_matches(kind: str, metadata: DriveFileMetadata, cache_metadata: dict[str, Any]) -> bool:
    return (
        cache_metadata.get("cache_version") == DRIVE_CACHE_VERSION
        and cache_metadata.get(f"{kind}_file_id") == metadata.file_id
        and cache_metadata.get(f"{kind}_modified_time") == metadata.modified_time
    )


def _restore_sales_cache(metadata: DriveFileMetadata | None = None) -> bool:
    start = _timer()
    if not CACHE_SALES_PATH.exists() or not CACHE_SALES_EXTRAS_PATH.exists():
        return False
    try:
        clean = pd.read_parquet(CACHE_SALES_PATH)
        with CACHE_SALES_EXTRAS_PATH.open("rb") as handle:
            extras = pickle.load(handle)
        result = ImportResult(
            raw=pd.DataFrame(columns=extras.get("source_columns", [])),
            clean=clean,
            quality=extras.get("quality", {}),
            sheet_name=extras.get("sheet_name", ""),
            comparison=extras.get("comparison", {}),
        )
        file_name = metadata.name if metadata else extras.get("file_name", "Cached sales data")
        modified_time = metadata.modified_time if metadata else extras.get("modified_time")
        store_sales_import_in_session(result, file_name, DRIVE_SOURCE_LABEL, "drive", modified_time)
        if metadata:
            _set_drive_sales_success(metadata, result, "本地缓存", [])
        else:
            st = _get_streamlit()
            st.session_state["drive_sales_file_id"] = extras.get("file_id")
            st.session_state["drive_sales_file_name"] = file_name
            st.session_state["drive_sales_modified_time"] = modified_time
            st.session_state["drive_sales_loaded_at"] = extras.get("cache_created_at")
            st.session_state["drive_sales_status"] = "使用本地缓存"
            st.session_state["drive_sales_row_count"] = int(len(clean))
            st.session_state["drive_sales_max_date"] = _sales_max_date(clean)
        _perf_log("restore_sales_parquet", start, len(clean), "hit")
        return True
    except Exception:
        logger.exception("Local sales cache restore failed")
        _perf_log("restore_sales_parquet", start, cache="corrupt")
        return False


def _write_sales_cache(metadata: DriveFileMetadata, result: ImportResult) -> None:
    start = _timer()
    try:
        _ensure_cache_dir()
        result.clean.to_parquet(CACHE_SALES_PATH, index=False)
        extras = {
            "file_id": metadata.file_id,
            "file_name": metadata.name,
            "modified_time": metadata.modified_time,
            "quality": result.quality,
            "comparison": result.comparison,
            "sheet_name": result.sheet_name,
            "source_columns": list(result.raw.columns),
            "cache_created_at": _now_text(),
        }
        with CACHE_SALES_EXTRAS_PATH.open("wb") as handle:
            pickle.dump(extras, handle)
        cache_metadata = _read_cache_metadata()
        cache_metadata.update(
            {
                "cache_version": DRIVE_CACHE_VERSION,
                "sales_file_id": metadata.file_id,
                "sales_modified_time": metadata.modified_time,
                "sales_file_name": metadata.name,
                "sales_size": metadata.size,
                "sales_max_date": _sales_max_date(result.clean),
                "cache_created_at": _now_text(),
            }
        )
        _write_cache_metadata(cache_metadata)
        _perf_log("write_sales_parquet", start, len(result.clean), "miss")
    except Exception:
        logger.exception("Local sales cache write failed")


def _restore_target_cache(metadata: DriveFileMetadata | None = None) -> bool:
    start = _timer()
    if not CACHE_TARGETS_PATH.exists():
        return False
    try:
        with CACHE_TARGETS_PATH.open("rb") as handle:
            parsed = pickle.load(handle)
        file_name = metadata.name if metadata else getattr(parsed, "cache_file_name", "Cached targets")
        modified_time = metadata.modified_time if metadata else getattr(parsed, "cache_modified_time", None)
        store_target_workbook_in_session(parsed, file_name, DRIVE_SOURCE_LABEL, "drive", modified_time)
        if metadata:
            _set_drive_target_success(metadata, parsed, "本地缓存", [])
        else:
            st = _get_streamlit()
            st.session_state["drive_target_file_name"] = file_name
            st.session_state["drive_target_modified_time"] = modified_time
            st.session_state["drive_target_status"] = "使用本地缓存"
            st.session_state["drive_target_year"] = parsed.target_year
        rows = 0 if parsed.company_targets is None else len(parsed.company_targets)
        _perf_log("restore_target_cache", start, rows, "hit")
        return True
    except Exception:
        logger.exception("Local target cache restore failed")
        _perf_log("restore_target_cache", start, cache="corrupt")
        return False


def _write_target_cache(metadata: DriveFileMetadata, parsed: XFTargetWorkbook) -> None:
    start = _timer()
    try:
        _ensure_cache_dir()
        with CACHE_TARGETS_PATH.open("wb") as handle:
            pickle.dump(parsed, handle)
        cache_metadata = _read_cache_metadata()
        cache_metadata.update(
            {
                "cache_version": DRIVE_CACHE_VERSION,
                "target_file_id": metadata.file_id,
                "target_modified_time": metadata.modified_time,
                "target_file_name": metadata.name,
                "target_size": metadata.size,
                "cache_created_at": _now_text(),
            }
        )
        _write_cache_metadata(cache_metadata)
        rows = 0 if parsed.company_targets is None else len(parsed.company_targets)
        _perf_log("write_target_cache", start, rows, "miss")
    except Exception:
        logger.exception("Local target cache write failed")


def _restore_any_local_cache() -> DriveLoadStatus | None:
    metadata = _read_cache_metadata()
    if metadata.get("cache_version") != DRIVE_CACHE_VERSION:
        return None
    sales_metadata = None
    if metadata.get("sales_file_id") and metadata.get("sales_file_name"):
        sales_metadata = DriveFileMetadata(
            file_id=str(metadata.get("sales_file_id")),
            name=str(metadata.get("sales_file_name")),
            modified_time=metadata.get("sales_modified_time"),
            size=metadata.get("sales_size"),
        )
    target_metadata = None
    if metadata.get("target_file_id") and metadata.get("target_file_name"):
        target_metadata = DriveFileMetadata(
            file_id=str(metadata.get("target_file_id")),
            name=str(metadata.get("target_file_name")),
            modified_time=metadata.get("target_modified_time"),
            size=metadata.get("target_size"),
        )
    sales_ok = _restore_sales_cache(sales_metadata)
    target_ok = _restore_target_cache(target_metadata)
    if not sales_ok and not target_ok:
        return None
    sales_status = DriveLoadItemStatus(
        "cached" if sales_ok else "failed",
        "销售数据已从本地缓存加载。" if sales_ok else "本地销售缓存不可用。",
        metadata.get("sales_file_name"),
        metadata.get("sales_modified_time"),
        metadata.get("sales_file_id"),
    )
    target_status = DriveLoadItemStatus(
        "cached" if target_ok else "failed",
        "目标数据已从本地缓存加载。" if target_ok else "本地目标缓存不可用。",
        metadata.get("target_file_name"),
        metadata.get("target_modified_time"),
        metadata.get("target_file_id"),
    )
    return DriveLoadStatus(True, "当前使用本地缓存数据。", sales_status, target_status)


def _get_streamlit():
    import streamlit as st

    return st


def _to_plain_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    try:
        return dict(value)
    except Exception:
        return {}


def get_drive_config(secrets: Any | None = None) -> DriveConfig:
    if secrets is None:
        try:
            secrets = _get_streamlit().secrets
        except Exception as exc:
            raise DriveUserError("尚未配置 Google Drive Secrets。") from exc

    try:
        oauth_section = _to_plain_dict(secrets["google_oauth"])
        drive_section = _to_plain_dict(secrets["google_drive"])
    except Exception as exc:
        raise DriveUserError("尚未配置 Google OAuth Secrets。") from exc

    required_oauth_fields = ["client_id", "client_secret", "refresh_token"]
    missing_oauth = [field for field in required_oauth_fields if not oauth_section.get(field)]
    if missing_oauth:
        raise DriveUserError(f"Google OAuth Secrets 缺少字段：{', '.join(missing_oauth)}。")

    folder_id = str(drive_section.get("folder_id", "")).strip()
    if not folder_id:
        raise DriveUserError("Google Drive Secrets 缺少 folder_id。")

    return DriveConfig(
        client_id=str(oauth_section["client_id"]).strip(),
        client_secret=str(oauth_section["client_secret"]).strip(),
        refresh_token=str(oauth_section["refresh_token"]).strip(),
        token_uri=str(oauth_section.get("token_uri", "https://oauth2.googleapis.com/token")).strip()
        or "https://oauth2.googleapis.com/token",
        folder_id=folder_id,
        sales_file_name=str(drive_section.get("sales_file_name", "XF_Sales_Latest.xlsx")).strip() or "XF_Sales_Latest.xlsx",
        target_file_name=str(drive_section.get("target_file_name", "XF_Targets_Latest.xlsx")).strip() or "XF_Targets_Latest.xlsx",
    )


@st.cache_resource(show_spinner=False)
def get_drive_service(config: DriveConfig):
    try:
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise DriveUserError("缺少 Google Drive 读取依赖，请确认 requirements.txt 已安装 google-api-python-client 和 google-auth。") from exc

    try:
        credentials = Credentials(
            token=None,
            refresh_token=config.refresh_token,
            token_uri=config.token_uri,
            client_id=config.client_id,
            client_secret=config.client_secret,
            scopes=[DRIVE_SCOPE],
        )
        credentials.refresh(Request())
        return build("drive", "v3", credentials=credentials, cache_discovery=False)
    except RefreshError as exc:
        logger.warning("Google OAuth refresh failed: %s", exc.__class__.__name__)
        text = str(exc).lower()
        if "invalid_grant" in text:
            raise DriveUserError("Google Drive 授权已失效，请管理员重新完成一次 OAuth 授权。") from exc
        raise DriveUserError("Google Drive access token 刷新失败，请检查 OAuth refresh token 是否仍然有效。") from exc
    except Exception as exc:
        logger.exception("Google Drive service initialization failed")
        raise DriveUserError("Google Drive 连接失败，请检查 OAuth Client ID、Client Secret 和 Refresh Token 是否完整有效。") from exc


def _escape_drive_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _normalize_drive_name(value: str) -> str:
    return " ".join(str(value).strip().casefold().split())


def _is_excel_file_name(name: str) -> bool:
    stripped = str(name).strip()
    if not stripped or stripped.startswith(".") or stripped.startswith("~$"):
        return False
    return stripped.casefold().endswith(EXCEL_EXTENSIONS)


def _parse_modified_time(value: str | None) -> pd.Timestamp:
    if not value:
        return pd.Timestamp.min.tz_localize("UTC")
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    return pd.Timestamp.min.tz_localize("UTC") if pd.isna(parsed) else parsed


def _parse_date_from_filename(name: str) -> pd.Timestamp | None:
    text = str(name)
    patterns = [
        r"(?<!\d)(20\d{2})[-_](0[1-9]|1[0-2])[-_](0[1-9]|[12]\d|3[01])(?!\d)",
        r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)",
        r"(?<!\d)(0[1-9]|[12]\d|3[01])[-_](0[1-9]|1[0-2])[-_](20\d{2})(?!\d)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        groups = match.groups()
        if len(groups[0]) == 4:
            year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
        else:
            day, month, year = int(groups[0]), int(groups[1]), int(groups[2])
        parsed = pd.Timestamp(year=year, month=month, day=day)
        if not pd.isna(parsed):
            return parsed
    return None


def _parse_year_from_filename(name: str) -> int | None:
    match = re.search(r"(?<!\d)(20\d{2})(?!\d)", str(name))
    return int(match.group(1)) if match else None


def _parse_version_from_filename(name: str) -> int | None:
    match = re.search(r"(?i)(?:^|[^a-z0-9])v(?:ersion)?[_ -]?(\d+)(?:[^a-z0-9]|$)", str(name))
    return int(match.group(1)) if match else None


def _drive_candidate_from_item(item: dict[str, Any]) -> DriveFileCandidate | None:
    name = str(item.get("name", ""))
    if not _is_excel_file_name(name):
        return None
    date = _parse_date_from_filename(name)
    version = _parse_version_from_filename(name)
    year = _parse_year_from_filename(name)
    reason = "文件名日期最新" if date is not None else "Drive 修改时间最新"
    return DriveFileCandidate(
        metadata=_metadata_from_drive_item(item, name),
        filename_date=date,
        version=version,
        year=year,
        reason=reason,
    )


def _candidate_modified_time(candidate: DriveFileCandidate) -> pd.Timestamp:
    return _parse_modified_time(candidate.metadata.modified_time)


def _list_drive_children(service, folder_id: str, mime_type: str | None = None) -> list[dict[str, Any]]:
    query_parts = [f"'{_escape_drive_query_value(folder_id)}' in parents", "trashed = false"]
    if mime_type:
        query_parts.append(f"mimeType = '{_escape_drive_query_value(mime_type)}'")
    files: list[dict[str, Any]] = []
    page_token = None
    try:
        while True:
            response = (
                service.files()
                .list(
                    q=" and ".join(query_parts),
                    fields="nextPageToken,files(id,name,mimeType,modifiedTime,size,webViewLink)",
                    pageSize=100,
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    corpora="allDrives",
                )
                .execute()
            )
            files.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
    except Exception as exc:
        logger.exception("Google Drive child listing failed")
        raise DriveUserError("Google Drive 文件夹内容读取失败，请检查文件夹权限。") from exc
    return files


def _metadata_from_drive_item(item: dict[str, Any], fallback_name: str) -> DriveFileMetadata:
    return DriveFileMetadata(
        file_id=str(item.get("id")),
        name=str(item.get("name", fallback_name)),
        modified_time=item.get("modifiedTime"),
        mime_type=item.get("mimeType"),
        size=item.get("size"),
        web_view_link=item.get("webViewLink"),
    )


def find_drive_file(service, folder_id: str, file_name: str) -> DriveFileMetadata:
    query = (
        f"'{_escape_drive_query_value(folder_id)}' in parents and "
        f"name = '{_escape_drive_query_value(file_name)}' and trashed = false"
    )
    try:
        response = (
            service.files()
            .list(
                q=query,
                fields="files(id,name,mimeType,modifiedTime,size,webViewLink)",
                pageSize=10,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                corpora="allDrives",
            )
            .execute()
        )
    except Exception as exc:
        logger.exception("Google Drive file lookup failed for file_name=%s", file_name)
        raise DriveUserError("Google Drive 文件查找失败，请检查 Drive API、folder_id 和文件夹权限。") from exc

    files = response.get("files", [])
    if not files:
        children = _list_drive_children(service, folder_id)
        child_names = [str(item.get("name", "")) for item in children if item.get("name")]
        logger.info("Google Drive files visible in current folder: %s", child_names)
        normalized_target = _normalize_drive_name(file_name)
        normalized_matches = [
            item for item in children if _normalize_drive_name(str(item.get("name", ""))) == normalized_target
        ]
        if normalized_matches:
            files = normalized_matches
        else:
            logger.info("Google Drive file not found in current folder file_name=%s", file_name)
    if not files:
        raise DriveUserError(f"Google Drive 文件夹中未找到文件：{file_name}。")
    files = sorted(files, key=lambda item: item.get("modifiedTime", ""), reverse=True)
    item = files[0]
    logger.info("Google Drive file selected name=%s", item.get("name", file_name))
    return _metadata_from_drive_item(item, file_name)


def find_drive_folder(service, parent_folder_id: str, folder_name: str) -> DriveFileMetadata:
    query = (
        f"'{_escape_drive_query_value(parent_folder_id)}' in parents and "
        f"name = '{_escape_drive_query_value(folder_name)}' and "
        "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )
    try:
        response = (
            service.files()
            .list(
                q=query,
                fields="files(id,name,mimeType,modifiedTime,webViewLink)",
                pageSize=10,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                corpora="allDrives",
            )
            .execute()
        )
    except Exception as exc:
        logger.exception("Google Drive folder lookup failed for folder_name=%s", folder_name)
        raise DriveUserError("Google Drive 子文件夹查找失败，请检查文件夹权限。") from exc
    folders = response.get("files", [])
    if not folders:
        children = _list_drive_children(service, parent_folder_id, "application/vnd.google-apps.folder")
        child_names = [str(item.get("name", "")) for item in children if item.get("name")]
        logger.info("Google Drive folders visible in root folder: %s", child_names)
        normalized_target = _normalize_drive_name(folder_name)
        normalized_matches = [
            item for item in children if _normalize_drive_name(str(item.get("name", ""))) == normalized_target
        ]
        if normalized_matches:
            folders = normalized_matches
        else:
            logger.info("Google Drive subfolder not found folder_name=%s", folder_name)
    if not folders:
        raise DriveUserError(f"Google Drive 文件夹中未找到子文件夹：{folder_name}。")
    folders = sorted(folders, key=lambda item: item.get("modifiedTime", ""), reverse=True)
    item = folders[0]
    logger.info("Google Drive subfolder selected name=%s", item.get("name", folder_name))
    return _metadata_from_drive_item(item, folder_name)


def find_drive_file_in_folder_path(
    service,
    root_folder_id: str,
    subfolder_name: str,
    file_names: list[str],
) -> DriveFileMetadata:
    search_locations: list[tuple[str, str]] = []
    try:
        subfolder = find_drive_folder(service, root_folder_id, subfolder_name)
        logger.info("Google Drive searching subfolder name=%s", subfolder.name)
        search_locations.append((subfolder.file_id, subfolder_name))
    except DriveUserError as exc:
        logger.info("Google Drive subfolder unavailable name=%s reason=%s", subfolder_name, exc.__class__.__name__)
    search_locations.append((root_folder_id, "root"))

    errors: list[str] = []
    for folder_id, label in search_locations:
        for file_name in file_names:
            logger.info("Google Drive searching file folder=%s file_name=%s", label, file_name)
            try:
                return find_drive_file(service, folder_id, file_name)
            except DriveUserError as exc:
                errors.append(str(exc))
    names = " / ".join(file_names)
    raise DriveUserError(f"Google Drive 中未找到文件：{subfolder_name}/{names}。")


def list_drive_excel_candidates(service, root_folder_id: str, subfolder_name: str) -> list[DriveFileCandidate]:
    folder = find_drive_folder(service, root_folder_id, subfolder_name)
    items = _list_drive_children(service, folder.file_id)
    candidates = [candidate for item in items if (candidate := _drive_candidate_from_item(item)) is not None]
    candidate_names = [candidate.metadata.name for candidate in candidates]
    logger.info("Google Drive Excel candidates folder=%s files=%s", subfolder_name, candidate_names)
    return candidates


def _sales_candidate_sort_key(candidate: DriveFileCandidate) -> tuple[pd.Timestamp, pd.Timestamp]:
    effective_date = candidate.filename_date
    if effective_date is None:
        effective_date = _candidate_modified_time(candidate).tz_convert(None).normalize()
    return effective_date, _candidate_modified_time(candidate)


def sorted_sales_candidates(candidates: list[DriveFileCandidate]) -> list[DriveFileCandidate]:
    return sorted(candidates, key=_sales_candidate_sort_key, reverse=True)


def _target_candidate_sort_key(candidate: DriveFileCandidate, analysis_year: int | None) -> tuple[int, int, pd.Timestamp]:
    year_matches = 1 if analysis_year is not None and candidate.year == analysis_year else 0
    version = candidate.version if candidate.version is not None else -1
    return year_matches, version, _candidate_modified_time(candidate)


def sorted_target_candidates(candidates: list[DriveFileCandidate], analysis_year: int | None) -> list[DriveFileCandidate]:
    fixed_names = {name.casefold() for name in ["XF_Targets_Latest.xlsx", *TARGET_FILE_FALLBACK_NAMES]}
    target_like = [
        candidate
        for candidate in candidates
        if "target" in candidate.metadata.name.casefold()
        or "目标" in candidate.metadata.name
        or candidate.metadata.name.casefold() in fixed_names
    ]
    return sorted(target_like, key=lambda candidate: _target_candidate_sort_key(candidate, analysis_year), reverse=True)


def _target_selection_reason(candidate: DriveFileCandidate, analysis_year: int | None) -> str:
    parts = []
    if analysis_year is not None and candidate.year == analysis_year:
        parts.append("当前年度匹配")
    if candidate.version is not None:
        parts.append(f"版本号 v{candidate.version}")
    parts.append("Drive 修改时间最新")
    return " + ".join(parts)


def _analysis_year_from_session() -> int | None:
    st = _get_streamlit()
    df = st.session_state.get("clean_data")
    if df is None or "Performance Date" not in df.columns:
        return None
    dates = pd.to_datetime(df["Performance Date"], errors="coerce").dropna()
    return None if dates.empty else int(dates.max().year)


def get_drive_file_metadata(service, file_id: str) -> DriveFileMetadata:
    try:
        item = (
            service.files()
            .get(
                fileId=file_id,
                fields="id,name,mimeType,modifiedTime,size,webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )
    except Exception as exc:
        logger.exception("Google Drive metadata lookup failed file_id=%s", file_id)
        raise DriveUserError("Google Drive 文件 metadata 读取失败。") from exc
    return DriveFileMetadata(
        file_id=str(item.get("id")),
        name=str(item.get("name", "")),
        modified_time=item.get("modifiedTime"),
        mime_type=item.get("mimeType"),
        size=item.get("size"),
        web_view_link=item.get("webViewLink"),
    )


def download_drive_file(service, file_id: str) -> BytesIO:
    try:
        from googleapiclient.http import MediaIoBaseDownload
    except ImportError as exc:
        raise DriveUserError("缺少 Google Drive 下载依赖 google-api-python-client。") from exc

    output = BytesIO()
    try:
        request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
        downloader = MediaIoBaseDownload(output, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    except Exception as exc:
        logger.exception("Google Drive file download failed file_id=%s", file_id)
        raise DriveUserError("Google Drive 文件下载失败，请检查文件权限和网络连接。") from exc
    output.seek(0)
    return output


def store_sales_import_in_session(result: ImportResult, file_name: str, source_label: str, source_type: str, modified_time: str | None = None) -> None:
    st = _get_streamlit()
    st.session_state["quality"] = result.quality
    st.session_state["comparison"] = result.comparison
    st.session_state["sheet_name"] = result.sheet_name
    st.session_state["clean_data"] = result.clean
    st.session_state["current_file_name"] = file_name
    st.session_state["source_file_name"] = file_name
    st.session_state["data_source"] = source_label
    st.session_state["sales_source_type"] = source_type
    st.session_state["data_last_updated"] = modified_time
    st.session_state["source_columns"] = list(result.raw.columns)


def _sales_max_date(clean: pd.DataFrame) -> str:
    dates = pd.to_datetime(clean.get("Performance Date"), errors="coerce").dropna()
    return "" if dates.empty else str(dates.max().date())


def _validate_sales_result(result: ImportResult) -> None:
    clean = result.clean
    if clean is None or clean.empty:
        raise DriveUserError("销售文件校验失败：清洗后没有有效数据。")
    required_columns = ["Performance Date", "Sales Amount", "Customer Code", "Product Code"]
    missing = [column for column in required_columns if column not in clean.columns]
    if missing:
        raise DriveUserError(f"销售文件校验失败：缺少字段 {', '.join(missing)}。")
    if pd.to_datetime(clean["Performance Date"], errors="coerce").dropna().empty:
        raise DriveUserError("销售文件校验失败：没有有效 Performance Date。")
    if pd.to_numeric(clean["Sales Amount"], errors="coerce").dropna().empty:
        raise DriveUserError("销售文件校验失败：没有有效 Sales Amount。")
    if clean["Customer Code"].dropna().astype(str).str.strip().eq("").all():
        raise DriveUserError("销售文件校验失败：没有有效 Customer Code。")
    if clean["Product Code"].dropna().astype(str).str.strip().eq("").all():
        raise DriveUserError("销售文件校验失败：没有有效 Product Code。")


def _validate_target_workbook(parsed: XFTargetWorkbook) -> None:
    if parsed.target_year is None:
        raise DriveUserError("目标文件校验失败：未识别目标年度。")
    target_df = parsed.company_targets
    if target_df is None or target_df.empty:
        raise DriveUserError("目标文件校验失败：未识别公司月度目标。")
    if "Month" not in target_df.columns:
        raise DriveUserError("目标文件校验失败：缺少月份字段。")
    months = set(pd.to_numeric(target_df["Month"], errors="coerce").dropna().astype(int).tolist())
    if set(range(1, 13)) - months:
        raise DriveUserError("目标文件校验失败：缺少 1-12 月金额目标。")
    if not parsed.structure_label:
        raise DriveUserError("目标文件校验失败：目标结构无效。")


def _now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _set_drive_sales_success(metadata: DriveFileMetadata, result: ImportResult, reason: str, candidates: list[DriveFileCandidate]) -> None:
    st = _get_streamlit()
    st.session_state["drive_sales_file_id"] = metadata.file_id
    st.session_state["drive_sales_file_name"] = metadata.name
    st.session_state["drive_sales_modified_time"] = metadata.modified_time
    st.session_state["drive_sales_loaded_at"] = _now_text()
    st.session_state["drive_sales_status"] = "已连接"
    st.session_state["drive_sales_row_count"] = int(len(result.clean))
    st.session_state["drive_sales_max_date"] = _sales_max_date(result.clean)
    st.session_state["drive_sales_selection_reason"] = reason
    st.session_state["drive_sales_candidates"] = [candidate.metadata.name for candidate in candidates[:10]]


def _set_drive_target_success(metadata: DriveFileMetadata, parsed: XFTargetWorkbook, reason: str, candidates: list[DriveFileCandidate]) -> None:
    st = _get_streamlit()
    st.session_state["drive_target_file_id"] = metadata.file_id
    st.session_state["drive_target_file_name"] = metadata.name
    st.session_state["drive_target_modified_time"] = metadata.modified_time
    st.session_state["drive_target_loaded_at"] = _now_text()
    st.session_state["drive_target_status"] = "已连接"
    st.session_state["drive_target_year"] = parsed.target_year
    st.session_state["drive_target_selection_reason"] = reason
    st.session_state["drive_target_candidates"] = [candidate.metadata.name for candidate in candidates[:10]]


def store_target_workbook_in_session(parsed: XFTargetWorkbook, file_name: str, source_label: str, source_type: str, modified_time: str | None = None) -> None:
    st = _get_streamlit()
    target_df = parsed.company_targets.copy()
    target_df["Revised Target"] = pd.to_numeric(target_df["Revised Target"], errors="coerce").fillna(
        pd.to_numeric(target_df["Original Target"], errors="coerce")
    )
    st.session_state["target_data"] = target_df
    st.session_state["target_annual_targets"] = parsed.annual_targets
    st.session_state["target_amount_data"] = parsed.amount_data
    st.session_state["target_case_data"] = parsed.case_data
    st.session_state["target_excel_name"] = file_name
    st.session_state["target_structure_label"] = parsed.structure_label
    st.session_state["target_source"] = source_label
    st.session_state["target_source_type"] = source_type
    st.session_state["target_drive_modified_time"] = modified_time

    if parsed.target_year:
        year_targets = target_df[target_df["Year"].astype("Int64").eq(int(parsed.target_year))]
        if not year_targets.empty:
            st.session_state["home_annual_target"] = float(pd.to_numeric(year_targets["Revised Target"], errors="coerce").fillna(0).sum())
            clean_data = st.session_state.get("clean_data")
            if clean_data is not None and "Performance Date" in clean_data.columns:
                dates = pd.to_datetime(clean_data["Performance Date"], errors="coerce").dropna()
                if not dates.empty and dates.max().year == int(parsed.target_year):
                    anchor_month = int(dates.max().month)
                    month_rows = year_targets[year_targets["Month"].astype(int).eq(anchor_month)]
                    if not month_rows.empty:
                        st.session_state["home_monthly_target"] = float(
                            pd.to_numeric(month_rows.iloc[-1]["Revised Target"], errors="coerce")
                        )


def _load_sales_file(service, config: DriveConfig, force: bool) -> DriveLoadItemStatus:
    st = _get_streamlit()
    if (
        not force
        and st.session_state.get("sales_source_type") == "manual"
        and st.session_state.get("clean_data") is not None
    ):
        return DriveLoadItemStatus("skipped", "当前会话已手动上传销售数据，优先使用手动上传。")
    try:
        candidates = sorted_sales_candidates(list_drive_excel_candidates(service, config.folder_id, SALES_FOLDER_NAME))
    except DriveUserError:
        if st.session_state.get("clean_data") is not None:
            st.session_state["drive_sales_status"] = "使用上次成功版本"
            return DriveLoadItemStatus("using_previous", "Drive 中未找到有效销售文件，当前继续使用本次会话已加载数据。")
        raise
    if not candidates:
        if st.session_state.get("clean_data") is not None:
            st.session_state["drive_sales_status"] = "使用上次成功版本"
            return DriveLoadItemStatus("using_previous", "Drive 中未找到有效销售文件，当前继续使用本次会话已加载数据。")
        raise DriveUserError("Drive 中未找到有效销售 Excel 文件。")

    current_id = st.session_state.get("drive_sales_file_id")
    current_modified = st.session_state.get("drive_sales_modified_time")
    latest_metadata = candidates[0].metadata
    if (
        st.session_state.get("clean_data") is not None
        and current_id
        and current_modified
        and latest_metadata.file_id == current_id
        and latest_metadata.modified_time == current_modified
    ):
        st.session_state["drive_sales_status"] = "已是最新"
        return DriveLoadItemStatus("unchanged", "当前已是最新数据。", latest_metadata.name, latest_metadata.modified_time, latest_metadata.file_id)

    cache_metadata = _read_cache_metadata()
    if _cache_matches("sales", latest_metadata, cache_metadata) and _restore_sales_cache(latest_metadata):
        return DriveLoadItemStatus("cached", "销售数据已从本地缓存加载。", latest_metadata.name, latest_metadata.modified_time, latest_metadata.file_id)

    failures: list[str] = []
    for candidate in candidates:
        metadata = candidate.metadata
        try:
            content = download_drive_file(service, metadata.file_id).getvalue()
            result = import_excel(_NamedBytesIO(content, metadata.name))
            _validate_sales_result(result)
        except ValueError as exc:
            failures.append(f"{metadata.name}: {exc}")
            logger.warning("Google Drive sales candidate rejected file_name=%s reason=%s", metadata.name, exc.__class__.__name__)
            continue
        except DriveUserError as exc:
            failures.append(f"{metadata.name}: {exc}")
            logger.warning("Google Drive sales candidate rejected file_name=%s reason=%s", metadata.name, exc.__class__.__name__)
            continue
        except Exception as exc:
            failures.append(f"{metadata.name}: 解析失败")
            logger.exception("Google Drive sales parse failed file_name=%s", metadata.name)
            continue
        store_sales_import_in_session(result, metadata.name, DRIVE_SOURCE_LABEL, "drive", metadata.modified_time)
        st.session_state["sales_drive_file_id"] = metadata.file_id
        st.session_state["sales_drive_modified_time"] = metadata.modified_time
        _set_drive_sales_success(metadata, result, candidate.reason, candidates)
        _write_sales_cache(metadata, result)
        logger.info("Google Drive sales selected file=%s reason=%s", metadata.name, candidate.reason)
        return DriveLoadItemStatus("loaded", "销售数据已从 Google Drive 加载。", metadata.name, metadata.modified_time, metadata.file_id)

    if st.session_state.get("clean_data") is not None:
        st.session_state["drive_sales_status"] = "使用上次成功版本"
        logger.warning("Google Drive sales all candidates failed, keeping previous data")
        return DriveLoadItemStatus("using_previous", "最新文件解析失败，当前继续使用上一次成功数据。")
    raise DriveUserError("Google Drive 未找到可解析的销售 Excel 文件。")


def _load_target_file(service, config: DriveConfig, force: bool) -> DriveLoadItemStatus:
    st = _get_streamlit()
    if (
        not force
        and st.session_state.get("target_source_type") == "manual"
        and st.session_state.get("target_data") is not None
    ):
        return DriveLoadItemStatus("skipped", "当前会话已手动上传目标数据，优先使用手动上传。")
    analysis_year = _analysis_year_from_session()
    try:
        candidates = sorted_target_candidates(list_drive_excel_candidates(service, config.folder_id, TARGETS_FOLDER_NAME), analysis_year)
    except DriveUserError:
        if st.session_state.get("target_data") is not None:
            st.session_state["drive_target_status"] = "使用上次成功版本"
            return DriveLoadItemStatus("using_previous", "Drive 中未找到有效目标文件，当前继续使用本次会话已加载数据。")
        raise
    if not candidates:
        if st.session_state.get("target_data") is not None:
            st.session_state["drive_target_status"] = "使用上次成功版本"
            return DriveLoadItemStatus("using_previous", "Drive 中未找到有效目标文件，当前继续使用本次会话已加载数据。")
        raise DriveUserError("Drive 中未找到有效目标 Excel 文件。")

    current_id = st.session_state.get("drive_target_file_id")
    current_modified = st.session_state.get("drive_target_modified_time")
    latest_metadata = candidates[0].metadata
    if (
        st.session_state.get("target_data") is not None
        and current_id
        and current_modified
        and latest_metadata.file_id == current_id
        and latest_metadata.modified_time == current_modified
    ):
        st.session_state["drive_target_status"] = "已是最新"
        return DriveLoadItemStatus("unchanged", "当前已是最新数据。", latest_metadata.name, latest_metadata.modified_time, latest_metadata.file_id)

    cache_metadata = _read_cache_metadata()
    if _cache_matches("target", latest_metadata, cache_metadata) and _restore_target_cache(latest_metadata):
        return DriveLoadItemStatus("cached", "目标数据已从本地缓存加载。", latest_metadata.name, latest_metadata.modified_time, latest_metadata.file_id)

    failures: list[str] = []
    for candidate in candidates:
        metadata = candidate.metadata
        try:
            content = download_drive_file(service, metadata.file_id).getvalue()
            parsed = parse_xf_target_workbook(_NamedBytesIO(content, metadata.name))
            _validate_target_workbook(parsed)
        except ValueError as exc:
            failures.append(f"{metadata.name}: {exc}")
            logger.warning("Google Drive target candidate rejected file_name=%s reason=%s", metadata.name, exc.__class__.__name__)
            continue
        except DriveUserError as exc:
            failures.append(f"{metadata.name}: {exc}")
            logger.warning("Google Drive target candidate rejected file_name=%s reason=%s", metadata.name, exc.__class__.__name__)
            continue
        except Exception:
            failures.append(f"{metadata.name}: 解析失败")
            logger.exception("Google Drive target parse failed file_name=%s", metadata.name)
            continue
        store_target_workbook_in_session(parsed, metadata.name, DRIVE_SOURCE_LABEL, "drive", metadata.modified_time)
        st.session_state["target_drive_file_id"] = metadata.file_id
        st.session_state["target_drive_modified_time"] = metadata.modified_time
        reason = _target_selection_reason(candidate, analysis_year)
        _set_drive_target_success(metadata, parsed, reason, candidates)
        _write_target_cache(metadata, parsed)
        logger.info("Google Drive target selected file=%s reason=%s", metadata.name, reason)
        return DriveLoadItemStatus("loaded", "目标数据已从 Google Drive 加载。", metadata.name, metadata.modified_time, metadata.file_id)

    if st.session_state.get("target_data") is not None:
        st.session_state["drive_target_status"] = "使用上次成功版本"
        logger.warning("Google Drive target all candidates failed, keeping previous data")
        return DriveLoadItemStatus("using_previous", "最新目标文件解析失败，当前继续使用上一次成功数据。")
    raise DriveUserError("Google Drive 未找到可解析的目标 Excel 文件。")


def _status_from_error(message: str) -> DriveLoadItemStatus:
    return DriveLoadItemStatus("failed", message)


def load_drive_business_files(force: bool = False) -> DriveLoadStatus:
    st = _get_streamlit()
    start = _timer()
    if not force and st.session_state.get("clean_data") is None:
        cached = _restore_any_local_cache()
        if cached is not None:
            st.session_state["drive_load_status"] = cached
            _perf_log("load_drive_business_files", start, len(st.session_state.get("clean_data", [])), "local-cache")
            return cached

    try:
        config = get_drive_config()
    except DriveUserError as exc:
        status = DriveLoadStatus(
            configured=False,
            message=str(exc),
            sales=DriveLoadItemStatus("not_configured", "Google Drive 销售数据未配置。"),
            targets=DriveLoadItemStatus("not_configured", "Google Drive 目标数据未配置。"),
        )
        st.session_state["drive_load_status"] = status
        return status

    try:
        service = get_drive_service(config)
    except DriveUserError as exc:
        cached = _restore_any_local_cache()
        if cached is not None:
            cached = DriveLoadStatus(
                configured=True,
                message="Google Drive 暂时无法访问，当前继续使用缓存数据。",
                sales=cached.sales,
                targets=cached.targets,
            )
            st.session_state["drive_load_status"] = cached
            _perf_log("load_drive_business_files", start, len(st.session_state.get("clean_data", [])), "drive-failed-cache-hit")
            return cached
        status = DriveLoadStatus(
            configured=True,
            message=str(exc),
            sales=_status_from_error("Google Drive 销售文件读取失败，当前可使用手动上传作为备用。"),
            targets=_status_from_error("Google Drive 目标文件读取失败，当前可使用手动上传作为备用。"),
        )
        st.session_state["drive_load_status"] = status
        return status

    try:
        sales_status = _load_sales_file(service, config, force)
    except DriveUserError as exc:
        sales_status = _status_from_error(str(exc))

    try:
        target_status = _load_target_file(service, config, force)
    except DriveUserError as exc:
        target_status = _status_from_error(str(exc))

    status = DriveLoadStatus(
        configured=True,
        message="Google Drive 已配置。",
        sales=sales_status,
        targets=target_status,
    )
    st.session_state["drive_load_status"] = status
    _perf_log("load_drive_business_files", start, len(st.session_state.get("clean_data", [])) if st.session_state.get("clean_data") is not None else None, "drive")
    return status


def ensure_drive_data_loaded(force: bool = False) -> DriveLoadStatus:
    st = _get_streamlit()
    if not force and st.session_state.get("drive_auto_load_attempted"):
        status = st.session_state.get("drive_load_status")
        if isinstance(status, DriveLoadStatus):
            return status
    st.session_state["drive_auto_load_attempted"] = True
    return load_drive_business_files(force=force)


def clear_drive_state() -> None:
    st = _get_streamlit()
    for key in ["drive_auto_load_attempted", "drive_load_status"]:
        st.session_state.pop(key, None)
    try:
        st.cache_data.clear()
    except Exception:
        pass


def _source_text(source_type: str | None, source_label: str | None) -> str:
    if source_type == "drive":
        return DRIVE_SOURCE_LABEL
    if source_type == "manual":
        return MANUAL_SOURCE_LABEL
    return source_label or "暂无数据"


def _sales_cutoff_text() -> str:
    st = _get_streamlit()
    df = st.session_state.get("clean_data")
    if df is None or "Performance Date" not in df.columns:
        return "无"
    dates = pd.to_datetime(df["Performance Date"], errors="coerce").dropna()
    return "无" if dates.empty else str(dates.max().date())


def _target_years_text() -> str:
    st = _get_streamlit()
    targets = st.session_state.get("target_data")
    if targets is None or targets.empty or "Year" not in targets.columns:
        return "无"
    years = sorted(targets["Year"].dropna().astype(int).unique().tolist())
    return ", ".join(str(year) for year in years) if years else "无"


def render_data_source_sidebar(show_uploaders: bool = False):
    st = _get_streamlit()
    uploaded_sales = None
    uploaded_targets = None
    with st.sidebar:
        st.markdown("### 数据来源 / Data Source")
        status = st.session_state.get("drive_load_status")
        if isinstance(status, DriveLoadStatus) and not status.configured:
            st.caption("Google Drive：尚未配置")
        elif isinstance(status, DriveLoadStatus):
            st.caption("Google Drive：已配置")
        else:
            st.caption("Google Drive：未检查")
        st.caption("系统会自动选择 Google Drive 文件夹中最新且通过校验的 Excel。")

        if st.button("刷新 Google Drive 数据", use_container_width=True):
            clear_drive_state()
            with st.spinner("正在重新加载 Google Drive 数据..."):
                refreshed = load_drive_business_files(force=True)
            messages = [item.message for item in [refreshed.sales, refreshed.targets] if item.message]
            if messages:
                st.session_state["drive_refresh_message"] = "；".join(messages)
            st.rerun()
        if st.session_state.get("drive_refresh_message"):
            st.caption(st.session_state["drive_refresh_message"])

        st.markdown("**销售数据**")
        sales_loaded = st.session_state.get("clean_data") is not None
        st.caption(f"状态：{st.session_state.get('drive_sales_status') or ('已加载' if sales_loaded else '未加载')}")
        st.caption(f"来源：{_source_text(st.session_state.get('sales_source_type'), st.session_state.get('data_source'))}")
        st.caption(f"当前文件名：{st.session_state.get('drive_sales_file_name') or st.session_state.get('source_file_name') or st.session_state.get('current_file_name') or '无'}")
        st.caption(f"Drive 修改时间：{st.session_state.get('drive_sales_modified_time') or st.session_state.get('sales_drive_modified_time') or '无'}")
        st.caption(f"Dashboard 加载时间：{st.session_state.get('drive_sales_loaded_at') or '无'}")
        st.caption(f"数据截止日期：{st.session_state.get('drive_sales_max_date') or _sales_cutoff_text()}")
        st.caption(f"数据行数：{st.session_state.get('drive_sales_row_count') or (len(st.session_state.get('clean_data')) if sales_loaded else '无')}")
        if st.session_state.get("drive_sales_selection_reason"):
            st.caption(f"选择依据：{st.session_state['drive_sales_selection_reason']}")

        st.markdown("**目标数据**")
        target_loaded = st.session_state.get("target_data") is not None
        st.caption(f"状态：{st.session_state.get('drive_target_status') or ('已加载' if target_loaded else '未加载')}")
        st.caption(f"来源：{_source_text(st.session_state.get('target_source_type'), st.session_state.get('target_source'))}")
        st.caption(f"当前文件名：{st.session_state.get('drive_target_file_name') or st.session_state.get('target_excel_name') or '无'}")
        st.caption(f"Drive 修改时间：{st.session_state.get('drive_target_modified_time') or st.session_state.get('target_drive_modified_time') or '无'}")
        st.caption(f"Dashboard 加载时间：{st.session_state.get('drive_target_loaded_at') or '无'}")
        st.caption(f"识别年度：{st.session_state.get('drive_target_year') or _target_years_text()}")
        if st.session_state.get("drive_target_selection_reason"):
            st.caption(f"选择依据：{st.session_state['drive_target_selection_reason']}")

        if show_uploaders:
            with st.expander("手动上传销售数据", expanded=False):
                uploaded_sales = st.file_uploader(
                    "上传销售明细 / Upload Unleashed Sales Data",
                    type=["xlsx"],
                    key="sales_data_upload",
                )
            with st.expander("手动上传目标数据", expanded=False):
                uploaded_targets = st.file_uploader(
                    "上传目标表 / Upload Targets Excel",
                    type=["xlsx"],
                    key="sidebar_target_excel_upload",
                )
    return uploaded_sales, uploaded_targets
