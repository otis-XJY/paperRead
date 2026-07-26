"""Offline tests for the bounded Feishu event queue."""

import os
import tempfile
import unittest
from datetime import datetime, timezone

from feishu_event_queue import DocumentEventStore, normalize_document_event


class FeishuEventQueueTests(unittest.TestCase):
    def test_bitable_event_and_operator_are_normalized(self):
        normalized = normalize_document_event({
            "header": {
                "event_id": "event-1",
                "event_type": "drive.file.bitable_record_changed_v1",
                "create_time": "1770000000000",
            },
            "event": {
                "file_token": "app-token",
                "file_type": "bitable",
                "operator_id": "ou_me",
                "record_id": "rec-1",
            },
        })
        self.assertEqual(normalized["operation"], "modified")
        self.assertEqual(normalized["operator_id"], "ou_me")
        self.assertEqual(normalized["record_id"], "rec-1")

    def test_resource_title_enriches_event_and_consumption_clears_rows(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        try:
            store = DocumentEventStore(path)
            store.upsert_resource({
                "file_token": "doc-token",
                "file_type": "docx",
                "title": "工作文档",
                "source": "wiki",
            })
            store.add({
                "header": {
                    "event_id": "event-2",
                    "event_type": "drive.file.edit_v1",
                    "create_time": "1770000000000",
                },
                "event": {"file_token": "doc-token", "file_type": "docx", "operator_id": "ou_me"},
            })
            start = datetime(2025, 1, 1, tzinfo=timezone.utc)
            end = datetime(2030, 1, 1, tzinfo=timezone.utc)
            rows = store.between(start, end, operator_id="ou_me")
            self.assertEqual(rows[0]["title"], "工作文档")
            self.assertEqual(rows[0]["source"], "wiki")
            self.assertEqual(store.clear_through(end), 1)
            self.assertEqual(store.between(start, end), [])
            store.close()
        finally:
            for candidate in (path, path + "-wal", path + "-shm"):
                if os.path.exists(candidate):
                    os.remove(candidate)


if __name__ == "__main__":
    unittest.main()
