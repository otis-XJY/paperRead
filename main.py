"""
Zotero AI Daily Papers - Main Module

An intelligent academic paper fetching, analysis, and archiving system.

Author: paperRead Contributors
Version: 1.0.0
License: MIT
"""

import asyncio
import aiohttp
import feedparser
import json
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from openai import OpenAI
from pyzotero import zotero
from notifier import notifier
from feishu_wiki import FeishuWikiClient
from zotero_indexer import build_knowledge_base

__version__ = "1.0.0"

# ================= 1. 配置区 =================

def load_categories_config():
    """从 categories.json 加载类目配置"""
    config_file = "categories.json"
    if not os.path.exists(config_file):
        raise FileNotFoundError(f"找不到类目配置文件: {config_file}")
    with open(config_file, "r", encoding="utf-8") as f:
        return json.load(f)

CONFIG = {
    "categories": load_categories_config(),
    "llm_model": "deepseek-ai/DeepSeek-V4-Pro",
    "base_url": "https://api-inference.modelscope.cn/v1/",
    # 多模型备选：遇到 429 限速时自动切换到下一个模型
    # 所有模型共用同一个 base_url 和 API key
    "fallback_models": [
        "deepseek-ai/DeepSeek-V4-Pro",
        "ZhipuAI/GLM-5.1",
        "MiniMax/MiniMax-M2.5",
        "moonshotai/Kimi-K2.5",
    ],
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


def is_rate_limit_error(exc):
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "too many requests" in msg


class MultiModelLLM:
    """
    多模型自动切换的 LLM 调用器。
    遇到 429 限速或 401 鉴权错误时，自动切换到下一个备选模型。
    """

    def __init__(self, client, models):
        self.client = client
        self.models = list(models)
        self.current_idx = 0
        self._model_failures = {i: 0 for i in range(len(models))}

    @property
    def current_model(self):
        return self.models[self.current_idx]

    def switch_to_next(self, reason=""):
        old_model = self.current_model
        self.current_idx = (self.current_idx + 1) % len(self.models)
        new_model = self.current_model
        if reason:
            print(f"🔄 LLM 模型切换: {old_model} → {new_model}（原因: {reason}）")
        else:
            print(f"🔄 LLM 模型切换: {old_model} → {new_model}")
        return new_model

    def call(self, messages, response_format=None, max_rounds=2):
        """
        调用 LLM，自动处理限速切换。
        策略：第一轮逐个模型尝试（429 立即切换下一个），全部失败后等 60s 进入第二轮。
        返回 OpenAI 响应对象。
        """
        total_models = len(self.models)
        last_exc = None

        for round_idx in range(max_rounds):
            if round_idx > 0:
                print(f"⏳ 所有模型均被限速，等待 60s 后进入第 {round_idx + 1} 轮...")
                time.sleep(60)

            for i in range(total_models):
                model = self.models[(self.current_idx + i) % total_models]
                try:
                    kwargs = {"model": model, "messages": messages}
                    if response_format:
                        kwargs["response_format"] = response_format
                    resp = self.client.chat.completions.create(**kwargs)
                    # 验证响应有效性：choices 为空或 content 为 None 视为失败
                    if not resp.choices or resp.choices[0].message.content is None:
                        last_exc = ValueError(f"模型 {model} 返回空响应 (choices={resp.choices})")
                        print(f"⚠️ 模型 {model} 返回空响应，切换下一个...")
                        continue
                    self.current_idx = (self.current_idx + i) % total_models
                    return resp
                except Exception as e:
                    last_exc = e
                    if is_rate_limit_error(e):
                        print(f"⚠️ 模型 {model} 限速(429)，切换下一个...")
                        continue
                    elif is_auth_error(e):
                        print(f"⚠️ 模型 {model} 鉴权失败(401)，切换下一个...")
                        continue
                    else:
                        raise

        raise RuntimeError(
            f"所有 LLM 模型经 {max_rounds} 轮尝试均不可用。最后一个错误: {last_exc}"
        )


llm = MultiModelLLM(client, CONFIG["fallback_models"])
print(f"🤖 LLM 模型池: {CONFIG['fallback_models']}，当前使用: {llm.current_model}")
zot = zotero.Zotero(os.getenv("ZOTERO_USER_ID"), 'user', os.getenv("ZOTERO_API_KEY"))

STATE_FILE = "state.json"
HISTORY_FILE = "history.json"
HTTP_TIMEOUT_SECONDS = 25
RETRY_TIMES = 3
RETRY_BASE_DELAY_SECONDS = 1.0

# 运行时错误收集器，所有错误汇总后发送到飞书
_errors: list[dict] = []


def log_error(msg: str, category: str = "", error_type: str = "runtime"):
    """记录一条错误，最终汇总发送到飞书"""
    print(msg)
    _errors.append({"category": category, "type": error_type, "message": msg})
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
DEBUG_PHASE_ONE = os.getenv("DEBUG_PHASE_ONE", "1") == "1"
ENABLE_NOTIFICATION = os.getenv("ENABLE_NOTIFICATION", "1") == "1"
ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID")

# 飞书知识库配置（可选）
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET")
FEISHU_WIKI_ROOT_NODE_TOKEN = os.getenv("FEISHU_WIKI_ROOT_NODE_TOKEN")
FEISHU_WIKI_DAILY_FOLDER_NAME = os.getenv("FEISHU_WIKI_DAILY_FOLDER_NAME", "DailyPapers")
ENABLE_FEISHU_WIKI = bool(FEISHU_APP_ID and FEISHU_APP_SECRET and FEISHU_WIKI_ROOT_NODE_TOKEN)
feishu_wiki_client = None
if ENABLE_FEISHU_WIKI:
    feishu_wiki_client = FeishuWikiClient(
        app_id=FEISHU_APP_ID,
        app_secret=FEISHU_APP_SECRET,
        root_node_token=FEISHU_WIKI_ROOT_NODE_TOKEN,
        daily_folder_name=FEISHU_WIKI_DAILY_FOLDER_NAME,
    )


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
    if not text or not isinstance(text, str):
        return {}
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
        return {}
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
    default_state = {"is_first_run": True, "last_date": "2000-01-01T00:00:00Z", "initialized_categories": []}
    state = load_json_file(STATE_FILE, default_state)
    if not isinstance(state, dict):
        return default_state
    if "is_first_run" not in state or "last_date" not in state:
        return default_state
    # 向后兼容：旧格式没有 initialized_categories，视为全局首次运行已完成
    if "initialized_categories" not in state:
        state["initialized_categories"] = list(CONFIG["categories"].keys()) if not state["is_first_run"] else []
    return state

def save_state(last_date, initialized_categories=None):
    data = {"is_first_run": False, "last_date": last_date}
    if initialized_categories is not None:
        data["initialized_categories"] = initialized_categories
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def ensure_knowledge_base():
    kb_path = "knowledge_base.json"
    if os.path.exists(kb_path):
        kb = load_json_file(kb_path, {})
        if isinstance(kb, dict):
            return kb
        print("⚠️ knowledge_base.json 格式异常，尝试重新构建")

    print("🔄 正在构建 Zotero 知识库...")
    try:
        build_knowledge_base()
    except Exception as e:
        log_error(f"[KB] 知识库构建失败: {e}", error_type="knowledge_base_build")
        return {}

    kb = load_json_file(kb_path, {})
    if not isinstance(kb, dict):
        log_error("[KB] 重新构建后 knowledge_base.json 仍然无效", error_type="knowledge_base_build")
        return {}

    return kb


def simple_first_run_filter(paper):
    title = (paper.get("title") or "").strip()
    summary = (paper.get("summary") or "").strip()
    # 冷启动仅做轻量过滤：标题/摘要不能为空，避免无效条目入库
    return bool(title) and bool(summary)

# ================= 3. 两阶段 AI 分析 =================
def check_relevance_phase_one(paper, kb_entries, category_name=""):
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
        res = llm.call(
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
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
        log_error(f"[LLM] 阶段一初筛报错: {e}", category=category_name, error_type="llm_phase_one")
        return {"is_relevant": False, "matched_titles":[], "error": True}

def deep_analyze_phase_two(paper, category_name, matched_full_notes):
    prompt = f"""
    你是{category_name}专家学者，了解这个领域的经典方法和前沿进展。
    【你过去写下的核心笔记】（仅针对强相关论文）：{json.dumps(matched_full_notes, ensure_ascii=False)}
    
    【今日新论文】：标题：{paper['title']} | 摘要：{paper['summary']}
    
    任务：深入对比新老论文，严格输出 JSON 格式：
    {{"recommendation": "必读/值得看/可跳过", "comparison": "一句话说明与你过去笔记中论文的具体异同", "methodology": "核心方法简述", "core_concepts": ["术语1"], "sharp_review": "批判性分析"}}
    """
    try:
        res = llm.call(
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        return safe_json_parse(res.choices[0].message.content)
    except Exception as e:
        if is_auth_error(e):
            raise RuntimeError(
                "LLM 鉴权失败（401）。请确认使用的是 ModelScope Token，并设置 MODELSCOPE_API_KEY。"
            ) from e
        log_error(f"[LLM] 阶段二深读报错: {e}", category=category_name, error_type="llm_phase_two")
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
        res = llm.call(
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
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
        log_error(f"[LLM] 首次运行深读报错: {e}", category=category_name, error_type="llm_phase_two")
        return None

# ================= 4. 动态抓取模块 =================

OAI_PMH_ENDPOINT = "http://export.arxiv.org/oai2"
OAI_PMH_NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "arxiv": "http://arxiv.org/OAI/arXivRaw/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def parse_oai_pmh_response(xml_text):
    """解析 OAI-PMH XML 响应，返回论文列表（标准 paper dict 格式）"""
    papers = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return papers

    for record in root.findall(".//oai:record", OAI_PMH_NS):
        header = record.find("oai:header", OAI_PMH_NS)
        if header is not None and header.get("status") == "deleted":
            continue

        meta = record.find(".//arxiv:arXivRaw", OAI_PMH_NS)
        if meta is None:
            # 降级到 oai_dc 格式
            meta = record.find(".//oai:metadata", OAI_PMH_NS)
            if meta is None:
                continue
            dc = meta.find(".//dc:", OAI_PMH_NS)  # fallback
            # oai_dc 格式
            identifier_el = record.find(".//oai:identifier", OAI_PMH_NS)
            title_el = meta.find(".//dc:title", OAI_PMH_NS)
            desc_el = meta.find(".//dc:description", OAI_PMH_NS)
            date_el = meta.find(".//dc:date", OAI_PMH_NS)
            creators = meta.findall(".//dc:creator", OAI_PMH_NS)

            pid = identifier_el.text.replace("oai:arXiv.org:", "") if identifier_el is not None else ""
            title = (title_el.text or "").replace("\n", " ").strip() if title_el is not None else ""
            summary = (desc_el.text or "").replace("\n", " ").strip() if desc_el is not None else ""
            published = (date_el.text or "").strip() if date_el is not None else ""
            authors = [c.text.strip() for c in creators if c.text]
        else:
            # arXivRaw 格式（更丰富）
            pid_el = meta.find("arxiv:id", OAI_PMH_NS)
            title_el = meta.find("arxiv:title", OAI_PMH_NS)
            abstract_el = meta.find("arxiv:abstract", OAI_PMH_NS)
            authors_el = meta.find("arxiv:authors", OAI_PMH_NS)
            date_el = meta.find("arxiv:submitter", OAI_PMH_NS)  # 没有直接的 date 字段

            # 从 identifier 获取日期
            identifier_el = header.find("oai:identifier", OAI_PMH_NS) if header is not None else None
            # 从 datestamp 获取日期
            datestamp_el = header.find("oai:datestamp", OAI_PMH_NS) if header is not None else None

            pid = pid_el.text.strip() if pid_el is not None and pid_el.text else ""
            title = (title_el.text or "").replace("\n", " ").strip() if title_el is not None and title_el.text else ""
            summary = (abstract_el.text or "").replace("\n", " ").strip() if abstract_el is not None and abstract_el.text else ""
            published = (datestamp_el.text or "").strip() if datestamp_el is not None and datestamp_el.text else ""

            # 解析作者
            authors = []
            if authors_el is not None and authors_el.text:
                # arXivRaw 的 authors 是逗号分隔的字符串
                authors = [a.strip() for a in authors_el.text.split(",") if a.strip()]

        if pid and title:
            papers.append({
                "id": pid,
                "title": title,
                "summary": summary,
                "published": published,
                "authors": authors,
            })

    return papers


async def fetch_oai_pmh(session, arxiv_categories, last_date, max_results_per_cat=200, cat_name=""):
    """
    通过 OAI-PMH 协议批量拉取指定 arXiv 分类的最新论文。
    返回值: list[dict] — 标准 paper dict 格式
    """
    all_papers = {}
    # OAI-PMH 的 from 参数接受 YYYY-MM-DD 格式
    from_date = last_date[:10] if last_date else "2000-01-01"

    for cat in arxiv_categories:
        url = (
            f"{OAI_PMH_ENDPOINT}?verb=ListRecords"
            f"&set={cat}&metadataPrefix=arXivRaw&from={from_date}"
        )
        page = 0
        resumption_token = None

        while True:
            if resumption_token:
                req_url = f"{OAI_PMH_ENDPOINT}?verb=ListRecords&resumptionToken={urllib.parse.quote(resumption_token)}"
            else:
                req_url = url

            try:
                async with session.get(req_url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status == 503:
                        # OAI-PMH 503 = retry after Retry-After seconds
                        retry_after = int(resp.headers.get("Retry-After", "60"))
                        print(f"⚠️ OAI-PMH 503，等待 {retry_after}s 后重试 ({cat})")
                        await asyncio.sleep(retry_after)
                        continue
                    elif resp.status != 200:
                        log_error(f"[OAI-PMH] HTTP {resp.status} for {cat}", category=cat_name, error_type="arxiv_fetch")
                        break
                    xml_text = await resp.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log_error(f"[OAI-PMH] 请求失败 {cat}: {e}", category=cat_name, error_type="arxiv_fetch")
                break

            papers = parse_oai_pmh_response(xml_text)
            for p in papers:
                if p["id"] not in all_papers:
                    all_papers[p["id"]] = p

            # 检查 resumptionToken
            try:
                root = ET.fromstring(xml_text)
                token_el = root.find(".//oai:resumptionToken", OAI_PMH_NS)
                if token_el is not None and token_el.text and token_el.text.strip():
                    resumption_token = token_el.text.strip()
                    page += 1
                    if page > 10:  # 安全上限，避免无限翻页
                        break
                    await asyncio.sleep(3)  # OAI-PMH 建议 3s 间隔
                    continue
                else:
                    break  # 无更多分页
            except ET.ParseError:
                break

        print(f"📥 OAI-PMH {cat}: 获取 {len(all_papers)} 篇论文")
        await asyncio.sleep(3)  # 分类之间间隔 3s

    return list(all_papers.values())


def local_keyword_filter(papers, keywords):
    """
    本地关键词过滤：将 arXiv API 查询语法解析为关键词，在 title/abstract 中匹配。
    正确处理 AND 逻辑：同一关键词内的所有条件必须同时满足。
    不同关键词之间是 OR 关系（任一关键词组全部命中即保留）。
    """
    # 从查询语法中提取搜索词组（每个关键词是一个 AND 条件组）
    keyword_groups = []  # list of list of (field, terms_list)

    for kw in keywords:
        # 按 AND 分割（忽略大小写）
        and_parts = re.split(r'\s+AND\s+', kw, flags=re.IGNORECASE)

        conditions = []  # 当前关键词的 AND 条件列表
        for part in and_parts:
            part = part.strip()

            # 提取 ti:"xxx" 中的关键词
            ti_match = re.search(r'ti:"([^"]+)"', part)
            if ti_match:
                phrase = ti_match.group(1).lower()
                conditions.append(("title", phrase.split()))
                continue

            # 提取 abs:xxx 中的关键词
            abs_match = re.search(r'abs:(\w+)', part)
            if abs_match:
                conditions.append(("abstract", [abs_match.group(1).lower()]))
                continue

            # 提取 all:xxx 中的关键词
            all_match = re.search(r'all:(\w+)', part)
            if all_match:
                conditions.append(("both", [all_match.group(1).lower()]))
                continue

            # 忽略 cat:xxx 条件（OAI-PMH 已按分类拉取）

        if conditions:
            keyword_groups.append(conditions)

    if not keyword_groups:
        return papers

    matched = []
    for p in papers:
        title_lower = p.get("title", "").lower()
        abstract_lower = p.get("summary", "").lower()
        combined = title_lower + " " + abstract_lower

        # 检查是否满足任一关键词组的所有条件
        for group in keyword_groups:
            all_conditions_met = True
            for field, terms in group:
                if field == "title":
                    if not all(t in title_lower for t in terms):
                        all_conditions_met = False
                        break
                elif field == "abstract":
                    if not all(t in abstract_lower for t in terms):
                        all_conditions_met = False
                        break
                elif field == "both":
                    if not all(t in combined for t in terms):
                        all_conditions_met = False
                        break

            if all_conditions_met:
                matched.append(p)
                break  # 任一关键词组全部命中即保留

    return matched


async def fetch_arxiv_single(session, url, max_retries=3, base_delay=6.0):
    """
    单次抓取 arXiv 的函数，使用更长的延迟和重试策略。
    返回值：
      - 非空字符串：成功获取的响应内容（可能包含 0 条或更多条目）
      - None：所有重试均失败（429/超时/网络错误），表示抓取失败
    """
    timeout = aiohttp.ClientTimeout(total=45, connect=10)
    last_error = None
    for attempt in range(max_retries):
        try:
            # 每次请求前都添加延迟，避免触发速率限制
            if attempt > 0:
                delay = base_delay * (2 ** (attempt - 1))
                print(f"⏳ 延迟 {delay:.1f}s 后重试...")
                await asyncio.sleep(delay)

            async with session.get(url, timeout=timeout) as resp:
                if resp.status == 429:
                    # 速率限制，固定等待 60s
                    wait_time = 60
                    print(f"⚠️ 遇到速率限制，等待 {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    last_error = "429 速率限制"
                    continue
                elif resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                return await resp.text()
        except RuntimeError as e:
            last_error = str(e)
            if attempt == max_retries - 1:
                print(f"❌ 抓取失败，已放弃: {url}，原因: {e}")
                return None
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

            last_error = error_msg
            print(f"❌ {error_msg}")

            if attempt == max_retries - 1:
                print(f"❌ 抓取失败，已放弃: {url}")
                return None

            # 其他网络错误也等待一段时间
            delay = 5.0 * (attempt + 1)
            print(f"⏳ 网络错误延迟 {delay:.1f}s 后重试...")
            await asyncio.sleep(delay)
        except Exception as e:
            error_type = type(e).__name__
            last_error = f"{error_type}: {e}"
            print(f"❌ 未知错误({error_type}): {e}")
            if attempt == max_retries - 1:
                print(f"❌ 抓取失败，已放弃: {url}")
                return None
            delay = 5.0 * (attempt + 1)
            print(f"⏳ 未知错误延迟 {delay:.1f}s 后重试...")
            await asyncio.sleep(delay)

    # 所有重试均为 429，循环正常结束
    print(f"❌ 所有 {max_retries} 次重试均被限速，放弃: {url}")
    return None


async def fetch_arxiv(session, keywords, state, arxiv_categories=None, cat_is_first_run=None, cat_name=""):
    all_papers = {}
    max_published_date = state["last_date"]

    # 分类级别的首次运行标志优先于全局标志
    is_first = cat_is_first_run if cat_is_first_run is not None else state["is_first_run"]

    if is_first:
        latest_candidates = {}
        hot_candidates = {}
        hot_order = []
        failed_keywords = []

        # 首次运行：冷启动填充，不过滤日期，全量接收 arXiv 返回的论文
        first_run_cutoff = "2000-01-01T00:00:00Z"

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
            if latest_text is None:
                log_error(f"[arXiv] 首次运行关键词抓取失败: {kw}", category=cat_name, error_type="arxiv_fetch")
                failed_keywords.append(kw)
            elif latest_text:
                latest_feed = feedparser.parse(latest_text)
                for e in latest_feed.entries:
                    pub_date = e.get('published', '')
                    if pub_date > first_run_cutoff:
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

            # 请求之间添加更长的延迟（30秒，避免触发 arXiv 速率限制）
            print("⏳ 请求间隔 30s...")
            await asyncio.sleep(30.0)

            # 拉取 Relevance 10 篇
            hot_text = await fetch_arxiv_single(session, hot_url)
            if hot_text is None:
                if kw not in failed_keywords:
                    log_error(f"[arXiv] 首次运行关键词抓取失败: {kw}", category=cat_name, error_type="arxiv_fetch")
                    failed_keywords.append(kw)
            elif hot_text:
                hot_feed = feedparser.parse(hot_text)
                for e in hot_feed.entries:
                    pub_date = e.get('published', '')
                    if pub_date > first_run_cutoff:
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

            # 关键词之间添加更长的延迟（45秒，避免触发 arXiv 速率限制）
            print("⏳ 关键词间隔 45s...")
            await asyncio.sleep(45.0)

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
        return merged, max_published_date, failed_keywords

    failed_keywords = []

    for kw in keywords:
        encoded_kw = urllib.parse.quote(kw)
        print(f"🔍 增量拉取：抓取最新论文对比 {state['last_date']} -> {kw}")
        url = f"http://export.arxiv.org/api/query?search_query={encoded_kw}&sortBy=submittedDate&sortOrder=descending&max_results=30"

        text = await fetch_arxiv_single(session, url)
        if text is None:
            # fetch_arxiv_single 返回 None 表示所有重试均失败（429/超时/网络错误）
            log_error(f"[arXiv] 增量抓取关键词失败: {kw}", category=cat_name, error_type="arxiv_fetch")
            failed_keywords.append(kw)
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

        # 请求之间添加延迟（避免触发 arXiv 速率限制）
        print("⏳ 请求间隔 30s...")
        await asyncio.sleep(30.0)

    # OAI-PMH 备选方案：当有关键词因限速/超时失败时，通过 OAI-PMH 按分类批量拉取
    if failed_keywords and arxiv_categories:
        print(f"🔄 {len(failed_keywords)} 个关键词失败，尝试 OAI-PMH 备选方案...")
        try:
            oai_papers = await fetch_oai_pmh(session, arxiv_categories, state["last_date"], cat_name=cat_name)
            if oai_papers:
                matched = local_keyword_filter(oai_papers, failed_keywords)
                new_count = 0
                for p in matched:
                    if p["id"] not in all_papers:
                        all_papers[p["id"]] = p
                        new_count += 1
                        if p.get("published", "") > max_published_date:
                            max_published_date = p["published"]
                print(f"✅ OAI-PMH 备选方案: 拉取 {len(oai_papers)} 篇，关键词匹配 {len(matched)} 篇，新增 {new_count} 篇")
                # OAI-PMH 成功则清除失败标记
                failed_keywords = []
            else:
                log_error("[OAI-PMH] 备选方案未返回任何论文", category=cat_name, error_type="arxiv_fetch")
        except Exception as e:
            log_error(f"[OAI-PMH] 备选方案执行失败: {e}", category=cat_name, error_type="arxiv_fetch")

    return list(all_papers.values()), max_published_date, failed_keywords

# ================= 5. 主流程 =================
async def main():
    print("🚀 开始执行 main.py")
    if DRY_RUN:
        print("🧪 DRY_RUN=1，本次仅本地演练：不会写入 Zotero，也不会更新 history/state 文件")
    
    # 错误处理
    try:
        await _main_impl()
    except Exception as e:
        print(f"❌ 程序运行出错: {e}")
        import traceback
        traceback.print_exc()
        
        # 发送错误通知
        if ENABLE_NOTIFICATION and not DRY_RUN:
            error_payload = list(_errors)
            error_payload.append({
                "category": "运行流程",
                "type": "runtime",
                "message": str(e),
            })
            notifier.send_structured_error_report(error_payload)
        raise


async def _main_impl():
    print("🚀 开始执行 main.py")
    if DRY_RUN:
        print("🧪 DRY_RUN=1，本次仅本地演练：不会写入 Zotero，也不会更新 history/state 文件")
    kb = ensure_knowledge_base()
    if not kb:
        print("⚠️ 未能获得可用知识库，将以空知识库继续运行")

    # 检查所有配置的类目是否都在知识库中有数据
    missing_categories = [cat_name for cat_name in CONFIG["categories"] if cat_name not in kb]
    if missing_categories and kb:
        print(f"⚠️ 发现 {len(missing_categories)} 个类目缺少知识库数据: {', '.join(missing_categories)}")
        print("🔄 正在重新构建知识库...")
        try:
            build_knowledge_base()
            kb = load_json_file("knowledge_base.json", {})
            if not isinstance(kb, dict):
                print("⚠️ 重新构建后 knowledge_base.json 格式异常，使用空知识库")
                kb = {}
            else:
                print("✅ 知识库重新构建完成")
        except Exception as e:
            print(f"❌ 知识库重新构建失败: {e}")
            import traceback
            traceback.print_exc()
            log_error(f"[KB] 分类知识库重建失败: {e}", error_type="knowledge_base_build")

    history = load_json_file(HISTORY_FILE, [])
    if not isinstance(history, list):
        print("⚠️ history.json 格式异常，重置为空列表")
        history = []
    history_set = set(history)
    
    state = load_state()
    initialized_categories = set(state.get("initialized_categories", []))
    global_max_date = state["last_date"]
    print(f"🧭 当前状态: is_first_run={state['is_first_run']}, last_date={state['last_date']}")
    print(f"🧭 已初始化分类: {', '.join(initialized_categories) if initialized_categories else '无'}")
    
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
        
        # 飞书知识库：为所有类目预建 DailyPapers 及子目录（保证首次运行时能写入）
        if feishu_wiki_client:
            try:
                print("📚 正在确保飞书知识库目录就绪（DailyPapers → 分类）...")
                feishu_wiki_client.bootstrap_layout(list(CONFIG["categories"].keys()))
                print("✅ 飞书知识库目录准备完成")
            except Exception as e:
                log_error(f"[Feishu] 初始化知识库目录失败: {e}", error_type="feishu_sync")

    # 统计变量
    stats = {
        "categories": {},
        "total_papers": 0,
        "papers": {},  # 存储新论文的详细信息
        "total_attempted_analysis": 0,  # 进入分析流程的论文数
        "llm_failures": 0,  # LLM 分析失败的论文数
    }

    all_failed_keywords = []  # 追踪所有抓取失败的关键词

    async with aiohttp.ClientSession() as session:
        for cat_name, cat_info in CONFIG["categories"].items():
            print(f"\n--- 正在处理分类: {cat_name} ---")
            stats["categories"][cat_name] = 0

            # 分类级别的首次运行判断：未在 initialized_categories 中的分类走首次运行流程
            cat_is_first_run = cat_name not in initialized_categories
            if cat_is_first_run:
                print(f"🆕 分类 {cat_name} 尚未初始化，执行首次运行流程")

            # 动态抓取（支持首次与增量）
            papers, cat_max_date, failed_kws = await fetch_arxiv(
                session, cat_info["keywords"], state,
                cat_info.get("arxiv_categories", []),
                cat_is_first_run=cat_is_first_run,
                cat_name=cat_name
            )
            if cat_max_date > global_max_date: global_max_date = cat_max_date
            if failed_kws:
                all_failed_keywords.extend([(cat_name, kw) for kw in failed_kws])
            
            kb_entries = kb.get(cat_name, [])

            for p in papers:
                if p['id'] in history_set:
                    continue

                stats["total_attempted_analysis"] += 1

                if cat_is_first_run:
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
                        log_error(f"[Zotero] 首次运行条目写入失败: {p['title'][:40]}... 原因: {_zotero_err}", category=cat_name, error_type="zotero_write")
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
                            log_error(f"[Zotero] 首次运行笔记创建失败: {p['title'][:40]}... 原因: {_note_err}", category=cat_name, error_type="zotero_write")
                        print("✅ 首次运行已直存至 Zotero")
                        history.append(p['id'])
                        history_set.add(p['id'])
                        # 飞书知识库同步
                        doc_url = None
                        if feishu_wiki_client:
                            try:
                                doc_url = feishu_wiki_client.mirror_paper_to_wiki(cat_name, {
                                    "title": p['title'],
                                    "authors": p.get('authors', []),
                                    "published": p.get('published', ''),
                                    "arxiv_id": p['id'],
                                    "recommendation": first_run_analysis.get('recommendation', '值得看'),
                                    "methodology": first_run_analysis.get('methodology', ''),
                                    "core_concepts": first_run_analysis.get('core_concepts', []),
                                    "sharp_review": first_run_analysis.get('sharp_review', ''),
                                    "summary": first_run_analysis.get('summary', ''),
                                })
                                if doc_url:
                                    print(f"📝 已同步至飞书知识库: {doc_url}")
                            except Exception as _wiki_err:
                                log_error(f"[飞书] 知识库同步失败: {p['title'][:40]}... 原因: {_wiki_err}", category=cat_name, error_type="feishu_sync")
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
                            "feishu_wiki_url": doc_url or "",
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
                phase_one_res = check_relevance_phase_one(p, kb_entries, category_name=cat_name)
                if DEBUG_PHASE_ONE:
                    print(
                        "📊 阶段一输出: "
                        f"score={phase_one_res.get('score', 0)}, "
                        f"is_relevant={phase_one_res.get('is_relevant', False)}, "
                        f"matched_titles={len(phase_one_res.get('matched_titles', []))}, "
                        f"reason={phase_one_res.get('reason', '')}"
                    )
                if not phase_one_res.get("is_relevant"):
                    if phase_one_res.get("error"):
                        stats["llm_failures"] += 1
                    print(f"⏭️ 评分不够或无相关性，跳过: {p['title'][:30]}...")
                    continue

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
                    log_error(f"[Zotero] 增量条目写入失败: {p['title'][:40]}... 原因: {_zotero_err}", category=cat_name, error_type="zotero_write")
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
                        log_error(f"[Zotero] 增量笔记创建失败: {p['title'][:40]}... 原因: {_note_err}", category=cat_name, error_type="zotero_write")
                    print(f"✅ 成功同步至 Zotero")
                    history.append(p['id'])
                    history_set.add(p['id'])
                    # 飞书知识库同步
                    doc_url = None
                    if feishu_wiki_client:
                        try:
                            doc_url = feishu_wiki_client.mirror_paper_to_wiki(cat_name, {
                                "title": p['title'],
                                "authors": p.get('authors', []),
                                "published": p.get('published', ''),
                                "arxiv_id": p['id'],
                                "recommendation": analysis.get('recommendation', '值得看'),
                                "methodology": analysis.get('methodology', ''),
                                "core_concepts": analysis.get('core_concepts', []),
                                "sharp_review": analysis.get('sharp_review', ''),
                                "comparison": analysis.get('comparison', ''),
                            })
                            if doc_url:
                                print(f"📝 已同步至飞书知识库: {doc_url}")
                        except Exception as _wiki_err:
                            log_error(f"[飞书] 知识库同步失败: {p['title'][:40]}... 原因: {_wiki_err}", category=cat_name, error_type="feishu_sync")
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
                        "feishu_wiki_url": doc_url or "",
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

            # 分类处理完成，标记为已初始化
            if cat_is_first_run and cat_name not in initialized_categories:
                initialized_categories.add(cat_name)
                print(f"✅ 分类 {cat_name} 首次运行完成，已标记为已初始化")

    # 抓取失败汇总
    has_failures = bool(all_failed_keywords) or bool(_errors)
    if all_failed_keywords:
        total_kw = sum(len(cat_info["keywords"]) for cat_info in CONFIG["categories"].values())
        failed_count = len(all_failed_keywords)
        print(f"\n🚨 抓取失败汇总: {failed_count}/{total_kw} 个关键词因限速/超时未能获取数据")
        for cat_name, kw in all_failed_keywords:
            print(f"   ❌ [{cat_name}] {kw}")
        if failed_count >= total_kw * 0.5:
            print("⚠️ 超过半数关键词抓取失败，本次结果可能不完整！")

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
        # history.json 始终更新（记录已处理的论文，避免下次重复处理）
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False)

        # state.json 仅在无抓取失败时更新 last_date，确保失败的关键词下次能重新抓取
        # 但 initialized_categories 始终保存（分类初始化不受关键词失败影响）
        if not all_failed_keywords:
            save_state(global_max_date, list(initialized_categories))
            print(f"\n🎉 任务完成！记录的最新论文时间戳为：{global_max_date}")
        else:
            # 有失败时只更新 initialized_categories，不更新 last_date
            _state = load_state()
            _state["initialized_categories"] = list(initialized_categories)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(_state, f, ensure_ascii=False)
            print(f"\n⚠️ 任务完成但存在抓取失败，不更新 last_date（当前: {state['last_date']}），下次将重试失败的关键词")

        # 发送通知
        if ENABLE_NOTIFICATION:
            has_errors = bool(_errors) or bool(all_failed_keywords)

            if stats["total_papers"] > 0:
                print("📤 发送新论文通知...")
                notifier.send_papers_detail(stats, state["is_first_run"])
                if has_errors:
                    print("📤 追发结构化错误通知...")
                    notifier.send_structured_error_report(_errors, all_failed_keywords, stats)
            elif has_errors:
                print("📤 发送结构化错误告警...")
                notifier.send_structured_error_report(_errors, all_failed_keywords, stats)
            else:
                print("📤 发送无新论文通知...")
                notifier.send_no_papers_notification(state["is_first_run"], CONFIG["categories"])

if __name__ == "__main__":
    asyncio.run(main())
