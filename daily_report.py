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
    "🎯 可观测操作焦点",
    "明日计划建议",
    "风险或待跟进",
    "📚 PaperRead 论文与未来研究建议",
    "📄 飞书文档变更",
    "💬 群聊问题解答",
)


def build_daily_report_card(text):
    """Convert the rendered report into readable card sections."""
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
                    if str(data.get("status", "")).lower() in {"afk", "away"}:
                        afk_seconds += duration
                    else:
                        active_seconds += duration
                        event_start = parse_timestamp(event.get("timestamp"))
                        active_intervals.append(
                            (event_start, event_start + timedelta(seconds=duration))
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

        if focus_events:
            # Focus events intentionally overlap for the 60-second activity
            # window.  Collapse intervals per monitor/window before summing so
            # three monitors cannot make the report exceed elapsed time.
            def merge_intervals(intervals):
                merged = []
                for left, right in sorted(intervals):
                    if merged and left <= merged[-1][1]:
                        merged[-1] = (merged[-1][0], max(merged[-1][1], right))
                    else:
                        merged.append((left, right))
                return merged

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

        def rows(mapping, fields):
            result = []
            for values, seconds in sorted(mapping.items(), key=lambda item: item[1], reverse=True):
                row = dict(zip(fields, values if isinstance(values, tuple) else (values,)))
                row["hours"] = round(seconds / 3600, 2)
                result.append(row)
            return result

        window_rows = rows(windows, ("app", "title", "context"))
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

    def send_text(self, chat_id, text):
        """Send the report as a readable interactive card."""
        report_text = str(text)
        report_date = ""
        first_line = report_text.splitlines()[0] if report_text.splitlines() else ""
        match = re.search(r"工作日报\s+(\d{4}-\d{2}-\d{2})", first_line)
        if match:
            report_date = f" · {match.group(1)}"
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": f"工作日报{report_date}"},
            },
            "elements": build_daily_report_card(report_text),
        }
        content = json.dumps(card, ensure_ascii=False)
        return self._request(
            "POST",
            "/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            json={"receive_id": chat_id, "msg_type": "interactive", "content": content},
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
    try:
        activity = store.between(start, end)
    finally:
        store.close()
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
        title = _report_text(item.get("title") or item.get("name") or item.get("topic"))
        detail = _report_text(item.get("detail") or item.get("summary") or item.get("answer"))
        return "：".join(part for part in (title, detail) if part)
    return _report_text(item)


def _append_clean_items(lines, items, empty_text="暂无"):
    items = _report_items(items)
    if not items:
        lines.append(f"- {empty_text}")
        return
    for item in items:
        text = _clean_item_text(item)
        if text:
            lines.append(f"- {text}")


def render_report_payload(payload):
    """Render the compact report with only user-facing summary fields."""
    date = _report_text(payload.get("date")) or "工作日报"
    lines = [f"📅 工作日报 {date}", "", "✅ 今日完成"]
    _append_clean_items(lines, payload.get("today_completed"))

    lines.extend(["", "⏱ 时间投入"])
    investments = _report_items(payload.get("time_investment"))
    if investments:
        for item in investments:
            if not isinstance(item, dict):
                lines.append(f"- {_report_text(item)}")
                continue
            label = _report_text(item.get("app_or_topic") or item.get("title") or item.get("name")) or "事项"
            duration = _report_hours(item.get("hours"))
            detail = _report_text(item.get("detail") or item.get("summary"))
            line = f"- {label}：{duration}"
            if detail:
                line += f"，{detail}"
            lines.append(line)
    else:
        lines.append("- 暂无")

    lines.extend(["", "📊 工作节奏"])
    rhythm = payload.get("rhythm") if isinstance(payload.get("rhythm"), dict) else {}
    lines.append(
        f"- 有效活动 {_report_hours(rhythm.get('active_hours'))}，离开 {_report_hours(rhythm.get('away_hours'))}"
    )
    lines.append(
        f"- 应用切换 {_report_text(rhythm.get('application_switches')) or '0'} 次，"
        f"窗口切换 {_report_text(rhythm.get('window_switches')) or '0'} 次，"
        f"键盘 {_report_text(rhythm.get('keypresses')) or '0'} 次，"
        f"鼠标点击 {_report_text(rhythm.get('mouse_clicks')) or '0'} 次"
    )

    lines.extend(["", "🎯 可观测操作焦点"])
    concentration = payload.get("concentration") if isinstance(payload.get("concentration"), dict) else {}
    if concentration.get("summary"):
        lines.append(f"- {_report_text(concentration.get('summary'))}")
    _append_clean_items(lines, concentration.get("findings"))
    if not concentration.get("summary") and not concentration.get("findings"):
        lines.append("- 暂无")

    for heading, key in (("明日计划建议", "tomorrow_plan"), ("风险或待跟进", "risks")):
        lines.extend(["", heading])
        _append_clean_items(lines, payload.get(key))

    lines.extend(["", "📚 PaperRead 论文与未来研究建议"])
    papers = payload.get("papers") if isinstance(payload.get("papers"), dict) else {}
    if papers.get("summary"):
        lines.append(f"- 总结：{_report_text(papers.get('summary'))}")
    lines.append("- 未来建议：")
    _append_clean_items(lines, _report_items(papers.get("suggestions"))[:3])

    lines.extend(["", "📄 飞书文档变更"])
    documents = payload.get("documents") if isinstance(payload.get("documents"), dict) else {}
    for label, key in (("新增", "added"), ("修改", "modified"), ("删除/回收", "deleted")):
        lines.append(f"- {label}：")
        _append_clean_items(lines, documents.get(key))

    lines.extend(["", "💬 群聊问题解答"])
    _append_clean_items(lines, payload.get("questions"))
    return "\n".join(lines).strip()


def generate_report(
    chat_messages,
    activity_summary,
    paperread_messages=None,
    knowledge_documents=None,
    document_activity=None,
    question_messages=None,
):
    """Generate a compact structured report and render it for Feishu."""
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
    question_text = _bounded_context(
        question_messages,
        lambda item: f"[{item['time']}] {item['sender_name']}: {item['text']}",
    ) or "（没有需要回答的群聊问题）"
    prompt = f"""你是我的工作日报助手。请根据以下信息生成简洁、客观的中文日报。

日报时间范围：{activity_summary['period']['start']} 至 {activity_summary['period']['end']}。
不要把计划写成已完成事实，不要编造无法从输入判断的内容。

群聊记录：
{chat_text}

ActivityWatch 数据：
{json.dumps(activity_summary, ensure_ascii=False, indent=2)}

PaperRead 今日推送：
{paperread_text}

PaperRead 关联文档：
{knowledge_text}

飞书直接变更事件：
{changes_text}

待回答问题：
{question_text}

要求：
1. 保留今日完成、时间投入、工作节奏、可观测操作焦点、明日计划建议、风险或待跟进、PaperRead 论文与未来研究建议、飞书文档变更、群聊问题解答。
2. 所有时间使用小时 h。
3. PaperRead 只输出整体总结和最多 3 条未来研究建议，不逐篇复述论文。
4. 文档变更按新增、修改、删除/回收分类；只描述事件，不推断具体正文改动。
5. 可观测操作焦点只描述窗口、显示器、输入和时间数据，不描述心理状态。
6. 只返回一个 JSON 对象，不要 Markdown 或解释文字。

JSON 格式：
{{"date":"YYYY-MM-DD","today_completed":[{{"title":"","detail":""}}],"time_investment":[{{"app_or_topic":"","hours":0,"share_percent":0,"detail":""}}],"rhythm":{{"active_hours":0,"away_hours":0,"active_share_percent":0,"application_switches":0,"window_switches":0,"keypresses":0,"mouse_clicks":0}},"concentration":{{"summary":"","findings":[{{"title":"","detail":""}}]}},"tomorrow_plan":[{{"title":"","detail":""}}],"risks":[{{"title":"","detail":""}}],"papers":{{"summary":"","suggestions":[{{"title":"","detail":""}}]}},"documents":{{"added":[],"modified":[],"deleted":[]}},"questions":[{{"title":"","answer":""}}]}}
"""
    response = llm.call(
        [{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        response_validator=parse_report_payload,
    )
    return render_report_payload(parse_report_payload(response))


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
    report = generate_report(
        ordinary_messages,
        activity,
        paperread_messages,
        knowledge_documents,
        document_activity,
        question_messages,
    )
    client.send_text(chat_id, report)
    write_monthly_report(client, report, start.date().isoformat())
    clear_consumed_document_activity(end)
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
    report = generate_report(
        ordinary_messages,
        activity,
        paperread_messages,
        knowledge_documents,
        document_activity,
        question_messages,
    )
    if args.preview:
        print(report)
    else:
        client.send_text(chat_id, report)
        write_monthly_report(client, report, start.date().isoformat())
        clear_consumed_document_activity(end)
        print(f"日报已发送，读取群聊消息 {len(messages)} 条。")


if __name__ == "__main__":
    main()
