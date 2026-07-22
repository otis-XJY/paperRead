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

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


FEISHU_BASE = "https://open.feishu.cn/open-apis"
DEFAULT_AW_URL = "http://127.0.0.1:5600"
DEFAULT_TIMEZONE = "Asia/Shanghai"


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
    tz = ZoneInfo(tz_name or os.getenv("REPORT_TIMEZONE", DEFAULT_TIMEZONE))
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
        bucket_counts = {"window": 0, "afk": 0, "input": 0}
        window_event_sequence = []

        for bucket_id, metadata in self.buckets().items():
            identity = f"{bucket_id} {metadata.get('name', '')} {metadata.get('type', '')}".lower()
            if "window" in identity:
                kind = "window"
            elif "afk" in identity:
                kind = "afk"
            elif "input" in identity:
                kind = "input"
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

        def rows(mapping, fields):
            result = []
            for values, seconds in sorted(mapping.items(), key=lambda item: item[1], reverse=True):
                row = dict(zip(fields, values if isinstance(values, tuple) else (values,)))
                row["hours"] = round(seconds / 3600, 2)
                result.append(row)
            return result

        window_rows = rows(windows, ("app", "title", "url"))
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
    """Minimal Feishu client for reading one group and sending a report."""

    def __init__(self, app_id=None, app_secret=None):
        self.app_id = app_id or os.environ["DAILY_REPORT_FEISHU_APP_ID"]
        self.app_secret = app_secret or os.environ["DAILY_REPORT_FEISHU_APP_SECRET"]
        self._token = None

    def token(self):
        if self._token:
            return self._token
        response = requests.post(
            f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取飞书 tenant_access_token 失败: {data}")
        self._token = data["tenant_access_token"]
        return self._token

    def _request(self, method, path, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.token()}"
        response = requests.request(method, f"{FEISHU_BASE}{path}", headers=headers, timeout=20, **kwargs)
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书 API 失败: {data}")
        return data.get("data", {})

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

    def list_wiki_documents(self, root_node_token, max_documents=200):
        """Traverse a Wiki subtree and return its docx nodes."""
        root_data = self._request(
            "GET",
            "/wiki/v2/spaces/get_node",
            params={"token": root_node_token},
        )
        root_node = root_data.get("node", root_data)
        space_id = root_node.get("space_id", "")
        if not space_id:
            return []

        documents = []
        queue = [root_node_token]
        visited = set()
        while queue and len(documents) < max_documents:
            parent_token = queue.pop(0)
            if parent_token in visited:
                continue
            visited.add(parent_token)
            page_token = None
            while True:
                params = {"parent_node_token": parent_token, "page_size": 50}
                if page_token:
                    params["page_token"] = page_token
                data = self._request(
                    "GET",
                    f"/wiki/v2/spaces/{space_id}/nodes",
                    params=params,
                )
                for node in data.get("items", []):
                    node_token = node.get("node_token", "")
                    obj_token = node.get("obj_token", "")
                    obj_type = str(node.get("obj_type", "")).lower()
                    if obj_type == "docx" and obj_token:
                        documents.append(
                            {
                                "document_id": obj_token,
                                "node_token": node_token,
                                "title": node.get("title", ""),
                                "url": f"https://my.feishu.cn/docx/{obj_token}",
                            }
                        )
                        if len(documents) >= max_documents:
                            return documents
                    if node_token and node.get("has_child"):
                        queue.append(node_token)
                if not data.get("has_more") or not data.get("page_token"):
                    break
                page_token = data["page_token"]
        return documents

    def list_document_versions(self, document_id, start, end):
        """Return document versions created or updated inside the report window."""
        versions = []
        page_token = None
        all_versions = []
        while True:
            params = {"obj_type": "docx", "page_size": 100}
            if page_token:
                params["page_token"] = page_token
            data = self._request(
                "GET",
                f"/drive/v1/files/{document_id}/versions",
                params=params,
            )
            all_versions.extend(data.get("items", []))
            if not data.get("has_more") or not data.get("page_token"):
                break
            page_token = data["page_token"]

        all_versions.sort(
            key=lambda item: (
                parse_timestamp(item["create_time"]).timestamp()
                if item.get("create_time")
                else 0
            )
        )
        for index, item in enumerate(all_versions):
            created_at = parse_timestamp(item["create_time"]) if item.get("create_time") else None
            updated_at = parse_timestamp(item["update_time"]) if item.get("update_time") else None
            event_time = updated_at if updated_at and start <= updated_at <= end else created_at
            if not event_time or not (start <= event_time <= end):
                continue

            raw_status = str(item.get("status", "")).lower()
            is_deleted = raw_status in {"1", "2", "deleted", "trash", "statusdeleted", "statustrash"}
            if is_deleted:
                operation = "deleted"
            elif index == 0 and created_at and start <= created_at <= end:
                operation = "added"
            else:
                operation = "modified"
            versions.append(
                {
                    "document_id": document_id,
                    "title": item.get("name", ""),
                    "version": item.get("version", ""),
                    "operation": operation,
                    "event_time": event_time.isoformat(),
                    "creator_id": item.get("creator_id", ""),
                    "status": item.get("status", ""),
                }
            )
        return versions

    def send_text(self, chat_id, text):
        content = json.dumps({"text": text}, ensure_ascii=False)
        return self._request(
            "POST",
            "/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            json={"receive_id": chat_id, "msg_type": "text", "content": content},
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
    """Collect version activity for document links visible in today's chat."""
    if os.getenv("DAILY_REPORT_DOCUMENT_ACTIVITY_ENABLED", "1") != "1":
        return []

    max_documents = int(os.getenv("DAILY_REPORT_MAX_DOCUMENT_ACTIVITY", "30"))
    activity = []
    seen_urls = set()
    link_pattern = re.compile(r"https?://[^/\s]+/(?:docx|wiki)/[A-Za-z0-9_-]+")
    for message in messages:
        for url in link_pattern.findall(message.get("text", "")):
            if url in seen_urls:
                continue
            seen_urls.add(url)
            if len(seen_urls) > max_documents:
                return activity
            try:
                document_id = client.document_id_from_link(url)
                if not document_id:
                    continue
                for version in client.list_document_versions(document_id, start, end):
                    version["url"] = url
                    activity.append(version)
            except Exception as exc:
                print(f"⚠️ 无法读取飞书文档变更 {url}: {exc}")
    root_token = os.getenv("DAILY_REPORT_FEISHU_WIKI_ROOT_NODE_TOKEN", "").strip()
    if root_token:
        try:
            wiki_documents = client.list_wiki_documents(
                root_token,
                max_documents=int(os.getenv("DAILY_REPORT_MAX_WIKI_DOCUMENTS", "200")),
            )
            for item in wiki_documents:
                if len(seen_urls) >= max_documents:
                    break
                document_id = item.get("document_id", "")
                url = item.get("url", "")
                if not document_id or url in seen_urls:
                    continue
                seen_urls.add(url)
                for version in client.list_document_versions(document_id, start, end):
                    version["url"] = url
                    if item.get("title") and not version.get("title"):
                        version["title"] = item["title"]
                    activity.append(version)
        except Exception as exc:
            print(f"⚠️ 无法扫描飞书知识库文档树: {exc}")
    return activity


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


def _append_report_items(lines, items, empty_text="暂无"):
    if not items:
        lines.append(f"• {empty_text}")
        return
    for item in items:
        if isinstance(item, dict):
            title = _report_text(item.get("title") or item.get("name") or item.get("topic"))
            detail = _report_text(item.get("detail") or item.get("summary") or item.get("answer"))
            evidence = _report_text(item.get("evidence") or item.get("basis"))
            text = "：".join(part for part in (title, detail) if part)
            if evidence:
                text = f"{text}（依据：{evidence}）" if text else f"依据：{evidence}"
        else:
            text = _report_text(item)
        if text:
            lines.append(f"• {text}")


def render_report_payload(payload):
    """Render the structured report as compact, detailed Feishu chat text."""
    date = _report_text(payload.get("date")) or "工作日报"
    lines = [f"📅 工作日报 {date}", "", "✅ 今日完成"]
    _append_report_items(lines, _report_items(payload.get("today_completed")))

    lines.extend(["", "⏱️ 时间投入"])
    investments = _report_items(payload.get("time_investment"))
    if investments:
        for item in investments:
            if not isinstance(item, dict):
                lines.append(f"• {_report_text(item)}")
                continue
            label = _report_text(item.get("app_or_topic") or item.get("title") or item.get("name")) or "事项"
            duration = _report_hours(item.get("hours"))
            share = item.get("share_percent")
            share_text = f"，占有效活跃 {float(share):.1f}%" if isinstance(share, (int, float)) else ""
            detail = _report_text(item.get("detail") or item.get("summary"))
            evidence = _report_text(item.get("evidence") or item.get("windows"))
            line = f"• {label}：{duration}{share_text}"
            if detail:
                line += f"；{detail}"
            if evidence:
                line += f"（证据：{evidence}）"
            lines.append(line)
    else:
        lines.append("• 暂无")
    boundary = _report_text(payload.get("evidence_boundary"))
    if boundary:
        lines.append(f"• 证据边界：{boundary}")

    lines.extend(["", "📊 工作节奏"])
    rhythm = payload.get("rhythm") if isinstance(payload.get("rhythm"), dict) else {}
    active = _report_hours(rhythm.get("active_hours"))
    away = _report_hours(rhythm.get("away_hours"))
    active_share = rhythm.get("active_share_percent")
    share_text = f"，活跃占比 {float(active_share):.1f}%" if isinstance(active_share, (int, float)) else ""
    lines.append(f"• 有效活跃 {active}，离开 {away}{share_text}")
    lines.append(
        f"• 应用切换 {_report_text(rhythm.get('application_switches')) or '无法确定'} 次，"
        f"窗口切换 {_report_text(rhythm.get('window_switches')) or '无法确定'} 次"
    )
    lines.append(
        f"• 键盘 {_report_text(rhythm.get('keypresses')) or '无法确定'} 次，"
        f"鼠标点击 {_report_text(rhythm.get('mouse_clicks')) or '无法确定'} 次"
    )

    lines.extend(["", "🎯 专注度分析"])
    concentration = payload.get("concentration")
    if isinstance(concentration, dict):
        summary = _report_text(concentration.get("summary"))
        if summary:
            lines.append(f"• 总结：{summary}")
        _append_report_items(lines, _report_items(concentration.get("findings")))
        boundary = _report_text(concentration.get("evidence_boundary"))
        if boundary:
            lines.append(f"• 证据边界：{boundary}")
    else:
        lines.append("• 暂无足够的连续窗口数据")

    sections = (
        ("🗓️ 明日计划建议", "tomorrow_plan"),
        ("⚠️ 风险或待跟进", "risks"),
    )
    for heading, key in sections:
        lines.extend(["", heading])
        _append_report_items(lines, _report_items(payload.get(key)))

    lines.extend(["", "📚 PaperRead 论文与未来研究建议"])
    papers = payload.get("papers")
    if isinstance(papers, dict):
        if _report_text(papers.get("summary")):
            lines.append(f"• 总结：{_report_text(papers.get('summary'))}")
        _append_report_items(lines, _report_items(papers.get("items") or papers.get("papers")))
        if papers.get("suggestions"):
            lines.append("• 后续建议：")
            _append_report_items(lines, _report_items(papers.get("suggestions")))
    else:
        _append_report_items(lines, _report_items(papers))

    lines.extend(["", "📝 飞书文档变更"])
    documents = payload.get("documents") if isinstance(payload.get("documents"), dict) else {}
    for label, key in (("新增", "added"), ("修改", "modified"), ("删除/回收", "deleted")):
        values = _report_items(documents.get(key))
        lines.append(f"• {label}：")
        _append_report_items(lines, values)

    lines.extend(["", "💬 群聊问题解答"])
    _append_report_items(lines, _report_items(payload.get("questions")))
    return "\n".join(lines).strip()


def generate_report(
    chat_messages,
    activity_summary,
    paperread_messages=None,
    knowledge_documents=None,
    document_activity=None,
    question_messages=None,
):
    """Call the repository's existing multi-model LLM and return plain text."""
    from llm_client import llm  # Reuse the shared client/model fallback pool.

    paperread_messages = paperread_messages or []
    knowledge_documents = knowledge_documents or []
    document_activity = document_activity or []
    question_messages = question_messages or []
    chat_text = "\n".join(
        f"[{item['time']}] ({item['sender_type']}) {item['text']}" for item in chat_messages
    ) or "（当天群聊中没有可读取的文本消息）"
    paperread_text = _bounded_context(
        paperread_messages,
        lambda item: f"[{item['time']}] {item['text']}",
    ) or "（今天没有识别到 PaperRead 论文推送）"
    knowledge_text = _bounded_context(
        knowledge_documents,
        lambda item: f"来源：{item['url']}\n{item['text']}",
    ) or "（没有读取到 PaperRead 关联的飞书知识库文档）"
    document_activity_text = _bounded_context(
        document_activity,
        lambda item: (
            f"[{item['event_time']}] {item['operation']} | {item['title']} | "
            f"版本 {item['version']} | creator={item['creator_id']} | {item['url']}"
        ),
    ) or "（时间范围内没有读取到已知飞书文档的版本变更）"
    question_text = _bounded_context(
        question_messages,
        lambda item: f"[{item['time']}] {item['sender_name']}: {item['text']}",
    ) or "（没有检测到需要回答的群聊问题）"
    activity_text = json.dumps(activity_summary, ensure_ascii=False, indent=2)
    prompt = f"""你是我的工作日报助手。请根据飞书群聊记录和 ActivityWatch 数据生成一份简洁、客观的中文日报。

日报时间范围：{activity_summary['period']['start']} 至 {activity_summary['period']['end']}。
请区分“今天已经完成的工作”和“明天计划做的工作”，不要把计划写成已完成事实；没有证据的内容不要臆造。
输出包含以下部分：
1. 今日完成（3-6 条）
2. 时间投入（主要软件/窗口和时长）
3. 工作节奏（有效活跃、离开、键盘次数、鼠标移动/点击）
4. 明日计划建议（结合用户明确写出的计划，给出优先级和具体建议）
5. 风险或待跟进（没有则写“暂无”）
总长度控制在 2200 个中文字符以内，以保留详细分析为优先；不要为了短而删除关键论文、事项或证据。

【飞书群聊记录】
{chat_text}

【ActivityWatch 数据】
{activity_text}
"""
    additional_prompt = f"""

【额外分析要求】
1. 所有时间统一使用小时 h，不要输出 min、分钟或 minutes。
2. “时间投入”必须结合窗口标题、网页标题、URL 和群聊证据进行细化：
   - VS Code 等开发工具：识别项目、具体工作主题或改进事项，并给出各自用时；
   - 浏览器：识别重点网页、论文、教程或配置事项，并给出各自用时；
   - 无法从证据判断的内容要标注“无法确定”，不要编造。
3. “工作节奏”只保留有数据支撑的细节，例如活跃占比、应用/窗口切换次数、单位活跃小时的键鼠强度；没有意义的指标可以删除。
4. 增加“专注度分析”部分。结合 ActivityWatch 的 `concentration` 数据说明：
   - 连续同一窗口/应用的平均、中位和最长持续时间；
   - 10 分钟以上连续会话数量与时长、短会话比例；
   - 应用/窗口每有效活跃小时的切换次数，并据此判断是否存在频繁上下文切换。
   不要生成没有统计依据的单一“专注分数”；切换不必然代表分心（例如开发与查资料的正常协作），必须结合窗口标题和事项解释，并明确这是行为代理指标而非心理状态测量。
5. 增加“PaperRead 论文与未来研究建议”部分。结合 PaperRead 推送和知识库原文，说明：
   - 哪些论文与当前研究方向相关；
   - 对当前 GeoCoT-VLN、VLN、CoT 或相关研究有什么启发；
   - 可以形成哪些具体的后续研究问题、实验或改进方向。
   必须区分论文原文事实、群聊中明确表达的计划和模型推断；知识库未读取成功时要明确说明依据不完整。
6. 增加“飞书文档变更”部分，按新增、修改、删除/回收分类，结合变更时间、文档标题和版本信息说明当天实际处理过的文档。版本记录只能证明文档版本活动，不能证明具体删改了哪些句子；不要过度推断。

【PaperRead 今日推送】
{paperread_text}

【需要回答的群聊问题】请在日报中新增“群聊问题解答”部分，逐条回答以下由已配置群聊提问对象提出的问题。先给明确结论，再说明依据；如果现有群聊、ActivityWatch、PaperRead或知识库信息不足，必须明确说出无法确定以及需要补充什么信息，不要编造。
{question_text}

【PaperRead 关联的飞书知识库内容】
{knowledge_text}

【时间范围内的飞书文档变更】
{document_activity_text}
"""
    question_target_configured = bool(
        os.getenv("DAILY_REPORT_QUESTION_SENDER_ID", "").strip()
        or os.getenv("DAILY_REPORT_QUESTION_SENDER_NAME", "").strip()
    )
    refinement_prompt = f"""

【细化与隐私要求】
1. “今日完成”只写有证据支持的动作和结果，每条尽量包含“做了什么—作用于什么对象—得到什么结果”。
   - 群聊明确说已完成、已修改、已推送的事项可写为完成事实；ActivityWatch 只能证明使用过某个应用或页面，不能单独证明代码已完成、问题已解决或配置已生效。
   - PaperRead 推送数量必须以消息记录为准；可以概括论文主题，但不要把模型推断写成论文原文结论。
   - 对“查看、排查、尝试、待确认”等状态保持原状态，不要升级成“完成”。
2. “时间投入”要比只列应用更具体：按应用或工作对象分组，给出小时 h、占有效活跃时间比例，并列出最多 2 个窗口/网页标题作为证据。
   - 对 VS Code，要尽可能从窗口标题识别项目名和具体文件、配置或开发任务；无法确认时写“项目/事项无法从窗口证据确定”。
   - 对浏览器，要区分服务器运维、cron-job、SSH、支付/配置、论文阅读等可辨认事项；无法确认的页面只写“浏览器页面，事项无法确定”。
   - 应用总时长与其子项不要重复相加；所有时长统一使用 h，保留两位小数。
3. “时间投入”必须同时给出一个简短的证据边界说明：哪些是窗口标题/URL直接支持的，哪些只是群聊内容与时间段的对应关系。
4. 输出保留并细化“今日完成、时间投入、工作节奏、明日计划建议、风险或待跟进、PaperRead 论文与未来研究建议、飞书文档变更、群聊问题解答”八个部分；没有证据的部分写“暂无”或“无法确定”，不要补写。
5. 当前群聊提问对象已通过 GitHub Actions 配置：{"已配置，仅回答该对象的问题" if question_target_configured else "未配置，不要识别或回答任何个人的问题"}。不要自行猜测、补充或输出其他群成员的身份信息。
6. 这是开源项目，日报正文不要泄露 chat_id、app_secret、Webhook、sender_id、完整私密 URL 参数或其他凭据；只输出必要的工作对象和结论。
总长度控制在 2200 个中文字符以内，以保证“今日完成”“时间投入”和 PaperRead 研究建议足够具体；保持条理清晰，不要泛泛压缩成摘要。
7. 你必须只返回一个合法 JSON 对象，不要返回 Markdown、代码围栏、解释文字、草稿或思考过程。模型的最终回答字段只放 JSON，不要把 reasoning/thinking 字段复制到 content。
8. JSON 使用以下字段，内容要保留足够详细的分析：
   {{"date":"YYYY-MM-DD","today_completed":[{{"title":"","detail":"","evidence":""}}],"time_investment":[{{"app_or_topic":"","hours":0,"share_percent":0,"detail":"","evidence":""}}],"evidence_boundary":"","rhythm":{{"active_hours":0,"away_hours":0,"active_share_percent":0,"application_switches":0,"window_switches":0,"keypresses":0,"mouse_clicks":0}},"concentration":{{"summary":"","findings":[{{"title":"","detail":"","evidence":""}}],"evidence_boundary":""}},"tomorrow_plan":[{{"title":"","detail":"","evidence":""}}],"risks":[{{"title":"","detail":"","evidence":""}}],"papers":{{"summary":"","items":[{{"title":"","detail":"","evidence":""}}],"suggestions":[{{"title":"","detail":"","evidence":""}}]}},"documents":{{"added":[],"modified":[],"deleted":[]}},"questions":[{{"title":"","answer":"","basis":""}}]}}
   `today_completed` 保持 3-6 条；`time_investment` 按应用/事项拆分并保留窗口证据；`papers.items` 保留每篇论文的主题、相关性和事实/推断边界。不要为了压缩 JSON 而删掉关键细节。
"""
    response = llm.call(
        [{"role": "user", "content": prompt + additional_prompt + refinement_prompt}],
        response_format={"type": "json_object"},
        response_validator=parse_report_payload,
    )
    return render_report_payload(parse_report_payload(response))


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
        print(f"日报已发送，读取群聊消息 {len(messages)} 条。")


if __name__ == "__main__":
    main()
