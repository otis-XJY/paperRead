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
                            (parse_timestamp(event.get("timestamp")), app, title, url)
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
        window_event_sequence.sort(key=lambda item: item[0])
        application_switches = 0
        window_switches = 0
        previous_app = None
        previous_window = None
        for _, app, title, url in window_event_sequence:
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
            "windows": window_rows[:80],
            "input": {key: round(value, 2) for key, value in input_totals.items()},
            "buckets_found": bucket_counts,
            "rhythm": rhythm,
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
        return self.read_document_text(document_id) if document_id else ""

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


def generate_report(
    chat_messages,
    activity_summary,
    paperread_messages=None,
    knowledge_documents=None,
):
    """Call the repository's existing multi-model LLM and return plain text."""
    from llm_client import llm  # Reuse the shared client/model fallback pool.

    paperread_messages = paperread_messages or []
    knowledge_documents = knowledge_documents or []
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
    additional_prompt = f"""

【额外分析要求】
1. 所有时间统一使用小时 h，不要输出 min、分钟或 minutes。
2. “时间投入”必须结合窗口标题、网页标题、URL 和群聊证据进行细化：
   - VS Code 等开发工具：识别项目、具体工作主题或改进事项，并给出各自用时；
   - 浏览器：识别重点网页、论文、教程或配置事项，并给出各自用时；
   - 无法从证据判断的内容要标注“无法确定”，不要编造。
3. “工作节奏”只保留有数据支撑的细节，例如活跃占比、应用/窗口切换次数、单位活跃小时的键鼠强度；没有意义的指标可以删除。
4. 增加“PaperRead 论文与未来研究建议”部分。结合 PaperRead 推送和知识库原文，说明：
   - 哪些论文与当前研究方向相关；
   - 对当前 GeoCoT-VLN、VLN、CoT 或相关研究有什么启发；
   - 可以形成哪些具体的后续研究问题、实验或改进方向。
   必须区分论文原文事实、群聊中明确表达的计划和模型推断；知识库未读取成功时要明确说明依据不完整。

【PaperRead 今日推送】
{paperread_text}

【PaperRead 关联的飞书知识库内容】
{knowledge_text}
"""
    response = llm.call([{"role": "user", "content": prompt + additional_prompt}])
    return response.choices[0].message.content.strip()


def run_report():
    start, end = reporting_window()
    chat_id = os.environ["DAILY_REPORT_FEISHU_CHAT_ID"]
    activity = ActivityWatchClient().summarize(start, end)
    client = FeishuClient()
    messages = normalize_messages(client.list_messages(chat_id, start, end))
    ordinary_messages, paperread_messages = split_chat_messages(messages)
    knowledge_documents = collect_knowledge_documents(client, paperread_messages)
    report = generate_report(
        ordinary_messages,
        activity,
        paperread_messages,
        knowledge_documents,
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
    knowledge_documents = collect_knowledge_documents(client, paperread_messages)
    report = generate_report(
        ordinary_messages,
        activity,
        paperread_messages,
        knowledge_documents,
    )
    if args.preview:
        print(report)
    else:
        client.send_text(chat_id, report)
        print(f"日报已发送，读取群聊消息 {len(messages)} 条。")


if __name__ == "__main__":
    main()
