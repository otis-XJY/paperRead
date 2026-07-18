import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from daily_report import (
    ActivityWatchClient,
    FeishuClient,
    extract_question_messages,
    is_question_message,
    is_paperread_message,
    message_text,
    normalize_messages,
    overlap_seconds,
    parse_report_payload,
    reporting_window,
    render_report_payload,
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
        self.assertEqual(summary["application_breakdown"][0]["top_windows"][0]["title"], "paperRead")

    def test_question_sender_must_be_configured(self):
        message = {"sender_name": "configured-user", "text": "How?"}
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(is_question_message(message))
        with patch.dict(
            os.environ,
            {"DAILY_REPORT_QUESTION_SENDER_NAME": "configured-user"},
            clear=True,
        ):
            self.assertTrue(is_question_message(message))

    def test_structured_report_payload_is_parsed_and_rendered(self):
        response = type(
            "Response",
            (),
            {
                "choices": [
                    type(
                        "Choice",
                        (),
                        {
                            "message": type(
                                "Message",
                                (),
                                {
                                    "content": '{"date":"2026-07-17","today_completed":[{"title":"测试","detail":"保留详细分析","evidence":"群聊"}],"rhythm":{"active_hours":7.02,"away_hours":1.5,"application_switches":3,"window_switches":4,"keypresses":100,"mouse_clicks":20}}'
                                },
                            )()
                        },
                    )()
                ]
            },
        )()
        payload = parse_report_payload(response)
        self.assertEqual(
            render_report_payload(payload).splitlines()[:4],
            ["📅 工作日报 2026-07-17", "", "✅ 今日完成", "• 测试：保留详细分析（依据：群聊）"],
        )

    def test_structured_report_payload_rejects_non_json_content(self):
        response = type(
            "Response",
            (),
            {
                "choices": [
                    type(
                        "Choice",
                        (),
                        {"message": type("Message", (), {"content": "not json"})()},
                    )()
                ]
            },
        )()
        with self.assertRaises(ValueError):
            parse_report_payload(response)

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

    def test_questions_from_xu_junyi_are_extracted(self):
        with patch.dict(
            os.environ,
            {"DAILY_REPORT_QUESTION_SENDER_NAME": "configured-user"},
            clear=True,
        ):
            self._assert_questions_from_configured_sender()

    def _assert_questions_from_configured_sender(self):
        messages = [
            {
                "sender_name": "configured-user ",
                "sender_id": "user-1",
                "text": "这个结果为什么会这样？",
            },
            {
                "sender_name": "configured-user ",
                "sender_id": "user-1",
                "text": "今天继续验证 GeoCoT-VLN。",
            },
            {
                "sender_name": "其他成员",
                "sender_id": "user-2",
                "text": "请问如何运行？",
            },
        ]
        self.assertTrue(is_question_message(messages[0]))
        self.assertFalse(is_question_message(messages[1]))
        self.assertEqual(extract_question_messages(messages), [messages[0]])

    def test_read_document_link_uses_docx_id(self):
        client = FeishuClient("app", "secret")
        with patch.object(client, "read_document_text", return_value="论文知识库内容") as read:
            content = client.read_document_link("https://my.feishu.cn/docx/AbC123")
        self.assertEqual(content, "论文知识库内容")
        read.assert_called_once_with("AbC123")

    def test_document_versions_are_filtered_and_classified(self):
        client = FeishuClient("app", "secret")
        client._request = lambda method, path, params=None: {
            "items": [
                {
                    "name": "ReflectVLN",
                    "version": "v1",
                    "create_time": "2026-07-15T02:00:00+00:00",
                    "update_time": "2026-07-15T02:00:00+00:00",
                    "creator_id": "user-1",
                    "status": "0",
                },
                {
                    "name": "ReflectVLN",
                    "version": "v2",
                    "create_time": "2026-07-15T12:00:00+00:00",
                    "update_time": "2026-07-15T12:00:00+00:00",
                    "creator_id": "user-2",
                    "status": "0",
                },
            ],
            "has_more": False,
        }
        start = datetime(2026, 7, 15, 1, tzinfo=timezone.utc)
        end = datetime(2026, 7, 15, 23, tzinfo=timezone.utc)
        versions = client.list_document_versions("AbC123", start, end)
        self.assertEqual([item["operation"] for item in versions], ["added", "modified"])
        self.assertEqual(versions[1]["creator_id"], "user-2")


if __name__ == "__main__":
    unittest.main()
