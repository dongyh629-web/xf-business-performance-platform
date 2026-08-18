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
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Callable, TypeVar

import pandas as pd
import streamlit as st

from app.config import LINE_ID_CANDIDATES, METHODOLOGY_VERSION
from app.cost_snapshots import (
    CostFileMetadata,
    CostSnapshot,
    CostSnapshotRegistry,
    build_cost_snapshot_registry,
    load_cost_snapshot_from_bytes,
)
from app.credit_notes import (
    CreditFileMetadata,
    CreditSnapshot,
    CreditSnapshotRegistry,
    build_credit_snapshot_registry,
    load_credit_snapshot_from_bytes,
    merge_credit_snapshots,
)
from app.data import ImportResult, import_excel
from app.google_transport import GoogleHttpStatusError, close_google_service, google_auth_request, stage_timer, with_google_transport_retry
from app.target_metrics import XFTargetWorkbook, parse_xf_target_workbook


logger = logging.getLogger(__name__)

DRIVE_SOURCE_LABEL = "Google Drive"
MANUAL_SOURCE_LABEL = "本次会话手动上传"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
SHORTCUT_MIME_TYPE = "application/vnd.google-apps.shortcut"
SALES_FOLDER_NAME = "sales data"
SALES_FOLDER_NAMES = ("sales data", "sales")
TARGETS_FOLDER_NAMES = ("targets data", "target data", "targets", "target")
COST_FOLDER_NAMES = ("cost data", "costs data", "cost", "costs")
COST_FOLDER_NAME = "Cost Data"
CREDIT_FOLDER_NAMES = ("credit notes", "credits", "credit data", "returns credits")
CREDIT_FOLDER_NAME = "Credit Notes"
TARGET_FILE_FALLBACK_NAMES = ["XF 2026销售目标_Target may.xlsx"]
EXCEL_EXTENSIONS = (".xlsx", ".xls")
DRIVE_CACHE_VERSION = f"drive_cache_v4_{METHODOLOGY_VERSION}"
CACHE_DIR = Path(".cache")
CACHE_METADATA_PATH = CACHE_DIR / "metadata.json"
CACHE_SALES_PATH = CACHE_DIR / "sales_clean.parquet"
CACHE_SALES_EXTRAS_PATH = CACHE_DIR / "sales_extras.pkl"
CACHE_TARGETS_PATH = CACHE_DIR / "targets_clean.pkl"
CACHE_COST_SNAPSHOTS_PATH = CACHE_DIR / "cost_snapshots.pkl"
CACHE_CREDIT_SNAPSHOT_PATH = CACHE_DIR / "credit_snapshot.pkl"
MERGED_CREDIT_SNAPSHOT_CACHE_MODE = "merged_all_snapshots"
MERGED_SALES_FILE_NAME = "Google Drive 合并销售数据"
SALES_REFRESH_CORE_KEYS = {
    "drive_auto_load_attempted",
    "drive_load_status",
    "quality",
    "comparison",
    "sheet_name",
    "clean_data",
    "current_file_name",
    "source_file_name",
    "data_source",
    "sales_source_type",
    "data_last_updated",
    "source_columns",
    "sales_drive_file_id",
    "sales_drive_modified_time",
}
REFRESH_STATE_PREFIXES = ("drive_sales_", "sales_drive_", "drive_cost_", "cost_snapshot", "drive_credit_", "credit_", "drive_target_", "target_")
SUCCESSFUL_REFRESH_STATUSES = {"loaded", "cached", "unchanged"}
REFRESH_TRANSACTION_TIMEOUT_SECONDS = 180
REFRESH_STALE_LOCK_SECONDS = 120
DRIVE_CONNECT_TIMEOUT_SECONDS = 5
DRIVE_READ_TIMEOUT_SECONDS = 30
DRIVE_CREDENTIALS_REFRESH_TIMEOUT_SECONDS = 20
SALES_STAGE_TIMEOUT_SECONDS = 150
TARGET_STAGE_TIMEOUT_SECONDS = 45
COST_STAGE_TIMEOUT_SECONDS = 75
CREDIT_STAGE_TIMEOUT_SECONDS = 60
SALES_PARSE_TIMEOUT_SECONDS = 90
TARGET_PARSE_TIMEOUT_SECONDS = 30
COST_PARSE_TIMEOUT_SECONDS = 30
CREDIT_PARSE_TIMEOUT_SECONDS = 30

T = TypeVar("T")


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


class DriveRestClient:
    BASE_URL = "https://www.googleapis.com/drive/v3"
    TIMEOUT = (DRIVE_CONNECT_TIMEOUT_SECONDS, DRIVE_READ_TIMEOUT_SECONDS)

    def __init__(self, session):
        self._session = session

    def close(self) -> None:
        self._session.close()

    def _request_json(self, stage: str, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        def operation():
            request_start = _timer()
            logger.info("drive_request_started stage=%s method=%s", stage, method)
            response = self._session.request(method, url, timeout=self.TIMEOUT, **kwargs)
            logger.info(
                "drive_request_completed stage=%s method=%s elapsed_seconds=%.3f status_code=%s",
                stage,
                method,
                _elapsed_seconds(request_start),
                response.status_code,
            )
            if response.status_code >= 400:
                raise GoogleHttpStatusError(response.status_code, stage)
            if not response.content:
                return {}
            return response.json()

        return with_google_transport_retry(stage, operation)

    def _request_bytes(self, stage: str, method: str, url: str, **kwargs: Any) -> bytes:
        def operation():
            request_start = _timer()
            logger.info("drive_request_started stage=%s method=%s", stage, method)
            response = self._session.request(method, url, timeout=self.TIMEOUT, **kwargs)
            logger.info(
                "drive_request_completed stage=%s method=%s elapsed_seconds=%.3f status_code=%s bytes=%s",
                stage,
                method,
                _elapsed_seconds(request_start),
                response.status_code,
                len(response.content or b""),
            )
            if response.status_code >= 400:
                raise GoogleHttpStatusError(response.status_code, stage)
            return bytes(response.content)

        return with_google_transport_retry(stage, operation)

    def list_files(self, **params: Any) -> dict[str, Any]:
        return self._request_json("drive_files_list", "GET", f"{self.BASE_URL}/files", params=params)

    def get_file(self, file_id: str, **params: Any) -> dict[str, Any]:
        return self._request_json("drive_file_get", "GET", f"{self.BASE_URL}/files/{file_id}", params=params)

    def download_file(self, file_id: str) -> bytes:
        return self._request_bytes(
            "drive_file_download",
            "GET",
            f"{self.BASE_URL}/files/{file_id}",
            params={"alt": "media", "supportsAllDrives": "true"},
        )


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


def _elapsed_seconds(start: float) -> float:
    return time.perf_counter() - start


def _raise_if_refresh_deadline_expired(start: float, stage: str, timeout_seconds: int = REFRESH_TRANSACTION_TIMEOUT_SECONDS) -> None:
    elapsed = _elapsed_seconds(start)
    if elapsed > timeout_seconds:
        logger.warning(
            "refresh_transaction_failed stage=%s elapsed_seconds=%.3f error_type=Timeout",
            stage,
            elapsed,
        )
        raise DriveUserError("Google Drive 数据加载超时，请稍后重试。")


class _StageTimeout(RuntimeError):
    pass


def _timeout_message(stage: str, timeout_seconds: int) -> str:
    return f"{stage} 超过 {timeout_seconds} 秒未完成。"


def _run_with_signal_timeout(stage: str, timeout_seconds: int, operation: Callable[[], T]) -> T:
    previous_handler = signal.getsignal(signal.SIGALRM)

    def _handle_timeout(_signum, _frame):
        raise _StageTimeout(_timeout_message(stage, timeout_seconds))

    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        return operation()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _run_with_thread_timeout(stage: str, timeout_seconds: int, operation: Callable[[], T]) -> T:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"xf-{stage}")
    future = executor.submit(operation)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError as exc:
        future.cancel()
        raise _StageTimeout(_timeout_message(stage, timeout_seconds)) from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _run_blocking_stage(stage: str, timeout_seconds: int, operation: Callable[[], T]) -> T:
    """Run a blocking parse stage with a bounded wait and user-safe timeout."""
    start = _timer()
    logger.info("%s_started timeout_seconds=%s", stage, timeout_seconds)
    try:
        if hasattr(signal, "SIGALRM") and threading.current_thread() is threading.main_thread():
            result = _run_with_signal_timeout(stage, timeout_seconds, operation)
        else:
            result = _run_with_thread_timeout(stage, timeout_seconds, operation)
        logger.info("%s_completed elapsed_seconds=%.3f", stage, _elapsed_seconds(start))
        return result
    except _StageTimeout as exc:
        logger.warning("%s_failed elapsed_seconds=%.3f error_type=Timeout", stage, _elapsed_seconds(start))
        raise DriveUserError(f"{stage} 超时，请稍后重试。") from exc
    except DriveUserError:
        logger.warning("%s_failed elapsed_seconds=%.3f error_type=DriveUserError", stage, _elapsed_seconds(start))
        raise
    except Exception as exc:
        logger.warning("%s_failed elapsed_seconds=%.3f error_type=%s", stage, _elapsed_seconds(start), exc.__class__.__name__)
        raise


