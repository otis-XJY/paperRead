"""Shared Feishu official-SDK transport for tenant and user calls.

Endpoint-specific payloads remain close to the existing code while
authentication and HTTP transport are delegated to ``lark-oapi``.  User-token
requests are used only by the local discovery/subscription process; the
long-lived event listener continues to use application identity.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional, Tuple

import lark_oapi as lark
import requests
from lark_oapi.core import AccessTokenType, BaseRequest, HttpMethod
from lark_oapi.core.model import RequestOption


class FeishuSDKError(RuntimeError):
    """Raised when an official Feishu SDK request fails."""


class FeishuOpenAPIClient:
    """Small JSON-oriented adapter around the official Feishu Python SDK."""

    # The SDK obtains the tenant token lazily.  A short network interruption
    # during that request used to terminate the whole daily-report workflow.
    # Retry transport failures here so both token acquisition and API calls
    # receive the same protection.
    _MAX_TRANSPORT_RETRIES = 3
    _RETRY_BACKOFF_SECONDS = 1.0

    def __init__(
        self,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        domain: Optional[str] = None,
        timeout: float = 20,
        access_token_type: AccessTokenType = AccessTokenType.TENANT,
        user_access_token: Optional[str] = None,
    ) -> None:
        self.app_id = (app_id or os.getenv("FEISHU_APP_ID") or "").strip()
        self.app_secret = (app_secret or os.getenv("FEISHU_APP_SECRET") or "").strip()
        if not self.app_id or not self.app_secret:
            raise ValueError("FEISHU_APP_ID 和 FEISHU_APP_SECRET 必须同时配置")
        self.domain = (domain or os.getenv("FEISHU_API_DOMAIN") or "https://open.feishu.cn").rstrip("/")
        self.access_token_type = access_token_type
        self.user_access_token = (user_access_token or "").strip()
        if self.access_token_type == AccessTokenType.USER and not self.user_access_token:
            raise ValueError("使用用户身份调用飞书 API 时必须提供 user_access_token")
        self.client = (
            lark.Client.builder()
            .app_id(self.app_id)
            .app_secret(self.app_secret)
            .domain(self.domain)
            .timeout(timeout)
            .enable_set_token(True)
            .build()
        )

    @staticmethod
    def _method(value: str) -> HttpMethod:
        try:
            return HttpMethod[str(value).upper()]
        except KeyError as exc:
            raise ValueError(f"不支持的 Feishu HTTP 方法: {value}") from exc

    @staticmethod
    def _payload(response: Any) -> Dict[str, Any]:
        raw = getattr(response, "raw", None)
        content = getattr(raw, "content", b"") if raw is not None else b""
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        if not content:
            return {"code": getattr(response, "code", None), "msg": getattr(response, "msg", "")}
        try:
            value = json.loads(content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise FeishuSDKError(f"飞书 SDK 返回了不可解析的响应: {content[:500]}") from exc
        return value if isinstance(value, dict) else {"data": value}

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Call a Feishu OpenAPI endpoint through ``lark-oapi``."""
        normalized_path = path if path.startswith("/") else f"/{path}"
        if not normalized_path.startswith("/open-apis/"):
            normalized_path = f"/open-apis{normalized_path}"

        def build_request() -> BaseRequest:
            request = BaseRequest()
            request.http_method = self._method(method)
            request.uri = normalized_path
            token_type = getattr(self, "access_token_type", AccessTokenType.TENANT)
            request.token_types = {token_type}
            request.body = json_body
            for key, value in (params or {}).items():
                if value is not None:
                    request.add_query(key, value)
            return request

        option = RequestOption()
        token_type = getattr(self, "access_token_type", AccessTokenType.TENANT)
        if token_type == AccessTokenType.USER:
            option.user_access_token = self.user_access_token

        response = None
        for attempt in range(self._MAX_TRANSPORT_RETRIES + 1):
            try:
                response = self.client.request(build_request(), option)
                break
            except requests.exceptions.RequestException:
                if attempt >= self._MAX_TRANSPORT_RETRIES:
                    raise
                time.sleep(self._RETRY_BACKOFF_SECONDS * (2 ** attempt))

        # ``response`` is set unless the SDK raised after the final retry.
        assert response is not None
        payload = self._payload(response)
        raw = getattr(response, "raw", None)
        status_code = getattr(raw, "status_code", 200) if raw is not None else 200
        code = payload.get("code", getattr(response, "code", None))
        if status_code >= 400 or (code not in (None, 0)):
            raise FeishuSDKError(
                f"飞书 SDK 请求失败 HTTP {status_code}, code={code}, "
                f"msg={payload.get('msg', '')}, log_id={getattr(response, 'get_log_id', lambda: '')()}"
            )
        return payload.get("data", payload)

    def send_interactive_card(self, chat_id: str, card: Dict[str, Any]) -> Dict[str, Any]:
        """Send a native interactive card through the official IM SDK API.

        Keeping this as a typed SDK call (rather than a hand-built HTTP
        request) makes the daily-report card compatible with SDK-managed
        tenant authentication and avoids the image-upload/rendering pipeline.
        """
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(json.dumps(card, ensure_ascii=False))
                .build()
            )
            .build()
        )
        response = self.client.im.v1.message.create(request)
        payload = self._payload(response)
        raw = getattr(response, "raw", None)
        status_code = getattr(raw, "status_code", 200) if raw is not None else 200
        code = payload.get("code", getattr(response, "code", None))
        if status_code >= 400 or (code not in (None, 0)):
            raise FeishuSDKError(
                f"飞书 SDK 发送卡片失败 HTTP {status_code}, code={code}, "
                f"msg={payload.get('msg', '')}, "
                f"log_id={getattr(response, 'get_log_id', lambda: '')()}"
            )
        return payload.get("data", payload)


def configured_feishu_credentials(prefix: str = "") -> Tuple[str, str]:
    """Return credentials with a prefixed pair falling back to shared values."""
    prefix = prefix.strip()
    app_id = os.getenv(f"{prefix}FEISHU_APP_ID", "").strip() if prefix else ""
    app_secret = os.getenv(f"{prefix}FEISHU_APP_SECRET", "").strip() if prefix else ""
    return (
        app_id or os.getenv("FEISHU_APP_ID", "").strip(),
        app_secret or os.getenv("FEISHU_APP_SECRET", "").strip(),
    )
