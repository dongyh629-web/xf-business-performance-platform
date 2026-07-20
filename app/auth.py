from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
from pathlib import Path
import secrets
import time
from urllib.parse import urlencode, urlsplit, urlunsplit

import pandas as pd
import streamlit as st

from app.google_drive import (
    DriveUserError,
    download_drive_file,
    find_drive_file_in_folder_path,
    get_drive_config,
    get_drive_service,
)


LOGIN_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]
OAUTH_CONTEXT_KEY = "auth_oauth_context"
OAUTH_STATE_CACHE_PATH = Path(".cache") / "oauth_state.json"
OAUTH_CONTEXT_TTL_SECONDS = 600
USER_SESSION_KEYS = ["auth_email", "auth_name", "auth_role", "auth_login_status"]
ROLE_PERMISSIONS = {
    "Admin": {
        "overview",
        "sales",
        "customers",
        "products",
        "business_tracking",
        "product_group_tracking",
        "customer_analysis",
        "customer_health",
        "product_analysis",
        "data_quality",
        "data_sync",
        "system",
        "margin",
        "target",
        "finance",
    },
    "Executive": {
        "overview",
        "sales",
        "customers",
        "products",
        "business_tracking",
        "product_group_tracking",
        "customer_analysis",
        "customer_health",
        "product_analysis",
        "margin",
        "target",
        "finance",
    },
    "Sales": {
        "overview",
        "sales",
        "customers",
        "products",
        "business_tracking",
        "product_group_tracking",
        "customer_analysis",
        "customer_health",
        "product_analysis",
    },
    "Finance": {
        "overview",
        "margin",
        "target",
        "finance",
    },
}
ROLE_LOOKUP = {role.casefold(): role for role in ROLE_PERMISSIONS}


@dataclass(frozen=True)
class AuthUser:
    email: str
    name: str
    role: str


def _plain_secret_section(name: str) -> dict[str, object]:
    try:
        return dict(st.secrets.get(name, {}))
    except Exception:
        return {}


def _current_url_without_auth_params() -> str:
    configured = str(_plain_secret_section("google_oauth").get("redirect_uri", "")).strip()
    if configured:
        return configured
    current_url = st.context.url
    if not current_url:
        return ""
    parsed = urlsplit(current_url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _oauth_client_config() -> dict[str, object]:
    oauth = _plain_secret_section("google_oauth")
    client_id = str(oauth.get("client_id", "")).strip()
    client_secret = str(oauth.get("client_secret", "")).strip()
    token_uri = str(oauth.get("token_uri", "https://oauth2.googleapis.com/token")).strip()
    if not client_id or not client_secret:
        raise DriveUserError("Google OAuth Secrets 缺少 client_id 或 client_secret。")
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": token_uri or "https://oauth2.googleapis.com/token",
        }
    }


def _new_code_verifier() -> str:
    return secrets.token_urlsafe(64)


def _auth_flow(*, redirect_uri: str | None = None, state: str | None = None, code_verifier: str | None = None):
    from google_auth_oauthlib.flow import Flow

    selected_redirect_uri = redirect_uri or _current_url_without_auth_params()
    if not selected_redirect_uri:
        raise DriveUserError("无法识别当前登录回调地址。")
    kwargs = {
        "scopes": LOGIN_SCOPES,
        "redirect_uri": selected_redirect_uri,
        "autogenerate_code_verifier": code_verifier is None,
    }
    if state:
        kwargs["state"] = state
    if code_verifier:
        kwargs["code_verifier"] = code_verifier
    flow = Flow.from_client_config(_oauth_client_config(), **kwargs)
    return flow


def _read_oauth_state_cache() -> dict[str, dict[str, str]]:
    try:
        raw = json.loads(OAUTH_STATE_CACHE_PATH.read_text())
    except FileNotFoundError:
        return {}
    try:
        return {str(key): value for key, value in raw.items() if isinstance(value, dict)}
    except Exception:
        return {}


def _write_oauth_state_cache(cache: dict[str, dict[str, str]]) -> None:
    OAUTH_STATE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = OAUTH_STATE_CACHE_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(cache, separators=(",", ":"), sort_keys=True))
    temp_path.replace(OAUTH_STATE_CACHE_PATH)


def _valid_oauth_context(context: dict[str, str]) -> dict[str, str] | None:
    try:
        created_at = int(context.get("created_at", "0"))
    except (TypeError, ValueError):
        return None
    if int(time.time()) - created_at > OAUTH_CONTEXT_TTL_SECONDS:
        return None
    normalized = {
        "state": str(context.get("state", "")).strip(),
        "code_verifier": str(context.get("code_verifier", "")).strip(),
        "redirect_uri": str(context.get("redirect_uri", "")).strip(),
    }
    if not all(normalized.values()):
        return None
    return normalized


