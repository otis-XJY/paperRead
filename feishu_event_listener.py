"""Long-running Feishu cloud-document event listener for Windows.

Run this process under the same logged-in Windows account as the self-hosted
runner.  The official SDK keeps the WebSocket connection alive and this module
only persists event metadata; it never stores document contents.
"""

from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
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


def _configure_logging(event_db_path):
    """Persist diagnostics because Task Scheduler runs this script with pythonw."""
    log_path = os.getenv("DAILY_REPORT_FEISHU_EVENT_LOG", "").strip()
    if not log_path:
        log_path = str(Path(event_db_path).with_name("feishu_event_listener.log"))
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path,
        maxBytes=1_000_000,
        backupCount=2,
        encoding="utf-8",
    )
    logger = logging.getLogger("paperRead.feishu_event_listener")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    # lark-oapi may configure the root logger before this function runs, so
    # basicConfig() is not reliable here. Attach the file handler directly.
    logger.handlers.clear()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def build_event_handler(store: DocumentEventStore):
    """Build handlers for the official Drive document event types."""
    logger = logging.getLogger("paperRead.feishu_event_listener")

    def on_event(data):
        payload = json.loads(lark.JSON.marshal(data))
        normalized = store.add(payload)
        if normalized.get("file_token"):
            store.upsert_resource({
                "file_token": normalized["file_token"],
                "file_type": normalized.get("file_type", ""),
                "title": normalized.get("title", ""),
                "parent_token": normalized.get("folder_token", ""),
                "deleted_at": normalized["event_time"] if normalized["operation"] == "deleted" else None,
            })
        logger.info(
            "document event: operation=%s file_type=%s file_token=%s event_type=%s",
            normalized["operation"],
            normalized["file_type"],
            normalized["file_token"],
            normalized["event_type"],
        )
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
        "register_p2_drive_file_bitable_field_changed_v1",
        "register_p2_drive_file_bitable_record_changed_v1",
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
    logger = _configure_logging(args.event_db)
    app_id, app_secret = _credentials()
    store = DocumentEventStore(args.event_db)
    handler = build_event_handler(store)
    ws_client = lark.ws.Client(
        app_id,
        app_secret,
        event_handler=handler,
        # Avoid logging connection URLs containing temporary access_key/ticket
        # values. Application events are still printed by on_event().
        log_level=lark.LogLevel.WARNING,
        auto_reconnect=True,
    )
    logger.info(
        "listener started; event_db=%s; event_types=%s",
        store.path,
        "created_in_folder,edit,title_updated,deleted,trashed",
    )
    print("飞书文档事件监听已启动。", flush=True)
    try:
        ws_client.start()
    except Exception:
        logger.exception("listener stopped with exception")
        raise
    finally:
        store.close()


if __name__ == "__main__":
    main()
