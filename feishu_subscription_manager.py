"""Discover accessible Feishu resources and subscribe document events.

This is deliberately a metadata-only job.  It finds current resources and
creates per-file subscriptions; it never downloads document bodies and never
tries to reconstruct events missed while the computer was offline.
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Dict, Iterable, List
from urllib.parse import quote

from feishu_event_queue import DocumentEventStore
from feishu_oauth import FeishuOAuthStore, load_valid_user_token
from feishu_sdk import FeishuOpenAPIClient
from lark_oapi.core import AccessTokenType


SUBSCRIBABLE_TYPES = {"docx", "doc", "sheet", "bitable"}


def _items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("items", "files", "nodes", "spaces"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _paginate(client: FeishuOpenAPIClient, path: str, params: Dict[str, Any], limit: int = 10000) -> Iterable[Dict[str, Any]]:
    page_token = ""
    count = 0
    while count < limit:
        query = dict(params)
        # Feishu Wiki and Drive list APIs accept at most 50 items per page.
        query["page_size"] = min(50, limit - count)
        if page_token:
            query["page_token"] = page_token
        data = client.request("GET", path, params=query)
        current = _items(data)
        for item in current:
            yield item
            count += 1
            if count >= limit:
                return
        if not data.get("has_more"):
            return
        page_token = str(data.get("page_token") or "")
        if not page_token:
            return


def _url(file_type: str, token: str) -> str:
    routes = {"docx": "docx", "doc": "docs", "sheet": "sheets", "bitable": "base", "folder": "drive/folder"}
    return f"{os.getenv('FEISHU_WEB_BASE', 'https://my.feishu.cn').rstrip('/')}/{routes.get(file_type, 'file')}/{token}"


class SubscriptionManager:
    def __init__(self):
        token, token_data = load_valid_user_token(FeishuOAuthStore())
        self.user_open_id = os.getenv("DAILY_REPORT_FEISHU_USER_OPEN_ID", "").strip() or str(token_data.get("open_id") or "")
        app_id = os.getenv("DAILY_REPORT_FEISHU_APP_ID") or os.getenv("FEISHU_APP_ID")
        app_secret = os.getenv("DAILY_REPORT_FEISHU_APP_SECRET") or os.getenv("FEISHU_APP_SECRET")
        self.client = FeishuOpenAPIClient(
            app_id=app_id,
            app_secret=app_secret,
            access_token_type=AccessTokenType.USER,
            user_access_token=token,
        )
        self.store = DocumentEventStore()

    def close(self):
        self.store.close()

    def discover(self) -> List[Dict[str, Any]]:
        resources: Dict[str, Dict[str, Any]] = {}
        if os.getenv("FEISHU_DISCOVERY_DRIVE_ENABLED", "1") == "1":
            self._discover_drive(resources)
        if os.getenv("FEISHU_DISCOVERY_WIKI_ENABLED", "1") == "1":
            self._discover_wiki(resources)
        for raw in os.getenv("FEISHU_DISCOVERY_FILE_TOKENS", "").split(","):
            token = raw.strip()
            if token:
                resources.setdefault(token, {"file_token": token, "file_type": "docx", "source": "configured"})
        for resource in resources.values():
            resource["url"] = _url(resource.get("file_type", ""), resource["file_token"])
            self.store.upsert_resource(resource)
        self.store.prune_resources()
        return list(resources.values())

    def _discover_drive(self, resources: Dict[str, Dict[str, Any]]) -> None:
        root = self.client.request("GET", "/drive/explorer/v2/root_folder/meta")
        root_token = str(root.get("token") or "")
        folder_tokens = [item.strip() for item in os.getenv("FEISHU_DISCOVERY_FOLDER_TOKENS", "").split(",") if item.strip()]
        if root_token:
            folder_tokens.insert(0, root_token)
        seen = set()
        while folder_tokens:
            folder = folder_tokens.pop(0)
            if not folder or folder in seen:
                continue
            seen.add(folder)
            for item in _paginate(self.client, "/drive/v1/files", {"folder_token": folder, "order_by": "EditedTime", "direction": "DESC"}):
                token = str(item.get("token") or item.get("file_token") or "")
                file_type = str(item.get("type") or item.get("file_type") or "")
                if not token:
                    continue
                if file_type == "folder":
                    folder_tokens.append(token)
                    continue
                resources[token] = {"file_token": token, "file_type": file_type, "title": str(item.get("name") or item.get("title") or ""), "source": "drive", "parent_token": folder}

    def _discover_wiki(self, resources: Dict[str, Dict[str, Any]]) -> None:
        spaces = _paginate(self.client, "/wiki/v2/spaces", {})
        wanted = {item.strip() for item in os.getenv("FEISHU_DISCOVERY_WIKI_SPACE_IDS", "").split(",") if item.strip()}
        for space in spaces:
            space_id = str(space.get("space_id") or space.get("id") or "")
            if not space_id or wanted and space_id not in wanted:
                continue
            queue = [""]
            visited = set()
            while queue:
                parent = queue.pop(0)
                if parent in visited:
                    continue
                visited.add(parent)
                params = {"space_id": space_id}
                if parent:
                    params["parent_node_token"] = parent
                for node in _paginate(self.client, f"/wiki/v2/spaces/{quote(space_id, safe='')}/nodes", params):
                    node_token = str(node.get("node_token") or "")
                    obj_token = str(node.get("obj_token") or "")
                    obj_type = str(node.get("obj_type") or "")
                    if node_token:
                        queue.append(node_token)
                    if obj_token and obj_type in SUBSCRIBABLE_TYPES:
                        resources[obj_token] = {"file_token": obj_token, "file_type": obj_type, "title": str(node.get("title") or ""), "source": "wiki", "parent_token": node_token}

    def subscribe(self, resources: Iterable[Dict[str, Any]]) -> Dict[str, int]:
        counts = {"subscribed": 0, "already": 0, "skipped": 0, "failed": 0}
        for resource in resources:
            token = str(resource.get("file_token") or "")
            file_type = str(resource.get("file_type") or "")
            if not token or file_type not in SUBSCRIBABLE_TYPES:
                counts["skipped"] += 1
                continue
            try:
                self.client.request("POST", f"/drive/v1/files/{quote(token, safe='')}/subscribe", params={"file_type": file_type})
                self.store.update_subscription_status(token, "subscribed")
                counts["subscribed"] += 1
            except Exception as exc:
                message = str(exc).lower()
                if "already" in message or "duplicate" in message or "106" in message and "subscribe" in message:
                    self.store.update_subscription_status(token, "already_subscribed")
                    counts["already"] += 1
                else:
                    self.store.update_subscription_status(token, f"failed:{str(exc)[:180]}")
                    counts["failed"] += 1
                    print(f"订阅失败: type={file_type}, token={token}, error={exc}")
        return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="发现飞书云文档并创建事件订阅")
    parser.add_argument("--discover", action="store_true", help="发现并订阅当前用户可访问的云空间和知识库资源")
    args = parser.parse_args()
    if not args.discover:
        parser.print_help()
        return
    manager = SubscriptionManager()
    try:
        resources = manager.discover()
        counts = manager.subscribe(resources)
        print(f"发现资源 {len(resources)} 个；订阅结果: {counts}；用户 open_id={manager.user_open_id}")
    finally:
        manager.close()


if __name__ == "__main__":
    main()
