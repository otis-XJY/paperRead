import asyncio
import aiohttp
import feedparser
import json
import os
from datetime import datetime, timedelta
from openai import OpenAI
from pyzotero import zotero

# ================= 🚀 灵活配置区 =================
# ================= 🚀 灵活配置区 (针对 UAV/MARL/人形机器人 优化) =================
CONFIG = {
    "categories": {
        "UAV_VLN": {
            # 关注无人机视角下的视觉语言导航
            "keywords": [
                'ti:"Vision-Language Navigation"', 
                'abs:"UAV" AND abs:"Navigation"', 
                'abs:"Drone" AND abs:"Language"',
                'ti:"Aerial" AND abs:"VLN"'
            ],
            "desc": "关注无人机(UAV/Drone)环境下的视觉语言导航、跨模态空间感知、自然语言指令执行及机载实时导航算法。"
        },
        "MultiAgent_Game_Theory": {
            # 关注多智能体决策、博弈论、规划
            "keywords": [
                'ti:"Game Theory" AND abs:"Multi-agent"', 
                'ti:"Decision Making" AND cat:cs.MA', 
                'abs:"Nash Equilibrium" AND abs:"Planning"',
                'ti:"Adversarial" AND abs:"Decision"'
            ],
            "desc": "关注多智能体决策规划、博弈论在协同与对抗中的应用、纳什均衡计算以及动态环境下的多机战略博弈。"
        },
        "MARL": {
            # 关注多智能体强化学习
            "keywords": [
                'ti:"Multi-Agent Reinforcement Learning"', 
                'abs:"MARL"', 
                'abs:"CTDE"',  # Centralized Training Decentralized Execution
                'ti:"Cooperative" AND abs:"Reinforcement Learning"'
            ],
            "desc": "关注多智能体强化学习(MARL)的核心算法，如协作机制(CTDE)、信用分配、通信协议以及大规模多智能体训练的稳定性。"
        },
        "Humanoid_Manipulation": {
            # 关注人形机器人操作
            "keywords": [
                'ti:"Humanoid" AND abs:"Manipulation"', 
                'abs:"Dexterous Hand"', 
                'ti:"Whole-body Control"',
                'cat:cs.RO AND "Humanoid Robot"'
            ],
            "desc": "关注人形机器人的上肢操作、灵巧手抓取、全身协调控制(WBC)、触觉反馈以及模仿学习在人形操作中的应用。"
        }
    },
    "comparison_depth": 5, 
    "llm_model": "Qwen/Qwen3.5-35B-A3B", 
    "base_url": "https://api-inference.modelscope.cn/v1/" 
}
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID")
ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY, base_url=CONFIG["base_url"])
zot = zotero.Zotero(ZOTERO_USER_ID, 'user', ZOTERO_API_KEY)

# ================= 📦 Zotero 操作辅助 =================
def get_or_create_collection(name, parent_key=None):
    colls = zot.collections()
    for c in colls:
        if c['data']['name'] == name and c['data'].get('parentCollection') == parent_key:
            return c['key']
    resp = zot.create_collections([{'name': name, 'parentCollection': parent_key}])
    return resp['successful']['0']['key']

def get_existing_papers_in_category(collection_key):
    """提取该分类下已有的论文标题，用于 AI 比较"""
    items = zot.collection_items(collection_key, limit=CONFIG["comparison_depth"])
    titles = [i['data'].get('title') for i in items if i['data']['itemType'] == 'preprint']
    return titles

# ================= 🔍 抓取模块 (支持高级语法) =================
async def fetch_arxiv(session, keywords):
    # 算“昨天”的日期，arXiv 延迟通常为 1-2 天，建议调试时可以用 days=2
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    # 构建复杂的查询字符串
    # 示例: (ti:"Reasoning") OR (abs:"RAG")
    query_string = " OR ".join([f"({kw})" for kw in keywords])
    url = f"http://export.arxiv.org/api/query?search_query={query_string}&sortBy=submittedDate&sortOrder=desc&max_results=30"
    
    async with session.get(url) as response:
        feed = feedparser.parse(await response.text())
        papers = []
        for e in feed.entries:
            # 过滤日期
            if e.published.startswith(yesterday):
                papers.append({
                    "id": e.id.split('/')[-1],
                    "title": e.title.replace('\n',' '),
                    "summary": e.summary.replace('\n',' '),
                    "published": e.published
                })
        return papers

