"""Bounded local queue and resource registry for Feishu document events."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


EVENT_OPERATION_MAP = {
    "created_in_folder": "added",
    "edit": "modified",
    "title_updated": "modified",
    "deleted": "deleted",
    "trashed": "deleted",
    "bitable_field_changed": "modified",
    "bitable_record_changed": "modified",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _first_value(value: Any, keys: Iterable[str]) -> str:
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if candidate not in (None, "") and not isinstance(candidate, (dict, list)):
                return str(candidate)
        for child in value.values():
            result = _first_value(child, keys)
            if result:
                return result
    elif isinstance(value, list):
        for child in value:
            result = _first_value(child, keys)
            if result:
                return result
    return ""


def _operator_id(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("operator_id", "operator_id_list", "operator"):
            candidate = value.get(key)
            if key == "operator_id" and candidate not in (None, "") and not isinstance(candidate, (dict, list)):
                return str(candidate)
            if key in ("operator_id_list", "operator"):
                result = _operator_value(candidate)
                if result:
                    return result
        for key in ("operator", "operator_id_list", "operator_id"):
            if key in value:
                result = _operator_value(value[key])
                if result:
                    return result
    return ""


def _operator_value(value: Any) -> str:
    if value not in (None, "") and not isinstance(value, (dict, list)):
        return str(value)
    if isinstance(value, dict):
        for key in ("open_id", "user_id", "id"):
            candidate = value.get(key)
            if candidate not in (None, "") and not isinstance(candidate, (dict, list)):
                return str(candidate)
        for child in value.values():
            result = _operator_value(child)
            if result:
                return result
    elif isinstance(value, list):
        for child in value:
            result = _operator_value(child)
            if result:
                return result
    return ""


def normalize_document_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Convert an SDK event into a compact, stable event shape."""
    header = event.get("header") or {}
    body = event.get("event") or {}
    event_type = str(header.get("event_type") or event.get("event_type") or "")
    short_type = event_type.rsplit(".", 1)[-1].replace("_v1", "")
    event_time = header.get("create_time") or event.get("create_time") or _now_iso()
    if str(event_time).isdigit():
        event_time = datetime.fromtimestamp(int(event_time) / 1000, tz=timezone.utc).isoformat()
    file_token = _first_value(body, ("file_token", "document_id", "obj_token", "node_token"))
    return {
        "event_id": str(header.get("event_id") or event.get("event_id") or f"{event_type}:{event_time}:{file_token}"),
        "event_type": event_type,
        "operation": EVENT_OPERATION_MAP.get(short_type, "modified"),
        "event_time": str(event_time),
        "file_token": file_token,
        "file_type": _first_value(body, ("file_type", "obj_type")),
        "title": _first_value(body, ("title", "name")),
        "folder_token": _first_value(body, ("folder_token", "parent_node_token")),
        "operator_id": _operator_id(body),
        "record_id": _first_value(body, ("record_id",)),
        "field_id": _first_value(body, ("field_id",)),
        "table_id": _first_value(body, ("table_id",)),
        "url": "",
        "raw": event,
    }


