import asyncio
import aiohttp
import feedparser
import json
import os
import re
import urllib.parse
from datetime import datetime
from openai import OpenAI
from pyzotero import zotero

# ================= 1. 配置区 =================
CONFIG = {
    "categories": {
        "UAV_VLN": 
        {"keywords":
         ['ti:"Vision-Language Navigation"', 
          '(abs:UAV AND abs:Navigation)'], 
          "desc": "无人机视觉语言导航、空间感知及指令执行。"},
        "MultiAgent_Game_Theory": 
        {"keywords":
         ['ti:"Game Theory" AND abs:Multi-agent', 
          'ti:"Decision Making" AND cat:cs.MA'], 
          "desc": "多智能体决策规划、博弈论及动态博弈。"},
        "MARL": 
        {"keywords":
         ['ti:"Multi-Agent Reinforcement Learning"', 
          'all:MARL', 
          'all:CTDE'], 
          "desc": "多智能体强化学习算法、协作机制及通信协议。"},
        "Humanoid_Manipulation": 
        {"keywords":
         ['ti:Humanoid AND abs:Manipulation', 
          'abs:"Dexterous Hand"', 
          'ti:"Whole-body Control"'], 
          "desc": "人形机器人操作、灵巧手抓取及全身协调控制。"}
    },
    "llm_model": "Qwen/Qwen3.5-35B-A3B",
    "base_url": "https://api-inference.modelscope.cn/v1/"
}

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), base_url=CONFIG["base_url"])
zot = zotero.Zotero(os.getenv("ZOTERO_USER_ID"), 'user', os.getenv("ZOTERO_API_KEY"))

STATE_FILE = "state.json"
HISTORY_FILE = "history.json"

def safe_json_parse(text):
    try: return json.loads(text)
    except:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        return json.loads(match.group(0)) if match else {}

def get_or_create_collection(name, parent_key=None):
    colls = zot.collections()
    for c in colls:
        if c['data']['name'] == name and c['data'].get('parentCollection') == parent_key:
            return c['key']
    resp = zot.create_collections([{'name': name, 'parentCollection': parent_key}])
    return resp['successful']['0']['key']

# ================= 2. 状态管理 =================
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f: return json.load(f)
    return {"is_first_run": True, "last_date": "2000-01-01T00:00:00Z"}

def save_state(last_date):
    with open(STATE_FILE, "w") as f:
        json.dump({"is_first_run": False, "last_date": last_date}, f)

