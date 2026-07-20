from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
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

from app.config import LINE_ID_CANDIDATES, METHODOLOGY_VERSION
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
DRIVE_CACHE_VERSION = f"drive_cache_v4_{METHODOLOGY_VERSION}"
CACHE_DIR = Path(".cache")
CACHE_METADATA_PATH = CACHE_DIR / "metadata.json"
CACHE_SALES_PATH = CACHE_DIR / "sales_clean.parquet"
CACHE_SALES_EXTRAS_PATH = CACHE_DIR / "sales_extras.pkl"
CACHE_TARGETS_PATH = CACHE_DIR / "targets_clean.pkl"
MERGED_SALES_FILE_NAME = "Google Drive 合并销售数据"


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


def _sales_cache_matches_manifest(manifest_signature: str, cache_metadata: dict[str, Any]) -> bool:
    return (
        cache_metadata.get("cache_version") == DRIVE_CACHE_VERSION
        and cache_metadata.get("sales_manifest_signature") == manifest_signature
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
            _set_drive_sales_merge_stats(extras.get("merge_stats", {}))
        _perf_log("restore_sales_parquet", start, len(clean), "hit")
        return True
    except Exception:
        logger.exception("Local sales cache restore failed")
        _perf_log("restore_sales_parquet", start, cache="corrupt")
        return False


def _write_sales_cache(
    metadata: DriveFileMetadata | None,
    result: ImportResult,
    manifest: list[dict[str, Any]] | None = None,
    manifest_signature: str | None = None,
    merge_stats: dict[str, Any] | None = None,
) -> None:
    start = _timer()
    try:
        _ensure_cache_dir()
        result.clean.to_parquet(CACHE_SALES_PATH, index=False)
        extras = {
            "file_id": metadata.file_id if metadata else manifest_signature,
            "file_name": metadata.name if metadata else MERGED_SALES_FILE_NAME,
            "modified_time": metadata.modified_time if metadata else None,
            "quality": result.quality,
            "comparison": result.comparison,
            "sheet_name": result.sheet_name,
            "source_columns": list(result.raw.columns),
            "cache_created_at": _now_text(),
            "merge_stats": merge_stats or {},
        }
        with CACHE_SALES_EXTRAS_PATH.open("wb") as handle:
            pickle.dump(extras, handle)
        cache_metadata = _read_cache_metadata()
        cache_metadata.update(
            {
                "cache_version": DRIVE_CACHE_VERSION,
                "sales_file_id": metadata.file_id if metadata else manifest_signature,
                "sales_modified_time": metadata.modified_time if metadata else None,
                "sales_file_name": metadata.name if metadata else MERGED_SALES_FILE_NAME,
                "sales_size": metadata.size if metadata else None,
                "sales_manifest": manifest or [],
                "sales_manifest_signature": manifest_signature,
                "sales_max_date": _sales_max_date(result.clean),
                "cache_created_at": _now_text(),
                "sales_merge_stats": merge_stats or {},
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
    if metadata.get("sales_manifest_signature"):
        sales_metadata = None
    elif metadata.get("sales_file_id") and metadata.get("sales_file_name"):
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


def _safe_file_id(file_id: str | None) -> str:
    text = str(file_id or "")
    if len(text) <= 10:
        return text
    return f"{text[:5]}...{text[-5:]}"


def _date_text(value: pd.Timestamp | None) -> str:
    if value is None or pd.isna(value):
        return "none"
    return str(pd.Timestamp(value).date())


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


def _log_sales_candidates(stage: str, candidates: list[DriveFileCandidate]) -> None:
    safe_rows = [
        {
            "rank": index + 1,
            "name": candidate.metadata.name,
            "file_id": _safe_file_id(candidate.metadata.file_id),
            "modifiedTime": candidate.metadata.modified_time,
            "filename_date": _date_text(candidate.filename_date),
            "sort_date": _date_text(_sales_candidate_sort_key(candidate)[0]),
            "reason": candidate.reason,
        }
        for index, candidate in enumerate(candidates)
    ]
    logger.info("Google Drive sales candidates %s: %s", stage, safe_rows)


def _sales_manifest(candidates: list[DriveFileCandidate]) -> list[dict[str, Any]]:
    return [
        {
            "file_id": candidate.metadata.file_id,
            "name": candidate.metadata.name,
            "modified_time": candidate.metadata.modified_time,
            "size": candidate.metadata.size,
            "filename_date": _date_text(candidate.filename_date),
        }
        for candidate in candidates
    ]


def _sales_manifest_signature(manifest: list[dict[str, Any]]) -> str:
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _non_empty_count(series: pd.Series) -> int:
    return int(series.dropna().astype(str).str.strip().ne("").sum())


def _first_non_empty_column(df: pd.DataFrame, columns: list[str]) -> str | None:
    for column in columns:
        if column in df.columns and _non_empty_count(df[column]) > 0:
            return column
    return None


def _complete_identity_column(df: pd.DataFrame, preferred: str, fallback: str) -> str | None:
    if preferred in df.columns and _non_empty_count(df[preferred]) == len(df):
        return preferred
    if fallback in df.columns and _non_empty_count(df[fallback]) > 0:
        return fallback
    return _first_non_empty_column(df, [preferred, fallback])


def _sales_dedupe_subset(df: pd.DataFrame) -> tuple[list[str], str]:
    line_id = _first_non_empty_column(df, LINE_ID_CANDIDATES)
    if line_id:
        return ["Order No.", line_id], f"Order No. + {line_id}"

    customer_identity = _complete_identity_column(df, "Customer Code", "Customer")
    product_identity = _complete_identity_column(df, "Product Code", "Product")
    subset = [
        column
        for column in ["Order No.", customer_identity, product_identity, "Order Date", "Quantity", "Sales Amount"]
        if column and column in df.columns
    ]
    return subset, " + ".join(subset)


def _dedupe_sales_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    if df.empty:
        return df.copy(), {"dedupe_key": "无", "duplicate_rows_removed": 0}
    subset, label = _sales_dedupe_subset(df)
    if not subset:
        return df.copy(), {"dedupe_key": "无可用字段", "duplicate_rows_removed": 0}
    before = len(df)
    deduped = df.drop_duplicates(subset=subset, keep="first").reset_index(drop=True)
    return deduped, {
        "dedupe_key": label,
        "duplicate_rows_removed": int(before - len(deduped)),
    }


def _sales_key_frame(df: pd.DataFrame) -> pd.DataFrame:
    subset, _ = _sales_dedupe_subset(df)
    if not subset:
        return pd.DataFrame(index=df.index)
    return df[subset].astype("string").fillna("")


def _count_new_sales_rows(previous: pd.DataFrame | None, current: pd.DataFrame) -> int:
    if previous is None or previous.empty:
        return int(len(current))
    previous_keys = _sales_key_frame(previous)
    current_keys = _sales_key_frame(current)
    if previous_keys.empty or current_keys.empty:
        return int(len(current))
    previous_set = set(map(tuple, previous_keys.to_numpy()))
    return int(sum(tuple(row) not in previous_set for row in current_keys.to_numpy()))


def _sales_date_range(clean: pd.DataFrame) -> tuple[str, str]:
    dates = pd.to_datetime(clean.get("Performance Date"), errors="coerce").dropna()
    if dates.empty:
        return "", ""
    return str(dates.min().date()), str(dates.max().date())


def _set_drive_sales_merge_stats(stats: dict[str, Any]) -> None:
    if not stats:
        return
    st = _get_streamlit()
    st.session_state["drive_sales_file_count"] = stats.get("file_count")
    st.session_state["drive_sales_input_rows"] = stats.get("input_rows")
    st.session_state["drive_sales_deduped_rows"] = stats.get("deduped_rows")
    st.session_state["drive_sales_duplicate_rows_removed"] = stats.get("duplicate_rows_removed")
    st.session_state["drive_sales_new_records"] = stats.get("new_records")
    st.session_state["drive_sales_earliest_date"] = stats.get("earliest_date")
    st.session_state["drive_sales_latest_date"] = stats.get("latest_date")
    st.session_state["drive_sales_dedupe_key"] = stats.get("dedupe_key")
    st.session_state["drive_sales_failed_files"] = stats.get("failed_files", [])
    st.session_state["drive_sales_loaded_files"] = stats.get("loaded_files", [])
    st.session_state["drive_sales_manifest_signature"] = stats.get("manifest_signature")


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
    required_columns = ["Performance Date", "Sales Amount"]
    missing = [column for column in required_columns if column not in clean.columns]
    if missing:
        raise DriveUserError(f"销售文件校验失败：缺少字段 {', '.join(missing)}。")
    if pd.to_datetime(clean["Performance Date"], errors="coerce").dropna().empty:
        raise DriveUserError("销售文件校验失败：没有有效 Performance Date。")
    if pd.to_numeric(clean["Sales Amount"], errors="coerce").dropna().empty:
        raise DriveUserError("销售文件校验失败：没有有效 Sales Amount。")
    customer_fields = [column for column in ["Customer Code", "Customer Key", "Customer Label", "Customer"] if column in clean.columns]
    product_fields = [column for column in ["Product Code", "Product Key", "Product Label", "Product"] if column in clean.columns]
    has_customer_identity = any(clean[column].dropna().astype(str).str.strip().ne("").any() for column in customer_fields)
    has_product_identity = any(clean[column].dropna().astype(str).str.strip().ne("").any() for column in product_fields)
    if not has_customer_identity:
        raise DriveUserError("销售文件校验失败：没有有效客户标识。")
    if not has_product_identity:
        raise DriveUserError("销售文件校验失败：没有有效产品标识。")


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


def _set_drive_sales_merge_success(
    result: ImportResult,
    candidates: list[DriveFileCandidate],
    manifest_signature: str,
    merge_stats: dict[str, Any],
) -> None:
    st = _get_streamlit()
    st.session_state["drive_sales_file_id"] = manifest_signature
    st.session_state["drive_sales_file_name"] = MERGED_SALES_FILE_NAME
    st.session_state["drive_sales_modified_time"] = max(
        (candidate.metadata.modified_time or "" for candidate in candidates),
        default="",
    )
    st.session_state["drive_sales_loaded_at"] = _now_text()
    st.session_state["drive_sales_status"] = "已连接"
    st.session_state["drive_sales_row_count"] = int(len(result.clean))
    st.session_state["drive_sales_max_date"] = _sales_max_date(result.clean)
    st.session_state["drive_sales_selection_reason"] = "合并 sales data 文件夹内全部销售 Excel"
    st.session_state["drive_sales_candidates"] = [candidate.metadata.name for candidate in candidates[:20]]
    _set_drive_sales_merge_stats(merge_stats)


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
        raw_candidates = list_drive_excel_candidates(service, config.folder_id, SALES_FOLDER_NAME)
        _log_sales_candidates("scanned", raw_candidates)
        candidates = sorted_sales_candidates(raw_candidates)
        _log_sales_candidates("sorted", candidates)
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

    manifest = _sales_manifest(candidates)
    manifest_signature = _sales_manifest_signature(manifest)
    current_signature = st.session_state.get("drive_sales_manifest_signature") or st.session_state.get("drive_sales_file_id")
    logger.info(
        "Google Drive sales folder manifest files=%s signature=%s force=%s current_signature=%s",
        [candidate.metadata.name for candidate in candidates],
        _safe_file_id(manifest_signature),
        force,
        _safe_file_id(str(current_signature) if current_signature else None),
    )
    if st.session_state.get("clean_data") is not None and current_signature == manifest_signature:
        st.session_state["drive_sales_status"] = "已是最新"
        return DriveLoadItemStatus("unchanged", "当前已是最新数据。", MERGED_SALES_FILE_NAME, None, manifest_signature)

    cache_metadata = _read_cache_metadata()
    if _sales_cache_matches_manifest(manifest_signature, cache_metadata) and _restore_sales_cache(None):
        st.session_state["drive_sales_manifest_signature"] = manifest_signature
        return DriveLoadItemStatus("cached", "销售数据已从本地缓存加载。", MERGED_SALES_FILE_NAME, None, manifest_signature)

    failures: list[str] = []
    imported_results: list[tuple[DriveFileCandidate, ImportResult]] = []
    previous_clean = None
    if CACHE_SALES_PATH.exists():
        try:
            previous_clean = pd.read_parquet(CACHE_SALES_PATH)
        except Exception:
            previous_clean = None
    logger.info("Google Drive sales candidate attempt order: %s", [candidate.metadata.name for candidate in candidates])
    for candidate in candidates:
        metadata = candidate.metadata
        try:
            logger.info(
                "Google Drive sales attempting file name=%s file_id=%s modifiedTime=%s filename_date=%s",
                metadata.name,
                _safe_file_id(metadata.file_id),
                metadata.modified_time,
                _date_text(candidate.filename_date),
            )
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
            failures.append(f"{metadata.name}: 解析失败（{exc.__class__.__name__}: {exc}）")
            logger.exception("Google Drive sales parse failed file_name=%s", metadata.name)
            continue
        imported_results.append((candidate, result))

    if imported_results:
        raw_frames = [result.raw for _, result in imported_results if result.raw is not None]
        clean_frames = [result.clean for _, result in imported_results if result.clean is not None and not result.clean.empty]
        combined_raw = pd.concat(raw_frames, ignore_index=True, sort=False) if raw_frames else pd.DataFrame()
        combined_clean = pd.concat(clean_frames, ignore_index=True, sort=False) if clean_frames else pd.DataFrame()
        input_rows = int(len(combined_clean))
        deduped_clean, dedupe_stats = _dedupe_sales_rows(combined_clean)
        earliest_date, latest_date = _sales_date_range(deduped_clean)
        new_records = _count_new_sales_rows(previous_clean, deduped_clean)
        loaded_files = [candidate.metadata.name for candidate, _ in imported_results]
        merge_stats = {
            "file_count": len(imported_results),
            "candidate_file_count": len(candidates),
            "input_rows": input_rows,
            "deduped_rows": int(len(deduped_clean)),
            "duplicate_rows_removed": dedupe_stats["duplicate_rows_removed"],
            "new_records": new_records,
            "earliest_date": earliest_date,
            "latest_date": latest_date,
            "dedupe_key": dedupe_stats["dedupe_key"],
            "failed_files": failures,
            "loaded_files": loaded_files,
            "manifest_signature": manifest_signature,
        }
        quality = dict(imported_results[0][1].quality)
        quality.update(
            {
                "Google Drive 读取文件数": len(imported_results),
                "Google Drive 候选文件数": len(candidates),
                "Google Drive 合并前行数": input_rows,
                "Google Drive 去重后行数": int(len(deduped_clean)),
                "Google Drive 跨文件去重行数": dedupe_stats["duplicate_rows_removed"],
                "Google Drive 本次新增记录数": new_records,
                "Google Drive 最早日期": earliest_date,
                "Google Drive 最新日期": latest_date,
                "Google Drive 去重键": dedupe_stats["dedupe_key"],
                "Google Drive 读取失败文件": "；".join(failures) if failures else "无",
            }
        )
        result = ImportResult(
            raw=combined_raw,
            clean=deduped_clean,
            quality=quality,
            sheet_name="; ".join(f"{candidate.metadata.name}:{import_result.sheet_name}" for candidate, import_result in imported_results),
            comparison={},
        )
        store_sales_import_in_session(result, MERGED_SALES_FILE_NAME, DRIVE_SOURCE_LABEL, "drive", None)
        st.session_state["sales_drive_file_id"] = manifest_signature
        st.session_state["sales_drive_modified_time"] = max((candidate.metadata.modified_time or "" for candidate in candidates), default="")
        _set_drive_sales_merge_success(result, candidates, manifest_signature, merge_stats)
        _write_sales_cache(None, result, manifest, manifest_signature, merge_stats)
        if failures:
            st.session_state["drive_sales_status"] = "部分文件失败"
            st.session_state["drive_sales_failure_details"] = failures
        else:
            st.session_state.pop("drive_sales_failure_details", None)
            st.session_state.pop("drive_sales_content_warning", None)
        logger.info(
            "Google Drive sales merged files=%s input_rows=%s deduped_rows=%s duplicate_rows_removed=%s new_records=%s earliest=%s latest=%s failed=%s",
            loaded_files,
            input_rows,
            len(deduped_clean),
            dedupe_stats["duplicate_rows_removed"],
            new_records,
            earliest_date,
            latest_date,
            failures,
        )
        message = (
            f"销售数据已合并 {len(imported_results)} 个 Google Drive 文件；"
            f"合并前 {input_rows:,} 行，去重后 {len(deduped_clean):,} 行，"
            f"本次新增 {new_records:,} 行。"
        )
        if failures:
            message = f"{message} 部分文件读取失败：{'；'.join(failures)}"
        return DriveLoadItemStatus("loaded", message, MERGED_SALES_FILE_NAME, None, manifest_signature)

    if st.session_state.get("clean_data") is not None:
        st.session_state["drive_sales_status"] = "使用上次成功版本"
        st.session_state["drive_sales_failure_details"] = failures
        logger.warning("Google Drive sales all candidates failed, keeping previous data")
        detail = "；".join(failures) if failures else "未记录具体原因"
        return DriveLoadItemStatus("using_previous", f"最新销售文件解析失败，当前继续使用上一次成功数据。失败原因：{detail}")
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


def _sidebar_time_text(value: object) -> str:
    if not value:
        return "无"
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.strftime("%H:%M")
    except ValueError:
        match = re.search(r"\b(\d{2}:\d{2})", text)
        return match.group(1) if match else text


def _sidebar_sync_status() -> str:
    st = _get_streamlit()
    status = st.session_state.get("drive_load_status")
    sales_loaded = st.session_state.get("clean_data") is not None
    if isinstance(status, DriveLoadStatus) and status.sales.status == "failed":
        return "● 同步异常"
    if sales_loaded:
        return "● 已同步"
    return "● 未同步"


def render_data_source_sidebar(show_uploaders: bool = False):
    st = _get_streamlit()
    try:
        from app.auth import current_user, role_allows

        user = current_user()
        can_use_data_sync = bool(user and role_allows(user.role, "data_sync"))
    except Exception:
        can_use_data_sync = False
    uploaded_sales = None
    uploaded_targets = None
    with st.sidebar:
        st.markdown("### 数据状态")
        status = st.session_state.get("drive_load_status")
        sales_loaded = st.session_state.get("clean_data") is not None
        target_loaded = st.session_state.get("target_data") is not None
        row_count = st.session_state.get("drive_sales_row_count") or (len(st.session_state.get("clean_data")) if sales_loaded else "无")
        loaded_at = st.session_state.get("drive_sales_loaded_at") or st.session_state.get("drive_target_loaded_at")
        st.caption(
            f"{_sidebar_sync_status()}\n\n"
            f"截止：{st.session_state.get('drive_sales_max_date') or _sales_cutoff_text()}  \n"
            f"更新：{_sidebar_time_text(loaded_at)}"
        )

        with st.expander("查看数据详情", expanded=False):
            if isinstance(status, DriveLoadStatus) and not status.configured:
                st.caption("Google Drive：尚未配置")
            elif isinstance(status, DriveLoadStatus):
                st.caption("Google Drive：已配置")
            else:
                st.caption("Google Drive：未检查")

            st.markdown("**销售数据**")
            st.caption(f"状态：{st.session_state.get('drive_sales_status') or ('已加载' if sales_loaded else '未加载')}")
            st.caption(f"来源：{_source_text(st.session_state.get('sales_source_type'), st.session_state.get('data_source'))}")
            st.caption(f"当前文件名：{st.session_state.get('drive_sales_file_name') or st.session_state.get('source_file_name') or st.session_state.get('current_file_name') or '无'}")
            st.caption(f"Drive 修改时间：{st.session_state.get('drive_sales_modified_time') or st.session_state.get('sales_drive_modified_time') or '无'}")
            st.caption(f"Dashboard 加载时间：{st.session_state.get('drive_sales_loaded_at') or '无'}")
            st.caption(f"数据截止日期：{st.session_state.get('drive_sales_max_date') or _sales_cutoff_text()}")
            st.caption(f"数据行数：{row_count}")
            if st.session_state.get("drive_sales_selection_reason"):
                st.caption(f"选择依据：{st.session_state['drive_sales_selection_reason']}")
            if st.session_state.get("drive_sales_file_count"):
                st.caption(f"读取文件数：{st.session_state.get('drive_sales_file_count')}")
                st.caption(f"合并前总行数：{st.session_state.get('drive_sales_input_rows')}")
                st.caption(f"去重后总行数：{st.session_state.get('drive_sales_deduped_rows')}")
                st.caption(f"跨文件去重行数：{st.session_state.get('drive_sales_duplicate_rows_removed')}")
                st.caption(f"本次新增记录数：{st.session_state.get('drive_sales_new_records')}")
                st.caption(f"数据日期范围：{st.session_state.get('drive_sales_earliest_date') or '无'} 至 {st.session_state.get('drive_sales_latest_date') or '无'}")
                st.caption(f"去重键：{st.session_state.get('drive_sales_dedupe_key') or '无'}")
            if st.session_state.get("drive_sales_failed_files"):
                st.caption("读取失败文件：")
                for failure in st.session_state.get("drive_sales_failed_files", []):
                    st.caption(f"- {failure}")

            st.markdown("**目标数据**")
            st.caption(f"状态：{st.session_state.get('drive_target_status') or ('已加载' if target_loaded else '未加载')}")
            st.caption(f"来源：{_source_text(st.session_state.get('target_source_type'), st.session_state.get('target_source'))}")
            st.caption(f"当前文件名：{st.session_state.get('drive_target_file_name') or st.session_state.get('target_excel_name') or '无'}")
            st.caption(f"Drive 修改时间：{st.session_state.get('drive_target_modified_time') or st.session_state.get('target_drive_modified_time') or '无'}")
            st.caption(f"Dashboard 加载时间：{st.session_state.get('drive_target_loaded_at') or '无'}")
            st.caption(f"识别年度：{st.session_state.get('drive_target_year') or _target_years_text()}")
            if st.session_state.get("drive_target_selection_reason"):
                st.caption(f"选择依据：{st.session_state['drive_target_selection_reason']}")

        if can_use_data_sync:
            with st.expander("数据同步", expanded=False):
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

                if show_uploaders:
                    st.markdown("**手动上传销售数据**")
                    uploaded_sales = st.file_uploader(
                        "上传销售明细 / Upload Unleashed Sales Data",
                        type=["xlsx"],
                        key="sales_data_upload",
                    )
                    st.markdown("**手动上传目标数据**")
                    uploaded_targets = st.file_uploader(
                        "上传目标表 / Upload Targets Excel",
                        type=["xlsx"],
                        key="sidebar_target_excel_upload",
                    )
    return uploaded_sales, uploaded_targets
