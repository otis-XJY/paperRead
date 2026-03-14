import asyncio
import aiohttp
import feedparser
import json
import os
import re
import time
import urllib.parse
from datetime import datetime
from openai import OpenAI
from pyzotero import zotero
from notifier import notifier

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

LLM_API_KEY = os.getenv("MODELSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
if not LLM_API_KEY:
    raise ValueError("缺少 LLM API Key，请设置 MODELSCOPE_API_KEY（推荐）或 OPENAI_API_KEY")


def is_auth_error(exc):
    msg = str(exc).lower()
    # 只匹配真正的鉴权失败，避免将限速(429)、无效参数等误判为鉴权错误
    return (
        "401" in msg
        or "authentication failed" in msg
        or "invalid api key" in msg
        or "invalid token" in msg
        or "unauthorized" in msg
    )


client = OpenAI(
    api_key=LLM_API_KEY,
    base_url=CONFIG["base_url"],
    timeout=90.0,
    max_retries=2,
)
zot = zotero.Zotero(os.getenv("ZOTERO_USER_ID"), 'user', os.getenv("ZOTERO_API_KEY"))

STATE_FILE = "state.json"
HISTORY_FILE = "history.json"
HTTP_TIMEOUT_SECONDS = 25
RETRY_TIMES = 3
RETRY_BASE_DELAY_SECONDS = 1.0
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
DEBUG_PHASE_ONE = os.getenv("DEBUG_PHASE_ONE", "1") == "1"
ENABLE_NOTIFICATION = os.getenv("ENABLE_NOTIFICATION", "1") == "1"
ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID")


def build_zotero_web_item_link(item_key):
    if not ZOTERO_USER_ID or not item_key:
        return ""
    return f"https://www.zotero.org/users/{ZOTERO_USER_ID}/items/{item_key}"


def build_zotero_collection_link(collection_key):
    if not ZOTERO_USER_ID or not collection_key:
        return ""
    return f"https://www.zotero.org/users/{ZOTERO_USER_ID}/collections/{collection_key}"


def extract_created_item_meta(resp):
    successful = resp.get("successful", {}) if isinstance(resp, dict) else {}
    if not successful:
        return "", ""
    first = next(iter(successful.values()))
    item_key = first.get("key", "")
    web_link = ((first.get("links") or {}).get("alternate") or {}).get("href", "") or build_zotero_web_item_link(item_key)
    return item_key, web_link


def ensure_item_in_collection(item_key, collection_key, context=""):
    """条目创建后强制归档到目标集合，作为 collections 字段可能被忽略时的兜底。"""
    if not item_key or not collection_key:
        print(f"⚠️  [{context}] 无法归档：item_key 或 collection_key 为空")
        return False
    try:
        obj = retry_sync(lambda: zot.item(item_key), f"读取条目({context})")
        current = list(obj.get("data", {}).get("collections") or [])
        if collection_key in current:
            return True
        obj["data"]["collections"] = list(dict.fromkeys(current + [collection_key]))
        retry_sync(lambda: zot.update_item(obj), f"归档条目到集合({context})")
        verified = retry_sync(lambda: zot.item(item_key), f"验证归档({context})")
        ok = collection_key in (verified.get("data", {}).get("collections") or [])
        col_link = build_zotero_collection_link(collection_key)
        if ok:
            print(f"📌 [{context}] 已归档至分类: {collection_key}")
            if col_link:
                print(f"   🔗 分类链接: {col_link}")
        else:
            print(f"⚠️  [{context}] 归档未生效 item={item_key} collection={collection_key}")
        return ok
    except Exception as e:
        print(f"⚠️  [{context}] 归档出错: {e}")
        return False


def load_json_file(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️ 读取 {path} 失败，使用默认值。原因: {e}")
        return default


def retry_sync(operation, operation_name, retries=RETRY_TIMES, base_delay=RETRY_BASE_DELAY_SECONDS):
    for attempt in range(retries):
        try:
            return operation()
        except Exception as e:
            if attempt == retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            print(f"⚠️ {operation_name} 失败（第 {attempt + 1}/{retries} 次）: {e}，{delay:.1f}s 后重试")
            time.sleep(delay)


async def fetch_text_with_retry(session, url, retries=RETRY_TIMES, base_delay=RETRY_BASE_DELAY_SECONDS):
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
    for attempt in range(retries):
        try:
            async with session.get(url, timeout=timeout) as resp:
                if resp.status != 200:
                    # 特殊处理 429 速率限制错误
                    if resp.status == 429:
                        raise RuntimeError(f"HTTP 429 (速率限制)")
                    raise RuntimeError(f"HTTP {resp.status}")
                return await resp.text()
        except Exception as e:
            if attempt == retries - 1:
                print(f"❌ 抓取失败，已放弃: {url}，原因: {e}")
                return ""
            # 429 错误使用更长的延迟
            if "429" in str(e):
                delay = 3.0  # 429 错误使用固定 3 秒延迟
                print(f"⚠️ 遇到速率限制（第 {attempt + 1}/{retries} 次）: {e}，{delay:.1f}s 后重试")
            else:
                delay = base_delay * (2 ** attempt)
                print(f"⚠️ 抓取失败（第 {attempt + 1}/{retries} 次）: {e}，{delay:.1f}s 后重试")
            await asyncio.sleep(delay)

def safe_json_parse(text):
    try: return json.loads(text)
    except:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        return json.loads(match.group(0)) if match else {}


def extract_authors_from_entry(entry):
    authors = []
    for author in (entry.get("authors") or []):
        name = (author.get("name") or "").strip()
        if name:
            authors.append(name)
    return authors


def authors_to_zotero_creators(authors):
    creators = []
    for full_name in (authors or []):
        name = (full_name or "").strip()
        if not name:
            continue
        parts = name.split()
        if len(parts) >= 2:
            creators.append({
                "creatorType": "author",
                "firstName": " ".join(parts[:-1]),
                "lastName": parts[-1],
            })
        else:
            creators.append({
                "creatorType": "author",
                "name": name,
            })
    return creators


def format_arxiv_published_time(published):
    if not published:
        return ""
    try:
        dt = datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ")
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return published


def normalize_parent_collection(parent_value):
    # Zotero 顶层集合的 parentCollection 可能是 None/""/False，统一归一化
    if parent_value in (None, "", False):
        return None
    return parent_value

def get_or_create_collection(name, parent_key=None):
    # limit=100 为 Zotero API 单页最大值；使用 everything() 自动拉取全部分页
    colls = retry_sync(lambda: zot.everything(zot.collections()), f"读取集合列表({name})")
    target_parent = normalize_parent_collection(parent_key)
    matched =[]
    for c in colls:
        collection_parent = normalize_parent_collection(c['data'].get('parentCollection'))
        if c['data']['name'] == name and collection_parent == target_parent:
            matched.append(c)

    if matched:
        matched.sort(key=lambda x: x['data'].get('dateAdded', ''))
        return matched[0]['key']

    # 【修复重点】动态构造 payload，剔除掉顶层目录不该有的 parentCollection 字段
    payload = {'name': name}
    if parent_key:
        payload['parentCollection'] = parent_key

    resp = retry_sync(
        lambda: zot.create_collections([payload]),
        f"创建集合({name})"
    )
    
    # 容错处理：打印详细失败原因
    if '0' not in resp.get('successful', {}):
        raise RuntimeError(f"创建Zotero集合失败，API返回: {resp.get('failed')}")
        
    return resp['successful']['0']['key']
# ================= 2. 状态管理 =================
def load_state():
    default_state = {"is_first_run": True, "last_date": "2000-01-01T00:00:00Z"}
    state = load_json_file(STATE_FILE, default_state)
    if not isinstance(state, dict):
        return default_state
    if "is_first_run" not in state or "last_date" not in state:
        return default_state
    return state

def save_state(last_date):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"is_first_run": False, "last_date": last_date}, f)