# ================= 3. 两阶段 AI 分析 =================
def check_relevance_phase_one(paper, kb_entries):
    # 提取短评作为上下文，极致省 Token
    short_context = [{"title": kb["title"], "review": kb["short_review"]} for kb in kb_entries]
    
    prompt = f"""
    判断待分析论文与已读库的关联度（0-10分）。
    【已读库简述】：{json.dumps(short_context, ensure_ascii=False)}
    【待分析论文】：{paper['title']} | 摘要：{paper['summary']}
    
    任务：
    1. 评估相关性分数。
    2. 如果分数 >= 7，找出【已读库】中哪几篇论文与它最相关（提供精确的 title 列表）。
    
    返回严格JSON: {{"is_relevant": true/false, "score": 8, "matched_titles": ["论文A", "论文B"]}}
    """
    try:
        res = client.chat.completions.create(
            model=CONFIG["llm_model"],
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        return safe_json_parse(res.choices[0].message.content)
    except Exception as e:
        print(f"阶段一初筛报错: {e}")
        return {"is_relevant": False, "matched_titles":[]}

def deep_analyze_phase_two(paper, category_name, matched_full_notes):
    prompt = f"""
    你是{category_name}专家。
    【你过去写下的核心笔记】（仅针对强相关论文）：{json.dumps(matched_full_notes, ensure_ascii=False)}
    
    【今日新论文】：标题：{paper['title']} | 摘要：{paper['summary']}
    
    任务：深入对比新老论文，严格输出 JSON 格式：
    {{"recommendation": "必读/值得看/可跳过", "comparison": "一句话说明与你过去笔记中论文的具体异同", "methodology": "核心方法简述", "core_concepts": ["术语1"], "sharp_review": "批判性分析"}}
    """
    try:
        res = client.chat.completions.create(
            model=CONFIG["llm_model"],
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        return safe_json_parse(res.choices[0].message.content)
    except Exception as e:
        print(f"阶段二深读报错: {e}")
        return None

# ================= 4. 动态抓取模块 =================
async def fetch_arxiv(session, keywords, state):
    all_papers = {}
    max_published_date = state["last_date"]
    
    for kw in keywords:
        encoded_kw = urllib.parse.quote(kw)
        urls_to_fetch = []
        
        if state["is_first_run"]:
            print(f"🚀 首次运行：拉取最新10篇 + 认可度最高(Relevance)10篇 -> {kw}")
            urls_to_fetch.append(f"http://export.arxiv.org/api/query?search_query={encoded_kw}&sortBy=submittedDate&sortOrder=descending&max_results=10")
            urls_to_fetch.append(f"http://export.arxiv.org/api/query?search_query={encoded_kw}&sortBy=relevance&sortOrder=descending&max_results=10")
        else:
            print(f"🔍 增量拉取：抓取最新论文对比 {state['last_date']} -> {kw}")
            urls_to_fetch.append(f"http://export.arxiv.org/api/query?search_query={encoded_kw}&sortBy=submittedDate&sortOrder=descending&max_results=30")

        for url in urls_to_fetch:
            async with session.get(url) as resp:
                if resp.status != 200: continue
                feed = feedparser.parse(await resp.text())
                
                for e in feed.entries:
                    pub_date = e.get('published', '')
                    # 增量过滤逻辑：只接受比 last_date 新的论文（首次运行时 last_date 极小，等于全收）
                    if pub_date > state["last_date"]:
                        pid = e.id.split('/')[-1]
                        all_papers[pid] = {"id": pid, "title": e.title.replace('\n', ' '), "summary": e.summary.replace('\n', ' '), "published": pub_date}
                        if pub_date > max_published_date:
                            max_published_date = pub_date

    return list(all_papers.values()), max_published_date

# ================= 5. 主流程 =================
async def main():
    if not os.path.exists("knowledge_base.json"):
        print("❌ 找不到 knowledge_base.json，请先运行 zotero_indexer.py")
        return
        
    with open("knowledge_base.json", "r", encoding="utf-8") as f: kb = json.load(f)
    history = json.load(open(HISTORY_FILE, "r")) if os.path.exists(HISTORY_FILE) else[]
    
    state = load_state()
    global_max_date = state["last_date"]

    root_key = get_or_create_collection("DailyPapers")
    cat_keys = {name: get_or_create_collection(name, root_key) for name in CONFIG["categories"]}

    async with aiohttp.ClientSession() as session:
        for cat_name, cat_info in CONFIG["categories"].items():
            print(f"\n--- 正在处理分类: {cat_name} ---")
            
            # 动态抓取（支持首次与增量）
            papers, cat_max_date = await fetch_arxiv(session, cat_info["keywords"], state)
            if cat_max_date > global_max_date: global_max_date = cat_max_date
            
            kb_entries = kb.get(cat_name, [])
            
            for p in papers:
                if p['id'] in history: continue
                history.append(p['id'])
                
                # 阶段一：轻量化相关性初筛
                phase_one_res = check_relevance_phase_one(p, kb_entries)
                if not phase_one_res.get("is_relevant"):
                    print(f"⏭️ 评分不够或无相关性，跳过: {p['title'][:30]}...")
                    continue
                
                # 阶段二：组装深读上下文并深度对比
                matched_titles = phase_one_res.get("matched_titles",[])
                print(f"🧠 强相关！命中历史笔记 {len(matched_titles)} 篇，开始深读对比: {p['title'][:30]}...")
                
                matched_full_notes = [{"title": entry["title"], "note": entry["full_note"]} 
                                      for entry in kb_entries if entry["title"] in matched_titles]
                
                analysis = deep_analyze_phase_two(p, cat_name, matched_full_notes)
                if not analysis or analysis.get("recommendation") == "可跳过": continue
                
                # 写入 Zotero
                item = zot.item_template('preprint')
                item['title'] = p['title']
                item['abstractNote'] = p['summary']
                item['url'] = f"https://arxiv.org/abs/{p['id']}"
                item['collections'] = [cat_keys[cat_name]]
                item['tags'] =[{"tag": cat_name}, {"tag": analysis.get("recommendation", "值得看")}]
                
                resp = zot.create_items([item])
                if resp['successful']:
                    item_key = list(resp['successful'].values())[0]['key']
                    badge_color = "#d9534f" if analysis.get('recommendation') == "必读" else "#f0ad4e"
                    concepts_html = "".join([f'<span style="background:#eef; color:#3366ff; padding:2px 6px; border-radius:10px; margin-right:5px; font-size:0.9em;">[[{c}]]</span>' for c in analysis.get('core_concepts',[])])
                    
                    # 动态生成关联信息
                    matched_html = f"<p><strong>🔗 触发的灵感来源：</strong> {', '.join(matched_titles)}</p>" if matched_titles else ""
                    
                    note_html = f"""
                    <h2 style="color: #2c3e50; border-bottom: 2px solid #eee;">{p['title']}</h2>
                    <p><strong>🔥 推荐指数：</strong> <span style="background:{badge_color}; color:white; padding:2px 8px; border-radius:4px;">{analysis.get('recommendation')}</span></p>
                    {matched_html}
                    <div style="background:#f9f9f9; border-left:5px solid #007bff; padding:10px; margin:10px 0;">
                        <strong>🔄 深度差量对比：</strong><br/>{analysis.get('comparison', '')}
                    </div>
                    <h3 style="color: #2980b9;">🧠 核心术语库</h3><p>{concepts_html}</p>
                    <h3 style="color: #2980b9;">🔬 方法论简析</h3><p>{analysis.get('methodology', '')}</p>
                    <h3 style="color: #2980b9;">💬 锐评</h3><p><i>{analysis.get('sharp_review', '')}</i></p>
                    """
                    
                    note_template = zot.item_template('note')
                    note_template['note'] = note_html
                    note_template['parentItem'] = item_key
                    zot.create_items([note_template])
                    print(f"✅ 成功同步至 Zotero")

    # 持久化状态
    with open(HISTORY_FILE, "w") as f: json.dump(history, f)
    save_state(global_max_date)
    print(f"\n🎉 任务完成！记录的最新论文时间戳为：{global_max_date}")

if __name__ == "__main__":
    asyncio.run(main())