class DocumentEventStore:
    """One bounded SQLite file shared by listener, discovery and daily report."""

    def __init__(self, path: Optional[str] = None):
        configured = path or os.getenv("DAILY_REPORT_FEISHU_EVENT_DB", "feishu_document_events.sqlite3")
        self.path = Path(configured)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path), timeout=30)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS document_events (
                event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, operation TEXT NOT NULL,
                event_time TEXT NOT NULL, file_token TEXT, file_type TEXT, title TEXT,
                folder_token TEXT, url TEXT, raw_json TEXT NOT NULL, received_at TEXT NOT NULL,
                operator_id TEXT, record_id TEXT, field_id TEXT, table_id TEXT
            )"""
        )
        self._ensure_columns({"operator_id": "TEXT", "record_id": "TEXT", "field_id": "TEXT", "table_id": "TEXT"})
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS document_resources (
                file_token TEXT PRIMARY KEY, file_type TEXT, title TEXT, url TEXT, source TEXT,
                parent_token TEXT, last_seen_at TEXT NOT NULL, deleted_at TEXT,
                subscription_status TEXT
            )"""
        )
        self._connection.commit()

    def _ensure_columns(self, columns: Dict[str, str]) -> None:
        existing = {row[1] for row in self._connection.execute("PRAGMA table_info(document_events)")}
        for name, definition in columns.items():
            if name not in existing:
                self._connection.execute(f"ALTER TABLE document_events ADD COLUMN {name} {definition}")

    def close(self) -> None:
        self._connection.close()

    def clear_through(self, end: datetime) -> int:
        cutoff = end.astimezone(timezone.utc).isoformat()
        cursor = self._connection.execute("DELETE FROM document_events WHERE event_time <= ?", (cutoff,))
        removed = cursor.rowcount
        self._connection.commit()
        try:
            self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._connection.execute("VACUUM")
        except sqlite3.OperationalError:
            pass
        return removed

    def add(self, event: Dict[str, Any]) -> Dict[str, Any]:
        normalized = normalize_document_event(event)
        raw_json = json.dumps(normalized["raw"], ensure_ascii=False)
        max_raw = max(0, int(os.getenv("DAILY_REPORT_FEISHU_EVENT_RAW_MAX_CHARS", "1000")))
        if max_raw and len(raw_json) > max_raw:
            raw_json = raw_json[:max_raw] + "…"
        self._connection.execute(
            """INSERT INTO document_events
              (event_id,event_type,operation,event_time,file_token,file_type,title,folder_token,url,
               raw_json,received_at,operator_id,record_id,field_id,table_id)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(event_id) DO UPDATE SET
              title=CASE WHEN excluded.title<>'' THEN excluded.title ELSE document_events.title END,
              url=CASE WHEN excluded.url<>'' THEN excluded.url ELSE document_events.url END""",
            (normalized["event_id"], normalized["event_type"], normalized["operation"], normalized["event_time"],
             normalized["file_token"], normalized["file_type"], normalized["title"], normalized["folder_token"],
             normalized["url"], raw_json, _now_iso(), normalized["operator_id"], normalized["record_id"],
             normalized["field_id"], normalized["table_id"]),
        )
        self._connection.commit()
        self._prune_events()
        return normalized

    def _prune_events(self) -> None:
        configured_maximum = int(os.getenv("DAILY_REPORT_FEISHU_EVENT_MAX_ROWS", "5000"))
        # A non-positive value explicitly disables row-count pruning.  Keep
        # the existing minimum only for positive configured limits.
        if configured_maximum <= 0:
            return
        maximum = max(100, configured_maximum)
        self._connection.execute(
            "DELETE FROM document_events WHERE event_id IN "
            "(SELECT event_id FROM document_events ORDER BY event_time DESC,event_id DESC LIMIT -1 OFFSET ?)",
            (maximum,),
        )
        self._connection.commit()

    def upsert_resource(self, resource: Dict[str, Any]) -> None:
        token = str(resource.get("file_token") or "")
        if not token:
            return
        self._connection.execute(
            """INSERT INTO document_resources
              (file_token,file_type,title,url,source,parent_token,last_seen_at,deleted_at,subscription_status)
              VALUES (?,?,?,?,?,?,?,?,?)
              ON CONFLICT(file_token) DO UPDATE SET
              file_type=COALESCE(NULLIF(excluded.file_type,''),document_resources.file_type),
              title=COALESCE(NULLIF(excluded.title,''),document_resources.title),
              url=COALESCE(NULLIF(excluded.url,''),document_resources.url),
              source=COALESCE(NULLIF(excluded.source,''),document_resources.source),
              parent_token=COALESCE(NULLIF(excluded.parent_token,''),document_resources.parent_token),
              last_seen_at=excluded.last_seen_at, deleted_at=excluded.deleted_at,
              subscription_status=COALESCE(NULLIF(excluded.subscription_status,''),document_resources.subscription_status)""",
            (token, resource.get("file_type", ""), resource.get("title", ""), resource.get("url", ""),
             resource.get("source", ""), resource.get("parent_token", ""), _now_iso(),
             resource.get("deleted_at"), resource.get("subscription_status", "")),
        )
        self._connection.commit()

    def update_subscription_status(self, file_token: str, status: str) -> None:
        self._connection.execute(
            "UPDATE document_resources SET subscription_status=?,last_seen_at=? WHERE file_token=?",
            (status, _now_iso(), file_token),
        )
        self._connection.commit()

    def prune_resources(self) -> None:
        configured_days = int(os.getenv("DAILY_REPORT_FEISHU_RESOURCE_RETENTION_DAYS", "7"))
        if configured_days > 0:
            cutoff = datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() - configured_days * 86400,
                timezone.utc,
            ).isoformat()
            self._connection.execute(
                "DELETE FROM document_resources WHERE deleted_at IS NOT NULL AND deleted_at<?",
                (cutoff,),
            )

        configured_maximum = int(os.getenv("DAILY_REPORT_FEISHU_RESOURCE_MAX_ROWS", "10000"))
        if configured_maximum > 0:
            maximum = max(100, configured_maximum)
            self._connection.execute(
                "DELETE FROM document_resources WHERE file_token IN "
                "(SELECT file_token FROM document_resources ORDER BY last_seen_at DESC LIMIT -1 OFFSET ?)",
                (maximum,),
            )
        self._connection.commit()

    def between(self, start: datetime, end: datetime, operator_id: str = "") -> list[Dict[str, Any]]:
        where = ""
        args = [start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()]
        if operator_id:
            where = " AND (e.operator_id=? OR e.operator_id='')"
            args.append(operator_id)
        rows = self._connection.execute(
            f"""SELECT e.event_id,e.event_type,e.operation,e.event_time,e.file_token,e.file_type,
                COALESCE(NULLIF(e.title,''),r.title),e.folder_token,COALESCE(NULLIF(e.url,''),r.url),
                e.operator_id,e.record_id,e.field_id,e.table_id,r.source
                FROM document_events e LEFT JOIN document_resources r ON r.file_token=e.file_token
                WHERE e.event_time>=? AND e.event_time<=?{where}
                ORDER BY e.event_time ASC,e.event_id ASC""",
            args,
        ).fetchall()
        fields = ("event_id", "event_type", "operation", "event_time", "file_token", "file_type", "title",
                  "folder_token", "url", "operator_id", "record_id", "field_id", "table_id", "source")
        return [dict(zip(fields, row)) for row in rows]
