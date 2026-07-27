"""Generate a daily work report from Feishu chat and local ActivityWatch data.

This module is intentionally run by a scheduled GitHub Actions job on a
self-hosted Windows runner. It does not persist chat or ActivityWatch data.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from datetime import datetime, time as dt_time, timedelta, timezone
from statistics import median
from urllib.parse import quote
try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python 3.8 compatibility
    from backports.zoneinfo import ZoneInfo

import requests

from feishu_sdk import FeishuOpenAPIClient, FeishuSDKError
from feishu_event_queue import DocumentEventStore

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


FEISHU_BASE = "https://open.feishu.cn/open-apis"
DEFAULT_AW_URL = "http://127.0.0.1:5600"
DEFAULT_TIMEZONE = "Asia/Shanghai"


DAILY_REPORT_CARD_HEADINGS = (
    "✅ 今日完成",
    "⏱ 时间投入",
    "📊 工作节奏",
    "明日计划建议",
    "风险或待跟进",
    "📚 PaperRead 论文与未来研究建议",
    "📄 飞书文档变更",
    "💬 群聊问题解答",
)


def _build_text_report_card(text):
    """Legacy fallback for a plain-text report preview."""
    lines = str(text or "").splitlines()
    sections = []
    current_heading = ""
    current_lines = []

    def flush():
        if not current_heading:
            return
        body = "\n".join(line for line in current_lines if line.strip()).strip()
        content = f"**{current_heading}**"
        if body:
            content += f"\n{body}"
        sections.append(
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": content[:6000]},
            }
        )

    for line in lines:
        if line.startswith("📅 工作日报"):
            continue
        heading = next(
            (candidate for candidate in DAILY_REPORT_CARD_HEADINGS if line.startswith(candidate)),
            None,
        )
        if heading:
            flush()
            current_heading = heading
            current_lines = []
        elif current_heading:
            current_lines.append(line)

    flush()
    if not sections:
        sections.append(
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": str(text or "")[:30000]},
            }
        )

    elements = []
    for index, section in enumerate(sections):
        if index:
            elements.append({"tag": "hr"})
        elements.append(section)
    return elements


def _card_text(content, max_chars=6000):
    return {
        # Card JSON 2.0 uses the markdown content component directly.  This
        # keeps the card compatible with the native chart/collapsible-panel
        # components instead of mixing JSON 1.0 ``div/lark_md`` elements.
        "tag": "markdown",
        "content": str(content or "")[:max_chars],
    }


def _card_divider():
    return {"tag": "hr"}


def _card_bar(percent, width=12):
    """Return a compact text bar that needs no image upload or chart service."""
    percent = max(0.0, min(_number(percent), 100.0))
    filled = int(round(percent / 100 * width))
    return "█" * filled + "░" * (width - filled)


def _card_metric_row(metrics):
    """Render small numeric indicators with native card columns."""
    columns = []
    for label, value in metrics:
        columns.append(
            {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "vertical_align": "top",
                "elements": [_card_text(f"**{label}**\n{value}", max_chars=500)],
            }
        )
    return {
        "tag": "column_set",
        "flex_mode": "stretch",
        "background_style": "default",
        "columns": columns,
    }


def _card_app_icon(application):
    """Return a small, readable icon for the application heading."""
    name = str(application or "").casefold()
    if "edge" in name or "chrome" in name or "firefox" in name or "browser" in name:
        return "🌐"
    if "chatgpt" in name or "agent" in name or "copilot" in name:
        return "🤖"
    if "code" in name or "visual studio" in name or "pycharm" in name:
        return "🧑‍💻"
    if "飞书" in name or "feishu" in name or "lark" in name:
        return "📄"
    if "explorer" in name or "文件" in name:
        return "🗂️"
    return "🧭"


def _card_collapsible_panel(title, content):
    """Render one expanded app-level group using Card JSON 2.0."""
    return {
        "tag": "collapsible_panel",
        "expanded": True,
        "header": {
            "title": {"tag": "plain_text", "content": str(title)[:120]},
        },
        "elements": [_card_text(content, max_chars=9000)],
    }


def _time_investment_chart(investments):
    """Build a native CardKit pie chart for application/topic time."""
    values = []
    for item in investments[:16]:
        app = _display_application(item.get("application") or item.get("app") or item.get("app_or_topic"))
        work = _report_text(item.get("main_work") or item.get("work_item") or item.get("title"))
        label = " · ".join(part for part in (app, work) if part) or "未命名事项"
        hours = _number(item.get("hours"))
        if hours > 0:
            values.append({"topic": label[:80], "hours": round(hours, 2)})
    if not values:
        return None
    return {
        "tag": "chart",
        "aspect_ratio": "16:9",
        "color_theme": "brand",
        "chart_spec": {
            "type": "pie",
            "data": [{"id": "time_investment", "values": values}],
            "categoryField": "topic",
            "valueField": "hours",
            "outerRadius": 0.8,
        },
    }


def _completed_item_markdown(item):
    if not isinstance(item, dict):
        return f"- {_report_text(item)}"
    app = _report_text(item.get("application") or item.get("app") or item.get("app_or_topic"))
    work = _report_text(item.get("main_work") or item.get("title") or item.get("name"))
    detail = _report_text(item.get("detail") or item.get("result") or item.get("summary"))
    title = " · ".join(part for part in (app, work) if part) or "已完成事项"
    status = _report_text(item.get("status"))
    evidence = item.get("evidence")
    evidence_text = ""
    if isinstance(evidence, list) and evidence:
        evidence_text = f"；证据：{'、'.join(str(value) for value in evidence[:3])}"
    suffix = "；".join(part for part in (detail, status) if part)
    if suffix:
        suffix += evidence_text
    elif evidence_text:
        suffix = evidence_text.lstrip("；")
    return f"- **{title}**" + (f"\n  {suffix}" if suffix else "")


def _feishu_browser_windows(activity_summary):
    """Return browser evidence that is clearly related to Feishu documents."""
    rows = (activity_summary or {}).get("browser_windows") or []
    matches = []
    for row in rows:
        label = " ".join(str(row.get(key) or "") for key in ("app", "title", "context"))
        if re.search(r"feishu|lark|飞书|docs|docx|wiki", label, re.IGNORECASE):
            title = _clean_window_title(row.get("title") or row.get("context") or row.get("app") or "飞书页面")
            matches.append({"title": title, "hours": _number(row.get("hours"))})
    return matches[:8]


def build_daily_report_card(payload, activity_summary=None, document_events_received=True):
    """Build a native Feishu card directly from structured report data.

    The card uses Card JSON 2.0 native components.  Text remains alongside the
    chart so the report is still readable when a client does not render charts.
    """
    if not isinstance(payload, dict):
        return _build_text_report_card(payload)

    elements = []
    completed = [item for item in _report_items(payload.get("today_completed")) if isinstance(item, dict)]
    elements.append(_card_text("**✅ 今日完成**  \n按应用展开；每个应用下再按主题、产出和证据分层说明。"))
    if completed:
        investments_for_grouping = [item for item in _report_items(payload.get("time_investment")) if isinstance(item, dict)]
        app_hours = defaultdict(float)
        for investment in investments_for_grouping:
            app_name = _display_application(
                investment.get("application") or investment.get("app") or investment.get("app_or_topic")
            )
            app_hours[app_name.casefold()] += _number(investment.get("hours"))

        grouped = {}
        group_order = []
        for item in completed:
            app_name = _display_application(item.get("application") or item.get("app") or item.get("app_or_topic"))
            key = app_name.casefold()
            if key not in grouped:
                grouped[key] = {"name": app_name, "items": []}
                group_order.append(key)
            grouped[key]["items"].append(item)

        # Keep topic-level time visible under the relevant application even
        # when the model has no direct completion evidence for that topic.  It
        # is explicitly labelled as observation/pending confirmation rather
        # than being presented as a completed result.
        for investment in investments_for_grouping:
            app_name = _display_application(
                investment.get("application") or investment.get("app") or investment.get("app_or_topic")
            )
            key = app_name.casefold()
            if key not in grouped:
                grouped[key] = {"name": app_name, "items": []}
                group_order.append(key)
            work = _report_text(investment.get("main_work") or investment.get("work_item") or investment.get("title"))
            if not work:
                continue
            existing_work = {
                _report_text(row.get("main_work") or row.get("title") or row.get("name")).casefold()
                for row in grouped[key]["items"]
                if isinstance(row, dict)
            }
            if work.casefold() in existing_work:
                continue
            evidence = investment.get("evidence") or []
            evidence_text = "、".join(str(value) for value in evidence[:3]) if isinstance(evidence, list) else str(evidence)
            grouped[key]["items"].append(
                {
                    "main_work": work,
                    "detail": _report_text(investment.get("detail") or investment.get("summary"))
                    or "根据应用/窗口主题账本观察到该工作线索。",
                    "status": "仅观察到访问，待结合产出确认",
                    "evidence": [evidence_text] if evidence_text else [],
                }
            )
        for key in group_order:
            group = grouped[key]
            lines = []
            for index, item in enumerate(group["items"], 1):
                work = _report_text(item.get("main_work") or item.get("title") or item.get("name")) or "未命名主题"
                detail = _report_text(item.get("detail") or item.get("result") or item.get("summary"))
                status = _report_text(item.get("status"))
                evidence = item.get("evidence")
                evidence_text = ""
                if isinstance(evidence, list) and evidence:
                    evidence_text = "证据：" + "、".join(str(value) for value in evidence[:3])
                # Repeat the application in the item label as a searchable
                # fallback while the surrounding panel provides the visual
                # hierarchy.
                lines.append(f"**{index}. {group['name']} · {work}**")
                for value in (detail, status, evidence_text):
                    if value:
                        lines.append(f"   {value}")
            hours = app_hours.get(key, 0.0)
            heading = f"{_card_app_icon(group['name'])} {group['name']}"
            if hours:
                heading += f"：{hours:.2f}h"
            elements.append(_card_collapsible_panel(heading, "\n".join(lines)))
    else:
        elements.append(_card_text("- 暂无可核实的完成事项。"))

    investments = [item for item in _report_items(payload.get("time_investment")) if isinstance(item, dict)]
    total_hours = sum(_number(item.get("hours")) for item in investments)
    elements.extend([_card_divider(), _card_text("**⏱ 时间投入**  \n按“应用 · 主要工作”拆分；饼图展示各主题时长占比，下面保留精确小时数。")])
    if investments:
        chart = _time_investment_chart(investments)
        if chart:
            elements.append(chart)
        for item in investments[:12]:
            app = _display_application(item.get("application") or item.get("app") or item.get("app_or_topic"))
            work = _report_text(item.get("main_work") or item.get("work_item") or item.get("title"))
            label = " · ".join(part for part in (app, work) if part) or "未命名事项"
            hours = _number(item.get("hours"))
            share = hours / total_hours * 100 if total_hours else 0.0
            detail = _report_text(item.get("detail") or item.get("summary"))
            content = f"**{label}**  {hours:.2f}h ({share:.1f}%)\n{_card_bar(share)}"
            if detail:
                content += f"  {detail}"
            elements.append(_card_text(content, 1200))
        elements.append(_card_text(f"可观测应用/事项合计：**{total_hours:.2f}h**"))
    else:
        elements.append(_card_text("- 暂无可用的应用/事项时长。"))

    rhythm = payload.get("rhythm") if isinstance(payload.get("rhythm"), dict) else {}
    concentration = (activity_summary or {}).get("concentration") or {}
    active_hours = _number(rhythm.get("active_hours") or (activity_summary or {}).get("active_hours"))
    away_hours = _number(rhythm.get("away_hours") or (activity_summary or {}).get("away_hours"))
    tracked_hours = active_hours + away_hours
    active_share = active_hours / tracked_hours * 100 if tracked_hours else 0.0
    app_switch_rate = _number(concentration.get("application_switches_per_active_hour"))
    window_switch_rate = _number(concentration.get("window_switches_per_active_hour"))
    elements.extend([_card_divider(), _card_text(
        "**📊 工作节奏**  \n只保留可理解的活动时段、连续工作和切换干扰，不展示原始输入计数。"
    )])
    elements.append(_card_metric_row([
        ("有效活动", f"{active_hours:.2f}h"),
        ("活动占比", f"{active_share:.1f}%"),
        ("最长连续工作", f"{_number(concentration.get('longest_window_session_hours')):.2f}h"),
    ]))
    elements.append(_card_text(
        f"活动/离开  {_card_bar(active_share)}  {active_hours:.2f}h / {away_hours:.2f}h\n"
        f"切换干扰：每有效小时约 {app_switch_rate:.1f} 次应用切换、{window_switch_rate:.1f} 次窗口切换。"
    ))
    focus_sessions = concentration.get("top_focus_sessions") or []
    if focus_sessions:
        details = "、".join(
            f"{_report_text(item.get('title') or item.get('app'))}({_number(item.get('hours')):.2f}h)"
            for item in focus_sessions[:3]
        )
        elements.append(_card_text(f"主要连续工作段：{details}"))

    documents = payload.get("documents") if isinstance(payload.get("documents"), dict) else {}
    feishu_windows = _feishu_browser_windows(activity_summary)
    elements.extend([_card_divider(), _card_text("**📄 飞书文档与协作**  \n把飞书文档事件与 Edge/浏览器中的飞书页面证据放在一起，避免孤立解读。")])
    if feishu_windows:
        browser_text = "、".join(f"{item['title']}({_report_hours(item['hours'])})" for item in feishu_windows)
        elements.append(_card_text(f"**浏览器中的飞书相关活动**：{browser_text}"))
    else:
        elements.append(_card_text("浏览器记录中未识别到可明确关联飞书/文档的页面。"))
    if not document_events_received:
        elements.append(_card_text("监听状态：当天未收到文档变更事件；这不能证明当天没有文档变更。"))
    related_work = [_clean_document_work(item) for item in _report_items(documents.get("related_work"))]
    related_work = [value for value in related_work if value]
    if related_work:
        elements.append(_card_text("**与今日工作的关联**\n" + "\n".join(f"- {value}" for value in related_work[:8]), 7000))
    document_lines = []
    for label, key in (("新增", "added"), ("修改", "modified"), ("删除/回收", "deleted")):
        values = [_clean_item_text(item) for item in _report_items(documents.get(key))]
        values = [value for value in values if value and value != "暂无"]
        if values:
            document_lines.append(f"**{label}**：" + "；".join(values[:5]))
    elements.append(_card_text("\n".join(document_lines) if document_lines else "未收到可展示的文档事件明细。", 5000))

    papers = payload.get("papers") if isinstance(payload.get("papers"), dict) else {}
    paper_summary = _report_text(papers.get("summary"))
    paper_ideas = [_clean_item_text(item) for item in _report_items(papers.get("suggestions"))]
    paper_ideas = [value for value in paper_ideas if value]
    if paper_summary or paper_ideas:
        paper_content = "**📚 PaperRead 与研究线索**"
        if paper_summary:
            paper_content += f"\n{paper_summary}"
        if paper_ideas:
            paper_content += "\n" + "\n".join(f"- {item}" for item in paper_ideas[:3])
        elements.extend([_card_divider(), _card_text(paper_content, 7000)])

    tomorrow = payload.get("tomorrow_plan")
    if isinstance(tomorrow, dict):
        plan_values = tomorrow.get("tasks") or tomorrow.get("items") or tomorrow.get("plans") or []
        idea_values = tomorrow.get("idea_suggestions") or tomorrow.get("ideas") or []
    else:
        plan_values = tomorrow
        idea_values = payload.get("idea_suggestions") or []
    plans = [_clean_item_text(item) for item in _report_items(plan_values)]
    plans = [value for value in plans if value]
    ideas = [_clean_idea_text(item) for item in _report_items(idea_values)]
    ideas = [value for value in ideas if value]
    elements.extend([_card_divider(), _card_text("**明日计划**")])
    elements.append(
        _card_text("**计划事项**\n" + "\n".join(f"- {item}" for item in plans[:5]) if plans else "**计划事项**\n- 暂无")
    )
    elements.append(
        _card_text(
            "**Idea 建议（结合 PaperRead 与今日工作）**\n"
            + "\n".join(f"- {item}" for item in ideas[:5])
            if ideas
            else "**Idea 建议（结合 PaperRead 与今日工作）**\n- 暂无可核实的 idea 建议"
        )
    )

    risks = [_clean_item_text(item) for item in _report_items(payload.get("risks"))]
    risks = [value for value in risks if value]
    elements.extend([_card_divider(), _card_text("**风险或待跟进**\n" + "\n".join(f"- {item}" for item in risks[:5]) if risks else "**风险或待跟进**\n- 暂无")])

    questions = [_clean_item_text(item) for item in _report_items(payload.get("questions"))]
    questions = [value for value in questions if value]
    elements.extend([_card_divider(), _card_text("**💬 群聊问题解答**\n" + "\n".join(f"- {item}" for item in questions[:5]) if questions else "**💬 群聊问题解答**\n- 暂无")])

    return elements


def parse_timestamp(value):
    """Parse an ActivityWatch or Feishu timestamp into an aware datetime."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = str(value).strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def reporting_window(now=None, tz_name=None):
    """Return today's 01:00 and the current time in the configured timezone."""
    # The report is intentionally fixed to China Standard Time. The optional
    # argument remains useful for isolated unit tests, but production calls do
    # not read a configuration variable.
    tz = ZoneInfo(tz_name or DEFAULT_TIMEZONE)
    current = now.astimezone(tz) if now else datetime.now(tz)
    start = datetime.combine(current.date(), dt_time(1, 0), tzinfo=tz)
    if current < start:
        raise ValueError("当前时间早于当天 01:00，无法生成日报")
    return start, current


