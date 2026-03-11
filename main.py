import asyncio
import aiohttp
import feedparser
import json
import os
from openai import OpenAI
from pyzotero import zotero

# ================= 1. 从环境变量获取配置 (适配 GitHub Actions) =================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID")
ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY")

KEYWORDS = ["large language models", "retrieval augmented generation", "agentic workflow"]
HISTORY_FILE = "history.json"

if not all([OPENAI_API_KEY, ZOTERO_USER_ID, ZOTERO_API_KEY]):
    raise ValueError("缺少必要的环境变量，请检查 GitHub Secrets 配置！")

# 启用 Gemini 的 OpenAI 兼容接口
client = OpenAI(
    api_key=OPENAI_API_KEY, # 请替换成您的ModelScope Access Token
    base_url="https://api-inference.modelscope.cn/v1/"
)
zot = zotero.Zotero(ZOTERO_USER_ID, 'user', ZOTERO_API_KEY)

# ================= 2. 抓取模块 =================
async def fetch_arxiv(session, keyword):
    keyword_query = keyword.replace(' ', '+')
    url = f"http://export.arxiv.org/api/query?search_query=all:%22{keyword_query}%22&sortBy=submittedDate&sortOrder=desc&max_results=5"
    async with session.get(url) as response:
        text = await response.text()
        feed = feedparser.parse(text)
        return [{"id": entry.id.split('/')[-1], "title": entry.title, "summary": entry.summary.replace('\n', ' ')} for entry in feed.entries]

# ================= 3. AI 分析模块 =================
def analyze_with_ai(papers):
    prompt = f"""
    你是顶尖AI研究员。请阅读以下 {len(papers)} 篇最新论文的标题和摘要。
    任务：
    1. 分类为：【必读】、【值得看】、【可跳过】。
    2. 为【必读】和【值得看】写一段锐评（创新点与局限性）。
    3. 为【必读】论文提取3-5个核心概念（术语）。
    返回严格的 JSON 格式：
    {{"results":[{{"id": "论文id", "category": "分类", "review": "锐评", "concepts":["概念1"]}}]}}
    
    论文数据：{json.dumps(papers)}
    """
    response = client.chat.completions.create(
        model="Qwen/Qwen3.5-35B-A3B", # ModelScope Model-Id
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}
    )

    return json.loads(response.choices[0].message.content)["results"]

# ================= 4. 写入 Zotero (聚合所有笔记功能) =================
def get_or_create_collection(name):
    collections = zot.collections()
    for c in collections:
        if c['data']['name'] == name:
            return c['key']
    # 如果不存在则创建
    resp = zot.create_collections([{'name': name}])
    return resp['successful']['0']['key']

def process_to_zotero(paper, ai_result):
    print(f"AI 的评价是: {ai_result['category']} | 理由: {ai_result['review'][:50]}...")
    if ai_result["category"] == "可跳过":
        return

    print(f"[{ai_result['category']}] 正在推送到 Zotero: {paper['title']}")
    
    # 获取或创建 DailyPapers 分类
    daily_coll_key = get_or_create_collection("DailyPapers")
    
    # 1. 创建父条目 (Preprint)
    item_template = zot.item_template('preprint')
    item_template['title'] = paper['title']
    item_template['abstractNote'] = paper['summary']
    item_template['url'] = f"https://arxiv.org/abs/{paper['id']}"
    item_template['collections'] = [daily_coll_key]
    
    # 将 AI 提取的“概念”直接作为 Zotero 标签写入 (方便 Zotero 内部搜索和图谱关联)
    tags = [{"tag": ai_result["category"]}]
    for concept in ai_result.get("concepts",[]):
         tags.append({"tag": concept})
    item_template['tags'] = tags
    
    resp = zot.create_items([item_template])
    if not resp['successful']: return
    
    new_item_key = list(resp['successful'].values())[0]['key']

    # 2. 创建精美排版的子笔记 (完全取代 Obsidian)
    note_template = zot.item_template('note')
    
    # Zotero 笔记支持 HTML，我们将排版做得漂亮点
    concepts_html = ", ".join([f"<b>{c}</b>" for c in ai_result.get("concepts", [])])
    
    html_content = f"""
    <h2 style="color: #d9534f;">AI 智能速读 ({ai_result['category']})</h2>
    <hr/>
    <h3>🧠 核心概念</h3>
    <p>{concepts_html if concepts_html else "无"}</p>
    <h3>💡 锐评与分析</h3>
    <p>{ai_result['review']}</p>
    <h3>📄 原文摘要</h3>
    <p>{paper['summary']}</p>
    """
    
    note_template['note'] = html_content
    note_template['parentItem'] = new_item_key
    zot.create_items([note_template])

# ================= 主流程 =================
async def main():
    print("1. 开始抓取最新论文...")
    history =[]
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r') as f:
            try: history = json.load(f)
            except: pass

    async with aiohttp.ClientSession() as session:
        tasks =[fetch_arxiv(session, kw) for kw in KEYWORDS]
        results = await asyncio.gather(*tasks)
    
    new_papers =[]
    for res in results:
        for p in res:
            if p['id'] not in history:
                new_papers.append(p)
                history.append(p['id'])
    
    new_papers = new_papers[:10] # 每次最多处理 10 篇
    if not new_papers:
        print("今天没有新的相关论文。")
        return

    print(f"2. 抓取到 {len(new_papers)} 篇新论文，开始 AI 分析...")
    ai_results = analyze_with_ai(new_papers)
    
    print("3. 开始写入 Zotero...")
    ai_dict = {res['id']: res for res in ai_results}
    for paper in new_papers:
        if paper['id'] in ai_dict:
            process_to_zotero(paper, ai_dict[paper['id']])
            
    # 保存历史记录 (供 GitHub Actions 提交回仓库)
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f)
    print("🎉 今日任务完成！")

if __name__ == "__main__":
    asyncio.run(main())