import unittest
from datetime import datetime, timezone

from daily_report import message_text, normalize_messages, overlap_seconds, reporting_window


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


if __name__ == "__main__":
    unittest.main()
