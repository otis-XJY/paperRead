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
                row["minutes"] = round(seconds / 60, 2)
                result.append(row)
            return result

        window_rows = rows(windows, ("app", "title", "url"))
        return {
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "active_minutes": round(active_seconds / 60, 2),
            "afk_minutes": round(afk_seconds / 60, 2),
            "applications": rows(apps, ("app",))[:30],
            "windows": window_rows[:80],
            "input": {key: round(value, 2) for key, value in input_totals.items()},
            "buckets_found": bucket_counts,
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


def normalize_messages(messages):
    normalized = []
    for message in messages:
        text = message_text(message)
        if not text:
            continue
        sender = message.get("sender", {})
        normalized.append(
            {
                "time": message.get("create_time") or message.get("update_time"),
                "sender_type": sender.get("sender_type", "unknown"),
                "text": text,
            }
        )
    return normalized[-int(os.getenv("DAILY_REPORT_MAX_MESSAGES", "200")) :]


def generate_report(chat_messages, activity_summary):
    """Call the repository's existing multi-model LLM and return plain text."""
    from llm_client import llm  # Reuse the shared client/model fallback pool.

    chat_text = "\n".join(
        f"[{item['time']}] ({item['sender_type']}) {item['text']}" for item in chat_messages
    ) or "（当天群聊中没有可读取的文本消息）"
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
总长度控制在 600 个中文字符以内，直接输出日报正文，不要解释数据来源。

【飞书群聊记录】
{chat_text}

【ActivityWatch 数据】
{activity_text}
"""
    response = llm.call([{"role": "user", "content": prompt}])
    return response.choices[0].message.content.strip()


def run_report():
    start, end = reporting_window()
    chat_id = os.environ["DAILY_REPORT_FEISHU_CHAT_ID"]
    activity = ActivityWatchClient().summarize(start, end)
    client = FeishuClient()
    messages = normalize_messages(client.list_messages(chat_id, start, end))
    report = generate_report(messages, activity)
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
    report = generate_report(messages, activity)
    if args.preview:
        print(report)
    else:
        client.send_text(chat_id, report)
        print(f"日报已发送，读取群聊消息 {len(messages)} 条。")


if __name__ == "__main__":
    main()