def simple_first_run_filter(paper):
    title = (paper.get("title") or "").strip()
    summary = (paper.get("summary") or "").strip()
    # 冷启动仅做轻量过滤：标题/摘要不能为空，避免无效条目入库
    return bool(title) and bool(summary)

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
    3. 给出一句简短判定理由，说明为什么判定为相关/不相关。
    
    返回严格JSON: {{"is_relevant": true/false, "score": 8, "matched_titles": ["论文A", "论文B"], "reason": "一句话理由"}}
    """
    try:
        res = retry_sync(
            lambda: client.chat.completions.create(
                model=CONFIG["llm_model"],
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            ),
            "阶段一初筛"
        )
        parsed = safe_json_parse(res.choices[0].message.content)
        if "is_relevant" not in parsed:
            parsed["is_relevant"] = False
        if "score" not in parsed:
            parsed["score"] = 0
        if "matched_titles" not in parsed or not isinstance(parsed["matched_titles"], list):
            parsed["matched_titles"] = []
        if "reason" not in parsed:
            parsed["reason"] = "模型未返回理由"
        return parsed
    except Exception as e:
        if is_auth_error(e):
            raise RuntimeError(
                "LLM 鉴权失败（401）。请确认使用的是 ModelScope Token，并设置 MODELSCOPE_API_KEY。"
            ) from e
        print(f"阶段一初筛报错: {e}")
        return {"is_relevant": False, "matched_titles":[]}

def deep_analyze_phase_two(paper, category_name, matched_full_notes):
    prompt = f"""
    你是{category_name}专家学者，了解这个领域的经典方法和前沿进展。
    【你过去写下的核心笔记】（仅针对强相关论文）：{json.dumps(matched_full_notes, ensure_ascii=False)}
    
    【今日新论文】：标题：{paper['title']} | 摘要：{paper['summary']}
    
    任务：深入对比新老论文，严格输出 JSON 格式：
    {{"recommendation": "必读/值得看/可跳过", "comparison": "一句话说明与你过去笔记中论文的具体异同", "methodology": "核心方法简述", "core_concepts": ["术语1"], "sharp_review": "批判性分析"}}
    """
    try:
        res = retry_sync(
            lambda: client.chat.completions.create(
                model=CONFIG["llm_model"],
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            ),
            "阶段二深读"
        )
        return safe_json_parse(res.choices[0].message.content)
    except Exception as e:
        if is_auth_error(e):
            raise RuntimeError(
                "LLM 鉴权失败（401）。请确认使用的是 ModelScope Token，并设置 MODELSCOPE_API_KEY。"
            ) from e
        print(f"阶段二深读报错: {e}")
        return None


def analyze_first_run_paper(paper, category_name):
    prompt = f"""
    你是{category_name}专家学者，了解这个领域的经典方法和前沿进展。
    当前为冷启动阶段，没有历史论文可对比。

    【论文】：标题：{paper['title']} | 摘要：{paper['summary']}

    任务：仅基于该论文内容输出结构化笔记，严格输出 JSON：
    {{"recommendation": "必读/值得看/可跳过", "methodology": "核心方法简述", "core_concepts": ["术语1"], "sharp_review": "批判性锐评", "summary": "一句话价值总结"}}
    """
    try:
        res = retry_sync(
            lambda: client.chat.completions.create(
                model=CONFIG["llm_model"],
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            ),
            "首次运行深读"
        )
        parsed = safe_json_parse(res.choices[0].message.content)
        if "recommendation" not in parsed:
            parsed["recommendation"] = "值得看"
        if "methodology" not in parsed:
            parsed["methodology"] = "模型未返回方法论"
        if "core_concepts" not in parsed or not isinstance(parsed["core_concepts"], list):
            parsed["core_concepts"] = []
        if "sharp_review" not in parsed:
            parsed["sharp_review"] = "模型未返回锐评"
        if "summary" not in parsed:
            parsed["summary"] = "模型未返回总结"
        return parsed
    except Exception as e:
        if is_auth_error(e):
            raise RuntimeError(
                "LLM 鉴权失败（401）。请确认使用的是 ModelScope Token，并设置 MODELSCOPE_API_KEY。"
            ) from e
        print(f"首次运行深读报错: {e}")
        return None

# ================= 4. 动态抓取模块 =================
async def fetch_arxiv_single(session, url, max_retries=3, base_delay=6.0):
    """
    单次抓取 arXiv 的函数，使用更长的延迟和重试策略
    """
    timeout = aiohttp.ClientTimeout(total=45, connect=10)
    for attempt in range(max_retries):
        try:
            # 每次请求前都添加延迟，避免触发速率限制
            if attempt > 0:
                delay = base_delay * (2 ** (attempt - 1))
                print(f"⏳ 延迟 {delay:.1f}s 后重试...")
                await asyncio.sleep(delay)

            async with session.get(url, timeout=timeout) as resp:
                if resp.status == 429:
                    # 速率限制，动态增加等待时间：首次7秒，之后每次增加3秒
                    wait_time = 7 + (attempt * 3)
                    print(f"⚠️ 遇到速率限制，等待 {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    continue
                elif resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                return await resp.text()
        except RuntimeError as e:
            if attempt == max_retries - 1:
                print(f"❌ 抓取失败，已放弃: {url}，原因: {e}")
                return ""
            print(f"⚠️ 抓取失败（第 {attempt + 1}/{max_retries} 次）: {e}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            # 捕获更具体的网络错误类型
            error_type = type(e).__name__
            if "timeout" in str(e).lower() or isinstance(e, asyncio.TimeoutError):
                error_msg = f"请求超时({error_type})"
            elif "connect" in str(e).lower():
                error_msg = f"连接失败({error_type})"
            else:
                error_msg = f"网络错误({error_type}): {e}"

            print(f"❌ {error_msg}")

            if attempt == max_retries - 1:
                print(f"❌ 抓取失败，已放弃: {url}")
                return ""

            # 其他网络错误也等待一段时间
            delay = 5.0 * (attempt + 1)
            print(f"⏳ 网络错误延迟 {delay:.1f}s 后重试...")
            await asyncio.sleep(delay)
        except Exception as e:
            error_type = type(e).__name__
            print(f"❌ 未知错误({error_type}): {e}")
            if attempt == max_retries - 1:
                print(f"❌ 抓取失败，已放弃: {url}")
                return ""
            delay = 5.0 * (attempt + 1)
            print(f"⏳ 未知错误延迟 {delay:.1f}s 后重试...")
            await asyncio.sleep(delay)

    return ""


async def fetch_arxiv(session, keywords, state):
    all_papers = {}
    max_published_date = state["last_date"]

    if state["is_first_run"]:
        latest_candidates = {}
        hot_candidates = {}
        hot_order = []

        for kw in keywords:
            encoded_kw = urllib.parse.quote(kw)
            print(f"🚀 首次运行：拉取最新10篇 + 认可度最高(Relevance)10篇 -> {kw}")

            latest_url = (
                f"http://export.arxiv.org/api/query?search_query={encoded_kw}"
                f"&sortBy=submittedDate&sortOrder=descending&max_results=10"
            )
            hot_url = (
                f"http://export.arxiv.org/api/query?search_query={encoded_kw}"
                f"&sortBy=relevance&sortOrder=descending&max_results=10"
            )

            # 拉取最新 10 篇
            latest_text = await fetch_arxiv_single(session, latest_url)
            if latest_text:
                latest_feed = feedparser.parse(latest_text)
                for e in latest_feed.entries:
                    pub_date = e.get('published', '')
                    if pub_date > state["last_date"]:
                        pid = e.id.split('/')[-1]
                        authors = extract_authors_from_entry(e)
                        paper = {
                            "id": pid,
                            "title": e.title.replace('\n', ' '),
                            "summary": e.summary.replace('\n', ' '),
                            "published": pub_date,
                            "authors": authors,
                        }
                        latest_candidates[pid] = paper
                        if pub_date > max_published_date:
                            max_published_date = pub_date

            # 请求之间添加更长的延迟（8秒）
            print("⏳ 请求间隔 8s...")
            await asyncio.sleep(8.0)

            # 拉取 Relevance 10 篇
            hot_text = await fetch_arxiv_single(session, hot_url)
            if hot_text:
                hot_feed = feedparser.parse(hot_text)
                for e in hot_feed.entries:
                    pub_date = e.get('published', '')
                    if pub_date > state["last_date"]:
                        pid = e.id.split('/')[-1]
                        authors = extract_authors_from_entry(e)
                        paper = {
                            "id": pid,
                            "title": e.title.replace('\n', ' '),
                            "summary": e.summary.replace('\n', ' '),
                            "published": pub_date,
                            "authors": authors,
                        }
                        if pid not in hot_candidates:
                            hot_order.append(pid)
                        hot_candidates[pid] = paper
                        if pub_date > max_published_date:
                            max_published_date = pub_date

            # 关键词之间添加更长的延迟（10秒）
            print("⏳ 关键词间隔 10s...")
            await asyncio.sleep(10.0)

        latest_ranked = sorted(latest_candidates.values(), key=lambda x: x.get("published", ""), reverse=True)
        latest_top10 = latest_ranked[:10]
        hot_ranked = [hot_candidates[pid] for pid in hot_order if pid in hot_candidates]
        hot_top10 = hot_ranked[:10]

        merged = []
        selected = set()

        for p in latest_top10:
            if p["id"] not in selected:
                merged.append(p)
                selected.add(p["id"])

        for p in hot_top10:
            if len(merged) >= 20:
                break
            if p["id"] not in selected:
                merged.append(p)
                selected.add(p["id"])

        print(
            f"📦 首次运行分类配额控制：latest={len(latest_top10)}，"
            f"hot={len(hot_top10)}，去重后返回={len(merged)}（上限20）"
        )
        return merged, max_published_date

    for kw in keywords:
        encoded_kw = urllib.parse.quote(kw)
        print(f"🔍 增量拉取：抓取最新论文对比 {state['last_date']} -> {kw}")
        url = f"http://export.arxiv.org/api/query?search_query={encoded_kw}&sortBy=submittedDate&sortOrder=descending&max_results=30"

        text = await fetch_arxiv_single(session, url)
        if not text:
            continue

        feed = feedparser.parse(text)
        new_papers_count = 0
        for e in feed.entries:
            pub_date = e.get('published', '')
            # 增量过滤逻辑：只接受比 last_date 新的论文（首次运行时 last_date 极小，等于全收）
            if pub_date > state["last_date"]:
                pid = e.id.split('/')[-1]
                all_papers[pid] = {
                    "id": pid,
                    "title": e.title.replace('\n', ' '),
                    "summary": e.summary.replace('\n', ' '),
                    "published": pub_date,
                    "authors": extract_authors_from_entry(e),
                }
                new_papers_count += 1
                if pub_date > max_published_date:
                    max_published_date = pub_date

        if new_papers_count == 0:
            print(f"✅ 该关键词暂无新论文: {kw}")

        # 请求之间添加延迟
        print("⏳ 请求间隔 6s...")
        await asyncio.sleep(6.0)

    return list(all_papers.values()), max_published_date

# ================= 5. 主流程 =================
async def main():
    print("🚀 开始执行 main.py")
    if DRY_RUN:
        print("🧪 DRY_RUN=1，本次仅本地演练：不会写入 Zotero，也不会更新 history/state 文件")
    if not os.path.exists("knowledge_base.json"):
        print("❌ 找不到 knowledge_base.json，请先运行 zotero_indexer.py")
        return
    
    # 错误处理
    try:
        await _main_impl()
    except Exception as e:
        print(f"❌ 程序运行出错: {e}")
        import traceback
        traceback.print_exc()
        
        # 发送错误通知
        if ENABLE_NOTIFICATION and not DRY_RUN:
            notifier.send_workflow_error(str(e))
        raise


async def _main_impl():
    print("🚀 开始执行 main.py")
    if DRY_RUN:
        print("🧪 DRY_RUN=1，本次仅本地演练：不会写入 Zotero，也不会更新 history/state 文件")
    if not os.path.exists("knowledge_base.json"):
        print("❌ 找不到 knowledge_base.json，请先运行 zotero_indexer.py")
        return
        
    kb = load_json_file("knowledge_base.json", {})
    if not isinstance(kb, dict):
        print("⚠️ knowledge_base.json 格式异常，使用空知识库")
        kb = {}

    history = load_json_file(HISTORY_FILE, [])
    if not isinstance(history, list):
        print("⚠️ history.json 格式异常，重置为空列表")
        history = []
    history_set = set(history)
    
    state = load_state()
    global_max_date = state["last_date"]
    print(f"🧭 当前状态: is_first_run={state['is_first_run']}, last_date={state['last_date']}")
    
    # 发送工作流开始通知
    if ENABLE_NOTIFICATION and not DRY_RUN:
        notifier.send_workflow_start(state["is_first_run"])

    if DRY_RUN:
        cat_keys = {name: None for name in CONFIG["categories"]}
    else:
        print("📚 正在获取/创建 Zotero 集合...")
        root_key = get_or_create_collection("DailyPapers")
        cat_keys = {name: get_or_create_collection(name, root_key) for name in CONFIG["categories"]}
        print(f"✅ Zotero 集合准备完成，root_key={root_key}")
        print("📁 分类集合映射:")
        for _cat_name, _cat_key in cat_keys.items():
            print(f"   - {_cat_name}: {_cat_key}")

    # 统计变量
    stats = {
        "categories": {},
        "total_papers": 0,
        "papers": {}  # 存储新论文的详细信息
    }

    async with aiohttp.ClientSession() as session:
        for cat_name, cat_info in CONFIG["categories"].items():
            print(f"\n--- 正在处理分类: {cat_name} ---")
            stats["categories"][cat_name] = 0
            
            # 动态抓取（支持首次与增量）
            papers, cat_max_date = await fetch_arxiv(session, cat_info["keywords"], state)
            if cat_max_date > global_max_date: global_max_date = cat_max_date
            
            kb_entries = kb.get(cat_name, [])
            
            for p in papers:
                if p['id'] in history_set:
                    continue

                if state["is_first_run"]:
                    if not simple_first_run_filter(p):
                        print(f"⏭️ 首次运行简单过滤未通过，跳过: {p['title'][:30]}...")
                        continue

                    print(f"📖 首次运行深读分析: {p['title'][:50]}...")
                    first_run_analysis = analyze_first_run_paper(p, cat_name)
                    if not first_run_analysis:
                        first_run_analysis = {
                            "recommendation": "值得看",
                            "methodology": "首次运行分析失败，暂无法生成方法论",
                            "core_concepts": [],
                            "sharp_review": "首次运行分析失败，暂无法生成锐评",
                            "summary": "首次运行分析失败，建议后续补充。",
                        }

                    history.append(p['id'])
                    history_set.add(p['id'])

                    if DRY_RUN:
                        print(
                            f"✅ DRY_RUN 首次深读完成（不写入）: {p['title'][:50]}... | "
                            f"推荐: {first_run_analysis.get('recommendation', '值得看')}"
                        )
                        continue

                    print(f"📝 首次运行直存 Zotero: {p['title'][:50]}...")
                    item = zot.item_template('preprint')
                    item['title'] = p['title']
                    item['abstractNote'] = p['summary']
                    item['url'] = f"https://arxiv.org/abs/{p['id']}"
                    item['date'] = p.get('published', '')
                    item['creators'] = authors_to_zotero_creators(p.get('authors', []))
                    item['collections'] = [cat_keys[cat_name]]
                    item['tags'] = [
                        {"tag": cat_name},
                        {"tag": "首次运行"},
                        {"tag": first_run_analysis.get("recommendation", "值得看")},
                    ]

                    try:
                        resp = retry_sync(lambda: zot.create_items([item]), "首次运行创建 Zotero 论文条目")
                    except Exception as _zotero_err:
                        print(f"⚠️ Zotero 条目写入失败，跳过此论文: {p['title'][:40]}... 原因: {_zotero_err}")
                        continue
                    if resp['successful']:
                        item_key, web_item_link = extract_created_item_meta(resp)
                        ensure_item_in_collection(item_key, cat_keys[cat_name], f"首次-{cat_name}")
                        note_template = zot.item_template('note')
                        badge_color = "#d9534f" if first_run_analysis.get("recommendation") == "必读" else "#f0ad4e"
                        authors_str = ", ".join(p.get("authors", [])) if p.get("authors") else "未知"
                        published_str = format_arxiv_published_time(p.get("published", ""))
                        concepts_html = "".join([
                            f'<span style="background:#eef; color:#3366ff; padding:2px 6px; border-radius:10px; margin-right:5px; font-size:0.9em;">[[{c}]]</span>'
                            for c in first_run_analysis.get("core_concepts", [])
                        ])
                        note_template['note'] = (
                            f"<h2 style=\"color:#2c3e50;border-bottom:2px solid #eee;\">{p['title']}</h2>"
                            f"<p><strong>🆕 入库阶段：</strong>首次运行（冷启动）</p>"
                            f"<p><strong>🔥 推荐指数：</strong> <span style=\"background:{badge_color}; color:white; padding:2px 8px; border-radius:4px;\">{first_run_analysis.get('recommendation', '值得看')}</span></p>"
                            f"<p><strong>📂 分类：</strong>{cat_name}</p>"
                            f"<p><strong>👤 作者：</strong>{authors_str}</p>"
                            f"<p><strong>🕒 arXiv上传时间：</strong>{published_str}</p>"
                            f"<p><strong>🔗 原文：</strong><a href=\"https://arxiv.org/abs/{p['id']}\">https://arxiv.org/abs/{p['id']}</a></p>"
                            f"<div style=\"background:#f9f9f9;border-left:5px solid #28a745;padding:10px;margin:10px 0;\">"
                            f"<strong>🧾 一句话总结：</strong><br/>{first_run_analysis.get('summary', '')}"
                            f"</div>"
                            f"<div style=\"background:#f9f9f9;border-left:5px solid #007bff;padding:10px;margin:10px 0;\">"
                            f"<strong>📄 摘要：</strong><br/>{p['summary']}"
                            f"</div>"
                            f"<h3 style=\"color:#2980b9;\">🧠 核心术语库</h3><p>{concepts_html}</p>"
                            f"<h3 style=\"color:#2980b9;\">🔬 核心方法简述</h3><p>{first_run_analysis.get('methodology', '')}</p>"
                            f"<h3 style=\"color:#2980b9;\">💬 锐评</h3><p><i>{first_run_analysis.get('sharp_review', '')}</i></p>"
                            f"<p><strong>📝 说明：</strong>该条目在冷启动阶段按关键词检索后完成单篇深读分析，"
                            f"后续增量任务将继续进行相关性对比与深度比较。</p>"
                        )
                        note_template['parentItem'] = item_key
                        try:
                            retry_sync(lambda: zot.create_items([note_template]), "首次运行创建 Zotero 说明笔记")
                        except Exception as _note_err:
                            print(f"⚠️ 笔记创建失败，但条目已入库: {p['title'][:40]}... 原因: {_note_err}")
                        print("✅ 首次运行已直存至 Zotero")
                        # 更新统计
                        stats["categories"][cat_name] += 1
                        stats["total_papers"] += 1
                        # 收集论文信息用于通知
                        if cat_name not in stats["papers"]:
                            stats["papers"][cat_name] = []
                        stats["papers"][cat_name].append({
                            "title": p['title'],
                            "arxiv_id": p['id'],
                            "authors": p.get('authors', []),
                            "published": p.get('published', ''),
                            "recommendation": first_run_analysis.get('recommendation', '值得看'),
                            "methodology": first_run_analysis.get('methodology', ''),
                            "core_concepts": first_run_analysis.get('core_concepts', []),
                            "sharp_review": first_run_analysis.get('sharp_review', ''),
                            "summary": first_run_analysis.get('summary', ''),
                            "zotero_link": web_item_link or f"https://www.zotero.org/users/{ZOTERO_USER_ID}/items/{item_key}",
                        })
                        
                        if web_item_link:
                            print(f"🔗 Zotero 直达链接: {web_item_link}")
                        else:
                            print(f"🔗 Zotero 条目 Key: {item_key}")
                    else:
                        print(
                            f"⚠️ 首次运行条目创建失败: {p['title'][:50]}... | "
                            f"failed={resp.get('failed')} | collection={cat_keys.get(cat_name)}"
                        )
                    continue
                
                # 阶段一：轻量化相关性初筛
                print(f"🧪 阶段一相关性判断: {p['title'][:50]}...")
                phase_one_res = check_relevance_phase_one(p, kb_entries)
                if DEBUG_PHASE_ONE:
                    print(
                        "📊 阶段一输出: "
                        f"score={phase_one_res.get('score', 0)}, "
                        f"is_relevant={phase_one_res.get('is_relevant', False)}, "
                        f"matched_titles={len(phase_one_res.get('matched_titles', []))}, "
                        f"reason={phase_one_res.get('reason', '')}"
                    )
                if not phase_one_res.get("is_relevant"):
                    print(f"⏭️ 评分不够或无相关性，跳过: {p['title'][:30]}...")
                    continue

                # 只在确认“相关”后记录历史，避免误伤其它分类
                history.append(p['id'])
                history_set.add(p['id'])
                
                # 阶段二：组装深读上下文并深度对比
                matched_titles = phase_one_res.get("matched_titles",[])
                print(f"🧠 强相关！命中历史笔记 {len(matched_titles)} 篇，开始深读对比: {p['title'][:30]}...")
                
                matched_full_notes = [{"title": entry["title"], "note": entry["full_note"]} 
                                      for entry in kb_entries if entry["title"] in matched_titles]
                
                print(f"📖 阶段二深读分析: {p['title'][:50]}...")
                analysis = deep_analyze_phase_two(p, cat_name, matched_full_notes)
                if not analysis or analysis.get("recommendation") == "可跳过": continue

                if DRY_RUN:
                    print(f"✅ DRY_RUN 命中相关论文（不写入）: {p['title'][:50]}... | 推荐: {analysis.get('recommendation', '值得看')}")
                    continue
                
                # 写入 Zotero
                print("📝 写入 Zotero 条目与笔记...")
                item = zot.item_template('preprint')
                item['title'] = p['title']
                item['abstractNote'] = p['summary']
                item['url'] = f"https://arxiv.org/abs/{p['id']}"
                item['date'] = p.get('published', '')
                item['creators'] = authors_to_zotero_creators(p.get('authors', []))
                item['collections'] = [cat_keys[cat_name]]
                item['tags'] =[{"tag": cat_name}, {"tag": analysis.get("recommendation", "值得看")}]
                
                try:
                    resp = retry_sync(lambda: zot.create_items([item]), "创建 Zotero 论文条目")
                except Exception as _zotero_err:
                    print(f"⚠️ Zotero 条目写入失败，跳过此论文: {p['title'][:40]}... 原因: {_zotero_err}")
                    continue
                if resp['successful']:
                    item_key, web_item_link = extract_created_item_meta(resp)
                    ensure_item_in_collection(item_key, cat_keys[cat_name], f"增量-{cat_name}")
                    badge_color = "#d9534f" if analysis.get('recommendation') == "必读" else "#f0ad4e"
                    authors_str = ", ".join(p.get("authors", [])) if p.get("authors") else "未知"
                    published_str = format_arxiv_published_time(p.get("published", ""))
                    concepts_html = "".join([f'<span style="background:#eef; color:#3366ff; padding:2px 6px; border-radius:10px; margin-right:5px; font-size:0.9em;">[[{c}]]</span>' for c in analysis.get('core_concepts',[])])
                    
                    # 动态生成关联信息
                    matched_html = f"<p><strong>🔗 触发的灵感来源：</strong> {', '.join(matched_titles)}</p>" if matched_titles else ""
                    
                    note_html = f"""
                    <h2 style="color: #2c3e50; border-bottom: 2px solid #eee;">{p['title']}</h2>
                    <p><strong>🔥 推荐指数：</strong> <span style="background:{badge_color}; color:white; padding:2px 8px; border-radius:4px;">{analysis.get('recommendation')}</span></p>
                    <p><strong>👤 作者：</strong>{authors_str}</p>
                    <p><strong>🕒 arXiv上传时间：</strong>{published_str}</p>
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
                    try:
                        retry_sync(lambda: zot.create_items([note_template]), "创建 Zotero 笔记")
                    except Exception as _note_err:
                        print(f"⚠️ 笔记创建失败，但条目已入库: {p['title'][:40]}... 原因: {_note_err}")
                    print(f"✅ 成功同步至 Zotero")
                    # 更新统计
                    stats["categories"][cat_name] += 1
                    stats["total_papers"] += 1
                    # 收集论文信息用于通知
                    if cat_name not in stats["papers"]:
                        stats["papers"][cat_name] = []
                    stats["papers"][cat_name].append({
                        "title": p['title'],
                        "arxiv_id": p['id'],
                        "authors": p.get('authors', []),
                        "published": p.get('published', ''),
                        "recommendation": analysis.get('recommendation', '值得看'),
                        "methodology": analysis.get('methodology', ''),
                        "core_concepts": analysis.get('core_concepts', []),
                        "sharp_review": analysis.get('sharp_review', ''),
                        "comparison": analysis.get('comparison', ''),
                        "zotero_link": web_item_link or f"https://www.zotero.org/users/{ZOTERO_USER_ID}/items/{item_key}",
                    })
                    
                    if web_item_link:
                        print(f"🔗 Zotero 直达链接: {web_item_link}")
                    else:
                        print(f"🔗 Zotero 条目 Key: {item_key}")
                else:
                    print(
                        f"⚠️ 增量条目创建失败: {p['title'][:50]}... | "
                        f"failed={resp.get('failed')} | collection={cat_keys.get(cat_name)}"
                    )

    # 持久化状态
    if DRY_RUN:
        print(f"\n🎉 DRY_RUN 完成！本次演练捕获到最新论文时间戳：{global_max_date}（未持久化）")
        # DRY_RUN模式也显示统计信息
        if stats["total_papers"] == 0:
            print("📊 本次扫描结果: 暂无新论文")
        else:
            print(f"📊 本次扫描结果: 发现 {stats['total_papers']} 篇新论文")
            for cat_name, count in stats["categories"].items():
                if count > 0:
                    print(f"   - {cat_name}: {count} 篇")
    else:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False)
        save_state(global_max_date)
        print(f"\n🎉 任务完成！记录的最新论文时间戳为：{global_max_date}")

        # 发送完成通知（包含详细论文信息或无新论文提示）
        if ENABLE_NOTIFICATION:
            if stats["total_papers"] > 0:
                print("📤 发送新论文通知...")
                notifier.send_papers_detail(stats, state["is_first_run"])
            else:
                print("📤 发送无新论文通知...")
                notifier.send_no_papers_notification(state["is_first_run"])

if __name__ == "__main__":
    asyncio.run(main())