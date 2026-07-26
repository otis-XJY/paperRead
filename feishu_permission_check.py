"""Read-only permission checks for the daily-report Feishu application."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from feishu_sdk import FeishuOpenAPIClient, configured_feishu_credentials

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def check(label, callback):
    try:
        result = callback()
        print(f"[PASS] {label}: {result}")
        return True
    except Exception as exc:
        print(f"[FAIL] {label}: {exc}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Check daily-report Feishu app permissions without writing any resource"
    )
    parser.add_argument(
        "--wiki-token",
        help="日报知识库根节点 token; type=wiki",
    )
    parser.add_argument(
        "--doc-token",
        help="可选的具体 docx 文档 token",
    )
    parser.add_argument(
        "--chat-id",
        help="日报群聊 ID，通常以 oc_ 开头",
    )
    args = parser.parse_args()

    app_id, app_secret = configured_feishu_credentials("DAILY_REPORT_")
    if not app_id or not app_secret:
        raise SystemExit(
            "未找到日报机器人凭证，请配置 DAILY_REPORT_FEISHU_APP_ID/SECRET "
            "或 FEISHU_APP_ID/SECRET。"
        )

    client = FeishuOpenAPIClient(app_id, app_secret)
    passed = 0
    total = 0

    def run(label, callback):
        nonlocal passed, total
        total += 1
        passed += int(check(label, callback))

    run(
        "应用身份可调用群聊 API",
        lambda: f"可见群聊数 {len(client.request('GET', '/im/v1/chats', params={'page_size': '1'}).get('items', []))}（仅取 1 页）",
    )

    if args.chat_id:
        now = datetime.now(timezone.utc)
        params = {
            "container_id_type": "chat",
            "container_id": args.chat_id,
            "start_time": str(int((now - timedelta(days=1)).timestamp())),
            "end_time": str(int(now.timestamp())),
            "sort_type": "ByCreateTimeAsc",
            "page_size": "1",
        }
        run(
            "日报群聊历史消息读取",
            lambda: f"读取到 {len(client.request('GET', '/im/v1/messages', params=params).get('items', []))} 条（最多检查 1 条）",
        )
    else:
        print("[SKIP] 日报群聊历史消息读取：未提供 --chat-id")

    if args.wiki_token:
        def wiki_auth(action):
            data = client.request(
                "GET",
                f"/drive/v1/permissions/{args.wiki_token}/members/auth",
                params={"type": "wiki", "action": action},
            )
            if not data.get("auth_result"):
                raise RuntimeError(f"auth_result={data.get('auth_result')}")
            return "auth_result=true"

        run("知识库根节点阅读权限", lambda: wiki_auth("view"))
        run("知识库根节点编辑权限", lambda: wiki_auth("edit"))
    else:
        print("[SKIP] 知识库根节点权限：未提供 --wiki-token")

    if args.doc_token:
        def doc_auth(action):
            data = client.request(
                "GET",
                f"/drive/v1/permissions/{args.doc_token}/members/auth",
                params={"type": "docx", "action": action},
            )
            if not data.get("auth_result"):
                raise RuntimeError(f"auth_result={data.get('auth_result')}")
            return "auth_result=true"

        run("具体文档阅读权限", lambda: doc_auth("view"))
        run("具体文档编辑权限", lambda: doc_auth("edit"))

    print(f"结果：{passed}/{total} 项通过")
    raise SystemExit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
