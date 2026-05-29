#!/usr/bin/env python3
"""
初始化飞书知识库目录结构：
  根 → 日更文件夹 → 各研究分类 → 推荐级别（必读/值得看/可跳过）

同时将已有论文根据文档中的推荐类型迁移至对应子目录。

用法：
  1. .env 中配置 FEISHU_APP_ID、FEISHU_APP_SECRET、FEISHU_WIKI_ROOT_NODE_TOKEN
  2. 可选：FEISHU_WIKI_DAILY_FOLDER_NAME（默认 DailyPapers）
  3. python bootstrap_feishu_wiki_layout.py

可选环境变量 FEISHU_WIKI_CATEGORIES：逗号分隔的分类名；省略则使用与 main.py CONFIG["categories"] 一致的默认三项。
"""

import os
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from feishu_wiki import FeishuWikiClient, CACHE_FILE

# 与 main.py 中 CONFIG["categories"] 的键保持一致（避免 import main 触发 LLM Key 校验）
_DEFAULT_CATEGORIES = ("UAV_VLN", "MultiAgent_Game_Theory", "MARL")


def _categories():
    raw = os.getenv("FEISHU_WIKI_CATEGORIES", "").strip()
    if raw:
        return [x.strip() for x in raw.split(",") if x.strip()]
    return list(_DEFAULT_CATEGORIES)


def main():
    app_id = os.getenv("FEISHU_APP_ID")
    secret = os.getenv("FEISHU_APP_SECRET")
    root = os.getenv("FEISHU_WIKI_ROOT_NODE_TOKEN")
    daily_name = os.getenv("FEISHU_WIKI_DAILY_FOLDER_NAME", "DailyPapers")

    if not all([app_id, secret, root]):
        print("❌ 请在 .env 中配置 FEISHU_APP_ID、FEISHU_APP_SECRET、FEISHU_WIKI_ROOT_NODE_TOKEN")
        sys.exit(1)

    cats = _categories()
    print(f"将在「{daily_name}」下创建/确认分类节点: {', '.join(cats)}")
    print()

    client = FeishuWikiClient(
        app_id=app_id,
        app_secret=secret,
        root_node_token=root,
        daily_folder_name=daily_name,
    )

    # Step 1: 获取 token
    print("[1/3] 获取 tenant_access_token...")
    token = client._ensure_token()
    print(f"  ✅ token: {token[:10]}...")

    # Step 2: 发现 wiki 空间
    print("[2/3] 发现 wiki 空间...")
    space_id = client._discover_space_id()
    print(f"  ✅ space_id: {space_id}")

    # Step 3: 创建/确认分类节点
    print(f"[3/3] 创建/确认分类节点...")
    client.bootstrap_layout(cats)

    # 验证最终结构（含推荐级别子节点）
    print()
    print("=== 最终目录结构 ===")
    root_children = client.list_child_nodes(root)
    daily_token = root_children.get(daily_name)
    if daily_token:
        print(f"📁 {daily_name} ({daily_token})")
        cat_children = client.list_child_nodes(daily_token)
        for title, token in cat_children.items():
            print(f"  📁 {title} ({token})")
            rec_children = client.list_child_nodes(token)
            for rec_title, rec_token in rec_children.items():
                print(f"    📁 {rec_title} ({rec_token})")
    else:
        print(f"⚠️ 未找到「{daily_name}」节点")

    print()
    print("✅ 目录已就绪。之后运行 python main.py 时，论文会写入对应分类下。")


if __name__ == "__main__":
    main()