def overlap_seconds(timestamp, duration, start, end):
    """Clip one event to the reporting window and return its duration."""
    event_start = parse_timestamp(timestamp).astimezone(timezone.utc)
    event_end = event_start + timedelta(seconds=max(float(duration or 0), 0))
    left = max(event_start, start.astimezone(timezone.utc))
    right = min(event_end, end.astimezone(timezone.utc))
    return max((right - left).total_seconds(), 0.0)


def _flatten_numbers(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _flatten_numbers(child, f"{prefix}.{key}" if prefix else key)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield prefix.lower(), float(value)


def _sum_matching_numbers(data, aliases):
    aliases = tuple(alias.lower() for alias in aliases)
    total = 0.0
    for key, value in _flatten_numbers(data):
        normalized = re.sub(r"[^a-z0-9]", "", key)
        if any(re.sub(r"[^a-z0-9]", "", alias) in normalized for alias in aliases):
            total += value
    return total


class ActivityWatchClient:
    """Small REST client for the local ActivityWatch server."""

    def __init__(self, base_url=None, timeout=10):
        self.base_url = (base_url or os.getenv("ACTIVITYWATCH_URL", DEFAULT_AW_URL)).rstrip("/")
        self.timeout = timeout

    def _get(self, path, params=None):
        response = requests.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def buckets(self):
        data = self._get("/api/0/buckets")
        return data if isinstance(data, dict) else {}

    def events(self, bucket_id, start, end):
        params = {
            "starttime": start.astimezone(timezone.utc).isoformat(),
            "endtime": end.astimezone(timezone.utc).isoformat(),
        }
        return self._get(f"/api/0/buckets/{bucket_id}/events", params=params)

    def clear_between(self, start, end):
        """Delete only the ActivityWatch events consumed by this report."""
        if os.getenv("DAILY_REPORT_ACTIVITY_CLEAR_ENABLED", "1") != "1":
            return 0
        removed = 0
        for bucket_id, metadata in self.buckets().items():
            identity = f"{bucket_id} {metadata.get('name', '')} {metadata.get('type', '')}".lower()
            if not any(kind in identity for kind in ("window", "afk", "input", "focus")):
                continue
            payload = self.events(bucket_id, start, end)
            events = payload if isinstance(payload, list) else payload.get("events", [])
            for event in events:
                event_id = event.get("id")
                if event_id is None:
                    continue
                response = requests.delete(
                    f"{self.base_url}/api/0/buckets/{quote(str(bucket_id), safe='')}/events/{quote(str(event_id), safe='')}",
                    timeout=self.timeout,
                )
                response.raise_for_status()
                removed += 1
        return removed

    def summarize(self, start, end):
        apps = defaultdict(float)
        windows = defaultdict(float)
        afk_seconds = 0.0
        active_seconds = 0.0
        input_totals = {"keypresses": 0.0, "mouse_distance": 0.0, "mouse_clicks": 0.0}
        bucket_counts = {"window": 0, "afk": 0, "input": 0, "focus": 0}
        window_event_sequence = []
        focus_events = []
        active_intervals = []
        away_intervals = []

        for bucket_id, metadata in self.buckets().items():
            identity = f"{bucket_id} {metadata.get('name', '')} {metadata.get('type', '')}".lower()
            if "window" in identity:
                kind = "window"
            elif "afk" in identity:
                kind = "afk"
            elif "input" in identity:
                kind = "input"
            elif "focus" in identity:
                kind = "focus"
            else:
                continue
            bucket_counts[kind] += 1
            try:
                events = self.events(bucket_id, start, end)
            except requests.RequestException as exc:
                raise RuntimeError(f"读取 ActivityWatch bucket {bucket_id} 失败: {exc}") from exc
            for event in events if isinstance(events, list) else events.get("events", []):
                duration = overlap_seconds(event.get("timestamp"), event.get("duration", 0), start, end)
                data = event.get("data") or {}
                if kind == "window":
                    app = str(data.get("app") or data.get("program") or "Unknown")
                    title = str(data.get("title") or "").strip()
                    url = str(data.get("url") or "").strip()
                    apps[app] += duration
                    windows[(app, title, url)] += duration
                    if duration > 0:
                        window_event_sequence.append(
                            (parse_timestamp(event.get("timestamp")), app, title, url, duration)
                        )
                elif kind == "afk":
                    raw_start = parse_timestamp(event.get("timestamp"))
                    event_start = max(raw_start, start.astimezone(timezone.utc))
                    event_end = min(
                        raw_start + timedelta(seconds=max(float(event.get("duration", 0) or 0), 0.0)),
                        end.astimezone(timezone.utc),
                    )
                    if event_end <= event_start:
                        continue
                    if str(data.get("status", "")).lower() in {"afk", "away"}:
                        away_intervals.append((event_start, event_end))
                    else:
                        active_intervals.append(
                            (event_start, event_end)
                        )
                else:
                    input_totals["keypresses"] += _sum_matching_numbers(
                        data, ("keypress", "key_press", "keys", "presses", "num_keys", "key_count")
                    )
                    input_totals["mouse_distance"] += _sum_matching_numbers(
                        data, ("mouse_distance", "mouse_dist", "move_distance", "distance")
                    )
                    input_totals["mouse_clicks"] += _sum_matching_numbers(
                        data, ("mouse_click", "clicks", "num_clicks")
                    )
                if kind == "focus":
                    focus_events.append((event, duration, data))

        def merge_intervals(intervals):
            merged = []
            for left, right in sorted(intervals):
                if merged and left <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], right))
                else:
                    merged.append((left, right))
            return merged

        # AFK and active buckets can overlap when more than one watcher is
        # installed.  Merge them before calculating human-facing durations.
        active_intervals = merge_intervals(active_intervals)
        away_intervals = merge_intervals(away_intervals)
        active_seconds = sum((right - left).total_seconds() for left, right in active_intervals)
        afk_seconds = sum((right - left).total_seconds() for left, right in away_intervals)

        if focus_events:
            # Focus events intentionally overlap for the 60-second activity
            # window.  Collapse intervals per monitor/window before summing so
            # three monitors cannot make the report exceed elapsed time.
            focus_by_key = defaultdict(list)
            focus_all = []
            focus_counts = {"keypresses": 0.0, "mouse_clicks": 0.0}
            for event, duration, data in focus_events:
                timestamp = parse_timestamp(event.get("timestamp"))
                end_time = timestamp + timedelta(seconds=max(float(duration), 0.0))
                focus_all.append((timestamp, end_time))
                monitor = str(data.get("monitor") or "unknown")
                app = str(data.get("app") or data.get("window_title") or "Unknown")
                title = str(data.get("window_title") or "").strip()
                focus_by_key[(app, title, monitor)].append((timestamp, end_time))
                focus_counts["keypresses"] += float(data.get("keypresses") or 0)
                focus_counts["mouse_clicks"] += float(data.get("mouse_clicks") or 0)
            apps.clear()
            windows.clear()
            window_event_sequence = []
            active_seconds = sum((right - left).total_seconds() for left, right in merge_intervals(focus_all))
            for (app, title, monitor), intervals in focus_by_key.items():
                seconds = sum((right - left).total_seconds() for left, right in merge_intervals(intervals))
                apps[app] += seconds
                windows[(app, title, monitor)] += seconds
                for left, right in merge_intervals(intervals):
                    window_event_sequence.append((left, app, title, monitor, (right - left).total_seconds()))
            input_totals["keypresses"] = focus_counts["keypresses"]
            input_totals["mouse_clicks"] = focus_counts["mouse_clicks"]
        elif active_intervals and window_event_sequence:
            # aw-watcher-window can keep reporting the last foreground window
            # while the machine is away.  Clip those intervals to not-AFK time.
            clipped_sequence = []
            apps.clear()
            windows.clear()
            for timestamp, app, title, url, duration in window_event_sequence:
                left = timestamp
                right = timestamp + timedelta(seconds=duration)
                clipped = 0.0
                for active_left, active_right in active_intervals:
                    clipped += max(
                        0.0,
                        (min(right, active_right) - max(left, active_left)).total_seconds(),
                    )
                if clipped <= 0:
                    continue
                clipped_sequence.append((timestamp, app, title, url, clipped))
                apps[app] += clipped
                windows[(app, title, url)] += clipped
            window_event_sequence = clipped_sequence
            active_seconds = sum(
                max(0.0, (right - left).total_seconds())
                for left, right in active_intervals
            )

        # Guard against overlapping watcher classifications producing more
        # tracked time than the actual reporting window.
        window_seconds = max(
            (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds(),
            0.0,
        )
        if active_seconds + afk_seconds > window_seconds:
            afk_seconds = max(window_seconds - active_seconds, 0.0)

        def rows(mapping, fields):
            result = []
            for values, seconds in sorted(mapping.items(), key=lambda item: item[1], reverse=True):
                row = dict(zip(fields, values if isinstance(values, tuple) else (values,)))
                row["hours"] = round(seconds / 3600, 2)
                result.append(row)
            return result

        window_rows = rows(windows, ("app", "title", "context"))
        browser_tokens = (
            "edge",
            "msedge",
            "chrome",
            "firefox",
            "brave",
            "browser",
        )

        def is_browser_row(row):
            context = " ".join(
                str(row.get(key) or "")
                for key in ("app", "title", "context")
            ).casefold()
            return any(token in context for token in browser_tokens)

        browser_rows = [row for row in window_rows if is_browser_row(row)][:30]
        application_observed_seconds = sum(apps.values())
        application_breakdown = []
        for app_row in rows(apps, ("app",))[:30]:
            app = app_row["app"]
            app_windows = [row for row in window_rows if row["app"] == app][:5]
            application_breakdown.append(
                {
                    "app": app,
                    "hours": app_row["hours"],
                    "share_of_active_percent": round(
                        apps[app] / active_seconds * 100, 1
                    ) if active_seconds else 0.0,
                    "share_of_application_observed_percent": round(
                        apps[app] / application_observed_seconds * 100, 1
                    ) if application_observed_seconds else 0.0,
                    "top_windows": app_windows,
                }
            )
        window_event_sequence.sort(key=lambda item: item[0])

        def build_sessions(sequence, key_function):
            """Merge adjacent ActivityWatch events into observable work sessions."""
            sessions = []
            session_gap = timedelta(seconds=15)
            for timestamp, app, title, url, duration in sequence:
                end = timestamp + timedelta(seconds=duration)
                key = key_function(app, title, url)
                if (
                    sessions
                    and sessions[-1]["key"] == key
                    and timestamp <= sessions[-1]["end"] + session_gap
                ):
                    sessions[-1]["end"] = max(sessions[-1]["end"], end)
                    sessions[-1]["seconds"] = (
                        sessions[-1]["end"] - sessions[-1]["start"]
                    ).total_seconds()
                else:
                    sessions.append(
                        {
                            "key": key,
                            "start": timestamp,
                            "end": end,
                            "seconds": max(duration, 0.0),
                            "app": app,
                            "title": title,
                            "url": url,
                        }
                    )
            return sessions

        window_sessions = build_sessions(
            window_event_sequence,
            lambda app, title, url: (app, title, url),
        )
        application_sessions = build_sessions(
            window_event_sequence,
            lambda app, title, url: app,
        )

        window_session_seconds = [item["seconds"] for item in window_sessions]
        long_sessions = [item for item in window_sessions if item["seconds"] >= 600]
        short_sessions = [item for item in window_sessions if item["seconds"] < 120]
        observed_window_seconds = sum(window_session_seconds)

        def session_hours(value):
            return round(value / 3600, 2)

        top_focus_sessions = sorted(
            long_sessions,
            key=lambda item: item["seconds"],
            reverse=True,
        )[:5]
        concentration = {
            "window_sessions": len(window_sessions),
            "application_sessions": len(application_sessions),
            "average_window_session_hours": session_hours(
                sum(window_session_seconds) / len(window_session_seconds)
            ) if window_session_seconds else 0.0,
            "median_window_session_hours": session_hours(
                median(window_session_seconds)
            ) if window_session_seconds else 0.0,
            "longest_window_session_hours": session_hours(
                max(window_session_seconds)
            ) if window_session_seconds else 0.0,
            "sessions_at_least_10_minutes": len(long_sessions),
            "deep_focus_hours": session_hours(sum(item["seconds"] for item in long_sessions)),
            "short_session_share_percent": round(
                len(short_sessions) / len(window_sessions) * 100, 1
            ) if window_sessions else 0.0,
            "window_switches_per_active_hour": round(
                max(len(window_sessions) - 1, 0) / active_seconds * 3600, 1
            ) if active_seconds else 0.0,
            "application_switches_per_active_hour": round(
                max(len(application_sessions) - 1, 0) / active_seconds * 3600, 1
            ) if active_seconds else 0.0,
            "deep_focus_share_of_active_percent": round(
                sum(item["seconds"] for item in long_sessions) / active_seconds * 100, 1
            ) if active_seconds else 0.0,
            "top_focus_sessions": [
                {
                    "app": item["app"],
                    "title": item["title"],
                    "url": item["url"],
                    "monitor": item["url"],
                    "hours": session_hours(item["seconds"]),
                }
                for item in top_focus_sessions
            ],
            "observed_window_hours": session_hours(observed_window_seconds),
        }
        application_switches = 0
        window_switches = 0
        previous_app = None
        previous_window = None
        for _, app, title, url, _ in window_event_sequence:
            current_window = (app, title, url)
            if previous_app is not None and app != previous_app:
                application_switches += 1
            if previous_window is not None and current_window != previous_window:
                window_switches += 1
            previous_app = app
            previous_window = current_window

        tracked_seconds = active_seconds + afk_seconds
        active_hours = active_seconds / 3600
        rhythm = {
            "tracked_hours": round(tracked_seconds / 3600, 2),
            "active_share_percent": round(
                active_seconds / tracked_seconds * 100, 1
            ) if tracked_seconds else 0.0,
            "application_switches": application_switches,
            "window_switches": window_switches,
            "keypresses_per_active_hour": round(
                input_totals["keypresses"] / active_hours, 1
            ) if active_hours else 0.0,
            "mouse_clicks_per_active_hour": round(
                input_totals["mouse_clicks"] / active_hours, 1
            ) if active_hours else 0.0,
        }
        return {
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "active_hours": round(active_seconds / 3600, 2),
            "away_hours": round(afk_seconds / 3600, 2),
            "applications": rows(apps, ("app",))[:30],
            "application_breakdown": application_breakdown,
            "windows": window_rows[:80],
            "input": {key: round(value, 2) for key, value in input_totals.items()},
            "buckets_found": bucket_counts,
            "rhythm": rhythm,
            "concentration": concentration,
            "application_observed_hours": round(
                application_observed_seconds / 3600, 2
            ),
            "browser_windows": browser_rows,
        }


class FeishuClient:
    """Feishu client backed by the official SDK and tenant identity."""

    def __init__(self, app_id=None, app_secret=None):
        self.app_id = (
            app_id
            or os.getenv("DAILY_REPORT_FEISHU_APP_ID")
            or os.getenv("FEISHU_APP_ID")
            or ""
        ).strip()
        self.app_secret = (
            app_secret
            or os.getenv("DAILY_REPORT_FEISHU_APP_SECRET")
            or os.getenv("FEISHU_APP_SECRET")
            or ""
        ).strip()
        self._sdk = FeishuOpenAPIClient(self.app_id, self.app_secret)

    def token(self):
        """Compatibility hook; token caching is owned by the official SDK."""
        return "managed-by-lark-oapi"

    def _request(self, method, path, **kwargs):
        kwargs.pop("headers", None)
        json_body = kwargs.pop("json", None)
        params = kwargs.pop("params", None)
        if kwargs:
            raise TypeError(f"不支持的 Feishu SDK 请求参数: {', '.join(kwargs)}")
        try:
            return self._sdk.request(
                method,
                path,
                params=params,
                json_body=json_body,
            )
        except FeishuSDKError as exc:
            raise RuntimeError(str(exc)) from exc

    def list_messages(self, chat_id, start, end):
        messages = []
        page_token = None
        while True:
            params = {
                "container_id_type": "chat",
                "container_id": chat_id,
                "start_time": str(int(start.timestamp())),
                "end_time": str(int(end.timestamp())),
                "sort_type": "ByCreateTimeAsc",
                "page_size": "50",
            }
            if page_token:
                params["page_token"] = page_token
            data = self._request("GET", "/im/v1/messages", params=params)
            messages.extend(data.get("items", []))
            if not data.get("has_more") or not data.get("page_token"):
                break
            page_token = data["page_token"]
        return messages

    def list_document_blocks(self, document_id):
        """Read all blocks from a Feishu docx document."""
        blocks = []
        page_token = None
        while True:
            params = {"page_size": "100"}
            if page_token:
                params["page_token"] = page_token
            data = self._request(
                "GET",
                f"/docx/v1/documents/{document_id}/blocks",
                params=params,
            )
            blocks.extend(data.get("items", []))
            if not data.get("has_more") or not data.get("page_token"):
                break
            page_token = data["page_token"]
        return blocks

    def read_document_text(self, document_id):
        """Extract readable text from all blocks in a Feishu docx document."""
        pieces = []

        def walk(value):
            if isinstance(value, dict):
                text_run = value.get("text_run")
                if isinstance(text_run, dict) and isinstance(text_run.get("content"), str):
                    pieces.append(text_run["content"])
                for child in value.values():
                    if child is not text_run:
                        walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        for block in self.list_document_blocks(document_id):
            walk(block)
        return "\n".join(piece.strip() for piece in pieces if piece.strip())

    def read_document_link(self, url):
        """Read a docx/wiki URL and return its document text."""
        document_id = self.document_id_from_link(url)
        return self.read_document_text(document_id) if document_id else ""

    def document_id_from_link(self, url):
        """Resolve a Feishu docx/wiki URL to the underlying document token."""
        match = re.search(r"https?://[^/\s]+/(docx|wiki)/([A-Za-z0-9_-]+)", url)
        if not match:
            return ""
        link_type, token = match.groups()
        document_id = token
        if link_type == "wiki":
            data = self._request(
                "GET",
                "/wiki/v2/spaces/get_node",
                params={"token": token},
            )
            node = data.get("node", data)
            document_id = node.get("obj_token") or node.get("node_token", "")
        return document_id

    def get_or_create_month_document(self, root_node_token, title):
        """Find or create one docx Wiki node directly under the configured root."""
        node_data = self._request(
            "GET",
            "/wiki/v2/spaces/get_node",
            params={"token": root_node_token},
        )
        root = node_data.get("node", node_data)
        space_id = root.get("space_id", "")
        if not space_id:
            raise RuntimeError("日报根节点没有返回 space_id")
        page_token = None
        while True:
            params = {"parent_node_token": root_node_token, "page_size": 50}
            if page_token:
                params["page_token"] = page_token
            data = self._request("GET", f"/wiki/v2/spaces/{space_id}/nodes", params=params)
            for node in data.get("items", []):
                if node.get("title") == title:
                    document_id = node.get("obj_token", "")
                    if document_id:
                        return document_id
                    node_token = node.get("node_token", "")
                    if node_token:
                        resolved = self._request(
                            "GET",
                            "/wiki/v2/spaces/get_node",
                            params={"token": node_token},
                        )
                        resolved_node = resolved.get("node", resolved)
                        return resolved_node.get("obj_token") or node_token
            if not data.get("has_more") or not data.get("page_token"):
                break
            page_token = data["page_token"]

        data = self._request(
            "POST",
            f"/wiki/v2/spaces/{space_id}/nodes",
            json={
                "obj_type": "docx",
                "node_type": "origin",
                "parent_node_token": root_node_token,
                "title": title,
            },
        )
        node = data.get("node", data)
        document_id = node.get("obj_token", "")
        if document_id:
            return document_id
        node_token = node.get("node_token", "")
        if node_token:
            resolved = self._request(
                "GET",
                "/wiki/v2/spaces/get_node",
                params={"token": node_token},
            )
            resolved_node = resolved.get("node", resolved)
            return resolved_node.get("obj_token") or node_token
        return ""

    def append_document_blocks(self, document_id, blocks):
        """Append simple blocks to a docx document through the SDK transport."""
        import uuid

        prefix = f"_daily_{uuid.uuid4().hex[:12]}"
        children_id = [f"{prefix}_{index}" for index, _ in enumerate(blocks)]
        descendants = []
        for block_id, block in zip(children_id, blocks):
            item = dict(block)
            item["block_id"] = block_id
            item.setdefault("children", [])
            descendants.append(item)
        self._request(
            "POST",
            f"/docx/v1/documents/{document_id}/blocks/{document_id}/descendant",
            params={"document_revision_id": -1},
            json={
                "index": -1,
                "children_id": children_id,
                "descendants": descendants,
            },
        )
        return True

    def send_report(self, chat_id, payload, activity_summary=None, document_events_received=True):
        """Send the structured daily report as an SDK-backed native card."""
        report_date = _report_text(payload.get("date")) if isinstance(payload, dict) else ""
        title = f"工作日报 · {report_date}" if report_date else "工作日报"
        card = {
            "schema": "2.0",
            "config": {"width_mode": "fill"},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": title},
            },
            "body": {
                "elements": build_daily_report_card(
                    payload,
                    activity_summary=activity_summary,
                    document_events_received=document_events_received,
                )
            },
        }
        return self._sdk.send_interactive_card(chat_id, card)

    def send_text(self, chat_id, text):
        """Backward-compatible card send for callers that only have text."""
        report_text = str(text)
        first_line = report_text.splitlines()[0] if report_text.splitlines() else ""
        match = re.search(r"工作日报\s+(\d{4}-\d{2}-\d{2})", first_line)
        report_date = f" · {match.group(1)}" if match else ""
        return self._sdk.send_interactive_card(
            chat_id,
            {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": "blue",
                    "title": {"tag": "plain_text", "content": f"工作日报{report_date}"},
                },
                "elements": _build_text_report_card(report_text),
            },
        )

    def list_chats(self):
        """List groups visible to the bot, useful for discovering chat_id."""
        data = self._request("GET", "/im/v1/chats", params={"page_size": "100"})
        return data.get("items", [])