def _raise_if_stage_timeout(start: float, stage: str, timeout_seconds: int) -> None:
    elapsed = _elapsed_seconds(start)
    if elapsed > timeout_seconds:
        logger.warning("%s_failed elapsed_seconds=%.3f error_type=StageTimeout", stage, elapsed)
        raise DriveUserError(f"{stage} 超过 {timeout_seconds} 秒未完成，请稍后重试。")


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
        logger.info("cache_write_started cache=sales rows=%s", len(result.clean))
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
        logger.info(
            "cache_write_completed cache=sales elapsed_seconds=%.3f rows=%s completed_date_max=%s",
            _elapsed_seconds(start),
            len(result.clean),
            _sales_max_date(result.clean),
        )
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
        rows = 0 if parsed.company_targets is None else len(parsed.company_targets)
        logger.info("cache_write_started cache=targets rows=%s source_file=%s", rows, metadata.name)
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
        logger.info("cache_write_completed cache=targets elapsed_seconds=%.3f rows=%s source_file=%s", _elapsed_seconds(start), rows, metadata.name)
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


def restore_drive_data_from_cache() -> DriveLoadStatus | None:
    st = _get_streamlit()
    if st.session_state.get("clean_data") is not None:
        status = st.session_state.get("drive_load_status")
        if isinstance(status, DriveLoadStatus):
            return status
        return DriveLoadStatus(
            True,
            "业务数据已加载。",
            DriveLoadItemStatus("loaded", "销售数据已加载。"),
            DriveLoadItemStatus("loaded" if st.session_state.get("target_data") is not None else "failed", "目标数据已加载。"),
        )
    with stage_timer("sales_load") as sales_done:
        cached = _restore_any_local_cache()
        clean = st.session_state.get("clean_data")
        sales_done(rows=len(clean) if clean is not None else None, status="local-cache" if cached is not None else "not-loaded")
    if cached is not None:
        target_data = st.session_state.get("target_data")
        with stage_timer("targets_load") as target_done:
            target_done(rows=len(target_data) if target_data is not None else None, status="local-cache")
        st.session_state["drive_load_status"] = cached
        return cached
    status = DriveLoadStatus(
        True,
        "尚未同步业务数据。",
        DriveLoadItemStatus("not_loaded", "尚未加载销售数据。"),
        DriveLoadItemStatus("not_loaded", "尚未加载目标数据。"),
    )
    st.session_state["drive_load_status"] = status
    st.session_state["drive_sales_status"] = "尚未同步"
    st.session_state["drive_target_status"] = "尚未同步"
    return status


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


