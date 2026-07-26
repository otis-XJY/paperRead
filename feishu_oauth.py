"""Small official-SDK based Feishu user OAuth helper.

The refresh token is the only long-lived credential written locally.  The
file contains no document content and is replaced atomically after refresh.
It is intentionally kept outside the repository by default.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

import lark_oapi as lark
from lark_oapi.api.authen.v1 import model as auth_model

from feishu_sdk import configured_feishu_credentials

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:
    pass


DEFAULT_OAUTH_SCOPE = (
    "drive:drive "
    "docx:document:readonly "
    "wiki:wiki:readonly "
    "sheets:spreadsheet:readonly "
    "bitable:app:readonly"
)


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def oauth_store_path() -> Path:
    return Path(
        os.getenv(
            "DAILY_REPORT_FEISHU_OAUTH_STORE",
            "D:/paperRead-runtime/feishu_oauth_tokens.json",
        )
    )


class FeishuOAuthStore:
    """Atomic JSON storage for one user's Feishu OAuth token pair."""

    def __init__(self, path: Optional[str] = None):
        self.path = Path(path) if path else oauth_store_path()

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"无法读取飞书 OAuth 文件: {self.path}") from exc
        return value if isinstance(value, dict) else {}

    def save(self, value: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)


class FeishuOAuthClient:
    """Exchange and refresh user tokens through lark-oapi authen APIs."""

    def __init__(self, app_id: Optional[str] = None, app_secret: Optional[str] = None):
        self.app_id, self.app_secret = configured_feishu_credentials("DAILY_REPORT_")
        self.app_id = (app_id or self.app_id).strip()
        self.app_secret = (app_secret or self.app_secret).strip()
        if not self.app_id or not self.app_secret:
            raise RuntimeError(
                "未读取到日报应用凭证，请确认当前目录存在 .env，且配置 "
                "DAILY_REPORT_FEISHU_APP_ID/DAILY_REPORT_FEISHU_APP_SECRET"
            )
        self.client = (
            lark.Client.builder()
            .app_id(self.app_id)
            .app_secret(self.app_secret)
            .domain(os.getenv("FEISHU_API_DOMAIN", "https://open.feishu.cn"))
            .timeout(20)
            .enable_set_token(True)
            .build()
        )

    def exchange_code(self, code: str) -> Dict[str, Any]:
        body = (
            auth_model.CreateAccessTokenRequestBody.builder()
            .grant_type("authorization_code")
            .code(code)
            .build()
        )
        request = auth_model.CreateAccessTokenRequest.builder().request_body(body).build()
        response = self.client.authen.v1.access_token.create(request)
        if getattr(response, "code", 0) not in (0, None):
            raise RuntimeError(f"飞书 OAuth 授权码交换失败: code={response.code}, msg={response.msg}")
        return self._body_to_dict(response.data)

    def refresh(self, refresh_token: str) -> Dict[str, Any]:
        body = (
            auth_model.CreateRefreshAccessTokenRequestBody.builder()
            .grant_type("refresh_token")
            .refresh_token(refresh_token)
            .build()
        )
        request = auth_model.CreateRefreshAccessTokenRequest.builder().request_body(body).build()
        response = self.client.authen.v1.refresh_access_token.create(request)
        if getattr(response, "code", 0) not in (0, None):
            raise RuntimeError(f"飞书 OAuth 刷新失败: code={response.code}, msg={response.msg}")
        return self._body_to_dict(response.data)

    @staticmethod
    def _body_to_dict(body: Any) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for name in (
            "access_token", "refresh_token", "expires_in", "refresh_expires_in",
            "open_id", "union_id", "user_id", "tenant_key",
        ):
            value = getattr(body, name, None)
            if value not in (None, ""):
                result[name] = value
        result["saved_at"] = _now()
        return result


def build_authorize_url(redirect_uri: str, state: str, scope: str = "") -> str:
    app_id, _ = configured_feishu_credentials("DAILY_REPORT_")
    if not app_id:
        raise RuntimeError(
            "未读取到日报应用 App ID，请确认当前目录存在 .env，且配置 "
            "DAILY_REPORT_FEISHU_APP_ID（或 FEISHU_APP_ID）"
        )
    params = {"app_id": app_id, "redirect_uri": redirect_uri, "state": state}
    if scope.strip():
        params["scope"] = scope.strip()
    return "https://open.feishu.cn/open-apis/authen/v1/authorize?" + urlencode(params)


