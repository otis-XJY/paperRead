import os
import json
import re
import time
from collections import defaultdict
from pyzotero import zotero
from bs4 import BeautifulSoup

ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID")
ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY")

if not ZOTERO_USER_ID or not ZOTERO_API_KEY:
    raise ValueError("缺少 Zotero 凭据，请检查 ZOTERO_USER_ID / ZOTERO_API_KEY")

zot = zotero.Zotero(ZOTERO_USER_ID, 'user', ZOTERO_API_KEY)


def extract_collection_link(collection_obj):
    """从 Zotero API 返回的集合对象中提取网页链接（优先 API 返回的真实 URL）"""
    if not isinstance(collection_obj, dict):
        return ""
    # 优先使用 API 返回的 links.alternate.href（权威且正确）
    links = collection_obj.get("links") or {}
    alt_link = (links.get("alternate") or {}).get("href", "")
    if alt_link:
        return alt_link
    # 降级：手动构建（如果 API 没有提供链接）
    collection_key = collection_obj.get("key", "")
    if not ZOTERO_USER_ID or not collection_key:
        return ""
    return f"https://www.zotero.org/users/{ZOTERO_USER_ID}/collections/{collection_key}"


def retry_sync(operation, operation_name, retries=3, base_delay=1.0):
    for attempt in range(retries):
        try:
            return operation()
        except Exception as e:
            # 接口不存在属于编程错误，直接抛出，避免无效重试
            if isinstance(e, AttributeError):
                raise
            if attempt == retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            print(f"⚠️ {operation_name} 失败（第 {attempt + 1}/{retries} 次）: {e}，{delay:.1f}s 后重试")
            time.sleep(delay)


def get_or_create_collection(name, parent_key=None):
    """创建或获取 Zotero 集合"""
    colls = retry_sync(lambda: zot.everything(zot.collections()), f"读取集合列表({name})")
    target_parent = normalize_parent_collection(parent_key)
    matched = []
    for c in colls:
        collection_parent = normalize_parent_collection(c['data'].get('parentCollection'))
        if c['data']['name'] == name and collection_parent == target_parent:
            matched.append(c)

    if matched:
        matched.sort(key=lambda x: x['data'].get('dateAdded', ''))
        return matched[0]['key']

    # 动态构造 payload，顶层集合不含 parentCollection 字段
    payload = {'name': name}
    if parent_key:
        payload['parentCollection'] = parent_key

    resp = retry_sync(
        lambda: zot.create_collections([payload]),
        f"创建集合({name})"
    )
    
    if '0' not in resp.get('successful', {}):
        raise RuntimeError(f"创建集合失败，API返回: {resp.get('failed')}")
    return resp['successful']['0']['key']


def ensure_collection_structure(root_key, categories):
    """确保 DailyPapers 及其子分类框架已创建"""
    print(f"📁 确保分类框架已就绪...")
    for cat_name in categories:
        try:
            cat_key = get_or_create_collection(cat_name, root_key)
            print(f"   ✓ {cat_name}: {cat_key}")
        except Exception as e:
            print(f"   ⚠️  {cat_name} 创建失败: {e}")



def get_item_children(item_key, title=""):
    # 兼容不同 pyzotero 版本：优先 item_children，其次 children
    if hasattr(zot, "item_children"):
        return retry_sync(lambda: zot.item_children(item_key), f"读取条目子项({title[:20]})")
    if hasattr(zot, "children"):
        return retry_sync(lambda: zot.children(item_key), f"读取条目子项({title[:20]})")
    raise AttributeError("当前 pyzotero 版本不支持读取条目子项（缺少 item_children/children 方法）")


def normalize_parent_collection(parent_value):
    if parent_value in (None, "", False):
        return None
    return parent_value


def get_or_create_daily_root_collection(collections):
    daily_roots = []
    for coll in collections:
        if coll['data'].get('name') != "DailyPapers":
            continue
        if normalize_parent_collection(coll['data'].get('parentCollection')) is None:
            daily_roots.append(coll)

    if daily_roots:
        # 若历史上存在多个同名根集合，复用最早创建的，避免继续扩散
        daily_roots.sort(key=lambda x: x['data'].get('dateAdded', ''))
        return daily_roots[0]['key']

    resp = retry_sync(lambda: zot.create_collections([{'name': 'DailyPapers'}]), "创建 DailyPapers 根集合")
    if '0' not in resp.get('successful', {}):
        raise RuntimeError(f"创建 DailyPapers 失败，API返回: {resp.get('failed')}")
    return resp['successful']['0']['key']