def message_text(message):
    """Extract readable text from Feishu text/post/card content."""
    content = message.get("body", {}).get("content") or message.get("content") or ""
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            return content

    pieces = []

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"text", "content"} and isinstance(child, str):
                    pieces.append(child)
                elif key not in {"url", "href", "image_key"}:
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str):
            pieces.append(value)

    walk(content)
    return " ".join(piece.strip() for piece in pieces if piece.strip())


def _sender_fields(message):
    sender = message.get("sender") or {}
    sender_id = str(
        sender.get("id")
        or sender.get("sender_id")
        or sender.get("app_id")
        or ""
    ).strip()
    sender_name = str(
        sender.get("name")
        or sender.get("sender_name")
        or sender.get("display_name")
        or ""
    ).strip()
    sender_type = str(sender.get("sender_type") or "unknown")
    return sender_id, sender_name, sender_type


def is_paperread_message(message, text=None):
    """Identify PaperRead bot messages using sender metadata and message shape."""
    text = text if text is not None else message_text(message)
    sender_id, sender_name, sender_type = _sender_fields(message)
    configured_ids = {
        value.strip()
        for value in (
            os.getenv("DAILY_REPORT_PAPERREAD_SENDER_ID", ""),
            os.getenv("DAILY_REPORT_PAPERREAD_APP_ID", ""),
        )
        if value.strip()
    }
    if sender_id and sender_id in configured_ids:
        return True
    if "paperread" in f"{sender_name} {sender_id}".lower():
        return True

    # Fallback for Feishu app messages whose sender name is not included in
    # the history response: PaperRead posts use a category counter and an
    # arXiv link, usually together with recommendation/methodology sections.
    looks_like_paperread = bool(
        re.search(r"(?im)^.{1,100}\s+-\s+\d+\s*/\s*\d+", text)
        and re.search(r"arxiv\.org/abs/", text, re.IGNORECASE)
        and ("推荐" in text or "方法论" in text or "锐评" in text)
    )
    return sender_type.lower() in {"app", "bot"} and looks_like_paperread


