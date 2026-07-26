import json
import importlib
import os
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from daily_report import (
    ActivityWatchClient,
    FeishuClient,
    build_daily_report_card,
    extract_question_messages,
    generate_report,
    is_question_message,
    is_paperread_message,
    message_text,
    normalize_messages,
    overlap_seconds,
    parse_report_payload,
    reporting_window,
    render_report_payload,
    split_chat_messages,
    write_monthly_report,
)


class DailyReportHelpersTest(unittest.TestCase):
    def test_structured_card_groups_work_time_rhythm_and_feishu_context(self):
        payload = {
            "date": "2026-07-26",
            "today_completed": [
                {
                    "application": "Microsoft Edge",
                    "main_work": "飞书 Wiki 整理",
                    "detail": "核对论文笔记目录并完成链接整理。",
                }
            ],
            "time_investment": [
                {
                    "application": "Microsoft Edge",
                    "main_work": "飞书 Wiki 整理",
                    "hours": 2,
                    "detail": "飞书文档页面",
                },
                {
                    "application": "VS Code",
                    "main_work": "日报卡片开发",
                    "hours": 1,
                },
            ],
            "rhythm": {
                "active_hours": 3,
                "away_hours": 1,
                "application_switches": 4,
                "window_switches": 8,
                "keypresses": 100,
                "mouse_clicks": 20,
            },
            "documents": {
                "collaboration_summary": "Edge 中打开飞书 Wiki，未收到直接文档事件。",
                "added": [],
                "modified": [],
                "deleted": [],
            },
            "tomorrow_plan": {
                "tasks": [{"title": "补充验证", "detail": "运行一组对照实验"}],
                "idea_suggestions": [
                    {
                        "title": "把 PaperRead 方向接入当前实验",
                        "detail": "比较今日已整理的飞书笔记与现有实验结果。",
                        "source": "PaperRead 今日推送",
                        "next_step": "明天先做小规模可行性验证。",
                    }
                ],
            },
            "risks": [{"title": "数据不足", "detail": "需要补充对照样本"}],
            "questions": [{"title": "为什么这样？", "answer": "需要结合实验日志确认。"}],
        }
        elements = build_daily_report_card(
            payload,
            activity_summary={
                "concentration": {"longest_window_session_hours": 1.25},
                "browser_windows": [
                    {"app": "msedge", "title": "飞书 Wiki - Microsoft Edge", "hours": 2}
                ],
            },
            document_events_received=False,
        )
        card_json = json.dumps(elements, ensure_ascii=False)
        self.assertIn("Microsoft Edge · 飞书 Wiki 整理", card_json)
        self.assertIn("可观测应用/事项合计：**3.00h**", card_json)
        self.assertIn("连续专注最长", card_json)
        self.assertIn("飞书文档与协作", card_json)
        self.assertIn("飞书 Wiki - Microsoft Edge(2.00h)", card_json)
        self.assertIn('"tag": "column_set"', card_json)
        self.assertIn("明日计划", card_json)
        self.assertIn("Idea 建议（结合 PaperRead 与今日工作）", card_json)
        self.assertIn("风险或待跟进", card_json)
        self.assertIn("群聊问题解答", card_json)

    def test_render_report_adds_time_shares_and_event_queue_warning(self):
        payload = {
            "date": "2026-07-25",
            "today_completed": [{"title": "参数排查", "detail": "发现数据问题"}],
            "time_investment": [
                {"app_or_topic": "ChatGPT", "hours": 7, "detail": "信息检索"},
                {"app_or_topic": "Microsoft Edge", "hours": 7, "detail": "浏览资料"},
            ],
            "rhythm": {"active_hours": 14, "away_hours": 0},
            "concentration": {"summary": "窗口切换频繁", "findings": []},
            "documents": {"added": [], "modified": [], "deleted": []},
        }
        report = render_report_payload(
            payload,
            activity_summary={
                "browser_windows": [
                    {"app": "msedge", "title": "Agent 研究资料 - Microsoft Edge", "hours": 2}
                ]
            },
            document_events_received=False,
        )
        self.assertIn("可观测应用/事项合计：14.00h", report)
        self.assertIn("ChatGPT：7.00h（50.0%）", report)
        self.assertIn("Microsoft Edge：7.00h（50.0%）", report)
        self.assertEqual(report.count("📊 工作节奏"), 1)
        self.assertNotIn("🎯 可观测操作焦点\n", report)
        self.assertIn("浏览器窗口记录：Agent 研究资料 - Microsoft Edge(2.00h)", report)
        self.assertIn("事件队列没有收到文档变更，不能据此确认当天没有变更", report)

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
        self.assertEqual(summary["concentration"]["window_sessions"], 2)
        self.assertEqual(summary["concentration"]["longest_window_session_hours"], 1.0)
        self.assertEqual(summary["concentration"]["window_switches_per_active_hour"], 0.5)
        self.assertEqual(summary["concentration"]["sessions_at_least_10_minutes"], 2)

    def test_focus_bucket_collapses_overlapping_monitor_activity(self):
        client = ActivityWatchClient()
        client.buckets = lambda: {"focus": {"name": "aw-watcher-focus"}}
        client.events = lambda bucket_id, start, end: [
            {
                "timestamp": "2026-07-15T01:00:00+00:00",
                "duration": 60,
                "data": {
                    "window_title": "Code",
                    "monitor": "0,0,1920,1080",
                    "keypresses": 1,
                    "mouse_clicks": 0,
                },
            },
            {
                "timestamp": "2026-07-15T01:00:30+00:00",
                "duration": 60,
                "data": {
                    "window_title": "Code",
                    "monitor": "0,0,1920,1080",
                    "keypresses": 0,
                    "mouse_clicks": 1,
                },
            },
        ]
        summary = client.summarize(
            datetime(2026, 7, 15, 1, tzinfo=timezone.utc),
            datetime(2026, 7, 15, 2, tzinfo=timezone.utc),
        )
        self.assertEqual(summary["active_hours"], 0.03)
        self.assertEqual(summary["input"]["keypresses"], 1)
        self.assertEqual(summary["input"]["mouse_clicks"], 1)
        self.assertEqual(summary["windows"][0]["title"], "Code")

    def test_monthly_report_is_idempotent_for_one_day(self):
        class FakeClient:
            def __init__(self):
                self.content = ""
                self.appended = 0

            def get_or_create_month_document(self, root, title):
                self.root = root
                self.title = title
                return "doc-1"

            def read_document_text(self, document_id):
                return self.content

            def append_document_blocks(self, document_id, blocks):
                self.appended += 1
                self.content = "[DAILY_REPORT:2026-07-24]"

        client = FakeClient()
        with patch.dict(
            os.environ,
            {"DAILY_REPORT_FEISHU_WIKI_ROOT_NODE_TOKEN": "root-1"},
            clear=False,
        ):
            write_monthly_report(client, "日报内容", "2026-07-24")
            write_monthly_report(client, "日报内容", "2026-07-24")
        self.assertEqual(client.appended, 1)

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
            ["📅 工作日报 2026-07-17", "", "✅ 今日完成", "- 测试：保留详细分析"],
        )
        self.assertNotIn("证据", render_report_payload(payload))
        self.assertNotIn("依据", render_report_payload(payload))

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

    def test_structured_report_payload_accepts_wrapped_json(self):
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
                                {"content": "思考说明\n```json\n{\"date\":\"2026-07-20\"}\n```"},
                            )()
                        },
                    )()
                ]
            },
        )()
        self.assertEqual(parse_report_payload(response), {"date": "2026-07-20"})

    def test_structured_report_payload_prefers_report_object_over_thinking_object(self):
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
                                    "content": '{"scratch":"内部推理"}\n{"date":"2026-07-20","today_completed":[]}'
                                },
                            )()
                        },
                    )()
                ]
            },
        )()
        self.assertEqual(
            parse_report_payload(response),
            {"date": "2026-07-20", "today_completed": []},
        )

    def test_generate_report_formats_json_schema_example(self):
        payload = {
            "date": "2026-07-20",
            "today_completed": [],
            "time_investment": [],
            "evidence_boundary": "",
            "rhythm": {},
            "tomorrow_plan": [],
            "risks": [],
            "papers": {},
            "documents": {},
            "questions": [],
        }
        response = type(
            "Response",
            (),
            {
                "choices": [
                    type(
                        "Choice",
                        (),
                        {"message": type("Message", (), {"content": json.dumps(payload)})()},
                    )()
                ]
            },
        )()
        activity = {
            "period": {
                "start": "2026-07-20T01:00:00+08:00",
                "end": "2026-07-20T23:00:00+08:00",
            },
            "active_hours": 0,
            "away_hours": 0,
            "rhythm": {},
        }
        fake_llm = type("FakeLLM", (), {})()
        fake_llm.call = Mock(return_value=response)
        fake_module = type("FakeLLMModule", (), {"llm": fake_llm})()
        with patch.dict(sys.modules, {"llm_client": fake_module}):
            report = generate_report([], activity)
        self.assertIn("工作日报 2026-07-20", report)
        self.assertEqual(
            fake_llm.call.call_args.kwargs["response_format"],
            {"type": "json_object"},
        )
        self.assertIs(fake_llm.call.call_args.kwargs["response_validator"], parse_report_payload)

    def test_llm_client_falls_back_after_invalid_structured_output(self):
        fake_openai = SimpleNamespace(OpenAI=lambda **kwargs: object())
        with patch.dict(
            os.environ,
            {"MODELSCOPE_API_KEY": "test-key", "HTTPS_PROXY": "", "ALL_PROXY": ""},
            clear=False,
        ), patch.dict(sys.modules, {"openai": fake_openai}):
            llm_client = importlib.import_module("llm_client")

        bad_response = type(
            "Response",
            (),
            {"choices": [type("Choice", (), {"message": type("Message", (), {"content": "not json"})()})()]},
        )()
        good_response = type(
            "Response",
            (),
            {"choices": [type("Choice", (), {"message": type("Message", (), {"content": "{}"})()})()]},
        )()
        completions = Mock()
        completions.create.side_effect = [bad_response, good_response]
        fake_client = type(
            "Client",
            (),
            {"chat": type("Chat", (), {"completions": completions})()},
        )()
        model_pool = llm_client.MultiModelLLM(fake_client, ["bad-model", "good-model"])
        result = model_pool.call(
            [{"role": "user", "content": "return JSON"}],
            response_format={"type": "json_object"},
            max_rounds=1,
            response_validator=parse_report_payload,
        )
        self.assertIs(result, good_response)
        self.assertEqual(completions.create.call_count, 2)

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

if __name__ == "__main__":
    unittest.main()