def configured_oauth_scope() -> str:
    """Return valid read/discovery scopes, unless explicitly overridden."""
    scope = os.getenv("FEISHU_OAUTH_SCOPE", "").strip()
    legacy = "drive:drive drive:file wiki:wiki docx:document sheet:spreadsheet bitable:app"
    if not scope or scope == legacy or "sheet:spreadsheet" in scope:
        return DEFAULT_OAUTH_SCOPE
    return scope


def obtain_user_token(
    store: Optional[FeishuOAuthStore] = None,
    redirect_uri: Optional[str] = None,
    open_browser: bool = True,
) -> Dict[str, Any]:
    """Run a one-time localhost callback and persist the resulting token."""
    store = store or FeishuOAuthStore()
    redirect_uri = redirect_uri or os.getenv(
        "FEISHU_OAUTH_REDIRECT_URI", "http://127.0.0.1:8765/feishu/oauth/callback"
    )
    parsed = urlparse(redirect_uri)
    if parsed.hostname not in ("127.0.0.1", "localhost"):
        raise RuntimeError("为避免暴露授权码，首次授权的 redirect_uri 必须是本机地址")
    state = secrets.token_urlsafe(24)
    result: Dict[str, str] = {}
    ready = threading.Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib callback method name
            query = parse_qs(urlparse(self.path).query)
            if query.get("state", [""])[0] != state:
                self.send_error(400, "invalid state")
                return
            result["code"] = query.get("code", [""])[0]
            result["error"] = query.get("error", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("飞书授权已收到，可以关闭此窗口。".encode("utf-8"))
            ready.set()

        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            return

    server = HTTPServer((parsed.hostname, parsed.port or 80), CallbackHandler)
    url = build_authorize_url(
        redirect_uri,
        state,
        configured_oauth_scope(),
    )
    print("请在浏览器完成一次飞书授权：")
    print(url)
    if open_browser:
        webbrowser.open(url)
    server.timeout = 300
    while not ready.is_set():
        server.handle_request()
    server.server_close()
    if result.get("error") or not result.get("code"):
        raise RuntimeError(f"飞书授权失败: {result.get('error') or '未收到 code'}")
    data = FeishuOAuthClient().exchange_code(result["code"])
    store.save(data)
    print(f"OAuth 已保存到 {store.path}；open_id={data.get('open_id', '')}")
    return data


def load_valid_user_token(store: Optional[FeishuOAuthStore] = None) -> Tuple[str, Dict[str, Any]]:
    store = store or FeishuOAuthStore()
    data = store.load()
    token = str(data.get("access_token") or "")
    saved_at = int(data.get("saved_at") or 0)
    expires_in = int(data.get("expires_in") or 0)
    if token and saved_at + max(60, expires_in - 120) > _now():
        return token, data
    refresh_token = str(data.get("refresh_token") or "")
    if not refresh_token:
        raise RuntimeError(f"没有可用的飞书用户 OAuth，请先运行: python feishu_oauth.py --authorize")
    refreshed = FeishuOAuthClient().refresh(refresh_token)
    # Feishu may rotate refresh tokens. Keep old values only when the response
    # does not include a replacement.
    if not refreshed.get("refresh_token"):
        refreshed["refresh_token"] = refresh_token
    if not refreshed.get("open_id"):
        refreshed["open_id"] = data.get("open_id", "")
    store.save(refreshed)
    return str(refreshed.get("access_token") or ""), refreshed


def main() -> None:
    parser = argparse.ArgumentParser(description="完成一次飞书用户 OAuth 授权")
    parser.add_argument("--authorize", action="store_true", help="打开浏览器完成授权并保存令牌")
    parser.add_argument("--show", action="store_true", help="显示当前令牌状态（不显示令牌内容）")
    args = parser.parse_args()
    store = FeishuOAuthStore()
    if args.authorize:
        obtain_user_token(store)
        return
    if args.show:
        data = store.load()
        print({"path": str(store.path), "open_id": data.get("open_id", ""), "has_access_token": bool(data.get("access_token")), "has_refresh_token": bool(data.get("refresh_token"))})
        return
    parser.print_help()


if __name__ == "__main__":
    main()
