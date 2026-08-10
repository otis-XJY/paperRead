"""Generate a daily work report from Feishu chat and local ActivityWatch data.

This module is intentionally run by a scheduled GitHub Actions job on a
self-hosted Windows runner. It does not persist chat or ActivityWatch data.
"""

from __future__ import annotations

import argparse
import heapq
import json
import os
import re
import time as time_module
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
from daily_report_topics import TopicClassifier, aggregate_theme_events, build_theme_sessions

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


FEISHU_BASE = "https://open.feishu.cn/open-apis"
DEFAULT_AW_URL = "http://127.0.0.1:5600"
DEFAULT_TIMEZONE = "Asia/Shanghai"


def _progress(message, **fields):
    """Emit a timestamped, non-sensitive progress line for CI diagnostics."""
    if os.getenv("DAILY_REPORT_PROGRESS", "1").strip().lower() in {"0", "false", "off", "no"}:
        return
    suffix = " ".join(f"{key}={value}" for key, value in fields.items())
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S%z")
    print(f"[DailyReport {stamp}] {message}" + (f" | {suffix}" if suffix else ""), flush=True)


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
    """Build a native CardKit pie chart for application time totals."""
    application_hours = defaultdict(float)
    for item in investments:
        app = _display_application(item.get("application") or item.get("app") or item.get("app_or_topic"))
        hours = _number(item.get("hours"))
        if app and hours > 0:
            application_hours[app] += hours
    values = []
    for app, hours in sorted(application_hours.items(), key=lambda pair: pair[1], reverse=True)[:6]:
        if hours > 0:
            values.append({"application": app[:80], "hours": round(hours, 2)})
    if not values:
        return None
    return {
        "tag": "chart",
        "aspect_ratio": "16:9",
        "color_theme": "brand",
        "chart_spec": {
            "type": "pie",
            "data": [{"id": "time_investment", "values": values}],
            "categoryField": "application",
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
    themes = [item for item in _report_items(payload.get("themes")) if isinstance(item, dict)]
    if not themes:
        themes = [
            {
                "theme_name": _report_text(item.get("main_work") or item.get("title") or item.get("name")),
                "summary": _report_text(item.get("detail") or item.get("summary")),
                # Legacy payloads without an explicit status came from the
                # model's completed-items field; treat a detailed item as
                # probable, while keeping bare observations out of the core.
                "status": item.get("status") or (
                    "probable" if _report_text(item.get("detail") or item.get("summary")) else "observed"
                ),
            }
            for item in completed
        ]
    elements.append(_card_text("**✅ 今日核心推进**  \n按研究主题合并跨应用活动；仅窗口访问不会自动写成完成成果。"))
    shown = 0
    for item in themes:
        status = str(item.get("status") or "observed").casefold()
        if status not in {"confirmed", "probable", "已形成结果", "已形成结果/仅观察到访问/待确认"}:
            continue
        title = _report_text(item.get("theme_name") or item.get("name") or item.get("main_work")) or "未命名主题"
        detail = _report_text(item.get("summary") or item.get("detail") or item.get("result"))
        outputs = _report_text(item.get("outputs"))
        next_step = _report_text(item.get("next_step"))
        duration = _format_duration(item.get("duration_seconds") or _number(item.get("hours")) * 3600)
        text = f"**{title}**（投入 {duration}）"
        for value in (detail, outputs, f"当前状态：{status}" if status else "", f"下一步：{next_step}" if next_step else ""):
            if value:
                text += f"\n{value}"
        elements.append(_card_collapsible_panel(title, text))
        shown += 1
        if shown >= 5:
            break
    if not shown:
        elements.append(_card_text("- 暂无可核实的核心推进；窗口访问记录保留在内部账本。"))

    investments = [item for item in _report_items(payload.get("time_investment")) if isinstance(item, dict)]
    theme_rows = [item for item in _report_items(payload.get("themes")) if isinstance(item, dict)]
    if not theme_rows:
        grouped = defaultdict(lambda: {"theme_name": "", "duration_seconds": 0.0, "application_breakdown": []})
        for item in investments:
            theme = _report_text(item.get("main_work") or item.get("work_item") or item.get("title") or item.get("app_or_topic")) or "未归因活动"
            grouped[theme]["theme_name"] = theme
            grouped[theme]["duration_seconds"] += _number(item.get("hours")) * 3600
            grouped[theme]["application_breakdown"].append(
                {"application": _display_application(item.get("application") or item.get("app")), "hours": _number(item.get("hours"))}
            )
        theme_rows = list(grouped.values())
    application_time = [
        item for item in _report_items(payload.get("application_time")) if isinstance(item, dict)
    ]
    if not application_time:
        application_time = [
            {"application": item.get("application"), "hours": item.get("hours")}
            for item in investments
        ]
    total_hours = sum(_number(item.get("hours")) for item in application_time)
    elements.extend([_card_divider(), _card_text("**⏱ 研究主题时间**  \n主题为主、应用为辅；饼图单独展示各应用的归因时间。")])
    if theme_rows:
        chart = _time_investment_chart(application_time)
        if chart:
            elements.append(chart)
        for item in theme_rows[:8]:
            theme = _report_text(item.get("theme_name") or item.get("name")) or "未归因活动"
            seconds = _number(item.get("duration_seconds"))
            hours = _number(item.get("hours")) or seconds / 3600
            apps = []
            for app_row in item.get("application_breakdown") or []:
                app = _display_application(app_row.get("application") or app_row.get("app"))
                app_hours = _number(app_row.get("hours"))
                if app and app_hours:
                    apps.append(f"{app} · {theme} {_format_duration(app_hours * 3600)}")
            detail = f"应用明细：{'、'.join(apps)}" if apps else ""
            content = f"**{theme}**：{_format_duration(seconds or hours * 3600)}"
            if detail:
                content += f"\n{detail}"
            elements.append(_card_text(content, 1800))
        elements.append(_card_text("应用归因合计：" + _format_duration(total_hours * 3600)))
        elements.append(_card_text(f"可观测应用/事项合计：**{total_hours:.2f}h**"))
    else:
        elements.append(_card_text("- 暂无可用的应用/事项时长。"))

    rhythm = payload.get("rhythm") if isinstance(payload.get("rhythm"), dict) else {}
    concentration = (activity_summary or {}).get("concentration") or {}
    theme_focus = (activity_summary or {}).get("theme_focus") or {}
    focus_source = theme_focus.get("source")
    focus_source_text = (
        "切换判定结合鼠标所在显示器、前台窗口和输入事件。"
        if focus_source == "cursor_position_and_input_focus"
        else "切换判定基于前台窗口活动。"
    )
    active_hours = _number(rhythm.get("active_hours") or (activity_summary or {}).get("active_hours"))
    away_hours = _number(rhythm.get("away_hours") or (activity_summary or {}).get("away_hours"))
    tracked_hours = active_hours + away_hours
    active_share = active_hours / tracked_hours * 100 if tracked_hours else 0.0
    elements.extend([_card_divider(), _card_text(
        "**🎯 专注情况**  \n基于有效活动与主题级会话估计，不直接测量心理状态。\n"
        + focus_source_text
    )])
    elements.append(_card_metric_row([
        ("有效活动", f"{active_hours:.2f}h"),
        ("主题会话中位数", _format_duration(theme_focus.get("median_focus_session_seconds"))),
        ("最长连续工作（主题）", _format_duration(theme_focus.get("longest_focus_session_seconds"))),
    ]))
    elements.append(_card_text(
        f"活动/离开  {_card_bar(active_share)}  {active_hours:.2f}h / {away_hours:.2f}h\n"
        f"25 分钟以上专注占比 { _number(theme_focus.get('focus_over_25m_share_percent')):.0f}%；"
        f"有效主题切换 {int(_number(theme_focus.get('effective_theme_switches')))} 次；"
        f"切换间隔中位数 {_format_duration(theme_focus.get('median_effective_switch_interval_seconds'))}。"
    ))
    focus_sessions = theme_focus.get("top_focus_sessions") or []
    if focus_sessions:
        details = "、".join(
            f"{_report_text(item.get('theme_name'))}({_format_duration(item.get('seconds'))})"
            for item in focus_sessions[:3]
        )
        elements.append(_card_text(f"最长主题会话：{details}"))

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
    paper_status = payload.get("paperread_status") if isinstance(payload.get("paperread_status"), dict) else {}
    if paper_summary or paper_ideas or paper_status:
        paper_content = "**📚 PaperRead 与研究线索**"
        if paper_status.get("detail"):
            paper_content += f"\n{paper_status.get('detail')}"
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


class _ActivityWatchTransport:
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
            _progress("ActivityWatch：事件清理已禁用")
            return 0
        max_seconds = max(float(os.getenv("DAILY_REPORT_ACTIVITY_CLEAR_MAX_SECONDS", "30")), 1.0)
        deadline = time_module.monotonic() + max_seconds
        _progress("ActivityWatch：开始清理已读取事件", max_seconds=max_seconds)
        removed = 0
        for bucket_id, metadata in self.buckets().items():
            identity = f"{bucket_id} {metadata.get('name', '')} {metadata.get('type', '')}".lower()
            if not any(kind in identity for kind in ("window", "afk", "input", "focus")):
                continue
            payload = self.events(bucket_id, start, end)
            events = payload if isinstance(payload, list) else payload.get("events", [])
            for event in events:
                if time_module.monotonic() >= deadline:
                    _progress("ActivityWatch：清理达到时间上限，停止继续删除", removed=removed)
                    return removed
                event_id = event.get("id")
                if event_id is None:
                    continue
                try:
                    response = requests.delete(
                        f"{self.base_url}/api/0/buckets/{quote(str(bucket_id), safe='')}/events/{quote(str(event_id), safe='')}",
                        timeout=min(self.timeout, 3),
                    )
                    response.raise_for_status()
                except requests.RequestException as exc:
                    _progress("ActivityWatch：单条事件删除失败，停止清理", removed=removed, error=type(exc).__name__)
                    return removed
                removed += 1
                if removed % 100 == 0:
                    _progress("ActivityWatch：事件清理进度", removed=removed)
        _progress("ActivityWatch：事件清理完成", removed=removed)
        return removed


def _attribute_window_sequence(sequence):
    """Attribute each real interval to at most one foreground window.

    ActivityWatch can emit overlapping windows for multiple monitors.  Split
    at event boundaries and select one candidate for each interval; this
    keeps screen exposure separate from reportable active time.
    """
    if not sequence:
        return []
    attribution_started = time_module.monotonic()
    _progress("ActivityWatch: 开始多显示器时间归因", events=len(sequence))

    # Sweep interval boundaries once.  The old implementation scanned the
    # full event list for every boundary (O(n²)); a busy ActivityWatch day can
    # contain tens of thousands of samples.  The heap keeps the most recent
    # active foreground event available in O(log n) per start/end event.
    started = defaultdict(list)
    ended = defaultdict(list)
    records = []
    boundaries = set()
    for index, item in enumerate(sequence):
        timestamp, app, title, url, raw_duration = item
        duration = max(float(raw_duration), 0.0)
        if duration <= 0:
            continue
        end = timestamp + timedelta(seconds=duration)
        records.append((index, timestamp, end, app, title, url))
        started[timestamp].append(index)
        ended[end].append(index)
        boundaries.add(timestamp)
        boundaries.add(end)
    if not records:
        return []

    record_by_index = {
        index: (timestamp, end, app, title, url)
        for index, timestamp, end, app, title, url in records
    }
    active = set()
    # (-timestamp, -index, index) makes the latest event the winner; index is
    # a deterministic tie-breaker when two monitors report the same timestamp.
    heap = []
    attributed = []
    ordered_boundaries = sorted(boundaries)
    for boundary_index, (left, right) in enumerate(zip(ordered_boundaries, ordered_boundaries[1:]), 1):
        for index in ended.get(left, ()):
            active.discard(index)
        for index in started.get(left, ()):
            timestamp = record_by_index[index][0]
            active.add(index)
            heapq.heappush(heap, (-timestamp.timestamp(), -index, index))
        while heap and heap[0][2] not in active:
            heapq.heappop(heap)
        seconds = (right - left).total_seconds()
        if seconds <= 0 or not heap:
            continue
        chosen_index = heap[0][2]
        _timestamp, _end, app, title, url = record_by_index[chosen_index]
        attributed.append((left, app, title, url, seconds))
        if boundary_index % 10000 == 0:
            _progress("ActivityWatch: 多显示器时间归因进度", boundaries=boundary_index, total_boundaries=len(ordered_boundaries))

    merged = []
    for item in attributed:
        if merged and merged[-1][1:4] == item[1:4] and merged[-1][0] + timedelta(seconds=merged[-1][4]) == item[0]:
            previous = merged[-1]
            merged[-1] = (previous[0], previous[1], previous[2], previous[3], previous[4] + item[4])
        else:
            merged.append(item)
    _progress(
        "ActivityWatch: 多显示器时间归因完成",
        events=len(sequence),
        boundaries=len(ordered_boundaries),
        attributed_segments=len(merged),
        elapsed_seconds=round(time_module.monotonic() - attribution_started, 2),
    )
    return merged


class ActivityWatchClient(_ActivityWatchTransport):
    def summarize(self, start, end):
        _progress("ActivityWatch: 开始读取活动数据", start=start.isoformat(), end=end.isoformat())
        apps = defaultdict(float)
        windows = defaultdict(float)
        afk_seconds = 0.0
        active_seconds = 0.0
        input_totals = {"keypresses": 0.0, "mouse_distance": 0.0, "mouse_clicks": 0.0}
        bucket_counts = {"window": 0, "afk": 0, "input": 0, "focus": 0}
        window_event_sequence = []
        focus_window_sequence = []
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
            _progress("ActivityWatch: 读取 bucket", kind=kind)
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
                    if duration > 0:
                        raw_start = parse_timestamp(event.get("timestamp")).astimezone(timezone.utc)
                        clipped_start = max(raw_start, start.astimezone(timezone.utc))
                        clipped_end = min(
                            raw_start + timedelta(seconds=max(float(event.get("duration", 0) or 0), 0.0)),
                            end.astimezone(timezone.utc),
                        )
                        clipped_duration = max((clipped_end - clipped_start).total_seconds(), 0.0)
                        window_event_sequence.append(
                            (clipped_start, app, title, url, clipped_duration)
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
            focus_window_sequence = []
            focus_counts = {"keypresses": 0.0, "mouse_clicks": 0.0}
            for event, duration, data in focus_events:
                raw_timestamp = parse_timestamp(event.get("timestamp"))
                raw_end_time = raw_timestamp + timedelta(seconds=max(float(duration), 0.0))
                timestamp = max(raw_timestamp, start.astimezone(timezone.utc))
                end_time = min(raw_end_time, end.astimezone(timezone.utc))
                if end_time <= timestamp:
                    continue
                focus_all.append((timestamp, end_time))
                monitor = str(data.get("monitor") or "unknown")
                # The focus watcher derives monitor from the actual cursor
                # position. Keep the coordinates as a deterministic fallback
                # when Windows returns an unknown monitor rectangle.
                if monitor == "unknown" and data.get("cursor_x") is not None and data.get("cursor_y") is not None:
                    monitor = f"cursor:{data.get('cursor_x')},{data.get('cursor_y')}"
                app = str(data.get("app") or data.get("window_title") or "Unknown")
                title = str(data.get("window_title") or "").strip()
                focus_by_key[(app, title, monitor)].append((timestamp, end_time))
                focus_counts["keypresses"] += float(data.get("keypresses") or 0)
                focus_counts["mouse_clicks"] += float(data.get("mouse_clicks") or 0)
                focus_window_sequence.append(
                    (timestamp, app, title, monitor, (end_time - timestamp).total_seconds())
                )
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

        screen_exposure_seconds = sum(max(float(item[4]), 0.0) for item in window_event_sequence)
        window_event_sequence = _attribute_window_sequence(window_event_sequence)
        if focus_window_sequence:
            focus_window_sequence = _attribute_window_sequence(focus_window_sequence)
        attributed_active_seconds = sum(max(float(item[4]), 0.0) for item in window_event_sequence)
        report_window_seconds = max(
            (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds(),
            0.0,
        )
        if attributed_active_seconds:
            active_seconds = max(
                active_seconds,
                min(attributed_active_seconds, report_window_seconds),
            )
        apps.clear()
        windows.clear()
        for timestamp, app, title, url, duration in window_event_sequence:
            apps[app] += duration
            windows[(app, title, url)] += duration

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
                row["seconds"] = round(float(seconds), 3)
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
        focus_sequence_for_themes = focus_window_sequence or window_event_sequence
        theme_events = [
            {
                "timestamp": timestamp.isoformat(),
                "app": app,
                "title": title,
                "url": url,
                "duration": duration,
            }
            for timestamp, app, title, url, duration in focus_sequence_for_themes
        ]
        theme_sessions = build_theme_sessions(
            theme_events,
            switch_min_seconds=float(os.getenv("DAILY_REPORT_SWITCH_MIN_SECONDS", "30")),
            session_gap_seconds=float(os.getenv("DAILY_REPORT_SESSION_GAP_SECONDS", "180")),
        )
        theme_session_seconds = [item["seconds"] for item in theme_sessions]
        deep_focus_threshold = float(os.getenv("DAILY_REPORT_DEEP_FOCUS_MINUTES", "25")) * 60
        deep_focus_seconds = sum(item["seconds"] for item in theme_sessions if item["seconds"] >= deep_focus_threshold)
        switch_intervals = [
            max((right["start"] - left["end"]).total_seconds(), 0.0)
            for left, right in zip(theme_sessions, theme_sessions[1:])
        ]
        theme_focus = {
            "source": "cursor_position_and_input_focus" if focus_window_sequence else "foreground_window",
            "median_focus_session_seconds": round(median(theme_session_seconds), 1) if theme_session_seconds else 0.0,
            "average_focus_session_seconds": round(sum(theme_session_seconds) / len(theme_session_seconds), 1) if theme_session_seconds else 0.0,
            "longest_focus_session_seconds": round(max(theme_session_seconds), 1) if theme_session_seconds else 0.0,
            "focus_over_25m_seconds": round(deep_focus_seconds, 1),
            "focus_over_25m_share_percent": round(deep_focus_seconds / active_seconds * 100, 1) if active_seconds else 0.0,
            "effective_theme_switches": max(len(theme_sessions) - 1, 0),
            "median_effective_switch_interval_seconds": round(median(switch_intervals), 1) if switch_intervals else 0.0,
            "top_focus_sessions": [
                {
                    "theme_name": item["theme_name"],
                    "applications": item.get("applications", []),
                    "seconds": round(item["seconds"], 1),
                }
                for item in sorted(theme_sessions, key=lambda row: row["seconds"], reverse=True)[:5]
            ],
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
        result = {
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
            "theme_focus": theme_focus,
            "theme_events": theme_events,
            "application_observed_hours": round(
                application_observed_seconds / 3600, 2
            ),
            "screen_exposure_seconds": round(screen_exposure_seconds, 3),
            "attributed_active_seconds": round(attributed_active_seconds, 3),
            "attributed_active_hours": round(attributed_active_seconds / 3600, 2),
            "browser_windows": browser_rows,
        }
        _progress(
            "ActivityWatch: 活动数据读取完成",
            active_hours=result["active_hours"],
            attributed_hours=result["attributed_active_hours"],
            screen_exposure_hours=round(result["screen_exposure_seconds"] / 3600, 2),
            window_rows=len(window_rows),
        )
        return result


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
        _progress("飞书消息：开始读取群聊历史", start=start.isoformat(), end=end.isoformat())
        messages = []
        page_token = None
        page_number = 0
        while True:
            page_number += 1
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
            page_items = data.get("items", [])
            messages.extend(page_items)
            _progress("飞书消息：收到分页", page=page_number, page_items=len(page_items), total=len(messages))
            if not data.get("has_more") or not data.get("page_token"):
                break
            page_token = data["page_token"]
        _progress("飞书消息：群聊历史读取完成", total=len(messages), pages=page_number)
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


def extract_message_content(message):
    """Extract text and linked URLs from Feishu text/post/card payloads.

    Feishu may return ``body.content`` as JSON, while links can be stored in
    ``href``/``url`` fields rather than in the visible text.  Keeping both
    fields lets PaperRead detection work for structured cards as well as
    ordinary text messages.
    """
    content = message.get("body", {}).get("content") or message.get("content") or ""
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            return {"text": content, "urls": []}

    pieces = []
    urls = []

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                key_lower = str(key).casefold()
                if key_lower in {"url", "href", "link"} and isinstance(child, str):
                    if child.startswith(("http://", "https://")) and child not in urls:
                        urls.append(child)
                    continue
                if key_lower in {"text", "content"} and isinstance(child, str):
                    pieces.append(child)
                elif key_lower not in {"image_key", "token", "file_token"}:
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str):
            pieces.append(value)

    walk(content)
    return {
        "text": " ".join(piece.strip() for piece in pieces if piece.strip()),
        "urls": urls,
    }


def message_text(message):
    """Extract readable text from Feishu text/post/card content."""
    return extract_message_content(message)["text"]


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


def is_paperread_message(message, text=None, urls=None):
    """Identify PaperRead bot messages using sender metadata and message shape."""
    extracted = extract_message_content(message) if text is None or urls is None else None
    text = text if text is not None else extracted["text"]
    urls = urls if urls is not None else extracted["urls"]
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

    # Structured card links may carry the arXiv URL only in href/url fields.
    combined = f"{text} {' '.join(urls)}"
    if sender_type.lower() in {"app", "bot"} and re.search(
        r"arxiv\.org/(?:abs|pdf)/", combined, re.IGNORECASE
    ) and re.search(r"paper\s*read|recommend|method|review|璁烘枃", combined, re.IGNORECASE):
        return True

    # Fallback for Feishu app messages whose sender name is not included in
    # the history response: PaperRead posts use a category counter and an
    # arXiv link, usually together with recommendation/methodology sections.
    looks_like_paperread = bool(
        re.search(r"(?im)^.{1,100}\s+-\s+\d+\s*/\s*\d+", text)
        and re.search(r"arxiv\.org/(?:abs|pdf)/", f"{text} {' '.join(urls)}", re.IGNORECASE)
        and ("推荐" in text or "方法论" in text or "锐评" in text)
    )
    return sender_type.lower() in {"app", "bot"} and looks_like_paperread


def normalize_messages(messages):
    normalized = []
    for message in messages:
        extracted = extract_message_content(message)
        text = extracted["text"]
        urls = extracted["urls"]
        if not text and not urls:
            continue
        sender_id, sender_name, sender_type = _sender_fields(message)
        normalized.append(
            {
                "time": message.get("create_time") or message.get("update_time"),
                "sender_type": sender_type,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "is_paperread": is_paperread_message(message, text, urls),
                "text": text,
                "urls": urls,
            }
        )
    return normalized


def split_chat_messages(messages):
    """Separate ordinary work chat from PaperRead's paper notifications."""
    paperread = [item for item in messages if item.get("is_paperread")]
    ordinary = [item for item in messages if not item.get("is_paperread")]
    ordinary_limit = int(os.getenv("DAILY_REPORT_MAX_CHAT_MESSAGES", os.getenv("DAILY_REPORT_MAX_MESSAGES", "200")))
    paperread_limit = int(os.getenv("DAILY_REPORT_MAX_PAPERREAD_MESSAGES", "100"))
    return ordinary[-ordinary_limit:], paperread[-paperread_limit:]


def paperread_diagnostics(raw_messages, paperread_messages, source_chat_id="", read_error=False):
    """Return a small, truthful status object for the PaperRead section."""
    raw_messages = raw_messages or []
    paperread_messages = paperread_messages or []
    if read_error:
        status = "read_error"
        detail = "独立 PaperRead 群聊读取失败，不能据此判断当天是否有推送"
    elif paperread_messages:
        status = "ok"
        detail = f"识别到 {len(paperread_messages)} 条 PaperRead 消息"
    elif raw_messages:
        status = "unrecognized"
        detail = "读取到群聊消息，但没有匹配到 PaperRead 发件人或论文链接"
    else:
        status = "none"
        detail = "独立 PaperRead 群聊在该时间段没有可读消息"
    return {
        "status": status,
        "detail": detail,
        "source_chat_id": source_chat_id,
        "raw_count": len(raw_messages),
        "recognized_count": len(paperread_messages),
    }


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
        _progress("PaperRead 文档：知识库读取已禁用")
        return []

    max_documents = int(os.getenv("DAILY_REPORT_MAX_KNOWLEDGE_DOCUMENTS", "8"))
    _progress("PaperRead 文档：开始读取关联文档", paperread_messages=len(paperread_messages), max_documents=max_documents)
    documents = []
    seen_urls = set()
    link_pattern = re.compile(r"https?://[^/\s]+/(?:docx|wiki)/[A-Za-z0-9_-]+")
    for message in paperread_messages:
        candidates = [message.get("text", "")] + list(message.get("urls") or [])
        for url in link_pattern.findall(" ".join(candidates)):
            if url in seen_urls:
                continue
            seen_urls.add(url)
            if len(documents) >= max_documents:
                return documents
            _progress("PaperRead 文档：开始读取单个关联文档", index=len(documents) + 1)
            try:
                text = client.read_document_link(url)
            except Exception as exc:
                _progress("PaperRead 文档：单个关联文档读取失败", error=type(exc).__name__)
                print(f"⚠️ 无法读取飞书知识库文档 {url}: {exc}")
                continue
            if text:
                documents.append({"url": url, "text": text[:12000]})
                _progress("PaperRead 文档：单个关联文档读取完成", documents=len(documents))
    _progress("PaperRead 文档：关联文档读取完成", documents=len(documents))
    return documents


def collect_document_activity(client, messages, start, end):
    """Read direct SDK listener events instead of scanning documents or folders."""
    if os.getenv("DAILY_REPORT_DOCUMENT_ACTIVITY_ENABLED", "1") != "1":
        _progress("飞书文档事件：事件读取已禁用")
        return []
    _progress("飞书文档事件：开始读取本地事件队列")
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
    _progress("飞书文档事件：事件队列读取完成", events=len(activity))
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
    """Aggregate attributed window time by application and conservative topic."""
    classifier = TopicClassifier()
    grouped = defaultdict(lambda: {"hours": 0.0, "evidence": []})
    for row in (activity_summary or {}).get("windows") or []:
        app = _display_application(row.get("app"))
        title = _clean_window_title(row.get("title") or row.get("context"))
        url = str(row.get("context") or row.get("url") or "").strip()
        match = classifier.classify(app, title, url, row)
        if match.visibility in {"system_noise", "non_work"}:
            continue
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


def _theme_time_ledger(activity_summary):
    """Aggregate attributed time across applications into research themes."""
    classifier = TopicClassifier()
    grouped = {}
    application_totals = defaultdict(float)
    for row in (activity_summary or {}).get("windows") or []:
        app = _display_application(row.get("app"))
        title = _clean_window_title(row.get("title") or row.get("context"))
        url = str(row.get("context") or row.get("url") or "").strip()
        seconds = max(
            _number(row.get("seconds"))
            if row.get("seconds") is not None
            else _number(row.get("hours")) * 3600.0,
            0.0,
        )
        match = classifier.classify(app, title, url, row)
        if seconds <= 0 or match.visibility in {"system_noise", "non_work"}:
            continue
        item = grouped.setdefault(
            match.theme_id,
            {
                "theme_id": match.theme_id,
                "theme_name": match.theme_name,
                "duration_seconds": 0.0,
                "applications": [],
                "application_breakdown": defaultdict(float),
                "activities": [],
                "visibility": match.visibility,
                "confidence": match.confidence,
            },
        )
        item["duration_seconds"] += seconds
        item["application_breakdown"][app] += seconds
        if app not in item["applications"]:
            item["applications"].append(app)
        if title and title not in item["activities"]:
            item["activities"].append(title)
        application_totals[app] += seconds

    themes = []
    for item in grouped.values():
        item["duration_seconds"] = round(item["duration_seconds"], 3)
        item["duration_minutes"] = round(item["duration_seconds"] / 60.0, 1)
        item["application_breakdown"] = [
            {"application": app, "hours": round(seconds / 3600.0, 2)}
            for app, seconds in sorted(item["application_breakdown"].items(), key=lambda pair: pair[1], reverse=True)
        ]
        themes.append(item)
    themes.sort(key=lambda item: item["duration_seconds"], reverse=True)
    return themes, [
        {"application": app, "hours": round(seconds / 3600.0, 2)}
        for app, seconds in sorted(application_totals.items(), key=lambda pair: pair[1], reverse=True)
    ]


def _format_duration(seconds):
    seconds = max(int(round(_number(seconds))), 0)
    minutes = int(round(seconds / 60.0))
    if minutes < 60:
        return f"{minutes} 分钟"
    hours, remainder = divmod(minutes, 60)
    return f"{hours} 小时" + (f" {remainder} 分钟" if remainder else "")


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

    theme_ledger, application_totals = _theme_time_ledger(activity_summary)
    model_themes = _report_items(result.get("themes"))
    model_by_name = {
        _report_text(item.get("theme_name") or item.get("name") or item.get("title")).casefold(): item
        for item in model_themes
        if isinstance(item, dict)
    }
    model_by_id = {
        str(item.get("theme_id") or item.get("id") or "").casefold(): item
        for item in model_themes
        if isinstance(item, dict) and str(item.get("theme_id") or item.get("id") or "").strip()
    }
    visible_themes = []
    unclassified = []
    min_theme_seconds = max(_number(os.getenv("DAILY_REPORT_MIN_THEME_MINUTES", "10")), 0.0) * 60.0
    for measured in theme_ledger:
        if measured["visibility"] == "unknown":
            unclassified.append(measured)
            continue
        narrative = model_by_id.get(measured["theme_id"].casefold()) or model_by_name.get(measured["theme_name"].casefold(), {})
        narrative_status = str(narrative.get("status") or "").casefold()
        core_min_seconds = max(_number(os.getenv("DAILY_REPORT_MIN_CORE_THEME_MINUTES", "20")), 0.0) * 60.0
        if (
            measured["duration_seconds"] < min_theme_seconds
            and narrative_status not in {"confirmed", "probable"}
        ) or (
            measured["visibility"] == "core"
            and measured["duration_seconds"] < core_min_seconds
            and narrative_status not in {"confirmed", "probable"}
        ):
            unclassified.append(measured)
            continue
        merged = dict(measured)
        for key in ("summary", "outputs", "status", "next_step", "confidence", "evidence_refs"):
            if narrative.get(key) not in (None, "", [], {}):
                merged[key] = narrative[key]
        merged.setdefault("status", "observed")
        visible_themes.append(merged)
    if visible_themes:
        result["themes"] = visible_themes[:8]
        result["application_time"] = application_totals
    appendix = dict(result.get("appendix") or {})
    appendix["unclassified_activity"] = unclassified[:20]
    result["appendix"] = appendix
    visible_app_totals = defaultdict(float)
    for theme in visible_themes:
        for row in theme.get("application_breakdown") or []:
            visible_app_totals[row["application"]] += _number(row.get("hours")) * 3600.0
    result["application_time"] = [
        {"application": app, "hours": round(seconds / 3600.0, 2)}
        for app, seconds in sorted(visible_app_totals.items(), key=lambda pair: pair[1], reverse=True)
    ]
    result["themes"] = visible_themes[:8]

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
    themes = [item for item in _report_items(payload.get("themes")) if isinstance(item, dict)]
    if themes:
        shown = 0
        for item in themes:
            status = str(item.get("status") or "observed").casefold()
            if status not in {"confirmed", "probable", "已形成结果", "已形成结果/仅观察到访问/待确认"}:
                continue
            title = _report_text(item.get("theme_name") or item.get("name") or item.get("main_work")) or "未命名主题"
            detail = _report_text(item.get("summary") or item.get("detail"))
            duration = _format_duration(item.get("duration_seconds") or _number(item.get("hours")) * 3600)
            line = f"- {title}（投入 {duration}）"
            if detail:
                line += f"：{detail}"
            lines.append(line)
            shown += 1
            if shown >= 5:
                break
        if not shown:
            lines.append("- 暂无可核实的核心推进；窗口访问记录不列入正文。")
    else:
        _append_clean_items(lines, payload.get("today_completed"))

    lines.extend(["", "⏱ 研究主题时间"])
    theme_rows = [item for item in _report_items(payload.get("themes")) if isinstance(item, dict)]
    observed_total = _number((activity_summary or {}).get("application_observed_hours"))
    if not observed_total:
        observed_total = sum(
            _number(item.get("hours"))
            for item in _report_items((activity_summary or {}).get("applications"))
            if isinstance(item, dict)
        )
    if theme_rows:
        for item in theme_rows[:8]:
            title = _report_text(item.get("theme_name") or item.get("name")) or "未归因活动"
            duration = _format_duration(item.get("duration_seconds") or _number(item.get("hours")) * 3600)
            apps = []
            for app_row in item.get("application_breakdown") or []:
                app = _display_application(app_row.get("application") or app_row.get("app"))
                if app:
                    apps.append(f"{app} {_format_duration(_number(app_row.get('hours')) * 3600)}")
            lines.append(f"- {title}：{duration}" + (f"（{ '、'.join(apps) }）" if apps else ""))
        if observed_total:
            lines.append(f"- 可观测应用/事项合计：{observed_total:.2f}h")
    elif _report_items(payload.get("time_investment")):
        investments = [item for item in _report_items(payload.get("time_investment")) if isinstance(item, dict)]
        total_investment = sum(_number(item.get("hours")) for item in investments)
        lines.append(f"- 可观测应用/事项合计：{total_investment:.2f}h")
        for item in investments[:8]:
            label = _report_text(item.get("application") or item.get("app") or item.get("app_or_topic")) or "未归因"
            hours = _number(item.get("hours"))
            share = hours / total_investment * 100 if total_investment else 0.0
            lines.append(f"- {label}：{hours:.2f}h（{share:.1f}%）")
        if observed_total:
            lines.append(f"- 可观测应用/事项合计：{observed_total:.2f}h")
    else:
        lines.append("- 暂无")
    # Keep a compact compatibility line for legacy payloads that have no
    # deterministic theme ledger; modern theme payloads keep raw evidence out
    # of the main report.
    if not theme_rows and (activity_summary or {}).get("browser_windows"):
        browser_items = []
        for row in (activity_summary or {}).get("browser_windows")[:5]:
            title = _clean_window_title(row.get("title") or row.get("context"))
            browser_items.append(f"{title}({_report_hours(row.get('hours'))})")
        if browser_items:
            lines.append("- 浏览器窗口记录：" + "、".join(browser_items))

    lines.extend(["", "🎯 专注情况（📊 工作节奏）"])
    rhythm = payload.get("rhythm") if isinstance(payload.get("rhythm"), dict) else {}
    activity_rhythm = (activity_summary or {}).get("rhythm") or {}
    concentration = (activity_summary or {}).get("concentration") or {}
    theme_focus = (activity_summary or {}).get("theme_focus") or {}
    if theme_focus.get("source") == "cursor_position_and_input_focus":
        lines.append("- 切换判定结合鼠标所在显示器、前台窗口和输入事件")
    active_hours = _number(rhythm.get("active_hours") or (activity_summary or {}).get("active_hours"))
    away_hours = _number(rhythm.get("away_hours") or (activity_summary or {}).get("away_hours"))
    active_share = active_hours / (active_hours + away_hours) * 100 if active_hours + away_hours else 0.0
    lines.append(f"- 有效活动 {active_hours:.2f}h，离开 {away_hours:.2f}h，活动占比 {active_share:.1f}%")
    lines.append(
        f"- 主题会话中位数 {_format_duration(theme_focus.get('median_focus_session_seconds'))}，"
        f"最长 {_format_duration(theme_focus.get('longest_focus_session_seconds'))}；"
        f"25 分钟以上占比 {_number(theme_focus.get('focus_over_25m_share_percent')):.0f}%"
    )
    lines.append(
        f"- 有效主题切换 {int(_number(theme_focus.get('effective_theme_switches')))} 次，"
        f"切换间隔中位数 {_format_duration(theme_focus.get('median_effective_switch_interval_seconds'))}"
    )

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
    paperread_status=None,
):
    """Generate the structured data used by both card and Wiki renderers."""
    paperread_messages = paperread_messages or []
    knowledge_documents = knowledge_documents or []
    document_activity = document_activity or []
    question_messages = question_messages or []
    paperread_status = paperread_status or paperread_diagnostics([], paperread_messages)
    chat_text = _bounded_context(
        chat_messages,
        lambda item: f"[{item['time']}] ({item['sender_type']}) {item['text']}",
    ) or "（今天没有可读的群聊文本消息）"
    paperread_text = _bounded_context(
        paperread_messages,
        lambda item: f"[{item['time']}] {item['text']} {' '.join(item.get('urls') or [])}",
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
PaperRead status: {json.dumps(paperread_status, ensure_ascii=False)}

PaperRead 关联文档：
{knowledge_text}

飞书直接变更事件：
{changes_text}

待回答问题：
{question_text}

要求：
1. 报告要像科研工作汇报：先按研究主题概括今日动作、已形成结果和当前状态，再列主题时间。不要把“打开窗口/停留页面”直接写成“完成调研/完成配置”。
2. 今日核心推进按主题组织，跨 Edge、飞书、VS Code、微信和 ChatGPT 的同一主题必须合并；只展示 confirmed 或高置信度 probable，最多 3-5 个主题。窗口标题只能证明访问或停留；只有群聊、文档变更、代码修改或明确文本证据才能表述为已形成结果。
3. 时间投入必须以主题为一级条目，并在每个主题下列出应用及其归因时长。严格使用确定性“主题—应用时长账本”，不得重新计算或修改 hours。
4. 浏览器标题中的“和另外 N 个页面”、个人标记和不可见字符不是工作内容，忽略这些噪声；使用清理后的核心标题和主题分类。无法确认主题时写“未能从窗口标题确认主题”，不要臆测。
5. “专注情况”只保留有效活动时长、主题级专注会话中位数/平均值/最长值、25分钟以上专注占比、有效主题切换次数和切换间隔中位数；不要输出键盘次数、鼠标点击次数或原始窗口切换总数，也不要把这些信号解释为心理状态。
6. 所有时间使用秒或分钟字段，正文由程序格式化为“小时+分钟”。
7. PaperRead 只输出整体总结和最多 3 条未来研究建议，不逐篇复述论文。
8. 飞书文档部分必须填写 collaboration_summary 和 related_work：将文档事件与对应研究主题关联，说明可确认的操作；不能虚构正文改动。
9. 明日计划必须保留 tasks，并额外填写 idea_suggestions（1-5条），每条结合 PaperRead 今日推送与今日研究主题给出可验证下一步。
10. 必须保留 risks 和 questions 字段，即使为空也返回空数组。
11. 只返回一个 JSON 对象，不要 Markdown 或解释文字。

JSON 格式：
{{"date":"YYYY-MM-DD","summary":{{"headline":"","main_progress":""}},"themes":[{{"theme_id":"","theme_name":"","summary":"","outputs":[],"status":"confirmed|probable|observed","next_step":"","confidence":0.0}}],"today_completed":[],"time_investment":[],"rhythm":{{"active_hours":0,"away_hours":0,"active_share_percent":0}},"concentration":{{"summary":"","findings":[]}},"tomorrow_plan":{{"tasks":[{{"title":"","detail":""}}],"idea_suggestions":[{{"title":"","detail":"","source":"","next_step":""}}]}},"idea_suggestions":[],"risks":[{{"title":"","detail":""}}],"papers":{{"summary":"","suggestions":[{{"title":"","detail":""}}]}},"documents":{{"collaboration_summary":"","related_work":[{{"title":"","application":"","action":"","detail":"","evidence":""}}],"added":[],"modified":[],"deleted":[]}},"questions":[{{"title":"","answer":""}}]}}
"""
    _progress(
        "LLM：开始生成结构化日报",
        chat_messages=len(chat_messages),
        paperread_messages=len(paperread_messages),
        knowledge_documents=len(knowledge_documents),
        document_events=len(document_activity),
    )
    llm_started = time_module.monotonic()
    try:
        from llm_client import llm

        response = llm.call(
            [{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            response_validator=parse_report_payload,
        )
        generated_payload = parse_report_payload(response)
        _progress(
            "LLM：结构化日报生成完成",
            elapsed_seconds=round(time_module.monotonic() - llm_started, 1),
        )
    except Exception as exc:
        # A daily report still has value when a remote model is unavailable:
        # retain measured activity and document-event facts, and explicitly
        # avoid turning window telemetry into claimed work outcomes.
        report_date = str((activity_summary.get("period") or {}).get("start") or "")[:10]
        generated_payload = {
            "date": report_date,
            "summary": {
                "headline": "LLM 服务不可用，以下为基于已采集证据的降级日报",
                "main_progress": "未生成模型总结；仅保留可观测活动和文档事件。",
            },
            "themes": [],
            "today_completed": [],
            "time_investment": [],
            "rhythm": dict(activity_summary.get("rhythm") or {}),
            "concentration": {},
            "tomorrow_plan": {"tasks": [], "idea_suggestions": []},
            "idea_suggestions": [],
            "risks": [{
                "title": "LLM 服务不可用",
                "detail": "未能生成语义总结；请在 LLM 服务恢复后重试生成完整日报。",
            }],
            "papers": {
                "summary": "未生成模型分析；PaperRead 原始记录未被推断为研究结论。",
                "suggestions": [],
            },
            "documents": {"collaboration_summary": "", "related_work": []},
            "questions": [],
            "generation_status": "fallback",
        }
        _progress(
            "LLM：生成失败，使用事实降级日报",
            elapsed_seconds=round(time_module.monotonic() - llm_started, 1),
            error_type=type(exc).__name__,
        )
    enriched = enrich_report_payload(
        generated_payload,
        activity_summary=activity_summary,
        document_activity=document_activity,
    )
    enriched["paperread_status"] = paperread_status
    focus = dict((activity_summary or {}).get("theme_focus") or {})
    focus.update(
        {
            "active_seconds": round(
                _number((activity_summary or {}).get("attributed_active_seconds"))
                or _number((activity_summary or {}).get("active_hours")) * 3600,
                1,
            ),
            "away_seconds": round(_number((activity_summary or {}).get("away_hours")) * 3600, 1),
        }
    )
    enriched["focus"] = focus
    enriched["paperread"] = {
        "status": paperread_status.get("status"),
        "messages_read": paperread_status.get("raw_count", 0),
        "messages_recognized": paperread_status.get("recognized_count", 0),
        "diagnostics": paperread_status,
    }
    return enriched


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
        _progress("月报 Wiki：未配置根节点，跳过写入")
        print("未配置 DAILY_REPORT_FEISHU_WIKI_ROOT_NODE_TOKEN，跳过月报写入。")
        return ""
    _progress("月报 Wiki：开始定位或创建月度文档", report_date=report_date)
    month_title = f"工作日报-{report_date[:7]}"
    document_id = client.get_or_create_month_document(root_token, month_title)
    if not document_id:
        raise RuntimeError(f"无法创建或定位月报文档: {month_title}")
    marker = f"[DAILY_REPORT:{report_date}]"
    if marker in client.read_document_text(document_id):
        _progress("月报 Wiki：当天内容已存在，跳过追加")
        return document_id
    client.append_document_blocks(document_id, _daily_report_blocks(report_date, report))
    _progress("月报 Wiki：日报已追加")
    return document_id


def run_report():
    _progress("日报：开始执行正式发送流程")
    start, end = reporting_window()
    _progress("日报：统计窗口已确定", start=start.isoformat(), end=end.isoformat())
    chat_id = os.environ["DAILY_REPORT_FEISHU_CHAT_ID"]
    paperread_chat_id = os.getenv("DAILY_REPORT_PAPERREAD_CHAT_ID", chat_id).strip() or chat_id
    activity = ActivityWatchClient().summarize(start, end)
    _progress("日报：ActivityWatch 阶段完成")
    client = FeishuClient()
    _progress("日报：飞书 SDK 客户端已初始化")
    raw_messages = client.list_messages(chat_id, start, end)
    messages = normalize_messages(raw_messages)
    _progress("日报：主群聊消息标准化完成", raw=len(raw_messages), normalized=len(messages))
    paperread_read_error = False
    if paperread_chat_id == chat_id:
        ordinary_messages, paperread_messages = split_chat_messages(messages)
        paperread_raw_messages = raw_messages
        _progress("日报：PaperRead 与主群聊相同，复用已读取消息")
    else:
        ordinary_messages, _embedded_paperread = split_chat_messages(messages)
        _progress("日报：开始读取独立 PaperRead 群聊")
        try:
            paperread_raw_messages = client.list_messages(paperread_chat_id, start, end)
            paperread_messages = [item for item in normalize_messages(paperread_raw_messages) if item.get("is_paperread")]
            _progress("日报：独立 PaperRead 群聊读取完成", raw=len(paperread_raw_messages), recognized=len(paperread_messages))
        except Exception:
            paperread_raw_messages = []
            paperread_messages = []
            paperread_read_error = True
            _progress("日报：独立 PaperRead 群聊读取失败", status="read_error")
    paperread_status = paperread_diagnostics(
        paperread_raw_messages, paperread_messages, paperread_chat_id, paperread_read_error
    )
    if os.getenv("DAILY_REPORT_SHOW_DEBUG_APPENDIX", "0") == "1":
        print(f"PaperRead diagnostics: {json.dumps(paperread_status, ensure_ascii=False)}", flush=True)
    question_messages = extract_question_messages(ordinary_messages)
    ordinary_messages = [item for item in ordinary_messages if item not in question_messages]
    _progress(
        "日报：群聊分流完成",
        ordinary=len(ordinary_messages),
        paperread=len(paperread_messages),
        questions=len(question_messages),
    )
    knowledge_documents = collect_knowledge_documents(client, paperread_messages)
    document_activity = collect_document_activity(client, messages, start, end)
    report_payload = generate_report_payload(
        ordinary_messages,
        activity,
        paperread_messages,
        knowledge_documents,
        document_activity,
        question_messages,
        paperread_status,
    )
    _progress("日报：结构化 payload 已生成")
    report = render_report_payload(
        report_payload,
        activity_summary=activity,
        document_events_received=bool(document_activity),
    )
    _progress("日报：文本/月报内容已渲染")
    _progress("日报：开始发送飞书卡片")
    client.send_report(
        chat_id,
        report_payload,
        activity_summary=activity,
        document_events_received=bool(document_activity),
    )
    _progress("日报：飞书卡片发送完成")
    write_monthly_report(client, report, start.date().isoformat())
    _progress("日报：月报写入阶段完成")
    _progress("日报：开始清理飞书文档事件队列")
    clear_consumed_document_activity(end)
    _progress("日报：飞书文档事件队列清理完成")
    try:
        cleared_activity = ActivityWatchClient().clear_between(start, end)
        if True:
            print(f"已清理 ActivityWatch 已读取事件: {cleared_activity} 条", flush=True)
        _progress("日报：ActivityWatch 事件清理完成", cleared=cleared_activity)
    except requests.RequestException as exc:
        if True:
            print(f"ActivityWatch 事件清理失败（日报已完成，不影响下次运行）: {exc}", flush=True)
        print(f"日报已发送：{start.isoformat()} 至 {end.isoformat()}；群聊消息 {len(messages)} 条。")
    _progress("日报：正式发送流程完成")


def main():
    parser = argparse.ArgumentParser(description="Generate and send the daily work report")
    parser.add_argument("--preview", action="store_true", help="生成日报但不发送到飞书（仅本地调试）")
    parser.add_argument("--list-chats", action="store_true", help="列出机器人所在群聊及 chat_id")
    args = parser.parse_args()
    if args.list_chats:
        _progress("日报：开始列出机器人可见群聊")
        for chat in FeishuClient().list_chats():
            print(f"{chat.get('name', '(unnamed)')}\t{chat.get('chat_id', '')}")
        _progress("日报：群聊列表读取完成")
        return
    _progress("日报：开始执行命令行流程", preview=args.preview)
    start, end = reporting_window()
    _progress("日报：统计窗口已确定", start=start.isoformat(), end=end.isoformat())
    chat_id = os.environ["DAILY_REPORT_FEISHU_CHAT_ID"]
    paperread_chat_id = os.getenv("DAILY_REPORT_PAPERREAD_CHAT_ID", chat_id).strip() or chat_id
    activity = ActivityWatchClient().summarize(start, end)
    _progress("日报：ActivityWatch 阶段完成")
    client = FeishuClient()
    _progress("日报：飞书 SDK 客户端已初始化")
    raw_messages = client.list_messages(chat_id, start, end)
    messages = normalize_messages(raw_messages)
    _progress("日报：主群聊消息标准化完成", raw=len(raw_messages), normalized=len(messages))
    paperread_read_error = False
    if paperread_chat_id == chat_id:
        ordinary_messages, paperread_messages = split_chat_messages(messages)
        paperread_raw_messages = raw_messages
        _progress("日报：PaperRead 与主群聊相同，复用已读取消息")
    else:
        ordinary_messages, _embedded_paperread = split_chat_messages(messages)
        _progress("日报：开始读取独立 PaperRead 群聊")
        try:
            paperread_raw_messages = client.list_messages(paperread_chat_id, start, end)
            paperread_messages = [item for item in normalize_messages(paperread_raw_messages) if item.get("is_paperread")]
            _progress("日报：独立 PaperRead 群聊读取完成", raw=len(paperread_raw_messages), recognized=len(paperread_messages))
        except Exception:
            paperread_raw_messages = []
            paperread_messages = []
            paperread_read_error = True
            _progress("日报：独立 PaperRead 群聊读取失败", status="read_error")
    paperread_status = paperread_diagnostics(
        paperread_raw_messages, paperread_messages, paperread_chat_id, paperread_read_error
    )
    if os.getenv("DAILY_REPORT_SHOW_DEBUG_APPENDIX", "0") == "1":
        print(f"PaperRead diagnostics: {json.dumps(paperread_status, ensure_ascii=False)}", flush=True)
    question_messages = extract_question_messages(ordinary_messages)
    ordinary_messages = [item for item in ordinary_messages if item not in question_messages]
    _progress(
        "日报：群聊分流完成",
        ordinary=len(ordinary_messages),
        paperread=len(paperread_messages),
        questions=len(question_messages),
    )
    knowledge_documents = collect_knowledge_documents(client, paperread_messages)
    document_activity = collect_document_activity(client, messages, start, end)
    report_payload = generate_report_payload(
        ordinary_messages,
        activity,
        paperread_messages,
        knowledge_documents,
        document_activity,
        question_messages,
        paperread_status,
    )
    _progress("日报：结构化 payload 已生成")
    report = render_report_payload(
        report_payload,
        activity_summary=activity,
        document_events_received=bool(document_activity),
    )
    _progress("日报：文本/月报内容已渲染")
    if args.preview:
        _progress("日报：preview 模式，不发送飞书卡片")
        print(report)
    else:
        _progress("日报：开始发送飞书卡片")
        client.send_report(
            chat_id,
            report_payload,
            activity_summary=activity,
            document_events_received=bool(document_activity),
        )
        _progress("日报：飞书卡片发送完成")
        write_monthly_report(client, report, start.date().isoformat())
        _progress("日报：月报写入阶段完成")
        _progress("日报：开始清理飞书文档事件队列")
        clear_consumed_document_activity(end)
        _progress("日报：飞书文档事件队列清理完成")
        try:
            cleared_activity = ActivityWatchClient().clear_between(start, end)
            print(f"已清理 ActivityWatch 已读取事件: {cleared_activity} 条", flush=True)
        except requests.RequestException as exc:
            print(f"ActivityWatch 事件清理失败（日报已完成，不影响下次运行）: {exc}", flush=True)
        print(f"日报已发送，读取群聊消息 {len(messages)} 条。")
        _progress("日报：命令行发送流程完成")


if __name__ == "__main__":
    main()