def _store_oauth_context(context: dict[str, str]) -> None:
    cache = _read_oauth_state_cache()
    now = int(time.time())
    cache = {
        state: stored
        for state, stored in cache.items()
        if _valid_oauth_context(stored) is not None
    }
    cache[context["state"]] = {**context, "created_at": str(now)}
    _write_oauth_state_cache(cache)


def _consume_oauth_context(state: str) -> dict[str, str] | None:
    cache = _read_oauth_state_cache()
    stored = cache.pop(state, None)
    cleaned_cache = {
        stored_state: stored_context
        for stored_state, stored_context in cache.items()
        if _valid_oauth_context(stored_context) is not None
    }
    _write_oauth_state_cache(cleaned_cache)
    if stored is None:
        return None
    return _valid_oauth_context(stored)


def _clear_oauth_context() -> None:
    for key in [OAUTH_CONTEXT_KEY, "auth_oauth_state"]:
        st.session_state.pop(key, None)


def _authorize_url() -> str:
    redirect_uri = _current_url_without_auth_params()
    code_verifier = _new_code_verifier()
    flow = _auth_flow(redirect_uri=redirect_uri, code_verifier=code_verifier)
    auth_url, state = flow.authorization_url(
        access_type="online",
        include_granted_scopes="true",
        prompt="select_account",
    )
    oauth_context = {
        "state": state,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
    }
    _store_oauth_context(oauth_context)
    st.session_state[OAUTH_CONTEXT_KEY] = oauth_context
    st.session_state["auth_oauth_state"] = state
    return auth_url


def _oauth_context(returned_state: str) -> dict[str, str]:
    context = _consume_oauth_context(returned_state)
    if context is None:
        raise PermissionError("登录会话已过期，请重新点击 Continue with Google。")
    state = str(context.get("state", "")).strip()
    code_verifier = str(context.get("code_verifier", "")).strip()
    redirect_uri = str(context.get("redirect_uri", "")).strip()
    if not state or not code_verifier or not redirect_uri:
        raise PermissionError("登录会话已过期，请重新点击 Continue with Google。")
    return {"state": state, "code_verifier": code_verifier, "redirect_uri": redirect_uri}


def _verify_callback_code(code: str, returned_state: str) -> dict[str, object]:
    from google.auth.transport.requests import Request
    from google.oauth2 import id_token

    context = _oauth_context(returned_state)
    if returned_state != context["state"]:
        raise PermissionError("登录状态校验失败，请重新点击 Continue with Google。")
    flow = _auth_flow(
        redirect_uri=context["redirect_uri"],
        state=context["state"],
        code_verifier=context["code_verifier"],
    )
    try:
        flow.fetch_token(code=code)
    except Exception as exc:
        message = str(exc)
        if "Missing code verifier" in message or "invalid_grant" in message:
            raise PermissionError("登录会话已过期，请重新点击 Continue with Google。") from exc
        raise
    credentials = flow.credentials
    token = credentials.id_token
    if not token:
        raise PermissionError("Google login did not return an identity token.")
    audience = _oauth_client_config()["web"]["client_id"]
    return id_token.verify_oauth2_token(token, Request(), audience)


def _normalize_email(value: object) -> str:
    return str(value or "").strip().lower()


