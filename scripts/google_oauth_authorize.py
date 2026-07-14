from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
DEFAULT_REDIRECT_HOST = "localhost"
DEFAULT_REDIRECT_PORT = 8080


def _load_local_secrets() -> dict[str, str]:
    secrets_path = Path(".streamlit/secrets.toml")
    if not secrets_path.exists():
        return {}
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    with secrets_path.open("rb") as handle:
        data = tomllib.load(handle)
    section = data.get("google_oauth", {})
    if not isinstance(section, dict):
        return {}
    return {str(key): str(value) for key, value in section.items() if value is not None}


def _oauth_client_fields() -> tuple[str, str]:
    local_secrets = _load_local_secrets()
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID") or local_secrets.get("client_id")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or local_secrets.get("client_secret")
    missing = []
    if not client_id:
        missing.append("client_id")
    if not client_secret:
        missing.append("client_secret")
    if missing:
        raise RuntimeError(
            "缺少 Google OAuth 配置："
            + ", ".join(missing)
            + "。请在本地 .streamlit/secrets.toml 的 [google_oauth] 中填写，"
            + "或设置 GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET 环境变量。"
        )
    return client_id, client_secret


def authorize(port: int = DEFAULT_REDIRECT_PORT) -> str:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError("缺少 google-auth-oauthlib，请先安装 requirements.txt 中的依赖。") from exc

    client_id, client_secret = _oauth_client_fields()
    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": DEFAULT_TOKEN_URI,
            "redirect_uris": [f"http://{DEFAULT_REDIRECT_HOST}:{port}/"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, scopes=[DRIVE_READONLY_SCOPE])
    credentials = flow.run_local_server(
        host=DEFAULT_REDIRECT_HOST,
        port=port,
        authorization_prompt_message=(
            "请在浏览器中使用管理员 Google 账号完成授权：{url}\n"
            "Google Cloud OAuth Client 的 Authorized redirect URI 需要包含："
            f"http://{DEFAULT_REDIRECT_HOST}:{port}/"
        ),
        success_message="授权完成，可以关闭此浏览器窗口，回到终端复制 refresh token。",
        open_browser=True,
        access_type="offline",
        prompt="consent",
    )
    if not credentials.refresh_token:
        raise RuntimeError("本次授权没有返回 refresh token。请确认使用 prompt=consent，并重新运行脚本。")
    return credentials.refresh_token


def main() -> int:
    parser = argparse.ArgumentParser(description="一次性获取 Google Drive readonly OAuth refresh token。")
    parser.add_argument("--port", type=int, default=DEFAULT_REDIRECT_PORT, help="本地 OAuth 回调端口，默认 8080。")
    args = parser.parse_args()

    try:
        refresh_token = authorize(port=args.port)
    except Exception as exc:
        print(f"授权失败：{exc}", file=sys.stderr)
        return 1

    print("\n授权成功。请把下面的 refresh token 复制到 Streamlit Secrets 的 [google_oauth] 中：\n")
    print(refresh_token)
    print(
        "\nSecrets 示例：\n"
        "[google_oauth]\n"
        'client_id = \"...\"\n'
        'client_secret = \"...\"\n'
        f'refresh_token = \"{refresh_token}\"\n'
        f'token_uri = \"{DEFAULT_TOKEN_URI}\"\n'
        "\n[google_drive]\n"
        'folder_id = \"...\"\n'
        'sales_file_name = \"XF_Sales_Latest.xlsx\"\n'
        'target_file_name = \"XF_Targets_Latest.xlsx\"\n'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