def extract_note_parts(html_content):
    if not html_content:
        return "", ""
    soup = BeautifulSoup(html_content, "html.parser")
    full_text = soup.get_text(separator=" ", strip=True)
    
    # 尝试提取我们之前生成的 "💬 锐评" 部分
    sharp_review = ""
    review_header = soup.find(string=re.compile("锐评"))
    if review_header:
        # 找到锐评标题后的 p 标签
        parent = review_header.parent
        next_p = parent.find_next_sibling("p")
        if next_p:
            sharp_review = next_p.get_text(strip=True)
            
    if not sharp_review:
        sharp_review = full_text[:100] + "..." # 降级方案
        
    return sharp_review, full_text

def build_knowledge_base():
    print("🔄 开始构建 Zotero 知识库...")
    kb = {}
    
    collections = retry_sync(lambda: zot.everything(zot.collections()), "读取 Zotero 集合列表")

    # 首次运行若不存在 DailyPapers，则自动创建一个根集合
    root_key = get_or_create_daily_root_collection(collections)
    if root_key:
        # 立即刷新集合列表，确保新创建的 DailyPapers 被识别
        collections = retry_sync(lambda: zot.everything(zot.collections()), "刷新 Zotero 集合列表")
        
        # 找到根集合对象以提取真实链接
        root_obj = None
        for coll in collections:
            if coll['key'] == root_key:
                root_obj = coll
                break
        root_link = extract_collection_link(root_obj) if root_obj else ""
        print(f"📁 DailyPapers 根集合已就绪: {root_key}")
        if root_link:
            print(f"🔗 DailyPapers 链接: {root_link}")
        
        # 【新增】确保子分类框架 (首次运行时创建所有必要的分类在 DailyPapers 下)
        categories = ["UAV_VLN", "MultiAgent_Game_Theory", "MARL", "Humanoid_Manipulation"]
        ensure_collection_structure(root_key, categories)
        
        # 重新拉取，确保后续分组逻辑看到最新集合列表
        collections = retry_sync(lambda: zot.everything(zot.collections()), "刷新 Zotero 集合列表")

    daily_root_keys = set()
    for coll in collections:
        if coll['data'].get('name') != "DailyPapers":
            continue
        if normalize_parent_collection(coll['data'].get('parentCollection')) is None:
            daily_root_keys.add(coll['key'])

    if not daily_root_keys:
        print("⚠️ 未找到 DailyPapers 根集合，knowledge_base 将为空。")
    else:
        print(f"📁 DailyPapers 根集合数量: {len(daily_root_keys)}")

    grouped = defaultdict(list)
    for coll in collections:
        cat_name = coll['data']['name']
        if cat_name == "DailyPapers":
            continue

        parent_key = normalize_parent_collection(coll['data'].get('parentCollection'))
        if parent_key not in daily_root_keys:
            continue
        grouped[cat_name].append(coll)

    for cat_name, coll_objs in grouped.items():
        cat_links = [extract_collection_link(coll_obj) for coll_obj in coll_objs if coll_obj]
        cat_links_str = " | ".join(cat_links) if cat_links else "无"
        print(f"正在索引分类: {cat_name}（集合数: {len(coll_objs)}）")
        print(f"   🔗 分类链接: {cat_links_str}")
        kb.setdefault(cat_name, [])
        seen_titles = set()

        for coll_obj in coll_objs:
            coll_key = coll_obj['key']
            # Zotero API 默认不会返回回收站项目；itemType 不支持 "-trashed" 这种写法
            items = retry_sync(lambda key=coll_key: zot.collection_items(key), f"读取分类条目({cat_name})")

            for item in items:
                if item['data']['itemType'] not in ['preprint', 'journalArticle']:
                    continue

                title = (item['data'].get('title', '') or '').strip()
                if not title or title in seen_titles:
                    continue

                children = get_item_children(item['key'], title)

                sharp_review, full_note = "", ""
                for child in children:
                    if child['data']['itemType'] == 'note':
                        sharp_review, full_note = extract_note_parts(child['data'].get('note', ''))

                kb[cat_name].append({
                    "title": title,
                    "short_review": sharp_review,  # 用于阶段一初筛
                    "full_note": full_note         # 用于阶段二深读
                })
                seen_titles.add(title)

    with open("knowledge_base.json", "w", encoding="utf-8") as f:
        json.dump(kb, f, ensure_ascii=False, indent=2)
    print("✅ 知识库构建完成！")

if __name__ == "__main__":
    build_knowledge_base()