def get_drive_service(config: DriveConfig):
    try:
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise DriveUserError("缺少 Google Drive 读取依赖，请确认 requirements.txt 已安装 google-auth。") from exc

    try:
        credentials = Credentials(
            token=None,
            refresh_token=config.refresh_token,
            token_uri=config.token_uri,
            client_id=config.client_id,
            client_secret=config.client_secret,
            scopes=[DRIVE_SCOPE],
        )
        with stage_timer("google_drive_initialization") as done:
            credentials_start = _timer()
            logger.info("credentials_refresh_started")
            with_google_transport_retry(
                "drive_credentials_refresh",
                lambda: credentials.refresh(google_auth_request(timeout=DRIVE_CREDENTIALS_REFRESH_TIMEOUT_SECONDS)),
            )
            logger.info(
                "credentials_refresh_completed elapsed_seconds=%.3f",
                _elapsed_seconds(credentials_start),
            )
            client = DriveRestClient(AuthorizedSession(credentials))
            done()
            return client
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
    seen_page_tokens: set[str] = set()
    page_count = 0
    try:
        while True:
            if page_token:
                if page_token in seen_page_tokens:
                    raise DriveUserError("Google Drive 分页响应重复，已停止读取以避免长时间等待。")
                seen_page_tokens.add(page_token)
            page_count += 1
            if page_count > 50:
                raise DriveUserError("Google Drive 文件夹分页过多，已停止读取以避免长时间等待。")
            response = service.list_files(
                q=" and ".join(query_parts),
                fields="nextPageToken,files(id,name,mimeType,modifiedTime,size,webViewLink,shortcutDetails)",
                pageSize=100,
                pageToken=page_token,
                supportsAllDrives="true",
                includeItemsFromAllDrives="true",
                corpora="allDrives",
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
    shortcut = item.get("shortcutDetails") or {}
    file_id = str(shortcut.get("targetId") or item.get("id"))
    mime_type = shortcut.get("targetMimeType") or item.get("mimeType")
    return DriveFileMetadata(
        file_id=file_id,
        name=str(item.get("name", fallback_name)),
        modified_time=item.get("modifiedTime"),
        mime_type=mime_type,
        size=item.get("size"),
        web_view_link=item.get("webViewLink"),
    )


def find_drive_file(service, folder_id: str, file_name: str) -> DriveFileMetadata:
    query = (
        f"'{_escape_drive_query_value(folder_id)}' in parents and "
        f"name = '{_escape_drive_query_value(file_name)}' and trashed = false"
    )
    try:
        response = service.list_files(
            q=query,
            fields="files(id,name,mimeType,modifiedTime,size,webViewLink)",
            pageSize=10,
            supportsAllDrives="true",
            includeItemsFromAllDrives="true",
            corpora="allDrives",
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


def _is_drive_folder_or_folder_shortcut(item: dict[str, Any]) -> bool:
    if item.get("mimeType") == FOLDER_MIME_TYPE:
        return True
    shortcut = item.get("shortcutDetails") or {}
    return item.get("mimeType") == SHORTCUT_MIME_TYPE and shortcut.get("targetMimeType") == FOLDER_MIME_TYPE


def _folder_display_name(item: dict[str, Any]) -> str:
    name = str(item.get("name", "")).strip()
    if item.get("mimeType") == SHORTCUT_MIME_TYPE:
        return f"{name} (shortcut)"
    return name


def _visible_folder_names(children: list[dict[str, Any]]) -> list[str]:
    return [_folder_display_name(item) for item in children if _is_drive_folder_or_folder_shortcut(item)]


def _find_folder_item_from_children(children: list[dict[str, Any]], folder_names: tuple[str, ...]) -> dict[str, Any] | None:
    normalized_targets = {_normalize_drive_name(name) for name in folder_names}
    matches = [
        item
        for item in children
        if _is_drive_folder_or_folder_shortcut(item)
        and _normalize_drive_name(str(item.get("name", ""))) in normalized_targets
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda item: item.get("modifiedTime", ""), reverse=True)[0]


def find_drive_folder_from_aliases(
    service,
    parent_folder_id: str,
    folder_type: str,
    folder_names: tuple[str, ...],
) -> DriveFileMetadata:
    try:
        children = _list_drive_children(service, parent_folder_id)
    except DriveUserError as exc:
        raise DriveUserError(f"{folder_type} 子文件夹查找失败，请检查文件夹权限。") from exc

    child_names = _visible_folder_names(children)
    logger.info(
        "Google Drive folder discovery type=%s tried=%s visible=%s",
        folder_type,
        list(folder_names),
        child_names,
    )
    item = _find_folder_item_from_children(children, folder_names)
    if item is None:
        tried = " / ".join(folder_names)
        visible = " / ".join(child_names) if child_names else "未发现直接子文件夹"
        raise DriveUserError(f"{folder_type} 子文件夹未找到。尝试名称：{tried}。实际发现：{visible}。")

    metadata = _metadata_from_drive_item(item, str(item.get("name", folder_names[0])))
    logger.info("Google Drive subfolder selected type=%s name=%s", folder_type, metadata.name)
    return metadata


def find_drive_folder(service, parent_folder_id: str, folder_name: str) -> DriveFileMetadata:
    return find_drive_folder_from_aliases(service, parent_folder_id, "Google Drive", (folder_name,))


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


def list_drive_excel_candidates_from_folders(
    service,
    root_folder_id: str,
    folder_type: str,
    subfolder_names: tuple[str, ...],
) -> list[DriveFileCandidate]:
    folder = find_drive_folder_from_aliases(service, root_folder_id, folder_type, subfolder_names)
    items = _list_drive_children(service, folder.file_id)
    candidates = [candidate for item in items if (candidate := _drive_candidate_from_item(item)) is not None]
    candidate_names = [candidate.metadata.name for candidate in candidates]
    logger.info("Google Drive Excel candidates folder=%s files=%s", folder.name, candidate_names)
    return candidates


def list_drive_cost_snapshot_candidates(service, root_folder_id: str) -> CostSnapshotRegistry:
    folder = find_drive_folder_from_aliases(service, root_folder_id, "Cost Data", COST_FOLDER_NAMES)
    items = _list_drive_children(service, folder.file_id)
    metadata = [
        CostFileMetadata(
            file_id=str(item.get("id", "")),
            name=str(item.get("name", "")),
            modified_time=item.get("modifiedTime"),
            size=item.get("size"),
        )
        for item in items
        if _is_excel_file_name(str(item.get("name", "")))
    ]
    registry = build_cost_snapshot_registry(metadata)
    logger.info(
        "Google Drive cost snapshot candidates folder=%s files=%s",
        COST_FOLDER_NAME,
        [entry.file_name for entry in registry.entries],
    )
    return registry


def list_drive_credit_snapshot_candidates(service, root_folder_id: str) -> CreditSnapshotRegistry:
    folder = find_drive_folder_from_aliases(service, root_folder_id, "Credit Notes", CREDIT_FOLDER_NAMES)
    items = _list_drive_children(service, folder.file_id)
    metadata = [
        CreditFileMetadata(
            file_id=str(item.get("id", "")),
            name=str(item.get("name", "")),
            modified_time=item.get("modifiedTime"),
            size=item.get("size"),
        )
        for item in items
        if _is_excel_file_name(str(item.get("name", "")))
    ]
    registry = build_credit_snapshot_registry(metadata)
    logger.info(
        "Google Drive credit snapshot candidates folder=%s files=%s",
        CREDIT_FOLDER_NAME,
        [entry.file_name for entry in registry.entries],
    )
    return registry


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


def _cost_manifest(registry: CostSnapshotRegistry) -> list[dict[str, Any]]:
    return [
        {
            "file_id": entry.file_id,
            "name": entry.file_name,
            "modified_time": entry.modified_time,
            "size": entry.size,
            "cost_version_date": _date_text(entry.cost_version_date),
        }
        for entry in registry.entries
    ]


def _cost_manifest_signature(manifest: list[dict[str, Any]]) -> str:
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _credit_manifest(registry: CreditSnapshotRegistry) -> list[dict[str, Any]]:
    return [
        {
            "file_id": entry.file_id,
            "name": entry.file_name,
            "modified_time": entry.modified_time,
            "size": entry.size,
            "snapshot_date": _date_text(entry.snapshot_date),
        }
        for entry in registry.entries
    ]


def _credit_manifest_signature(manifest: list[dict[str, Any]]) -> str:
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _restore_cost_snapshot_cache(manifest_signature: str | None = None) -> tuple[CostSnapshotRegistry, list[CostSnapshot]] | None:
    start = _timer()
    if not CACHE_COST_SNAPSHOTS_PATH.exists():
        return None
    try:
        with CACHE_COST_SNAPSHOTS_PATH.open("rb") as handle:
            payload = pickle.load(handle)
        cache_signature = payload.get("manifest_signature")
        if manifest_signature and cache_signature != manifest_signature:
            return None
        registry = payload.get("registry")
        snapshots = payload.get("snapshots")
        if not isinstance(registry, CostSnapshotRegistry) or not isinstance(snapshots, list):
            return None
        _perf_log("restore_cost_snapshots", start, len(snapshots), "hit")
        return registry, snapshots
    except Exception:
        logger.exception("Local cost snapshot cache restore failed")
        _perf_log("restore_cost_snapshots", start, cache="corrupt")
        return None


def _get_session_cost_snapshots() -> tuple[CostSnapshotRegistry, list[CostSnapshot]] | None:
    st = _get_streamlit()
    registry = st.session_state.get("cost_snapshot_registry")
    snapshots = st.session_state.get("cost_snapshots")
    if isinstance(registry, CostSnapshotRegistry) and isinstance(snapshots, list):
        return registry, snapshots
    return None


def _set_session_cost_snapshots(
    registry: CostSnapshotRegistry,
    snapshots: list[CostSnapshot],
    status: str,
    message: str,
) -> None:
    st = _get_streamlit()
    st.session_state["cost_snapshot_registry"] = registry
    st.session_state["cost_snapshots"] = snapshots
    st.session_state["drive_cost_status"] = status
    st.session_state["drive_cost_message"] = message
    st.session_state["drive_cost_snapshot_count"] = len(snapshots)
    st.session_state["drive_cost_registry_count"] = len(registry.entries)
    version_dates = sorted({str(snapshot.version_date.date()) for snapshot in snapshots})
    st.session_state["drive_cost_version_dates"] = ", ".join(version_dates)
    st.session_state["drive_cost_loaded_at"] = _now_text()
    st.session_state["drive_cost_load_status"] = DriveLoadItemStatus(status, message)


def _write_cost_snapshot_cache(
    registry: CostSnapshotRegistry,
    snapshots: list[CostSnapshot],
    manifest: list[dict[str, Any]],
    manifest_signature: str,
) -> None:
    start = _timer()
    try:
        logger.info("cache_write_started cache=cost snapshots=%s rows=%s", len(snapshots), sum(len(snapshot.data) for snapshot in snapshots))
        _ensure_cache_dir()
        with CACHE_COST_SNAPSHOTS_PATH.open("wb") as handle:
            pickle.dump(
                {
                    "registry": registry,
                    "snapshots": snapshots,
                    "manifest": manifest,
                    "manifest_signature": manifest_signature,
                    "cache_created_at": _now_text(),
                },
                handle,
            )
        cache_metadata = _read_cache_metadata()
        cache_metadata.update(
            {
                "cache_version": DRIVE_CACHE_VERSION,
                "cost_manifest": manifest,
                "cost_manifest_signature": manifest_signature,
                "cost_snapshot_count": len(snapshots),
                "cost_cache_created_at": _now_text(),
            }
        )
        _write_cache_metadata(cache_metadata)
        logger.info("cache_write_completed cache=cost elapsed_seconds=%.3f snapshots=%s", _elapsed_seconds(start), len(snapshots))
        _perf_log("write_cost_snapshots", start, len(snapshots), "miss")
    except Exception:
        logger.exception("Local cost snapshot cache write failed")


def _restore_credit_snapshot_cache(manifest_signature: str | None = None) -> tuple[CreditSnapshotRegistry, CreditSnapshot] | None:
    start = _timer()
    if not CACHE_CREDIT_SNAPSHOT_PATH.exists():
        return None
    try:
        with CACHE_CREDIT_SNAPSHOT_PATH.open("rb") as handle:
            payload = pickle.load(handle)
        cache_signature = payload.get("manifest_signature")
        if manifest_signature and cache_signature != manifest_signature:
            return None
        if payload.get("snapshot_mode") != MERGED_CREDIT_SNAPSHOT_CACHE_MODE:
            return None
        registry = payload.get("registry")
        snapshot = payload.get("snapshot")
        if not isinstance(registry, CreditSnapshotRegistry) or not isinstance(snapshot, CreditSnapshot):
            return None
        _perf_log("restore_credit_snapshot", start, len(snapshot.data), "hit")
        return registry, snapshot
    except Exception:
        logger.exception("Local credit snapshot cache restore failed")
        _perf_log("restore_credit_snapshot", start, cache="corrupt")
        return None


def _get_session_credit_snapshot() -> tuple[CreditSnapshotRegistry, CreditSnapshot] | None:
    st = _get_streamlit()
    registry = st.session_state.get("credit_snapshot_registry")
    snapshot = st.session_state.get("credit_snapshot")
    if isinstance(registry, CreditSnapshotRegistry) and isinstance(snapshot, CreditSnapshot):
        return registry, snapshot
    return None


def _set_session_credit_snapshot(
    registry: CreditSnapshotRegistry,
    snapshot: CreditSnapshot | None,
    status: str,
    message: str,
) -> None:
    st = _get_streamlit()
    st.session_state["credit_snapshot_registry"] = registry
    if snapshot is not None:
        st.session_state["credit_snapshot"] = snapshot
        st.session_state["credit_data"] = snapshot.data
        st.session_state["credit_quality"] = snapshot.quality
        st.session_state["credit_source_name"] = snapshot.file_name
        st.session_state["credit_sheet_name"] = "Google Drive Snapshot"
        st.session_state["credit_raw_columns"] = list(snapshot.data.columns)
        st.session_state["drive_credit_latest_snapshot"] = str(snapshot.snapshot_date.date())
        st.session_state["drive_credit_file_name"] = snapshot.file_name
        st.session_state["drive_credit_row_count"] = len(snapshot.data)
        st.session_state["drive_credit_note_count"] = snapshot.quality.get("Credit Note Count", 0)
        dates = pd.to_datetime(snapshot.data.get("Credit Date"), errors="coerce").dropna()
        st.session_state["drive_credit_date_range"] = "无" if dates.empty else f"{dates.min().date()} 至 {dates.max().date()}"
    else:
        st.session_state.pop("credit_snapshot", None)
        st.session_state.pop("credit_data", None)
        st.session_state.pop("credit_quality", None)
        st.session_state["drive_credit_latest_snapshot"] = "无"
        st.session_state["drive_credit_file_name"] = "无"
        st.session_state["drive_credit_row_count"] = 0
        st.session_state["drive_credit_note_count"] = 0
        st.session_state["drive_credit_date_range"] = "无"
    st.session_state["drive_credit_status"] = status
    st.session_state["drive_credit_message"] = message
    st.session_state["drive_credit_registry_count"] = len(registry.entries)
    st.session_state["drive_credit_snapshot_count"] = len(registry.valid_entries())
    st.session_state["drive_credit_candidates"] = [entry.file_name for entry in registry.entries[:10]]
    st.session_state["drive_credit_loaded_at"] = _now_text()
    st.session_state["drive_credit_load_status"] = DriveLoadItemStatus(status, message)


def _write_credit_snapshot_cache(
    registry: CreditSnapshotRegistry,
    snapshot: CreditSnapshot,
    manifest: list[dict[str, Any]],
    manifest_signature: str,
) -> None:
    start = _timer()
    try:
        logger.info("cache_write_started cache=credit rows=%s source_file=%s", len(snapshot.data), snapshot.file_name)
        _ensure_cache_dir()
        with CACHE_CREDIT_SNAPSHOT_PATH.open("wb") as handle:
            pickle.dump(
                {
                    "registry": registry,
                    "snapshot": snapshot,
                    "snapshot_mode": MERGED_CREDIT_SNAPSHOT_CACHE_MODE,
                    "manifest": manifest,
                    "manifest_signature": manifest_signature,
                    "cache_created_at": _now_text(),
                },
                handle,
            )
        cache_metadata = _read_cache_metadata()
        cache_metadata.update(
            {
                "cache_version": DRIVE_CACHE_VERSION,
                "credit_manifest": manifest,
                "credit_manifest_signature": manifest_signature,
                "credit_snapshot_file_name": snapshot.file_name,
                "credit_snapshot_date": str(snapshot.snapshot_date.date()),
                "credit_snapshot_mode": MERGED_CREDIT_SNAPSHOT_CACHE_MODE,
                "credit_rows": len(snapshot.data),
                "credit_note_count": snapshot.quality.get("Credit Note Count", 0),
                "credit_cache_created_at": _now_text(),
            }
        )
        _write_cache_metadata(cache_metadata)
        logger.info("cache_write_completed cache=credit elapsed_seconds=%.3f rows=%s source_file=%s", _elapsed_seconds(start), len(snapshot.data), snapshot.file_name)
        _perf_log("write_credit_snapshot", start, len(snapshot.data), "miss")
    except Exception:
        logger.exception("Local credit snapshot cache write failed")


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
    source_col = "Source File" if "Source File" in df.columns else None
    source_rank_col = "Source Snapshot Rank" if "Source Snapshot Rank" in df.columns else None
    if not source_col:
        deduped = df.copy().reset_index(drop=True)
        return deduped, {
            "dedupe_key": label,
            "duplicate_rows_removed": 0,
            "cross_file_duplicates_removed": 0,
            "intra_file_identical_rows_preserved": int(df.duplicated(subset=subset, keep=False).sum()),
            "ambiguous_duplicates_preserved": 0,
            "dedupe_scope": "未识别 Source File，保留同文件可能合法重复行。",
        }

    work = df.copy()
    work["_xf_original_position"] = range(len(work))
    if source_rank_col:
        work["_xf_source_rank"] = pd.to_numeric(work[source_rank_col], errors="coerce").fillna(10**9)
    else:
        source_order = {source: rank for rank, source in enumerate(work[source_col].astype("string").fillna("").drop_duplicates().tolist())}
        work["_xf_source_rank"] = work[source_col].astype("string").fillna("").map(source_order).fillna(10**9)

    key_cols = subset
    same_file_duplicate_rows = work.duplicated(subset=key_cols + [source_col], keep=False)
    cross_file_group_sizes = work.groupby(key_cols, dropna=False)[source_col].transform(lambda values: values.astype("string").nunique(dropna=False))
    cross_file_duplicate_rows = cross_file_group_sizes.gt(1)
    ambiguous_duplicates_preserved = 0
    ambiguous_key_cols = [
        column
        for column in key_cols
        if column not in {"Sales Amount"} and column in work.columns
    ]
    if "Sales Amount" in work.columns and ambiguous_key_cols:
        weak_source_counts = work.groupby(ambiguous_key_cols, dropna=False)[source_col].transform(
            lambda values: values.astype("string").nunique(dropna=False)
        )
        weak_amount_counts = work.groupby(ambiguous_key_cols, dropna=False)["Sales Amount"].transform("nunique")
        ambiguous_duplicates_preserved = int((weak_source_counts.gt(1) & weak_amount_counts.gt(1)).sum())

    keep_mask = pd.Series(True, index=work.index)
    if cross_file_duplicate_rows.any():
        latest_rank = work.loc[cross_file_duplicate_rows].groupby(key_cols, dropna=False)["_xf_source_rank"].transform("min")
        drop_cross_file_old_rows = cross_file_duplicate_rows.copy()
        drop_cross_file_old_rows.loc[cross_file_duplicate_rows] = (
            work.loc[cross_file_duplicate_rows, "_xf_source_rank"].to_numpy() != latest_rank.to_numpy()
        )
        keep_mask &= ~drop_cross_file_old_rows
    else:
        drop_cross_file_old_rows = pd.Series(False, index=work.index)

    deduped = (
        work.loc[keep_mask]
        .sort_values("_xf_original_position")
        .drop(columns=["_xf_original_position", "_xf_source_rank"])
        .reset_index(drop=True)
    )
    duplicate_rows_removed = int(before - len(deduped))
    return deduped, {
        "dedupe_key": label,
        "duplicate_rows_removed": duplicate_rows_removed,
        "cross_file_duplicates_removed": int(drop_cross_file_old_rows.sum()),
        "intra_file_identical_rows_preserved": int((same_file_duplicate_rows & ~drop_cross_file_old_rows).sum()),
        "ambiguous_duplicates_preserved": ambiguous_duplicates_preserved,
        "dedupe_scope": "同一 Source File 内保留全部原始行；跨 Source File 重叠记录保留较新快照。",
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
    st.session_state["drive_sales_cross_file_duplicates_removed"] = stats.get("cross_file_duplicates_removed")
    st.session_state["drive_sales_intra_file_identical_rows_preserved"] = stats.get("intra_file_identical_rows_preserved")
    st.session_state["drive_sales_ambiguous_duplicates_preserved"] = stats.get("ambiguous_duplicates_preserved")
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
        item = service.get_file(
            file_id,
            fields="id,name,mimeType,modifiedTime,size,webViewLink",
            supportsAllDrives="true",
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
        output = BytesIO(service.download_file(file_id))
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
    if clean is None or "Performance Date" not in clean.columns:
        return ""
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


def _load_sales_file(service, config: DriveConfig, force: bool) -> DriveLoadItemStatus:
    st = _get_streamlit()
    stage_start = _timer()
    if (
        not force
        and st.session_state.get("sales_source_type") == "manual"
        and st.session_state.get("clean_data") is not None
    ):
        return DriveLoadItemStatus("skipped", "当前会话已手动上传销售数据，优先使用手动上传。")
    try:
        raw_candidates = list_drive_excel_candidates_from_folders(service, config.folder_id, "Sales Data", SALES_FOLDER_NAMES)
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
        _raise_if_stage_timeout(stage_start, "sales_load", SALES_STAGE_TIMEOUT_SECONDS)
        metadata = candidate.metadata
        try:
            candidate_start = _timer()
            logger.info(
                "Google Drive sales attempting file name=%s modifiedTime=%s filename_date=%s",
                metadata.name,
                metadata.modified_time,
                _date_text(candidate.filename_date),
            )
            logger.info("sales_download_started file_name=%s", metadata.name)
            content = download_drive_file(service, metadata.file_id).getvalue()
            logger.info("sales_download_completed file_name=%s elapsed_seconds=%.3f bytes=%s", metadata.name, _elapsed_seconds(candidate_start), len(content))
            parse_start = _timer()
            logger.info("sales_parse_started file_name=%s", metadata.name)
            result = _run_blocking_stage(
                "sales_parse",
                SALES_PARSE_TIMEOUT_SECONDS,
                lambda content=content, name=metadata.name: import_excel(_NamedBytesIO(content, name)),
            )
            _validate_sales_result(result)
            logger.info("sales_parse_completed file_name=%s elapsed_seconds=%.3f rows=%s", metadata.name, _elapsed_seconds(parse_start), len(result.clean))
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
        _raise_if_stage_timeout(stage_start, "sales_load", SALES_STAGE_TIMEOUT_SECONDS)
        imported_results.append((candidate, result))

    if imported_results:
        raw_frames = [result.raw for _, result in imported_results if result.raw is not None]
        clean_frames = []
        for source_rank, (candidate, result) in enumerate(imported_results):
            if result.clean is None or result.clean.empty:
                continue
            clean_frame = result.clean.copy()
            clean_frame["Source File"] = candidate.metadata.name
            clean_frame["Source Snapshot Rank"] = source_rank
            clean_frame["Source Modified Time"] = candidate.metadata.modified_time
            clean_frame["Source Snapshot Date"] = (
                pd.Timestamp(candidate.filename_date).date().isoformat()
                if candidate.filename_date is not None and not pd.isna(candidate.filename_date)
                else pd.NA
            )
            clean_frames.append(clean_frame)
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
            "cross_file_duplicates_removed": dedupe_stats.get("cross_file_duplicates_removed", dedupe_stats["duplicate_rows_removed"]),
            "intra_file_identical_rows_preserved": dedupe_stats.get("intra_file_identical_rows_preserved", 0),
            "ambiguous_duplicates_preserved": dedupe_stats.get("ambiguous_duplicates_preserved", 0),
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
                "Google Drive 同文件完全相同行保留数": dedupe_stats.get("intra_file_identical_rows_preserved", 0),
                "Google Drive 跨文件重复删除行数": dedupe_stats.get("cross_file_duplicates_removed", dedupe_stats["duplicate_rows_removed"]),
                "Google Drive 模糊重复保留行数": dedupe_stats.get("ambiguous_duplicates_preserved", 0),
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
            "sales_loaded_summary source_file=%s rows=%s completed_date_max=%s loaded_files=%s input_rows=%s duplicate_rows_removed=%s new_records=%s earliest=%s latest=%s failed=%s",
            MERGED_SALES_FILE_NAME,
            len(deduped_clean),
            _sales_max_date(deduped_clean),
            loaded_files,
            input_rows,
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
    stage_start = _timer()
    if (
        not force
        and st.session_state.get("target_source_type") == "manual"
        and st.session_state.get("target_data") is not None
    ):
        return DriveLoadItemStatus("skipped", "当前会话已手动上传目标数据，优先使用手动上传。")
    analysis_year = _analysis_year_from_session()
    try:
        candidates = sorted_target_candidates(
            list_drive_excel_candidates_from_folders(service, config.folder_id, "Targets Data", TARGETS_FOLDER_NAMES),
            analysis_year,
        )
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
        _raise_if_stage_timeout(stage_start, "targets_load", TARGET_STAGE_TIMEOUT_SECONDS)
        metadata = candidate.metadata
        try:
            candidate_start = _timer()
            logger.info("targets_load_started file_name=%s", metadata.name)
            content = download_drive_file(service, metadata.file_id).getvalue()
            logger.info("targets_download_completed file_name=%s elapsed_seconds=%.3f bytes=%s", metadata.name, _elapsed_seconds(candidate_start), len(content))
            parse_start = _timer()
            logger.info("targets_parse_started file_name=%s", metadata.name)
            parsed = _run_blocking_stage(
                "targets_parse",
                TARGET_PARSE_TIMEOUT_SECONDS,
                lambda content=content, name=metadata.name: parse_xf_target_workbook(_NamedBytesIO(content, name)),
            )
            _validate_target_workbook(parsed)
            logger.info("targets_parse_completed file_name=%s elapsed_seconds=%.3f", metadata.name, _elapsed_seconds(parse_start))
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
        target_rows = len(st.session_state.get("target_data", [])) if st.session_state.get("target_data") is not None else 0
        logger.info(
            "targets_loaded_summary source_file=%s rows=%s target_year=%s reason=%s",
            metadata.name,
            target_rows,
            parsed.target_year,
            reason,
        )
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
        with stage_timer("sales_load") as sales_done:
            cached = _restore_any_local_cache()
            clean = st.session_state.get("clean_data")
            sales_done(rows=len(clean) if clean is not None else None, status="local-cache" if cached is not None else "cache-miss")
        if cached is not None:
            target_data = st.session_state.get("target_data")
            with stage_timer("targets_load") as target_done:
                target_done(rows=len(target_data) if target_data is not None else None, status="local-cache")
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
        try:
            with stage_timer("sales_load") as done:
                sales_status = _load_sales_file(service, config, force)
                clean = st.session_state.get("clean_data")
                done(rows=len(clean) if clean is not None else None, status=sales_status.status)
        except DriveUserError as exc:
            sales_status = _status_from_error(str(exc))

        try:
            with stage_timer("targets_load") as done:
                target_status = _load_target_file(service, config, force)
                target_data = st.session_state.get("target_data")
                done(rows=len(target_data) if target_data is not None else None, status=target_status.status)
        except DriveUserError as exc:
            target_status = _status_from_error(str(exc))
    finally:
        close_google_service(service)

    status = DriveLoadStatus(
        configured=True,
        message="Google Drive 已配置。",
        sales=sales_status,
        targets=target_status,
    )
    st.session_state["drive_load_status"] = status
    _perf_log("load_drive_business_files", start, len(st.session_state.get("clean_data", [])) if st.session_state.get("clean_data") is not None else None, "drive")
    return status


def load_drive_cost_snapshots(force: bool = False) -> tuple[CostSnapshotRegistry, list[CostSnapshot]]:
    start = _timer()
    stage_start = _timer()
    session_cost = _get_session_cost_snapshots()
    if not force and session_cost is not None:
        registry, snapshots = session_cost
        _perf_log("load_drive_cost_snapshots", start, len(snapshots), "session")
        return registry, snapshots
    if not force:
        cached = _restore_cost_snapshot_cache()
        if cached is not None:
            _set_session_cost_snapshots(cached[0], cached[1], "cached", "成本快照已从本地缓存加载。")
            _perf_log("load_drive_cost_snapshots", start, len(cached[1]), "local-cache")
            return cached
        registry = CostSnapshotRegistry([])
        _set_session_cost_snapshots(registry, [], "not_loaded", "尚未加载成本快照。请点击刷新 Google Drive 数据。")
        _perf_log("load_drive_cost_snapshots", start, 0, "not-loaded")
        return registry, []

    try:
        config = get_drive_config()
        with stage_timer("cost_snapshot_load") as done:
            service = get_drive_service(config)
            try:
                registry = list_drive_cost_snapshot_candidates(service, config.folder_id)
                done(rows=len(registry.entries), status="registry")
            finally:
                close_google_service(service)
    except DriveUserError as exc:
        if session_cost is not None:
            registry, snapshots = session_cost
            _set_session_cost_snapshots(
                registry,
                snapshots,
                "using_previous",
                f"成本快照刷新失败，当前继续使用上一次成功成本数据。失败原因：{exc}",
            )
            return registry, snapshots
        raise

    manifest = _cost_manifest(registry)
    manifest_signature = _cost_manifest_signature(manifest)
    if not force:
        cached = _restore_cost_snapshot_cache(manifest_signature)
        if cached is not None:
            _set_session_cost_snapshots(cached[0], cached[1], "cached", "成本快照已从本地缓存加载。")
            return cached

    snapshots: list[CostSnapshot] = []
    service = get_drive_service(config)
    try:
        with stage_timer("cost_snapshot_load") as done:
            for entry in registry.entries:
                _raise_if_stage_timeout(stage_start, "cost_load", COST_STAGE_TIMEOUT_SECONDS)
                if not entry.participates_in_matching:
                    continue
                try:
                    snapshot_start = _timer()
                    logger.info("cost_download_started file_name=%s", entry.file_name)
                    content = download_drive_file(service, str(entry.file_id)).getvalue()
                    logger.info("cost_download_completed file_name=%s elapsed_seconds=%.3f bytes=%s", entry.file_name, _elapsed_seconds(snapshot_start), len(content))
                    parse_start = _timer()
                    logger.info("cost_parse_started file_name=%s", entry.file_name)
                    snapshot = _run_blocking_stage(
                        "cost_parse",
                        COST_PARSE_TIMEOUT_SECONDS,
                        lambda content=content, entry=entry: load_cost_snapshot_from_bytes(content, entry),
                    )
                    logger.info("cost_parse_completed file_name=%s elapsed_seconds=%.3f rows=%s", entry.file_name, _elapsed_seconds(parse_start), len(snapshot.data))
                except Exception as exc:
                    entry.validation_status = "Invalid"
                    entry.errors.append(f"Snapshot parse failed: {exc.__class__.__name__}")
                    entry.participates_in_matching = False
                    logger.warning("Google Drive cost snapshot rejected file_name=%s reason=%s", entry.file_name, exc.__class__.__name__)
                    continue
                entry.row_count = snapshot.registry_entry.row_count
                entry.valid_sku_count = snapshot.registry_entry.valid_sku_count
                entry.duplicate_sku_count = snapshot.registry_entry.duplicate_sku_count
                entry.validation_status = snapshot.registry_entry.validation_status
                entry.warnings.extend(snapshot.registry_entry.warnings)
                entry.errors.extend(snapshot.registry_entry.errors)
                entry.participates_in_matching = snapshot.registry_entry.participates_in_matching
                if snapshot.registry_entry.participates_in_matching:
                    snapshots.append(snapshot)
                _raise_if_stage_timeout(stage_start, "cost_load", COST_STAGE_TIMEOUT_SECONDS)
            done(rows=len(snapshots), status="loaded")
    finally:
        close_google_service(service)

    manifest = _cost_manifest(registry)
    manifest_signature = _cost_manifest_signature(manifest)
    if not snapshots and session_cost is not None:
        previous_registry, previous_snapshots = session_cost
        _set_session_cost_snapshots(
            previous_registry,
            previous_snapshots,
            "using_previous",
            "Drive 中未找到有效成本快照，当前继续使用上一次成功成本数据。",
        )
        _perf_log("load_drive_cost_snapshots", start, len(previous_snapshots), "drive-failed-previous")
        return previous_registry, previous_snapshots

    _write_cost_snapshot_cache(registry, snapshots, manifest, manifest_signature)
    _set_session_cost_snapshots(registry, snapshots, "loaded", "成本快照已从 Google Drive 加载。")
    logger.info(
        "cost_loaded_summary snapshots=%s rows=%s versions=%s source_files=%s",
        len(snapshots),
        sum(len(snapshot.data) for snapshot in snapshots),
        [str(snapshot.version_date.date()) for snapshot in snapshots],
        [snapshot.file_name for snapshot in snapshots],
    )
    _perf_log("load_drive_cost_snapshots", start, len(snapshots), "drive")
    return registry, snapshots


def load_drive_credit_snapshot(force: bool = False) -> tuple[CreditSnapshotRegistry, CreditSnapshot | None]:
    start = _timer()
    stage_start = _timer()
    session_credit = _get_session_credit_snapshot()
    if not force and session_credit is not None:
        registry, snapshot = session_credit
        _perf_log("load_drive_credit_snapshot", start, len(snapshot.data), "session")
        return registry, snapshot
    if not force:
        cached = _restore_credit_snapshot_cache()
        if cached is not None:
            _set_session_credit_snapshot(cached[0], cached[1], "cached", "Credit Notes 已从本地缓存加载。")
            _perf_log("load_drive_credit_snapshot", start, len(cached[1].data), "local-cache")
            return cached
        registry = CreditSnapshotRegistry([])
        _set_session_credit_snapshot(registry, None, "not_loaded", "尚未加载 Credit Notes。请点击 Refresh Credit Notes。")
        _perf_log("load_drive_credit_snapshot", start, 0, "not-loaded")
        return registry, None

    try:
        config = get_drive_config()
        with stage_timer("credit_snapshot_load") as done:
            service = get_drive_service(config)
            try:
                registry = list_drive_credit_snapshot_candidates(service, config.folder_id)
                done(rows=len(registry.entries), status="registry")
            finally:
                close_google_service(service)
    except DriveUserError as exc:
        if session_credit is not None:
            registry, snapshot = session_credit
            _set_session_credit_snapshot(
                registry,
                snapshot,
                "using_previous",
                f"Credit Notes 刷新失败，当前继续使用上一次成功数据。失败原因：{exc}",
            )
            return registry, snapshot
        raise

    manifest = _credit_manifest(registry)
    manifest_signature = _credit_manifest_signature(manifest)
    if not force:
        cached = _restore_credit_snapshot_cache(manifest_signature)
        if cached is not None:
            _set_session_credit_snapshot(cached[0], cached[1], "cached", "Credit Notes 已从本地缓存加载。")
            return cached

    valid_entries = sorted(registry.valid_entries(), key=lambda entry: entry.snapshot_date or pd.Timestamp.min, reverse=True)
    if not valid_entries:
        if session_credit is not None:
            registry, snapshot = session_credit
            _set_session_credit_snapshot(registry, snapshot, "using_previous", "Drive 中未找到有效 Credit Snapshot，当前继续使用上一次成功数据。")
            return registry, snapshot
        _set_session_credit_snapshot(registry, None, "not_loaded", "Drive 中未找到有效 Credit Snapshot。")
        return registry, None

    loaded_snapshots: list[CreditSnapshot] = []
    service = get_drive_service(config)
    try:
        with stage_timer("credit_snapshot_load") as done:
            for selected_entry in valid_entries:
                try:
                    _raise_if_stage_timeout(stage_start, "credit_load", CREDIT_STAGE_TIMEOUT_SECONDS)
                    snapshot_start = _timer()
                    logger.info("credit_download_started file_name=%s", selected_entry.file_name)
                    content = download_drive_file(service, str(selected_entry.file_id)).getvalue()
                    logger.info("credit_download_completed file_name=%s elapsed_seconds=%.3f bytes=%s", selected_entry.file_name, _elapsed_seconds(snapshot_start), len(content))
                    parse_start = _timer()
                    logger.info("credit_parse_started file_name=%s", selected_entry.file_name)
                    parsed_snapshot = _run_blocking_stage(
                        "credit_parse",
                        CREDIT_PARSE_TIMEOUT_SECONDS,
                        lambda content=content, entry=selected_entry: load_credit_snapshot_from_bytes(content, entry),
                    )
                    loaded_snapshots.append(parsed_snapshot)
                    logger.info("credit_parse_completed file_name=%s elapsed_seconds=%.3f rows=%s", selected_entry.file_name, _elapsed_seconds(parse_start), len(parsed_snapshot.data))
                    _raise_if_stage_timeout(stage_start, "credit_load", CREDIT_STAGE_TIMEOUT_SECONDS)
                except Exception as exc:
                    selected_entry.validation_status = "Invalid"
                    selected_entry.errors.append(f"Snapshot parse failed: {exc.__class__.__name__}")
                    selected_entry.participates_in_matching = False
                    logger.warning("Google Drive credit snapshot rejected file_name=%s reason=%s", selected_entry.file_name, exc.__class__.__name__)
            done(rows=sum(len(snapshot.data) for snapshot in loaded_snapshots), status="loaded" if loaded_snapshots else "failed")
    finally:
        close_google_service(service)

    snapshot = merge_credit_snapshots(loaded_snapshots)
    manifest = _credit_manifest(registry)
    manifest_signature = _credit_manifest_signature(manifest)
    if snapshot is None:
        if session_credit is not None:
            previous_registry, previous_snapshot = session_credit
            _set_session_credit_snapshot(previous_registry, previous_snapshot, "using_previous", "Drive 中未找到有效 Credit Snapshot，当前继续使用上一次成功数据。")
            _perf_log("load_drive_credit_snapshot", start, len(previous_snapshot.data), "drive-failed-previous")
            return previous_registry, previous_snapshot
        _set_session_credit_snapshot(registry, None, "failed", "Credit Snapshot 读取失败。")
        _perf_log("load_drive_credit_snapshot", start, 0, "drive-failed")
        return registry, None

    _write_credit_snapshot_cache(registry, snapshot, manifest, manifest_signature)
    _set_session_credit_snapshot(registry, snapshot, "loaded", "Credit Notes 已从 Google Drive 加载。")
    dates = pd.to_datetime(snapshot.data.get("Credit Date"), errors="coerce").dropna()
    date_range = "无" if dates.empty else f"{dates.min().date()} 至 {dates.max().date()}"
    logger.info(
        "credit_loaded_summary source_file=%s rows=%s credit_notes=%s date_range=%s",
        snapshot.file_name,
        len(snapshot.data),
        snapshot.quality.get("Credit Note Count", 0),
        date_range,
    )
    _perf_log("load_drive_credit_snapshot", start, len(snapshot.data), "drive")
    return registry, snapshot


def ensure_drive_data_loaded(force: bool = False) -> DriveLoadStatus:
    st = _get_streamlit()
    if not force:
        if st.session_state.get("drive_cache_restore_attempted"):
            status = st.session_state.get("drive_load_status")
            if isinstance(status, DriveLoadStatus):
                return status
        st.session_state["drive_cache_restore_attempted"] = True
        return restore_drive_data_from_cache()
    st.session_state["drive_auto_load_attempted"] = True
    return load_drive_business_files(force=force)


def clear_drive_state() -> None:
    st = _get_streamlit()
    for key in ["drive_auto_load_attempted", "drive_cache_restore_attempted", "drive_load_status"]:
        st.session_state.pop(key, None)
    try:
        st.cache_data.clear()
    except Exception:
        pass


def _snapshot_refresh_state() -> dict[str, Any]:
    st = _get_streamlit()
    keys = {
        key
        for key in st.session_state.keys()
        if key in SALES_REFRESH_CORE_KEYS or any(str(key).startswith(prefix) for prefix in REFRESH_STATE_PREFIXES)
    }
    return {key: st.session_state.get(key) for key in keys}


def _restore_refresh_state(snapshot: dict[str, Any]) -> None:
    st = _get_streamlit()
    managed_keys = {
        key
        for key in st.session_state.keys()
        if key in SALES_REFRESH_CORE_KEYS or any(str(key).startswith(prefix) for prefix in REFRESH_STATE_PREFIXES)
    }
    for key in managed_keys - set(snapshot):
        st.session_state.pop(key, None)
    for key, value in snapshot.items():
        st.session_state[key] = value


def _successful_item_status(status: DriveLoadItemStatus | None) -> bool:
    return isinstance(status, DriveLoadItemStatus) and status.status in SUCCESSFUL_REFRESH_STATUSES


def _successful_cost_refresh(snapshots: list[CostSnapshot]) -> bool:
    st = _get_streamlit()
    status = st.session_state.get("drive_cost_load_status")
    return _successful_item_status(status) and bool(snapshots)


def refresh_credit_notes_transaction() -> tuple[CreditSnapshot | None, str]:
    refresh_start = _timer()
    logger.info("credit_refresh_transaction_started")
    previous_state = _snapshot_refresh_state()
    try:
        credit_stage_start = _timer()
        registry, snapshot = load_drive_credit_snapshot(force=True)
        _raise_if_stage_timeout(credit_stage_start, "credit_load", CREDIT_STAGE_TIMEOUT_SECONDS)
        _raise_if_refresh_deadline_expired(refresh_start, "credit_snapshot_load")
    except DriveUserError as exc:
        _restore_refresh_state(previous_state)
        logger.warning(
            "credit_refresh_transaction_failed stage=credit_snapshot_load elapsed_seconds=%.3f error_type=%s",
            _elapsed_seconds(refresh_start),
            exc.__class__.__name__,
        )
        return None, f"Credit Notes 刷新失败，当前继续使用旧数据。失败原因：{exc}"
    if snapshot is None:
        _restore_refresh_state(previous_state)
        message = _get_streamlit().session_state.get("drive_credit_message") or "未找到有效 Credit Snapshot。"
        logger.warning(
            "credit_refresh_transaction_failed stage=credit_validation elapsed_seconds=%.3f error_type=CreditRefreshIncomplete",
            _elapsed_seconds(refresh_start),
        )
        return None, f"Credit Notes 刷新未完成，当前继续使用旧数据。{message}"
    logger.info(
        "credit_refresh_transaction_completed elapsed_seconds=%.3f rows=%s snapshot=%s",
        _elapsed_seconds(refresh_start),
        len(snapshot.data),
        snapshot.file_name,
    )
    return snapshot, f"Credit Notes 已刷新：{snapshot.file_name}"


def refresh_drive_data_transaction() -> tuple[DriveLoadStatus | None, str]:
    refresh_start = _timer()
    logger.info("refresh_transaction_started")
    previous_state = _snapshot_refresh_state()
    clear_drive_state()
    try:
        cost_stage_start = _timer()
        logger.info("cost_load_started")
        _registry, cost_snapshots = load_drive_cost_snapshots(force=True)
        _raise_if_stage_timeout(cost_stage_start, "cost_load", COST_STAGE_TIMEOUT_SECONDS)
        logger.info("cost_load_completed elapsed_seconds=%.3f rows=%s", _elapsed_seconds(refresh_start), len(cost_snapshots))
    except DriveUserError as exc:
        _restore_refresh_state(previous_state)
        logger.warning(
            "refresh_transaction_failed stage=cost_load elapsed_seconds=%.3f error_type=%s",
            _elapsed_seconds(refresh_start),
            exc.__class__.__name__,
        )
        return None, f"成本快照刷新失败，当前继续使用旧数据。失败原因：{exc}"

    try:
        _raise_if_refresh_deadline_expired(refresh_start, "cost_load")
    except DriveUserError as exc:
        _restore_refresh_state(previous_state)
        return None, f"Google Drive 数据加载超时，当前继续使用旧数据。失败原因：{exc}"
    if not _successful_cost_refresh(cost_snapshots):
        cost_message = str(_get_streamlit().session_state.get("drive_cost_message") or "未找到有效成本快照。")
        _restore_refresh_state(previous_state)
        logger.warning(
            "refresh_transaction_failed stage=cost_validation elapsed_seconds=%.3f error_type=CostRefreshIncomplete",
            _elapsed_seconds(refresh_start),
        )
        return None, f"成本快照刷新未完成，当前继续使用旧数据。{cost_message}"

    logger.info("drive_file_discovery_started")
    logger.info("sales_load_started")
    business_stage_start = _timer()
    refreshed = load_drive_business_files(force=True)
    _raise_if_stage_timeout(business_stage_start, "business_files_load", SALES_STAGE_TIMEOUT_SECONDS + TARGET_STAGE_TIMEOUT_SECONDS)
    logger.info("drive_file_discovery_completed elapsed_seconds=%.3f", _elapsed_seconds(refresh_start))
    clean = _get_streamlit().session_state.get("clean_data")
    logger.info(
        "sales_load_completed elapsed_seconds=%.3f rows=%s status=%s",
        _elapsed_seconds(refresh_start),
        len(clean) if clean is not None else None,
        refreshed.sales.status,
    )
    logger.info("targets_load_completed elapsed_seconds=%.3f status=%s", _elapsed_seconds(refresh_start), refreshed.targets.status)
    try:
        _raise_if_refresh_deadline_expired(refresh_start, "business_files_load")
    except DriveUserError as exc:
        _restore_refresh_state(previous_state)
        return refreshed, f"Google Drive 数据加载超时，当前继续使用旧数据。失败原因：{exc}"
    if not _successful_item_status(refreshed.sales):
        sales_message = refreshed.sales.message or "销售数据刷新失败。"
        _restore_refresh_state(previous_state)
        logger.warning(
            "refresh_transaction_failed stage=sales_load elapsed_seconds=%.3f error_type=SalesRefreshIncomplete",
            _elapsed_seconds(refresh_start),
        )
        return refreshed, f"销售数据刷新失败，当前继续使用旧数据。失败原因：{sales_message}"

    messages = [refreshed.sales.message]
    cost_status = _get_streamlit().session_state.get("drive_cost_load_status")
    if isinstance(cost_status, DriveLoadItemStatus) and cost_status.message:
        messages.append(cost_status.message)
    if refreshed.targets.message:
        messages.append(refreshed.targets.message)
    clean = _get_streamlit().session_state.get("clean_data")
    logger.info(
        "refresh_transaction_completed elapsed_seconds=%.3f sales_rows=%s completed_date_max=%s source_file=%s",
        _elapsed_seconds(refresh_start),
        len(clean) if clean is not None else None,
        _sales_max_date(clean) if clean is not None else "无",
        _get_streamlit().session_state.get("drive_sales_file_name") or _get_streamlit().session_state.get("source_file_name") or "无",
    )
    return refreshed, "；".join(message for message in messages if message)


def render_drive_data_load_prompt(
    title: str = "尚未加载业务数据",
    message: str | None = None,
) -> bool:
    """Render a main-content Drive load prompt when session/cache data is unavailable."""
    st = _get_streamlit()
    if st.session_state.get("clean_data") is not None:
        st.session_state.pop("drive_refresh_in_progress", None)
        st.session_state.pop("drive_refresh_started_at", None)
        return False

    started_at = st.session_state.get("drive_refresh_started_at")
    if st.session_state.get("drive_refresh_in_progress") and started_at:
        try:
            lock_age = time.time() - float(started_at)
        except (TypeError, ValueError):
            lock_age = REFRESH_STALE_LOCK_SECONDS + 1
        if lock_age > REFRESH_STALE_LOCK_SECONDS:
            logger.warning("drive_refresh_stale_lock_cleared elapsed_seconds=%.3f", lock_age)
            st.session_state["drive_refresh_in_progress"] = False
            st.session_state.pop("drive_refresh_started_at", None)
            st.session_state["drive_refresh_message"] = "上一次同步未完成，已恢复按钮，可重新尝试。"

    st.warning(f"🟡 {title}")
    st.markdown(
        message
        or """
        当前会话尚未恢复业务数据。Streamlit Cloud 休眠或容器重建后，本地缓存可能暂时不可用。

        请点击下方按钮加载最新 Google Drive 数据。首次加载可能需要约 1 分钟，完成后页面会自动刷新。
        """
    )
    st.caption("Load latest Google Drive data. Please do not click repeatedly while loading.")

    in_progress = bool(st.session_state.get("drive_refresh_in_progress"))
    if in_progress:
        st.info("🔄 正在加载 Google Drive 数据，请稍候。")

    clicked = st.button(
        "加载最新 Google Drive 数据",
        key="drive_main_load_button",
        use_container_width=True,
        disabled=in_progress,
    )
    if not clicked:
        if st.session_state.get("drive_refresh_message"):
            st.caption(st.session_state["drive_refresh_message"])
        return True

    st.session_state["drive_refresh_in_progress"] = True
    st.session_state["drive_refresh_started_at"] = time.time()
    st.session_state["drive_refresh_message"] = "正在连接 Google Drive..."
    try:
        with st.spinner("正在加载 Google Drive 数据，通常需要约 1 分钟。请保持页面打开，不要连续点击。"):
            _refreshed, refresh_message = refresh_drive_data_transaction()
        st.session_state["drive_refresh_message"] = refresh_message or "Google Drive 数据加载完成。"
        st.success(st.session_state["drive_refresh_message"])
        st.rerun()
    except Exception as exc:
        logger.exception("Main Drive data load prompt failed")
        st.session_state["drive_refresh_message"] = f"Google Drive 数据加载失败：{exc.__class__.__name__}"
        st.error("Google Drive 数据加载失败，请稍后重试。已有旧数据不会被清空。")
        st.caption(st.session_state["drive_refresh_message"])
    finally:
        st.session_state["drive_refresh_in_progress"] = False
        st.session_state.pop("drive_refresh_started_at", None)
    return True


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

            st.markdown("**成本快照**")
            st.caption(f"状态：{st.session_state.get('drive_cost_status') or '未加载'}")
            st.caption(f"快照文件数：{st.session_state.get('drive_cost_snapshot_count', '无')}")
            st.caption(f"Registry 行数：{st.session_state.get('drive_cost_registry_count', '无')}")
            st.caption(f"成本版本日期：{st.session_state.get('drive_cost_version_dates') or '无'}")
            st.caption(f"Dashboard 加载时间：{st.session_state.get('drive_cost_loaded_at') or '无'}")
            if st.session_state.get("drive_cost_message"):
                st.caption(f"说明：{st.session_state['drive_cost_message']}")

            st.markdown("**Credit Notes**")
            st.caption(f"状态：{st.session_state.get('drive_credit_status') or '未加载'}")
            st.caption(f"最新快照：{st.session_state.get('drive_credit_latest_snapshot') or '无'}")
            st.caption(f"当前文件名：{st.session_state.get('drive_credit_file_name') or '无'}")
            st.caption(f"Registry 行数：{st.session_state.get('drive_credit_registry_count', '无')}")
            st.caption(f"数据行数：{st.session_state.get('drive_credit_row_count', '无')}")
            st.caption(f"退款单数：{st.session_state.get('drive_credit_note_count', '无')}")
            st.caption(f"日期范围：{st.session_state.get('drive_credit_date_range') or '无'}")
            st.caption(f"Dashboard 加载时间：{st.session_state.get('drive_credit_loaded_at') or '无'}")
            if st.session_state.get("drive_credit_message"):
                st.caption(f"说明：{st.session_state['drive_credit_message']}")

        if can_use_data_sync:
            with st.expander("数据同步", expanded=False):
                if st.button("刷新 Google Drive 数据", use_container_width=True):
                    with st.spinner("正在重新加载 Google Drive 数据..."):
                        _refreshed, message = refresh_drive_data_transaction()
                    if message:
                        st.session_state["drive_refresh_message"] = message
                    st.rerun()
                if st.session_state.get("drive_refresh_message"):
                    st.caption(st.session_state["drive_refresh_message"])

                if st.button("Refresh Credit Notes", use_container_width=True):
                    with st.spinner("正在重新加载 Credit Notes..."):
                        _snapshot, message = refresh_credit_notes_transaction()
                    if message:
                        st.session_state["drive_credit_refresh_message"] = message
                    st.rerun()
                if st.session_state.get("drive_credit_refresh_message"):
                    st.caption(st.session_state["drive_credit_refresh_message"])

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