def _is_active(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not pd.isna(value):
        return bool(int(value)) if float(value).is_integer() else False
    text = str(value).strip().lower()
    return text in {"true", "1", "1.0", "yes", "y", "active", "启用", "是"}


def normalize_role(value: object) -> str:
    role = str(value or "").strip()
    normalized = ROLE_LOOKUP.get(role.casefold())
    if not normalized:
        raise PermissionError("Your account role is not configured. Please contact the administrator.")
    return normalized


def find_user_record(users: pd.DataFrame, email: str) -> AuthUser:
    required = {"Email", "Name", "Role", "Active"}
    missing = required - set(users.columns)
    if missing:
        raise PermissionError(f"Users.xlsx 缺少字段：{', '.join(sorted(missing))}。")
    normalized_email = _normalize_email(email)
    rows = users[users["Email"].map(_normalize_email).eq(normalized_email)].copy()
    if rows.empty:
        raise PermissionError("Access Denied\n\nYou are not authorized to access this dashboard.")
    row = rows.iloc[0]
    if not _is_active(row.get("Active")):
        raise PermissionError("Access Denied\n\nYou are not authorized to access this dashboard.")
    role = normalize_role(row.get("Role"))
    name = str(row.get("Name", "")).strip()
    return AuthUser(email=normalized_email, name=name, role=role)


def has_permission(permission_key: str, role: str | None = None) -> bool:
    selected_role = role or st.session_state.get("auth_role")
    if not selected_role:
        return False
    try:
        normalized_role = normalize_role(selected_role)
    except PermissionError:
        return False
    return permission_key in ROLE_PERMISSIONS.get(normalized_role, set())


def role_allows(role: str | None, area: str) -> bool:
    return has_permission(area, role)


def require_permission(permission_key: str) -> None:
    user = current_user()
    if user is None or not has_permission(permission_key, user.role):
        st.error("Access Denied")
        st.stop()


def get_allowed_navigation(role: str) -> set[str]:
    try:
        normalized_role = normalize_role(role)
    except PermissionError:
        return set()
    return set(ROLE_PERMISSIONS.get(normalized_role, set()))


def current_user() -> AuthUser | None:
    if st.session_state.get("auth_login_status") != "logged_in":
        return None
    email = st.session_state.get("auth_email")
    name = st.session_state.get("auth_name")
    role = st.session_state.get("auth_role")
    if not email or not role:
        return None
    return AuthUser(email=str(email), name=str(name or email), role=str(role))


def is_logged_in() -> bool:
    return current_user() is not None


def set_login_session(user: AuthUser) -> None:
    st.session_state["auth_email"] = user.email
    st.session_state["auth_name"] = user.name
    st.session_state["auth_role"] = normalize_role(user.role)
    st.session_state["auth_login_status"] = "logged_in"


def logout() -> None:
    for key in USER_SESSION_KEYS + ["auth_error", "auth_oauth_state", OAUTH_CONTEXT_KEY]:
        st.session_state.pop(key, None)


@st.cache_data(show_spinner=False, ttl=300)
def load_users_table() -> pd.DataFrame:
    config = get_drive_config()
    service = get_drive_service(config)
    metadata = find_drive_file_in_folder_path(service, config.folder_id, "Master Data", ["Users.xlsx"])
    content = download_drive_file(service, metadata.file_id).getvalue()
    return pd.read_excel(BytesIO(content))


def authenticate_google_callback() -> None:
    code = st.query_params.get("code")
    if not code or is_logged_in():
        return
    state = str(st.query_params.get("state", "")).strip()
    try:
        info = _verify_callback_code(str(code), state)
        email = _normalize_email(info.get("email"))
        display_name = str(info.get("name") or email).strip()
        user = find_user_record(load_users_table(), email)
        user = AuthUser(email=user.email, name=user.name or display_name or user.email, role=user.role)
        set_login_session(user)
        _clear_oauth_context()
        st.query_params.clear()
        st.rerun()
    except Exception as exc:
        st.session_state["auth_error"] = str(exc)
        _clear_oauth_context()
        st.query_params.clear()
        st.rerun()


def _render_login_page() -> None:
    st.markdown(
        """
        <style>
        section[data-testid="stSidebar"] {display: none;}
        div[data-testid="stSidebarCollapsedControl"] {display: none;}
        .block-container {max-width: 560px; padding-top: 18vh;}
        .xf-login-title {color:#111827; font-size: 2rem; font-weight: 700; margin-bottom: 0.25rem;}
        .xf-login-subtitle {color:#2563eb; font-size: 1.1rem; margin-bottom: 1.5rem;}
        .xf-login-note {color:#6b7280; font-size: 0.9rem; margin-top: 1rem;}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown('<div class="xf-login-title">XF Business Dashboard</div>', unsafe_allow_html=True)
    st.markdown('<div class="xf-login-subtitle">鲜锋经营驾驶舱</div>', unsafe_allow_html=True)
    error = st.session_state.get("auth_error")
    if error:
        st.error(error)
    try:
        auth_url = _authorize_url()
        st.link_button("Continue with Google", auth_url, use_container_width=True)
    except Exception as exc:
        st.error(f"Google 登录暂不可用：{exc}")
    st.markdown('<div class="xf-login-note">Please sign in with your authorized Google account.</div>', unsafe_allow_html=True)


def require_login(permission_key: str | None = None) -> AuthUser:
    authenticate_google_callback()
    user = current_user()
    if user is None:
        _render_login_page()
        st.stop()
    if permission_key:
        require_permission(permission_key)
    return user


def render_user_sidebar() -> None:
    user = current_user()
    if not user:
        return
    st.markdown(f"**👤 {user.name}**")
    st.caption(user.role)


def render_logout_button() -> None:
    if not is_logged_in():
        return
    if st.button("Logout", use_container_width=True):
        logout()
        st.rerun()


def local_preview_login(role: str) -> None:
    host = urlsplit(st.context.url or "").hostname
    if host not in {"localhost", "127.0.0.1"}:
        return
    if role in ROLE_PERMISSIONS:
        set_login_session(AuthUser(email=f"{role.lower()}@preview.local", name=f"{role} Preview", role=role))
    elif role == "Denied":
        logout()
        st.session_state["auth_error"] = "Access Denied\n\nYou are not authorized to access this dashboard."
    if role in {*ROLE_PERMISSIONS, "Denied"}:
        st.query_params.clear()
        st.rerun()