def normalize_messages(messages):
    normalized = []
    for message in messages:
        text = message_text(message)
        if not text:
            continue
        sender_id, sender_name, sender_type = _sender_fields(message)
        normalized.append(
            {
                "time": message.get("create_time") or message.get("update_time"),
                "sender_type": sender_type,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "is_paperread": is_paperread_message(message, text),
                "text": text,
            }
        )
    return normalized[-int(os.getenv("DAILY_REPORT_MAX_MESSAGES", "200")) :]


def split_chat_messages(messages):
    """Separate ordinary work chat from PaperRead's paper notifications."""
    paperread = [item for item in messages if item.get("is_paperread")]
    ordinary = [item for item in messages if not item.get("is_paperread")]
    return ordinary, paperread


def is_question_message(message):
    """Return whether the configured sender's message contains a question."""
    configured_name = os.getenv(
        "DAILY_REPORT_QUESTION_SENDER_NAME",
        "",
    ).strip().casefold()
    configured_id = os.getenv("DAILY_REPORT_QUESTION_SENDER_ID", "").strip()
    sender_name = str(message.get("sender_name", "")).strip().casefold()
    sender_id = str(message.get("sender_id", "")).strip()
    if not (
        (configured_name and sender_name == configured_name)
        or (configured_id and sender_id == configured_id)
    ):
        return False

    text = str(message.get("text", "")).strip()
    question_markers = (
        "?",
        "？",
        "请问",
        "怎么",
        "如何",
        "为什么",
        "是否",
        "能否",
        "有没有",
        "什么",
    )
    return any(marker in text for marker in question_markers)