# ================= 🧠 AI 差量分析模块 =================
def analyze_paper_with_context(paper, category_name, context_titles):
    prompt = f"""
    你是一个{category_name}领域的专家。
    【当前领域关注点】：{CONFIG['categories'][category_name]['desc']}
    【已读相关论文】：{json.dumps(context_titles, ensure_ascii=False)}
    
    【待分析论文】：
    标题：{paper['title']}
    摘要：{paper['summary']}
    
    请严格按 JSON 格式输出以下深度分析：
    1. recommendation: 分为 "必读"、"值得看"、"可跳过"。
    2. comparison: 相比于已读列表，本文的独特之处（是开创、补充还是微调？）。
    3. methodology: 核心方法论或数学逻辑的简述。
    4. core_concepts: 提取3-5个核心术语（用于知识图谱）。
    5. sharp_review: 犀利的批判性短评（优缺点）。
    
    输出示例：
    {{
      "recommendation": "必读",
      "comparison": "在已读论文A的基础上，将RAG的检索环节替换为了动态图搜索...",
      "methodology": "采用了跨模态注意力机制进行对齐，计算复杂度从O(N^2)降至O(N)...",
      "core_concepts": ["Dynamic Graph", "Context Injection"],
      "sharp_review": "方法极具启发性，但实验部分仅在小型数据集上验证，泛化能力存疑。"
    }}
    """
    try:
        res = client.chat.completions.create(
            model=CONFIG["llm_model"],
            messages=[{"role": "system", "content": "你是一个严谨的学术助手。"},
                      {"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        return json.loads(res.choices[0].message.content)
    except Exception as e:
        print(f"AI 分析失败: {e}")
        return None

# ================= 📄 结构化笔记生成 =================
def create_html_note(paper, analysis):
    badge_color = "#d9534f" if analysis['recommendation'] == "必读" else "#f0ad4e"
    concepts_html = "".join([f'<span style="background:#eef; color:#3366ff; padding:2px 6px; border-radius:10px; margin-right:5px; font-size:0.9em;">[[{c}]]</span>' for c in analysis.get('core_concepts', [])])
    
    html = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6;">
        <h2 style="color: #2c3e50; border-bottom: 2px solid #eee;">{paper['title']}</h2>
        
        <p><strong>🔥 推荐指数：</strong> <span style="background:{badge_color}; color:white; padding:2px 8px; border-radius:4px;">{analysis['recommendation']}</span></p>

        <div style="background:#f9f9f9; border-left:5px solid #007bff; padding:10px; margin:10px 0;">
            <strong>🔄 差量对比（较已读）：</strong><br/>
            {analysis['comparison']}
        </div>

        <h3 style="color: #2980b9;">🧠 核心术语库</h3>
        <p>{concepts_html}</p>

        <h3 style="color: #2980b9;">🔬 方法论简析</h3>
        <p>{analysis['methodology']}</p>

        <h3 style="color: #2980b9;">💬 锐评</h3>
        <p><i>{analysis['sharp_review']}</i></p>
        
        <hr/>
        <p style="font-size:0.8em; color:#95a5a6;">分析生成于: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
    </div>
    """
    return html

# ================= 🚀 主流程 =================
async def main():
    root_key = get_or_create_collection("DailyPapers")
    cat_keys = {name: get_or_create_collection(name, root_key) for name in CONFIG["categories"]}
    
    history = []
    if os.path.exists("history.json"):
        with open("history.json") as f:
            try: history = json.load(f)
            except: history = []

    async with aiohttp.ClientSession() as session:
        for cat_name, cat_info in CONFIG["categories"].items():
            print(f"--- 正在处理分类: {cat_name} ---")
            papers = await fetch_arxiv(session, cat_info["keywords"])
            
            # 获取该分类下的对比背景
            context = get_existing_papers_in_category(cat_keys[cat_name])
            
            for p in papers:
                if p['id'] in history: continue
                
                # 记录 ID 防止重复
                history.append(p['id'])
                
                analysis = analyze_paper_with_context(p, cat_name, context)
                if not analysis or analysis["recommendation"] == "可跳过":
                    continue
                
                # 写入 Zotero
                item = zot.item_template('preprint')
                item['title'] = p['title']
                item['abstractNote'] = p['summary']
                item['url'] = f"https://arxiv.org/abs/{p['id']}"
                item['collections'] = [cat_keys[cat_name]]
                item['tags'] = [{"tag": cat_name}, {"tag": analysis["recommendation"]}]
                
                resp = zot.create_items([item])
                if resp['successful']:
                    item_key = list(resp['successful'].values())[0]['key']
                    # 生成并写入美化笔记
                    note_content = create_html_note(p, analysis)
                    note_template = zot.item_template('note')
                    note_template['note'] = note_content
                    note_template['parentItem'] = item_key
                    zot.create_items([note_template])
                    
                    print(f"✅ 已同步并创建笔记: {p['title']}")

    with open("history.json", "w") as f:
        json.dump(history, f)
    print("🎉 所有分类处理完成！")

if __name__ == "__main__":
    asyncio.run(main())