"""Deterministic activity-topic classification for the daily report.

The model writes the narrative, but this module owns classification and time
accounting so the same activity cannot be counted in multiple themes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional


COORDINATE_TITLE_RE = re.compile(
    r"^\s*-?\d+\s*,\s*-?\d+\s*,\s*-?\d+\s*,\s*-?\d+\s*$"
)
NOISE_RE = re.compile(
    r"taskbar|system tray|notification overflow|snippingtool|截图工具|"
    r"logitech\s*g\s*hub|mouse driver|托盘|溢出窗口",
    re.IGNORECASE,
)
NON_WORK_RE = re.compile(
    r"在线播放|喜剧之王|七味网|cloudmusic|网易云音乐|youtube|bilibili|"
    r"\bvideo\b|\bplayer\b|音乐|游戏|movie",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TopicMatch:
    theme_id: str
    theme_name: str
    visibility: str
    confidence: float


class TopicClassifier:
    """Classify one window/activity record using explicit deterministic rules."""

    _rules = (
        ("geocot", "GeoCoT", (r"geocot", r"param[_ -]?geocot")),
        ("multi_agent", "多智能体与 Agent", (r"multi[- ]agent", r"agentos", r"智能体", r"self[- ]evolving")),
        ("latent_reasoning_vln", "隐空间推理与 VLN", (r"隐空间推理", r"latent reasoning", r"\bvln\b")),
        ("paper_analysis", "论文阅读与理论分析", (r"\.pdf\b", r"\bpaper\b", r"论文", r"llms get lost in multi[- ]turn")),
        ("feishu_collaboration", "飞书文档与协作", (r"feishu", r"lark", r"飞书", r"docx", r"\bwiki\b")),
        ("project_config", "项目配置与环境变量", (r"\.env\b", r"配置文件", r"configuration", r"settings")),
    )

    def classify(self, app: str, title: str = "", url: str = "", context: Optional[dict] = None) -> TopicMatch:
        app_text = str(app or "").strip()
        title_text = str(title or "").strip()
        text = f"{app_text} {title_text} {url or ''}".strip()
        folded = text.casefold()

        if self._is_noise(app_text, title_text, url):
            return TopicMatch("system_noise", "系统噪声", "system_noise", 1.0)
        if NON_WORK_RE.search(text):
            return TopicMatch("non_work", "非工作活动", "non_work", 0.95)

        # Communication apps are one support topic unless their content/title
        # explicitly names a research theme.
        if re.search(r"weixin|wechat|微信", folded):
            return TopicMatch("communication", "沟通与事务", "support", 0.9)

        for theme_id, name, patterns in self._rules:
            if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
                return TopicMatch(theme_id, name, "core", 0.9)

        if re.search(r"chatgpt|kimi|z\.ai|copilot|claude", folded):
            return TopicMatch("ai_assist", "AI 辅助与未归因", "support", 0.55)
        if re.search(r"explorer|file explorer|文件资源管理器", folded):
            return TopicMatch("unclassified", "未归因活动", "unknown", 0.2)
        if not title_text or title_text.casefold() in {"unknown", "untitled"}:
            return TopicMatch("unclassified", "未归因活动", "unknown", 0.1)
        return TopicMatch("unclassified", "未归因活动", "unknown", 0.25)

    @staticmethod
    def _is_noise(app: str, title: str, url: str = "") -> bool:
        value = " ".join(str(part or "") for part in (app, title, url)).strip()
        return (
            not value
            or str(app or "").casefold() == "unknown"
            or bool(COORDINATE_TITLE_RE.fullmatch(str(title or "").strip()))
            or bool(NOISE_RE.search(value))
        )


def parse_event_time(value) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value or "").strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        result = datetime.fromisoformat(text)
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def aggregate_theme_events(events: Iterable[dict], classifier: Optional[TopicClassifier] = None) -> List[dict]:
    """Aggregate already-attributed events into cross-application themes."""
    classifier = classifier or TopicClassifier()
    grouped: Dict[str, dict] = {}
    for event in events:
        duration = max(float(event.get("duration") or 0.0), 0.0)
        if duration <= 0:
            continue
        match = classifier.classify(
            event.get("app", ""),
            event.get("title", ""),
            event.get("url") or event.get("context", ""),
            event,
        )
        bucket = grouped.setdefault(
            match.theme_id,
            {
                "theme_id": match.theme_id,
                "theme_name": match.theme_name,
                "duration_seconds": 0.0,
                "applications": [],
                "activities": [],
                "visibility": match.visibility,
                "confidence": match.confidence,
            },
        )
        bucket["duration_seconds"] += duration
        app = str(event.get("app") or "Unknown").strip()
        title = str(event.get("title") or event.get("context") or "").strip()
        if app and app not in bucket["applications"]:
            bucket["applications"].append(app)
        if title and title not in bucket["activities"]:
            bucket["activities"].append(title)
        bucket["confidence"] = min(bucket["confidence"], match.confidence)

    rows = []
    for item in grouped.values():
        item["duration_seconds"] = round(item["duration_seconds"], 3)
        item["duration_minutes"] = round(item["duration_seconds"] / 60.0, 1)
        rows.append(item)
    return sorted(rows, key=lambda item: item["duration_seconds"], reverse=True)


def build_theme_sessions(events: Iterable[dict], switch_min_seconds: float = 30.0, session_gap_seconds: float = 180.0) -> List[dict]:
    """Build sessions by theme, ignoring short noise switches."""
    classifier = TopicClassifier()
    ordered = []
    for event in events:
        match = classifier.classify(event.get("app", ""), event.get("title", ""), event.get("url") or event.get("context", ""), event)
        if match.visibility in {"system_noise", "non_work"}:
            continue
        duration = max(float(event.get("duration") or 0.0), 0.0)
        if duration <= 0:
            continue
        item = dict(event)
        item["theme_id"] = match.theme_id
        item["theme_name"] = match.theme_name
        item["visibility"] = match.visibility
        item["_start"] = parse_event_time(event.get("timestamp"))
        item["_end"] = item["_start"] + timedelta(seconds=duration)
        ordered.append(item)
    ordered.sort(key=lambda item: item["_start"])

    sessions: List[dict] = []
    for item in ordered:
        if sessions:
            previous = sessions[-1]
            gap = (item["_start"] - previous["end"]).total_seconds()
            if item["theme_id"] == previous["theme_id"] and gap <= session_gap_seconds:
                previous["end"] = max(previous["end"], item["_end"])
                previous["seconds"] = (previous["end"] - previous["start"]).total_seconds()
                continue
            if gap <= switch_min_seconds:
                continue
        sessions.append(
            {
                "theme_id": item["theme_id"],
                "theme_name": item["theme_name"],
                "start": item["_start"],
                "end": item["_end"],
                "seconds": (item["_end"] - item["_start"]).total_seconds(),
                "applications": [item.get("app", "")],
            }
        )
    return sessions