def extract_question_messages(messages):
    """Extract Xu Junyi's questions for explicit answers in the report."""
    return [message for message in messages if is_question_message(message)]


def collect_knowledge_documents(client, paperread_messages):
    """Read linked Feishu knowledge-base documents referenced by PaperRead."""
    if os.getenv("DAILY_REPORT_KNOWLEDGE_BASE_ENABLED", "1") != "1":
        return []

    max_documents = int(os.getenv("DAILY_REPORT_MAX_KNOWLEDGE_DOCUMENTS", "8"))
    documents = []
    seen_urls = set()
    link_pattern = re.compile(r"https?://[^/\s]+/(?:docx|wiki)/[A-Za-z0-9_-]+")
    for message in paperread_messages:
        for url in link_pattern.findall(message.get("text", "")):
            if url in seen_urls:
                continue
            seen_urls.add(url)
            if len(documents) >= max_documents:
                return documents
            try:
                text = client.read_document_link(url)
            except Exception as exc:
                print(f"⚠️ 无法读取飞书知识库文档 {url}: {exc}")
                continue
            if text:
                documents.append({"url": url, "text": text[:12000]})
    return documents


def collect_document_activity(client, messages, start, end):
    """Read direct SDK listener events instead of scanning documents or folders."""
    if os.getenv("DAILY_REPORT_DOCUMENT_ACTIVITY_ENABLED", "1") != "1":
        return []
    store = DocumentEventStore()
    event_db_path = str(store.path)
    try:
        activity = store.between(
            start,
            end,
            operator_id=os.getenv("DAILY_REPORT_FEISHU_USER_OPEN_ID", "").strip(),
        )
    finally:
        store.close()
    print(
        f"飞书文档事件队列：{event_db_path}；时间范围内收到 {len(activity)} 条事件",
        flush=True,
    )
    max_events = int(os.getenv("DAILY_REPORT_MAX_DOCUMENT_ACTIVITY", "100"))
    web_base = os.getenv("FEISHU_WEB_BASE", "https://my.feishu.cn").rstrip("/")
    for item in activity[:max_events]:
        token = item.get("file_token", "")
        file_type = str(item.get("file_type", "")).lower()
        item["url"] = item.get("url") or (
            f"{web_base}/{'wiki' if file_type == 'wiki' else 'docx'}/{token}"
            if token else ""
        )
        item["version"] = ""
        item["creator_id"] = ""
    return activity[:max_events]


def clear_consumed_document_activity(end):
    """Remove document events consumed by a successfully completed report."""
    store = DocumentEventStore()
    try:
        return store.clear_through(end)
    finally:
        store.close()


def _bounded_context(items, formatter, max_chars=60000, item_max_chars=8000):
    """Format context for the LLM without exceeding a safe character budget."""
    chunks = []
    used = 0
    for item in items:
        chunk = formatter(item)[:item_max_chars]
        if not chunk:
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        chunk = chunk[:remaining]
        chunks.append(chunk)
        used += len(chunk)
    return "\n\n".join(chunks)


def parse_report_payload(response):
    """Extract the structured JSON object from the model's final message.

    PaperRead's analysis path accepts providers that wrap JSON in prose or a
    Markdown code fence. Keep the same tolerant extraction here while still
    rejecting responses that contain no JSON object at all.
    """
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise ValueError("日报模型没有返回 choices")
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("日报模型的最终 content 为空")
    decoder = json.JSONDecoder()
    candidates = []
    for index, character in enumerate(content):
        if character != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(content, index)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            candidates.append(payload)
    if candidates:
        expected_keys = {
            "date",
            "today_completed",
            "time_investment",
            "rhythm",
            "papers",
            "documents",
            "questions",
        }
        return max(
            enumerate(candidates),
            key=lambda item: (
                len(expected_keys.intersection(item[1])),
                item[0],
            ),
        )[1]
    raise ValueError("日报模型返回内容中没有可解析的 JSON 对象")


