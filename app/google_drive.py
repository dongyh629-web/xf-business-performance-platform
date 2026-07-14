from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import logging
from typing import Any

import pandas as pd

from app.data import ImportResult, import_excel
from app.target_metrics import XFTargetWorkbook, parse_xf_target_workbook


logger = logging.getLogger(__name__)

DRIVE_SOURCE_LABEL = "Google Drive"
MANUAL_SOURCE_LABEL = "本次会话手动上传"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"


class DriveUserError(RuntimeError):
    """A user-safe Google Drive loading error."""


@dataclass(frozen=True)
class DriveConfig:
    service_account_info: dict[str, Any]
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


class _NamedBytesIO(BytesIO):
    def __init__(self, data: bytes, name: str):
        super().__init__(data)
        self.name = name


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
        gcp_section = _to_plain_dict(secrets["gcp_service_account"])
        drive_section = _to_plain_dict(secrets["google_drive"])
    except Exception as exc:
        raise DriveUserError("尚未配置 Google Drive Secrets。") from exc

    required_account_fields = ["type", "project_id", "private_key", "client_email", "token_uri"]
    missing_account = [field for field in required_account_fields if not gcp_section.get(field)]
    if missing_account:
        raise DriveUserError(f"Google Service Account Secrets 缺少字段：{', '.join(missing_account)}。")

    folder_id = str(drive_section.get("folder_id", "")).strip()
    if not folder_id:
        raise DriveUserError("Google Drive Secrets 缺少 folder_id。")

    account_info = dict(gcp_section)
    private_key = str(account_info.get("private_key", ""))
    account_info["private_key"] = private_key.replace("\\n", "\n")

    return DriveConfig(
        service_account_info=account_info,
        folder_id=folder_id,
        sales_file_name=str(drive_section.get("sales_file_name", "XF_Sales_Latest.xlsx")).strip() or "XF_Sales_Latest.xlsx",
        target_file_name=str(drive_section.get("target_file_name", "XF_Targets_Latest.xlsx")).strip() or "XF_Targets_Latest.xlsx",
    )


def get_drive_service(config: DriveConfig):
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise DriveUserError("缺少 Google Drive 读取依赖，请确认 requirements.txt 已安装 google-api-python-client 和 google-auth。") from exc

    try:
        credentials = service_account.Credentials.from_service_account_info(
            config.service_account_info,
            scopes=[DRIVE_SCOPE],
        )
        return build("drive", "v3", credentials=credentials, cache_discovery=False)
    except Exception as exc:
        logger.exception("Google Drive service initialization failed")
        raise DriveUserError("Google Drive 连接失败，请检查 Service Account Secrets 是否完整有效。") from exc


def _escape_drive_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


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
        raise DriveUserError(f"Google Drive 文件夹中未找到文件：{file_name}。")
    files = sorted(files, key=lambda item: item.get("modifiedTime", ""), reverse=True)
    item = files[0]
    return DriveFileMetadata(
        file_id=str(item.get("id")),
        name=str(item.get("name", file_name)),
        modified_time=item.get("modifiedTime"),
        mime_type=item.get("mimeType"),
        size=item.get("size"),
        web_view_link=item.get("webViewLink"),
    )


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
    metadata = find_drive_file(service, config.folder_id, config.sales_file_name)
    content = download_drive_file(service, metadata.file_id).getvalue()
    try:
        result = import_excel(_NamedBytesIO(content, metadata.name))
    except ValueError as exc:
        raise DriveUserError(f"Google Drive 销售文件读取失败：{exc}") from exc
    except Exception as exc:
        logger.exception("Google Drive sales parse failed file_name=%s", metadata.name)
        raise DriveUserError("Google Drive 销售文件解析失败，请确认文件是 Unleashed 销售明细。") from exc
    store_sales_import_in_session(result, metadata.name, DRIVE_SOURCE_LABEL, "drive", metadata.modified_time)
    st.session_state["sales_drive_file_id"] = metadata.file_id
    st.session_state["sales_drive_modified_time"] = metadata.modified_time
    return DriveLoadItemStatus("loaded", "销售数据已从 Google Drive 加载。", metadata.name, metadata.modified_time, metadata.file_id)


def _load_target_file(service, config: DriveConfig, force: bool) -> DriveLoadItemStatus:
    st = _get_streamlit()
    if (
        not force
        and st.session_state.get("target_source_type") == "manual"
        and st.session_state.get("target_data") is not None
    ):
        return DriveLoadItemStatus("skipped", "当前会话已手动上传目标数据，优先使用手动上传。")
    metadata = find_drive_file(service, config.folder_id, config.target_file_name)
    content = download_drive_file(service, metadata.file_id).getvalue()
    try:
        parsed = parse_xf_target_workbook(_NamedBytesIO(content, metadata.name))
    except ValueError as exc:
        raise DriveUserError(f"Google Drive 目标文件读取失败：{exc}") from exc
    except Exception as exc:
        logger.exception("Google Drive target parse failed file_name=%s", metadata.name)
        raise DriveUserError("Google Drive 目标文件解析失败，请确认文件是 XF 目标 Excel。") from exc
    store_target_workbook_in_session(parsed, metadata.name, DRIVE_SOURCE_LABEL, "drive", metadata.modified_time)
    st.session_state["target_drive_file_id"] = metadata.file_id
    st.session_state["target_drive_modified_time"] = metadata.modified_time
    return DriveLoadItemStatus("loaded", "目标数据已从 Google Drive 加载。", metadata.name, metadata.modified_time, metadata.file_id)


def _status_from_error(message: str) -> DriveLoadItemStatus:
    return DriveLoadItemStatus("failed", message)


def load_drive_business_files(force: bool = False) -> DriveLoadStatus:
    st = _get_streamlit()
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

        if st.button("刷新 Google Drive 数据", use_container_width=True):
            clear_drive_state()
            with st.spinner("正在重新加载 Google Drive 数据..."):
                load_drive_business_files(force=True)
            st.rerun()

        st.markdown("**销售数据**")
        sales_loaded = st.session_state.get("clean_data") is not None
        st.caption(f"状态：{'已加载' if sales_loaded else '未加载'}")
        st.caption(f"来源：{_source_text(st.session_state.get('sales_source_type'), st.session_state.get('data_source'))}")
        st.caption(f"文件名：{st.session_state.get('source_file_name') or st.session_state.get('current_file_name') or '无'}")
        st.caption(f"Drive 最后修改时间：{st.session_state.get('sales_drive_modified_time') or '无'}")
        st.caption(f"数据截止日期：{_sales_cutoff_text()}")

        st.markdown("**目标数据**")
        target_loaded = st.session_state.get("target_data") is not None
        st.caption(f"状态：{'已加载' if target_loaded else '未加载'}")
        st.caption(f"来源：{_source_text(st.session_state.get('target_source_type'), st.session_state.get('target_source'))}")
        st.caption(f"文件名：{st.session_state.get('target_excel_name') or '无'}")
        st.caption(f"Drive 最后修改时间：{st.session_state.get('target_drive_modified_time') or '无'}")
        st.caption(f"识别年度：{_target_years_text()}")

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
