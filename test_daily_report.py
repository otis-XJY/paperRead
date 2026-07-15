import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from daily_report import (
    ActivityWatchClient,
    FeishuClient,
    is_paperread_message,
    message_text,
    normalize_messages,
    overlap_seconds,
    reporting_window,
    split_chat_messages,
)


class DailyReportHelpersTest(unittest.TestCase):
    def test_overlap_clips_event_to_window(self):
        start = datetime(2026, 7, 15, 1, tzinfo=timezone.utc)
        end = datetime(2026, 7, 15, 2, tzinfo=timezone.utc)
        self.assertEqual(
            overlap_seconds("2026-07-15T00:30:00+00:00", 3600, start, end),
            1800,
        )

    def test_message_text_handles_feishu_json_content(self):
        message = {"body": {"content": '{"text":"完成实验，明天整理图表"}'}}
        self.assertEqual(message_text(message), "完成实验，明天整理图表")

    def test_normalize_messages_skips_empty_content(self):
        messages = [
            {"body": {"content": '{"text":"工作总结"}'}, "sender": {"sender_type": "user"}},
            {"body": {"content": '{"text":""}'}, "sender": {"sender_type": "user"}},
        ]
        self.assertEqual(len(normalize_messages(messages)), 1)

    def test_reporting_window_starts_at_one(self):
        now = datetime(2026, 7, 15, 23, 30, tzinfo=timezone.utc)
        start, end = reporting_window(now, "UTC")
        self.assertEqual(start.hour, 1)
        self.assertEqual(end.hour, 23)

    def test_activity_summary_uses_hours_and_details_rhythm(self):
        client = ActivityWatchClient()
        client.buckets = lambda: {
            "window": {"name": "aw-watcher-window"},
            "afk": {"name": "aw-watcher-afk"},
            "input": {"name": "aw-watcher-input"},
        }

        def events(bucket_id, start, end):
            if bucket_id == "window":
                return [
                    {
                        "timestamp": "2026-07-15T01:00:00+00:00",
                        "duration": 3600,
                        "data": {"app": "Code", "title": "paperRead"},
                    },
                    {
                        "timestamp": "2026-07-15T02:00:00+00:00",
                        "duration": 3600,
                        "data": {"app": "Edge", "title": "arXiv"},
                    },
                ]
            if bucket_id == "afk":
                return [
                    {
                        "timestamp": "2026-07-15T01:00:00+00:00",
                        "duration": 7200,
                        "data": {"status": "not-afk"},
                    }
                ]
            return [
                {
                    "timestamp": "2026-07-15T01:00:00+00:00",
                    "duration": 7200,
                    "data": {"keypresses": 100, "mouse_clicks": 20},
                }
            ]

        client.events = events
        start = datetime(2026, 7, 15, 1, tzinfo=timezone.utc)
        end = datetime(2026, 7, 15, 3, tzinfo=timezone.utc)
        summary = client.summarize(start, end)

        self.assertEqual(summary["active_hours"], 2.0)
        self.assertEqual(summary["away_hours"], 0.0)
        self.assertEqual(summary["applications"][0]["hours"], 1.0)
        self.assertEqual(summary["rhythm"]["active_share_percent"], 100.0)
        self.assertEqual(summary["rhythm"]["application_switches"], 1)
        self.assertEqual(summary["rhythm"]["keypresses_per_active_hour"], 50.0)

    def test_paperread_messages_are_separated(self):
        message = {
            "sender": {"sender_type": "app"},
            "body": {
                "content": '{"text":"UAV_VLN - 1/1\\n📖 推荐: 值得看\\nhttps://arxiv.org/abs/2607.12680"}'
            },
        }
        self.assertTrue(is_paperread_message(message))
        ordinary, paperread = split_chat_messages(normalize_messages([message]))
        self.assertEqual(ordinary, [])
        self.assertEqual(len(paperread), 1)

    def test_read_document_link_uses_docx_id(self):
        client = FeishuClient("app", "secret")
        with patch.object(client, "read_document_text", return_value="论文知识库内容") as read:
            content = client.read_document_link("https://my.feishu.cn/docx/AbC123")
        self.assertEqual(content, "论文知识库内容")
        read.assert_called_once_with("AbC123")


if __name__ == "__main__":
    unittest.main()