def _report_items(value):
    """Return report list fields in a renderer-friendly form."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _report_text(value):
    if isinstance(value, dict):
        return str(value.get("text") or value.get("title") or value.get("name") or "").strip()
    return str(value or "").strip()


def _report_hours(value):
    try:
        return f"{float(value):.2f}h"
    except (TypeError, ValueError):
        return "无法确定"


def _clean_item_text(item):
    if isinstance(item, dict):
        app = _report_text(item.get("application") or item.get("app"))
        title = _report_text(
            item.get("main_work") or item.get("title") or item.get("name") or item.get("topic")
        )
        detail = _report_text(item.get("detail") or item.get("summary") or item.get("answer"))
        heading = " · ".join(part for part in (app, title) if part)
        return "：".join(part for part in (heading, detail) if part)
    return _report_text(item)


def _clean_idea_text(item):
    """Render an idea with its evidence source and next validation step."""
    if not isinstance(item, dict):
        return _report_text(item)
    title = _report_text(item.get("title") or item.get("name") or item.get("topic"))
    detail = _report_text(item.get("detail") or item.get("summary"))
    source = _report_text(item.get("source") or item.get("evidence"))
    next_step = _report_text(item.get("next_step") or item.get("validation"))
    text = "：".join(part for part in (title, detail) if part)
    if source:
        text += f"（依据：{source}）"
    if next_step:
        text += f"；下一步：{next_step}"
    return text


def _clean_document_work(item):
    """Render an observed Feishu-document action with its source evidence."""
    if not isinstance(item, dict):
        return _report_text(item)
    title = _report_text(item.get("title") or item.get("name"))
    app = _report_text(item.get("application") or item.get("app"))
    action = _report_text(item.get("action") or item.get("operation"))
    detail = _report_text(item.get("detail") or item.get("summary"))
    evidence = _report_text(item.get("evidence"))
    prefix = " · ".join(part for part in (app, title, action) if part)
    text = "：".join(part for part in (prefix, detail) if part)
    if evidence:
        text += f"（证据：{evidence}）"
    return text


def _append_clean_items(lines, items, empty_text="暂无"):
    items = _report_items(items)
    if not items:
        lines.append(f"- {empty_text}")
        return
    for item in items:
        text = _clean_item_text(item)
        if text:
            lines.append(f"- {text}")


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _clean_window_title(value):
    """Remove browser tab-count and invisible-title noise before LLM analysis."""
    text = str(value or "")
    text = re.sub(r"[\u200b-\u200f\u202a-\u202e\ufeff]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.split(r"\s+和另外\s*\d+\s*个页面", text, maxsplit=1, flags=re.IGNORECASE)[0]
    text = re.sub(r"\s+-\s+(?:个人|工作|Microsoft Edge|Google Chrome).*$", "", text, flags=re.IGNORECASE)
    return text.strip(" -") or "未能从窗口标题确认主题"


def _display_application(value):
    """Normalize common ActivityWatch process names for human-facing reports."""
    raw = str(value or "Unknown").strip()
    aliases = {
        "msedge": "Microsoft Edge",
        "edge": "Microsoft Edge",
        "chrome": "Google Chrome",
        "firefox": "Firefox",
        "code": "Visual Studio Code",
        "explorer": "File Explorer",
        "feishu": "飞书",
    }
    return aliases.get(raw.casefold(), raw or "Unknown")


def _topic_label(app, title, url=""):
    """Map noisy window titles to a conservative, human-readable work topic."""
    text = f"{app} {title} {url}".casefold()
    if re.search(r"geocot|param[_ -]?geocot|geo\s*cot", text):
        return "GeoCoT / Param_GeoCoT"
    if re.search(r"\bagentos?\b|agentos|智能体|agent", text):
        return "Agent / AgentOs"
    if re.search(r"feishu|lark|飞书|docx|wiki", text):
        return "飞书文档与协作"
    if re.search(r"事件与回调|回调|权限管理|凭证|基础信息|developer|开发者后台", text):
        return "飞书开发者后台与权限配置"
    if re.search(r"\.env\b|配置文件|configuration|settings", text):
        return "项目配置与环境变量"
    cleaned = _clean_window_title(title)
    if cleaned != "未能从窗口标题确认主题":
        return cleaned[:100]
    return f"{app or 'Unknown'}（未能从窗口标题确认主题）"


def _topic_time_ledger(activity_summary):
    """Aggregate observable window time by application and conservative topic."""
    grouped = defaultdict(lambda: {"hours": 0.0, "evidence": []})
    for row in (activity_summary or {}).get("windows") or []:
        app = _display_application(row.get("app"))
        title = _clean_window_title(row.get("title") or row.get("context"))
        url = str(row.get("context") or row.get("url") or "").strip()
        topic = _topic_label(app, title, url)
        key = (app, topic)
        grouped[key]["hours"] += _number(row.get("hours"))
        if title and title not in grouped[key]["evidence"]:
            grouped[key]["evidence"].append(title)
    rows = []
    for (app, topic), item in sorted(grouped.items(), key=lambda pair: pair[1]["hours"], reverse=True):
        rows.append(
            {
                "application": app,
                "topic": topic,
                "hours": round(item["hours"], 2),
                "evidence": item["evidence"][:4],
            }
        )
    return rows[:40]


def _activity_context(activity_summary):
    """Build a topic-level evidence ledger instead of a raw telemetry dump."""
    total_hours = _number(activity_summary.get("application_observed_hours"))
    lines = [
        f"应用可观测时长合计：{total_hours:.2f}h。以下主题时长来自窗口标题/URL，只能证明访问或停留，不能单独证明完成成果。",
        "应用—主题时长账本（模型必须据此拆分 time_investment）：",
    ]
    ledger = _topic_time_ledger(activity_summary)
    for item in ledger:
        evidence = "、".join(item["evidence"]) or "无可读窗口标题"
        lines.append(
            f"- {item['application']} · {item['topic']}：{item['hours']:.2f}h；窗口证据：{evidence}"
        )
    if not ledger:
        lines.append("- 暂无可读的应用—主题窗口记录。")

    browser_windows = activity_summary.get("browser_windows") or []
    if browser_windows:
        lines.append("浏览器主题明细（优先用于今日完成、时间投入和飞书文档关联）：")
        for window in browser_windows[:30]:
            app = _display_application(window.get("app") or "Browser")
            title = _clean_window_title(window.get("title") or window.get("context"))
            topic = _topic_label(app, title, window.get("context"))
            lines.append(f"- {app} · {topic}：{_number(window.get('hours')):.2f}h；页面：{title}")
    else:
        lines.append("浏览器主题明细：当天没有识别到 Edge/Chrome/Firefox 等浏览器窗口。")
    return "\n".join(lines)


def enrich_report_payload(payload, activity_summary=None, document_activity=None):
    """Anchor model prose to measured topic durations and document events."""
    result = dict(payload or {})
    activity_summary = activity_summary or {}
    document_activity = document_activity or []

    ledger = _topic_time_ledger(activity_summary)
    if ledger:
        original = _report_items(result.get("time_investment"))
        normalized = []
        for row in ledger:
            app = row["application"]
            topic = row["topic"]
            candidate = None
            for item in original:
                if not isinstance(item, dict):
                    continue
                item_text = " ".join(
                    str(item.get(key) or "")
                    for key in ("application", "app", "app_or_topic", "main_work", "work_item", "title", "detail")
                ).casefold()
                if app.casefold() in item_text and (
                    topic.casefold() in item_text
                    or _topic_label(app, topic).casefold() in item_text
                ):
                    candidate = item
                    break
            evidence = row["evidence"]
            detail = _report_text((candidate or {}).get("detail") if candidate else "")
            if not detail:
                detail = f"窗口证据：{'、'.join(evidence[:3])}" if evidence else "未能从窗口标题确认更细节的工作内容"
            normalized.append(
                {
                    "application": app,
                    "main_work": topic,
                    "hours": row["hours"],
                    "detail": detail,
                    "evidence": evidence,
                }
            )
        result["time_investment"] = normalized

    documents = dict(result.get("documents") or {})
    related = list(_report_items(documents.get("related_work")))
    seen_related = {
        (_report_text(item.get("title")) if isinstance(item, dict) else _report_text(item)).casefold()
        for item in related
    }
    for event in document_activity:
        title = str(event.get("title") or event.get("file_token") or "未命名飞书文档").strip()
        key = title.casefold()
        if key in seen_related:
            continue
        seen_related.add(key)
        operation = str(event.get("operation") or "发生文档事件")
        operation = {
            "created": "新增",
            "modified": "修改",
            "updated": "更新",
            "deleted": "删除",
            "trashed": "回收",
        }.get(operation.casefold(), operation)
        related.append(
            {
                "title": title,
                "application": "飞书文档",
                "action": operation,
                "detail": "记录到文档事件；未读取或推断正文具体改动。",
                "evidence": str(event.get("event_time") or event.get("url") or ""),
            }
        )
    if related:
        documents["related_work"] = related[:12]
    if not _report_text(documents.get("collaboration_summary")):
        browser_rows = _feishu_browser_windows(activity_summary)
        if browser_rows:
            documents["collaboration_summary"] = (
                "浏览器记录显示当天访问了飞书相关页面："
                + "、".join(f"{row['title']}({_report_hours(row['hours'])})" for row in browser_rows[:5])
                + "；具体文档正文变化以文档事件为准。"
            )
    result["documents"] = documents
    return result


def render_report_payload(payload, activity_summary=None, document_events_received=True):
    """Render the compact report with only user-facing summary fields."""
    date = _report_text(payload.get("date")) or "工作日报"
    lines = [f"📅 工作日报 {date}", "", "✅ 今日完成"]
    _append_clean_items(lines, payload.get("today_completed"))

    lines.extend(["", "⏱ 时间投入"])
    investments = _report_items(payload.get("time_investment"))
    if investments:
        listed_hours = sum(
            _number(item.get("hours"))
            for item in investments
            if isinstance(item, dict)
        )
        if listed_hours:
            lines.append(
                f"- 可观测应用/事项合计：{listed_hours:.2f}h（以下百分比按此合计计算）"
            )
        for item in investments:
            if not isinstance(item, dict):
                lines.append(f"- {_report_text(item)}")
                continue
            label = " · ".join(
                part for part in (
                    _report_text(item.get("application") or item.get("app")),
                    _report_text(item.get("main_work") or item.get("work_item") or item.get("app_or_topic") or item.get("title") or item.get("name")),
                ) if part
            ) or "事项"
            hours = _number(item.get("hours"))
            share = hours / listed_hours * 100 if listed_hours else 0.0
            duration = _report_hours(hours)
            detail = _report_text(item.get("detail") or item.get("summary"))
            line = f"- {label}：{duration}（{share:.1f}%）"
            if detail:
                line += f"，{detail}"
            evidence = item.get("evidence")
            if isinstance(evidence, list) and evidence:
                line += f"；证据：{'、'.join(str(value) for value in evidence[:3])}"
            lines.append(line)
    else:
        lines.append("- 暂无")

    lines.extend(["", "📊 工作节奏"])
    rhythm = payload.get("rhythm") if isinstance(payload.get("rhythm"), dict) else {}
    activity_rhythm = (activity_summary or {}).get("rhythm") or {}
    concentration = (activity_summary or {}).get("concentration") or {}
    active_hours = _number(rhythm.get("active_hours") or (activity_summary or {}).get("active_hours"))
    away_hours = _number(rhythm.get("away_hours") or (activity_summary or {}).get("away_hours"))
    active_share = active_hours / (active_hours + away_hours) * 100 if active_hours + away_hours else 0.0
    lines.append(f"- 有效活动 {active_hours:.2f}h，离开 {away_hours:.2f}h，活动占比 {active_share:.1f}%")
    lines.append(
        f"- 最长连续工作 {_number(concentration.get('longest_window_session_hours')):.2f}h，"
        f"深度工作时段 {_number(concentration.get('deep_focus_hours')):.2f}h"
    )
    lines.append(
        f"- 切换干扰：每有效小时约 "
        f"{_number(concentration.get('application_switches_per_active_hour') or activity_rhythm.get('application_switches_per_active_hour')):.1f} 次应用切换、"
        f"{_number(concentration.get('window_switches_per_active_hour') or activity_rhythm.get('window_switches_per_active_hour')):.1f} 次窗口切换"
    )
    browser_windows = (activity_summary or {}).get("browser_windows") or []
    if browser_windows:
        browser_details = []
        for window in browser_windows[:5]:
            label = _clean_window_title(window.get("title") or window.get("app") or "未知窗口")
            browser_details.append(f"{label}({_number(window.get('hours')):.2f}h)")
        lines.append(f"- 浏览器窗口记录：{'、'.join(browser_details)}")

    lines.append("")
    concentration = payload.get("concentration") if isinstance(payload.get("concentration"), dict) else {}
    if concentration.get("summary"):
        lines.append(f"- 操作焦点：{_report_text(concentration.get('summary'))}")
    if concentration.get("findings"):
        _append_clean_items(lines, concentration.get("findings"))
    elif not concentration.get("summary"):
        lines.append("- 暂无")

    tomorrow = payload.get("tomorrow_plan")
    if isinstance(tomorrow, dict):
        plan_values = tomorrow.get("tasks") or tomorrow.get("items") or tomorrow.get("plans") or []
        idea_values = tomorrow.get("idea_suggestions") or tomorrow.get("ideas") or []
    else:
        plan_values = tomorrow
        idea_values = payload.get("idea_suggestions") or []
    lines.extend(["", "明日计划建议", "- 计划事项："])
    _append_clean_items(lines, plan_values)
    lines.append("- Idea 建议（结合 PaperRead 与今日工作）：")
    idea_items = _report_items(idea_values)
    if idea_items:
        for item in idea_items:
            text = _clean_idea_text(item)
            if text:
                lines.append(f"- {text}")
    else:
        lines.append("- 暂无可核实的 idea 建议")

    lines.extend(["", "风险或待跟进"])
    _append_clean_items(lines, payload.get("risks"))

    lines.extend(["", "📚 PaperRead 论文与未来研究建议"])
    papers = payload.get("papers") if isinstance(payload.get("papers"), dict) else {}
    if papers.get("summary"):
        lines.append(f"- 总结：{_report_text(papers.get('summary'))}")
    lines.append("- 未来建议：")
    _append_clean_items(lines, _report_items(papers.get("suggestions"))[:3])

    lines.extend(["", "📄 飞书文档变更"])
    documents = payload.get("documents") if isinstance(payload.get("documents"), dict) else {}
    collaboration_summary = _report_text(documents.get("collaboration_summary"))
    if collaboration_summary:
        lines.append(f"- 协作关联：{collaboration_summary}")
    related_work = [_clean_document_work(item) for item in _report_items(documents.get("related_work"))]
    related_work = [value for value in related_work if value]
    if related_work:
        lines.append("- 与今日工作的关联：")
        lines.extend(f"  - {value}" for value in related_work[:8])
    feishu_windows = _feishu_browser_windows(activity_summary)
    if feishu_windows:
        lines.append(
            "- 浏览器中的飞书相关活动："
            + "、".join(f"{item['title']}({_report_hours(item['hours'])})" for item in feishu_windows)
        )
    if not document_events_received:
        lines.append("- 监听状态：当天事件队列没有收到文档变更，不能据此确认当天没有变更；请检查监听器和飞书事件订阅。")
    for label, key in (("新增", "added"), ("修改", "modified"), ("删除/回收", "deleted")):
        lines.append(f"- {label}：")
        _append_clean_items(lines, documents.get(key))

    lines.extend(["", "💬 群聊问题解答"])
    _append_clean_items(lines, payload.get("questions"))
    return "\n".join(lines).strip()


def generate_report_payload(
    chat_messages,
    activity_summary,
    paperread_messages=None,
    knowledge_documents=None,
    document_activity=None,
    question_messages=None,
):
    """Generate the structured data used by both card and Wiki renderers."""
    from llm_client import llm

    paperread_messages = paperread_messages or []
    knowledge_documents = knowledge_documents or []
    document_activity = document_activity or []
    question_messages = question_messages or []
    chat_text = _bounded_context(
        chat_messages,
        lambda item: f"[{item['time']}] ({item['sender_type']}) {item['text']}",
    ) or "（今天没有可读的群聊文本消息）"
    paperread_text = _bounded_context(
        paperread_messages,
        lambda item: f"[{item['time']}] {item['text']}",
    ) or "（今天没有识别到 PaperRead 论文推送）"
    knowledge_text = _bounded_context(
        knowledge_documents,
        lambda item: f"来源：{item['url']}\n{item['text']}",
    ) or "（没有读取到 PaperRead 关联文档）"
    changes_text = _bounded_context(
        document_activity,
        lambda item: (
            f"[{item['event_time']}] {item['operation']} | {item.get('title') or item.get('file_token')} | "
            f"{item.get('file_type', '')} | {item.get('url', '')}"
        ),
    ) or "（时间范围内没有直接文档变更事件）"
    activity_context = _activity_context(activity_summary)
    model_activity = dict(activity_summary)
    model_activity.pop("input", None)
    model_rhythm = dict(model_activity.get("rhythm") or {})
    for noisy_key in (
        "keypresses",
        "mouse_clicks",
        "keypresses_per_active_hour",
        "mouse_clicks_per_active_hour",
    ):
        model_rhythm.pop(noisy_key, None)
    model_activity["rhythm"] = model_rhythm
    question_text = _bounded_context(
        question_messages,
        lambda item: f"[{item['time']}] {item['sender_name']}: {item['text']}",
    ) or "（没有需要回答的群聊问题）"
    prompt = f"""你是我的工作日报助手。请根据以下信息生成简洁、客观的中文日报。

