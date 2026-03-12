import os
import json
import re
from pyzotero import zotero
from bs4 import BeautifulSoup

ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID")
ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY")

zot = zotero.Zotero(ZOTERO_USER_ID, 'user', ZOTERO_API_KEY)

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
    
    for coll in zot.collections():
        cat_name = coll['data']['name']
        if cat_name == "DailyPapers": continue 
        
        print(f"正在索引分类: {cat_name}")
        items = zot.collection_items(coll['key'])
        kb[cat_name] =[]
        
        for item in items:
            if item['data']['itemType'] in ['preprint', 'journalArticle']:
                title = item['data'].get('title', '')
                children = zot.item_children(item['key'])
                
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