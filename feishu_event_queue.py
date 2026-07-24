"""Durable local queue for Feishu cloud-document change events."""

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
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _first_value(value: Any, keys: Iterable[str]) -> str:
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if candidate not in (None, ""):
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


def normalize_document_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Convert an SDK event payload into the report's stable event shape."""
    header = event.get("header") or {}
    event_body = event.get("event") or {}
    event_type = str(header.get("event_type") or event.get("event_type") or "")
    short_type = event_type.rsplit(".", 1)[-1].replace("_v1", "")
    operation = EVENT_OPERATION_MAP.get(short_type, "modified")
    event_id = str(header.get("event_id") or event.get("event_id") or "")
    event_time = header.get("create_time") or event.get("create_time") or _now_iso()
    if str(event_time).isdigit():
        event_time = datetime.fromtimestamp(
            int(event_time) / 1000,
            tz=timezone.utc,
        ).isoformat()
    file_token = _first_value(event_body, ("file_token", "document_id", "obj_token", "node_token"))
    file_type = _first_value(event_body, ("file_type", "obj_type"))
    title = _first_value(event_body, ("title", "name"))
    folder_token = _first_value(event_body, ("folder_token", "parent_node_token"))
    return {
        "event_id": event_id or f"{event_type}:{event_time}:{file_token}",
        "event_type": event_type,
        "operation": operation,
        "event_time": str(event_time),
        "file_token": file_token,
        "file_type": file_type,
        "title": title,
        "folder_token": folder_token,
        "url": "",
        "raw": event,
    }


class DocumentEventStore:
    """SQLite-backed event store safe to use from the listener and report."""

    def __init__(self, path: Optional[str] = None):
        configured = path or os.getenv("DAILY_REPORT_FEISHU_EVENT_DB", "feishu_document_events.sqlite3")
        self.path = Path(configured)
        if self.path.parent != Path(""):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path), timeout=30)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS document_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                operation TEXT NOT NULL,
                event_time TEXT NOT NULL,
                file_token TEXT,
                file_type TEXT,
                title TEXT,
                folder_token TEXT,
                url TEXT,
                raw_json TEXT NOT NULL,
                received_at TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def clear_through(self, end: datetime) -> int:
        """Delete events already consumed by a successful daily report.

        Events arriving after ``end`` remain queued for the next report. The
        database file and schema stay in place for the long-running listener.
        """
        cutoff = end.astimezone(timezone.utc).isoformat()
        cursor = self._connection.execute(
            "DELETE FROM document_events WHERE event_time <= ?",
            (cutoff,),
        )
        removed = cursor.rowcount
        self._connection.commit()
        try:
            self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._connection.execute("VACUUM")
        except sqlite3.OperationalError:
            # A concurrently running listener may hold the WAL briefly. The
            # rows are already deleted; space reclamation can happen later.
            pass
        return removed

    def add(self, event: Dict[str, Any]) -> Dict[str, Any]:
        normalized = normalize_document_event(event)
        self._connection.execute(
            """
            INSERT INTO document_events
              (event_id, event_type, operation, event_time, file_token, file_type,
               title, folder_token, url, raw_json, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
              title=CASE WHEN excluded.title <> '' THEN excluded.title ELSE document_events.title END,
              url=CASE WHEN excluded.url <> '' THEN excluded.url ELSE document_events.url END
            """,
            (
                normalized["event_id"],
                normalized["event_type"],
                normalized["operation"],
                normalized["event_time"],
                normalized["file_token"],
                normalized["file_type"],
                normalized["title"],
                normalized["folder_token"],
                normalized["url"],
                json.dumps(normalized["raw"], ensure_ascii=False),
                _now_iso(),
            ),
        )
        self._connection.commit()
        return normalized

    def between(self, start: datetime, end: datetime) -> list[Dict[str, Any]]:
        rows = self._connection.execute(
            """
            SELECT event_id, event_type, operation, event_time, file_token, file_type,
                   title, folder_token, url
            FROM document_events
            WHERE event_time >= ? AND event_time <= ?
            ORDER BY event_time ASC, event_id ASC
            """,
            (start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()),
        ).fetchall()
        fields = (
            "event_id", "event_type", "operation", "event_time", "file_token",
            "file_type", "title", "folder_token", "url",
        )
        return [dict(zip(fields, row)) for row in rows]
