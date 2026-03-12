import os
import json
import re
import time
from pyzotero import zotero
from bs4 import BeautifulSoup

ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID")
ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY")

if not ZOTERO_USER_ID or not ZOTERO_API_KEY:
    raise ValueError("缺少 Zotero 凭据，请检查 ZOTERO_USER_ID / ZOTERO_API_KEY")

zot = zotero.Zotero(ZOTERO_USER_ID, 'user', ZOTERO_API_KEY)


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


def get_item_children(item_key, title=""):
    # 兼容不同 pyzotero 版本：优先 item_children，其次 children
    if hasattr(zot, "item_children"):
        return retry_sync(lambda: zot.item_children(item_key), f"读取条目子项({title[:20]})")
    if hasattr(zot, "children"):
        return retry_sync(lambda: zot.children(item_key), f"读取条目子项({title[:20]})")
    raise AttributeError("当前 pyzotero 版本不支持读取条目子项（缺少 item_children/children 方法）")

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
    
    collections = retry_sync(lambda: zot.collections(), "读取 Zotero 集合列表")
    for coll in collections:
        cat_name = coll['data']['name']
        if cat_name == "DailyPapers": continue 
        
        print(f"正在索引分类: {cat_name}")
        items = retry_sync(lambda: zot.collection_items(coll['key']), f"读取分类条目({cat_name})")
        kb.setdefault(cat_name, [])
        
        for item in items:
            if item['data']['itemType'] in ['preprint', 'journalArticle']:
                title = item['data'].get('title', '')
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

    with open("knowledge_base.json", "w", encoding="utf-8") as f:
        json.dump(kb, f, ensure_ascii=False, indent=2)
    print("✅ 知识库构建完成！")

if __name__ == "__main__":
    build_knowledge_base()