日报时间范围：{activity_summary['period']['start']} 至 {activity_summary['period']['end']}。
不要把计划写成已完成事实，不要编造无法从输入判断的内容。

群聊记录：
{chat_text}

ActivityWatch 数据（已去除键盘/鼠标原始计数）：
{json.dumps(model_activity, ensure_ascii=False, indent=2)}

用于细节分析的应用和窗口记录：
{activity_context}

PaperRead 今日推送：
{paperread_text}

PaperRead 关联文档：
{knowledge_text}

飞书直接变更事件：
{changes_text}

待回答问题：
{question_text}

要求：
1. 报告要像工作汇报：先概括每个应用实际支持的工作主题、产出和当前状态，再列数据。不要把“打开窗口/停留页面”直接写成“完成调研/完成配置”。
2. 今日完成按应用组织，尤其详细拆解浏览器中的主题。对每个应用至少说明：主要主题、查阅/编辑/配置的具体对象、已形成的结果或仍待确认的部分。窗口标题只能证明访问或停留；只有群聊、文档变更或明确文本证据才能表述为已完成成果。
3. 时间投入必须逐条对应“应用 · 主题”，严格使用“应用—主题时长账本”拆分。例如 Microsoft Edge · GeoCoT、Microsoft Edge · Agent、Microsoft Edge · 飞书文档与协作，不得只输出 Microsoft Edge 总时长。hours 只能来自账本，detail 说明该主题对应的窗口证据。
4. 浏览器标题中的“和另外 N 个页面”、个人标记和不可见字符不是工作内容，忽略这些噪声；使用清理后的核心标题和主题分类。无法确认主题时写“未能从窗口标题确认主题”，不要臆测。
5. “工作节奏”只保留有效活动时长、离开时长、活动占比、最长连续工作段、深度工作时段和切换干扰的可读描述；不要输出键盘次数、鼠标点击次数或每次原始切换总数，也不要把这些信号解释为心理状态。
5. 所有时间使用小时 h。
6. PaperRead 只输出整体总结和最多 3 条未来研究建议，不逐篇复述论文。
7. 飞书文档部分必须填写 collaboration_summary 和 related_work：把文档事件（例如 SUM、FantasyVLN 的修改时间）与 Edge 中飞书页面主题对应起来，说明可确认的操作（查看、编辑、更新、权限配置等）及其证据；不能虚构正文改动。文档变更按新增、修改、删除/回收分类。如果“飞书直接变更事件”为空，必须写成“未收到事件，无法确认当天无变更”，不能直接断言“暂无变更”。
9. 可观测操作焦点只描述窗口、显示器和时间段，不描述心理状态，并合并到工作节奏。
10. 明日计划必须保留 tasks，并额外填写 idea_suggestions（1-5 条）。每条 idea 必须同时连接 PaperRead 今日推送中的具体方向、今日已完成工作或时间投入中的具体对象，并给出下一步可验证动作；如果证据不足，明确标注为待验证假设，不要编造论文结论。
11. 必须保留 risks 和 questions 字段，即使为空也返回空数组。
12. 只返回一个 JSON 对象，不要 Markdown 或解释文字。

