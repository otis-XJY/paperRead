"""Windows ActivityWatch watcher for pointer monitor and foreground window.

Only event metadata and counters are emitted.  Key contents are never stored.
The watcher is intended to run in the interactive Windows user session, not as
the LocalSystem account.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import os
import socket
import time
from datetime import datetime, timezone

import requests


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class WindowsFocusCollector:
    """Publish observable input-focus events to a local ActivityWatch server."""

    BUCKET_PREFIX = "aw-watcher-focus"

    def __init__(self, base_url=None, active_seconds=60):
        if os.name != "nt":
            raise RuntimeError("focus_watcher.py 只能在 Windows 交互式会话中运行")
        self.base_url = (base_url or os.getenv("ACTIVITYWATCH_URL", "http://127.0.0.1:5600")).rstrip("/")
        self.active_seconds = max(int(active_seconds), 1)
        self.bucket_id = f"{self.BUCKET_PREFIX}_{socket.gethostname()}"
        self._user32 = ctypes.windll.user32
        self._create_bucket()

    def _create_bucket(self):
        response = requests.post(
            f"{self.base_url}/api/0/buckets/{self.bucket_id}",
            json={
                "type": "focus",
                "client": self.BUCKET_PREFIX,
                "hostname": socket.gethostname(),
            },
            timeout=10,
        )
        response.raise_for_status()

    def _window_context(self):
        point = wintypes.POINT()
        if not self._user32.GetCursorPos(ctypes.byref(point)):
            point.x, point.y = 0, 0
        monitor = self._user32.MonitorFromPoint(point, 2)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        monitor_name = "unknown"
        if monitor and self._user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            monitor_name = f"{info.rcMonitor.left},{info.rcMonitor.top},{info.rcMonitor.right},{info.rcMonitor.bottom}"
        hwnd = self._user32.GetForegroundWindow()
        title_buffer = ctypes.create_unicode_buffer(512)
        title = ""
        if hwnd:
            self._user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
            title = title_buffer.value.strip()
        return {
            "monitor": monitor_name,
            "cursor_x": int(point.x),
            "cursor_y": int(point.y),
            "window_title": title,
            "window_handle": int(hwnd or 0),
        }

    def record(self, input_kind):
        context = self._window_context()
        context["input_kind"] = input_kind
        context["keypresses"] = 1 if input_kind == "keypress" else 0
        context["mouse_clicks"] = 1 if input_kind in {"click", "scroll"} else 0
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration": self.active_seconds,
            "data": context,
        }
        response = requests.post(
            f"{self.base_url}/api/0/buckets/{self.bucket_id}/events",
            json=[event],
            timeout=10,
        )
        response.raise_for_status()

    def run(self):
        try:
            from pynput import keyboard, mouse
        except ImportError as exc:
            raise RuntimeError("请安装 pynput 后运行焦点采集器") from exc

        mouse_listener = mouse.Listener(
            on_click=lambda *_args: self.record("click"),
            on_scroll=lambda *_args: self.record("scroll"),
        )
        keyboard_listener = keyboard.Listener(on_press=lambda _key: self.record("keypress"))
        mouse_listener.start()
        keyboard_listener.start()
        print(f"三屏焦点采集已启动：{self.bucket_id}", flush=True)
        try:
            while True:
                time.sleep(1)
        finally:
            mouse_listener.stop()
            keyboard_listener.stop()


def main():
    parser = argparse.ArgumentParser(description="Record Windows pointer/window focus")
    parser.add_argument(
        "--active-seconds",
        type=int,
        default=int(os.getenv("FOCUS_ACTIVE_SECONDS", "60")),
    )
    args = parser.parse_args()
    WindowsFocusCollector(active_seconds=args.active_seconds).run()


if __name__ == "__main__":
    main()
