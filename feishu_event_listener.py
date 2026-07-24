"""Long-running Feishu cloud-document event listener for Windows.

Run this process under the same logged-in Windows account as the self-hosted
runner.  The official SDK keeps the WebSocket connection alive and this module
only persists event metadata; it never stores document contents.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Tuple

import lark_oapi as lark

from feishu_event_queue import DocumentEventStore

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _credentials() -> Tuple[str, str]:
    app_id = (
        os.getenv("DAILY_REPORT_FEISHU_APP_ID")
        or os.getenv("FEISHU_APP_ID")
        or ""
    ).strip()
    app_secret = (
        os.getenv("DAILY_REPORT_FEISHU_APP_SECRET")
        or os.getenv("FEISHU_APP_SECRET")
        or ""
    ).strip()
    if not app_id or not app_secret:
        raise RuntimeError("需要配置 FEISHU_APP_ID/FEISHU_APP_SECRET 或日报专用凭证")
    return app_id, app_secret


def build_event_handler(store: DocumentEventStore):
    """Build handlers for the official Drive document event types."""

    def on_event(data):
        payload = json.loads(lark.JSON.marshal(data))
        normalized = store.add(payload)
        print(
            "收到飞书文档事件: "
            f"{normalized['operation']} {normalized['file_type']} "
            f"{normalized['file_token']} ({normalized['event_type']})",
            flush=True,
        )

    builder = lark.EventDispatcherHandler.builder("", "")
    for method_name in (
        "register_p2_drive_file_created_in_folder_v1",
        "register_p2_drive_file_edit_v1",
        "register_p2_drive_file_title_updated_v1",
        "register_p2_drive_file_deleted_v1",
        "register_p2_drive_file_trashed_v1",
    ):
        register = getattr(builder, method_name, None)
        if register is None:
            raise RuntimeError(f"当前 lark-oapi 不支持事件处理器: {method_name}")
        register(on_event)
    return builder.build()


def main() -> None:
    parser = argparse.ArgumentParser(description="Listen to Feishu document events")
    parser.add_argument(
        "--event-db",
        default=os.getenv("DAILY_REPORT_FEISHU_EVENT_DB", "feishu_document_events.sqlite3"),
        help="SQLite event queue path",
    )
    args = parser.parse_args()
    app_id, app_secret = _credentials()
    store = DocumentEventStore(args.event_db)
    handler = build_event_handler(store)
    ws_client = lark.ws.Client(
        app_id,
        app_secret,
        event_handler=handler,
        log_level=lark.LogLevel.INFO,
        auto_reconnect=True,
    )
    print("飞书文档事件监听已启动。", flush=True)
    try:
        ws_client.start()
    finally:
        store.close()


if __name__ == "__main__":
    main()