JSON 格式：
{{"date":"YYYY-MM-DD","today_completed":[{{"application":"","main_work":"","detail":"","evidence":"","status":"已形成结果/仅观察到访问/待确认"}}],"time_investment":[{{"application":"","main_work":"","hours":0,"share_percent":0,"detail":"","evidence":[]}}],"rhythm":{{"active_hours":0,"away_hours":0,"active_share_percent":0,"summary":"","findings":[{{"title":"","detail":""}}]}},"concentration":{{"summary":"","findings":[{{"title":"","detail":""}}]}},"tomorrow_plan":{{"tasks":[{{"title":"","detail":""}}],"idea_suggestions":[{{"title":"","detail":"","source":"","next_step":""}}]}},"idea_suggestions":[],"risks":[{{"title":"","detail":""}}],"papers":{{"summary":"","suggestions":[{{"title":"","detail":""}}]}},"documents":{{"collaboration_summary":"","related_work":[{{"title":"","application":"","action":"","detail":"","evidence":""}}],"added":[],"modified":[],"deleted":[]}},"questions":[{{"title":"","answer":""}}]}}
"""
    response = llm.call(
        [{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        response_validator=parse_report_payload,
    )
    return enrich_report_payload(
        parse_report_payload(response),
        activity_summary=activity_summary,
        document_activity=document_activity,
    )


def generate_report(
    chat_messages,
    activity_summary,
    paperread_messages=None,
    knowledge_documents=None,
    document_activity=None,
    question_messages=None,
):
    """Return the textual form retained in the monthly Feishu Wiki report."""
    payload = generate_report_payload(
        chat_messages,
        activity_summary,
        paperread_messages,
        knowledge_documents,
        document_activity,
        question_messages,
    )
    return render_report_payload(
        payload,
        activity_summary=activity_summary,
        document_events_received=bool(document_activity),
    )


def _daily_report_blocks(date_text, report):
    marker = f"[DAILY_REPORT:{date_text}]"
    blocks = [
        {
            "block_type": 3,
            "heading1": {"elements": [{"text_run": {"content": f"工作日报 {date_text}"}}]},
        },
        {
            "block_type": 2,
            "text": {"elements": [{"text_run": {"content": marker}}]},
        },
    ]
    for line in report.splitlines():
        if not line.strip():
            continue
        blocks.append(
            {
                "block_type": 2,
                "text": {"elements": [{"text_run": {"content": line[:4000]}}]},
            }
        )
    return blocks


def write_monthly_report(client, report, report_date):
    """Append today's report to the one Wiki document for its calendar month."""
    root_token = os.getenv("DAILY_REPORT_FEISHU_WIKI_ROOT_NODE_TOKEN", "").strip()
    if not root_token:
        print("未配置 DAILY_REPORT_FEISHU_WIKI_ROOT_NODE_TOKEN，跳过月报写入。")
        return ""
    month_title = f"工作日报-{report_date[:7]}"
    document_id = client.get_or_create_month_document(root_token, month_title)
    if not document_id:
        raise RuntimeError(f"无法创建或定位月报文档: {month_title}")
    marker = f"[DAILY_REPORT:{report_date}]"
    if marker in client.read_document_text(document_id):
        return document_id
    client.append_document_blocks(document_id, _daily_report_blocks(report_date, report))
    return document_id


def run_report():
    start, end = reporting_window()
    chat_id = os.environ["DAILY_REPORT_FEISHU_CHAT_ID"]
    activity = ActivityWatchClient().summarize(start, end)
    client = FeishuClient()
    messages = normalize_messages(client.list_messages(chat_id, start, end))
    ordinary_messages, paperread_messages = split_chat_messages(messages)
    question_messages = extract_question_messages(ordinary_messages)
    ordinary_messages = [item for item in ordinary_messages if item not in question_messages]
    knowledge_documents = collect_knowledge_documents(client, paperread_messages)
    document_activity = collect_document_activity(client, messages, start, end)
    report_payload = generate_report_payload(
        ordinary_messages,
        activity,
        paperread_messages,
        knowledge_documents,
        document_activity,
        question_messages,
    )
    report = render_report_payload(
        report_payload,
        activity_summary=activity,
        document_events_received=bool(document_activity),
    )
    client.send_report(
        chat_id,
        report_payload,
        activity_summary=activity,
        document_events_received=bool(document_activity),
    )
    write_monthly_report(client, report, start.date().isoformat())
    clear_consumed_document_activity(end)
    try:
        cleared_activity = ActivityWatchClient().clear_between(start, end)
        if True:
            print(f"已清理 ActivityWatch 已读取事件: {cleared_activity} 条", flush=True)
    except requests.RequestException as exc:
        if True:
            print(f"ActivityWatch 事件清理失败（日报已完成，不影响下次运行）: {exc}", flush=True)
        print(f"日报已发送：{start.isoformat()} 至 {end.isoformat()}；群聊消息 {len(messages)} 条。")


def main():
    parser = argparse.ArgumentParser(description="Generate and send the daily work report")
    parser.add_argument("--preview", action="store_true", help="生成日报但不发送到飞书（仅本地调试）")
    parser.add_argument("--list-chats", action="store_true", help="列出机器人所在群聊及 chat_id")
    args = parser.parse_args()
    if args.list_chats:
        for chat in FeishuClient().list_chats():
            print(f"{chat.get('name', '(unnamed)')}\t{chat.get('chat_id', '')}")
        return
    start, end = reporting_window()
    chat_id = os.environ["DAILY_REPORT_FEISHU_CHAT_ID"]
    activity = ActivityWatchClient().summarize(start, end)
    client = FeishuClient()
    messages = normalize_messages(client.list_messages(chat_id, start, end))
    ordinary_messages, paperread_messages = split_chat_messages(messages)
    question_messages = extract_question_messages(ordinary_messages)
    ordinary_messages = [item for item in ordinary_messages if item not in question_messages]
    knowledge_documents = collect_knowledge_documents(client, paperread_messages)
    document_activity = collect_document_activity(client, messages, start, end)
    report_payload = generate_report_payload(
        ordinary_messages,
        activity,
        paperread_messages,
        knowledge_documents,
        document_activity,
        question_messages,
    )
    report = render_report_payload(
        report_payload,
        activity_summary=activity,
        document_events_received=bool(document_activity),
    )
    if args.preview:
        print(report)
    else:
        client.send_report(
            chat_id,
            report_payload,
            activity_summary=activity,
            document_events_received=bool(document_activity),
        )
        write_monthly_report(client, report, start.date().isoformat())
        clear_consumed_document_activity(end)
        try:
            cleared_activity = ActivityWatchClient().clear_between(start, end)
            print(f"已清理 ActivityWatch 已读取事件: {cleared_activity} 条", flush=True)
        except requests.RequestException as exc:
            print(f"ActivityWatch 事件清理失败（日报已完成，不影响下次运行）: {exc}", flush=True)
        print(f"日报已发送，读取群聊消息 {len(messages)} 条。")


if __name__ == "__main__":
    main()
