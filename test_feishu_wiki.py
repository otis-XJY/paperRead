"""
独立测试飞书知识库写入功能

用法：
  1. 确保 .env 中已配置 FEISHU_APP_ID、FEISHU_APP_SECRET、FEISHU_WIKI_ROOT_NODE_TOKEN
  2. （可选）FEISHU_WIKI_DAILY_FOLDER_NAME，与 main.py 使用的日更目录名一致
  3. python test_feishu_wiki.py
"""

import os

# 尝试加载 .env（如果安装了 python-dotenv）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # 未安装 python-dotenv 时，需手动 export 环境变量

from feishu_wiki import FeishuWikiClient

APP_ID = os.getenv("FEISHU_APP_ID")
APP_SECRET = os.getenv("FEISHU_APP_SECRET")
ROOT_NODE = os.getenv("FEISHU_WIKI_ROOT_NODE_TOKEN")
DAILY_FOLDER = os.getenv("FEISHU_WIKI_DAILY_FOLDER_NAME", "DailyPapers")

if not all([APP_ID, APP_SECRET, ROOT_NODE]):
    print("❌ 请先在 .env 中配置以下变量：")
    print("   FEISHU_APP_ID")
    print("   FEISHU_APP_SECRET")
    print("   FEISHU_WIKI_ROOT_NODE_TOKEN")
    exit(1)

# 模拟一条论文数据（结构与 main.py 中 mirror_paper_to_wiki 的入参一致）
MOCK_PAPER = {
    "title": "[Test] Feishu Wiki Integration Test Paper",
    "authors": ["Alice", "Bob"],
    "published": "2026-05-08",
    "arxiv_id": "2605.00001",
    "recommendation": "必读",
    "methodology": "This is a test document to verify Feishu Wiki write functionality.",
    "core_concepts": ["test", "feishu", "wiki"],
    "sharp_review": "A mock paper purely for integration testing. No real content.",
    "summary": "Testing if the pipeline can successfully create and write to a Feishu Wiki document.",
}

CATEGORY = "TestCategory"


def main():
    print("=== 飞书知识库写入测试 ===\n")

    client = FeishuWikiClient(
        app_id=APP_ID,
        app_secret=APP_SECRET,
        root_node_token=ROOT_NODE,
        daily_folder_name=DAILY_FOLDER,
    )

    # Step 1: 测试 token 获取
    print("[1/4] 测试 tenant_access_token 获取...")
    token = client._ensure_token()
    print(f"  ✅ token 获取成功: {token[:10]}...")

    # Step 2: 测试 wiki 空间发现
    print("[2/4] 测试 wiki 空间发现...")
    space_id = client._discover_space_id()
    print(f"  ✅ space_id: {space_id}")

    # Step 3: 测试分类节点创建/查找
    print(f"[3/4] 确保分类节点 '{CATEGORY}' 存在...")
    node_token = client.ensure_category_node(CATEGORY)
    print(f"  ✅ 分类节点 token: {node_token}")

    # Step 4: 写入测试论文
    print(f"[4/4] 写入测试论文 '{MOCK_PAPER['title'][:50]}'...")
    doc_url = client.mirror_paper_to_wiki(CATEGORY, MOCK_PAPER)
    print(f"  ✅ 文档已创建: {doc_url}")

    print("\n=== 测试完成 ===")
    print(f"请前往飞书知识库查看: {doc_url}")


if __name__ == "__main__":
    main()
