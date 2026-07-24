import os
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from feishu_event_queue import DocumentEventStore, normalize_document_event


class FeishuDocumentEventTests(unittest.TestCase):
    def test_normalizes_drive_event(self):
        event = {
            "header": {
                "event_id": "evt-1",
                "event_type": "p2.drive.file.created_in_folder_v1",
                "create_time": "1780000000000",
            },
            "event": {
                "file_type": "docx",
                "file_token": "doc-1",
                "folder_token": "folder-1",
            },
        }
        normalized = normalize_document_event(event)
        self.assertEqual(normalized["operation"], "added")
        self.assertEqual(normalized["file_token"], "doc-1")

    def test_store_deduplicates_and_filters_by_time(self):
        database = Path.cwd() / f".test-events-{os.getpid()}.sqlite3"
        try:
            store = DocumentEventStore(str(database))
            event = {
                "header": {
                    "event_id": "evt-1",
                    "event_type": "p2.drive.file.edit_v1",
                    "create_time": "2026-07-24T10:00:00+00:00",
                },
                "event": {"file_type": "docx", "file_token": "doc-1"},
            }
            store.add(event)
            store.add(event)
            rows = store.between(
                datetime(2026, 7, 24, 9, tzinfo=timezone.utc),
                datetime(2026, 7, 24, 11, tzinfo=timezone.utc),
            )
            store.close()
        finally:
            if database.exists():
                database.unlink()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "modified")

    def test_clear_through_removes_consumed_events_only(self):
        database = Path.cwd() / f".test-events-clear-{os.getpid()}.sqlite3"
        try:
            store = DocumentEventStore(str(database))
            for event_id, event_time in (
                ("old", "2026-07-24T10:00:00+00:00"),
                ("new", "2026-07-24T12:00:00+00:00"),
            ):
                store.add(
                    {
                        "header": {
                            "event_id": event_id,
                            "event_type": "p2.drive.file.edit_v1",
                            "create_time": event_time,
                        },
                        "event": {"file_type": "docx", "file_token": event_id},
                    }
                )
            removed = store.clear_through(
                datetime(2026, 7, 24, 11, tzinfo=timezone.utc)
            )
            remaining = store.between(
                datetime(2026, 7, 24, 0, tzinfo=timezone.utc),
                datetime(2026, 7, 24, 23, tzinfo=timezone.utc),
            )
            store.close()
        finally:
            for suffix in ("", "-wal", "-shm"):
                target = Path(f"{database}{suffix}")
                if target.exists():
                    target.unlink()
        self.assertEqual(removed, 1)
        self.assertEqual([row["event_id"] for row in remaining], ["new"])


if __name__ == "__main__":
    unittest.main()
