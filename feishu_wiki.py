"""
飞书知识库镜像客户端

将 Zotero DailyPapers 的论文笔记同步到飞书 Wiki 知识库，
镜像 Zotero 的目录结构（DailyPapers → 各研究分类）。

依赖：requests（已存在于 requirements.txt）
"""

import json
import os
import time

import requests

CACHE_FILE = "feishu_wiki_node_cache.json"
FEISHU_BASE = "https://open.feishu.cn/open-apis"


class FeishuWikiClient:
    def __init__(self, app_id, app_secret, root_node_token):
        self.app_id = app_id
        self.app_secret = app_secret
        self.root_node_token = root_node_token
        self._token = None
        self._token_expiry = 0
        self._space_id = None
        self._cache = self._load_node_cache()

    # ── Token 管理 ──────────────────────────────────────────────

    def _get_tenant_access_token(self):
        url = f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal"
        resp = requests.post(url, json={
            "app_id": self.app_id,
            "app_secret": self.app_secret,
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取 tenant_access_token 失败: {data}")
        self._token = data["tenant_access_token"]
        self._token_expiry = time.time() + data.get("expire", 7200) - 300

    def _ensure_token(self):
        if not self._token or time.time() >= self._token_expiry:
            self._get_tenant_access_token()
        return self._token

    # ── 通用 API 请求 ───────────────────────────────────────────

    def _api_request(self, method, url, json_body=None, params=None):
        token = self._ensure_token()
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.request(method, url, json=json_body, params=params,
                                headers=headers, timeout=15)
        if resp.status_code == 401:
            self._get_tenant_access_token()
            headers["Authorization"] = f"Bearer {self._token}"
            resp = requests.request(method, url, json=json_body, params=params,
                                    headers=headers, timeout=15)
        if resp.status_code >= 400:
            try:
                err_body = resp.json()
            except Exception:
                err_body = resp.text
            raise RuntimeError(f"飞书 API HTTP {resp.status_code}: {err_body}")
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书 API 错误: {data}")
        return data.get("data", data)

    # ── Wiki 空间发现 ───────────────────────────────────────────

    def _discover_space_id(self):
        if self._space_id:
            return self._space_id
        data = self._api_request(
            "GET",
            f"{FEISHU_BASE}/wiki/v2/spaces/get_node",
            params={"token": self.root_node_token},
        )
        node = data.get("node", data)
        self._space_id = node.get("space_id", "")
        return self._space_id

    # ── Wiki 节点操作 ───────────────────────────────────────────

    def list_child_nodes(self, parent_node_token):
        space_id = self._discover_space_id()
        children = {}
        page_token = None
        while True:
            params = {"parent_node_token": parent_node_token, "page_size": 50}
            if page_token:
                params["page_token"] = page_token
            data = self._api_request(
                "GET",
                f"{FEISHU_BASE}/wiki/v2/spaces/{space_id}/nodes",
                params=params,
            )
            for node in data.get("items", []):
                title = node.get("title", "")
                token = node.get("node_token", "")
                if title and token:
                    children[title] = token
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
        return children

    def get_or_create_child_node(self, parent_node_token, title):
        cache_key = f"{parent_node_token}/{title}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        children = self.list_child_nodes(parent_node_token)
        if title in children:
            self._cache[cache_key] = children[title]
            self._save_node_cache(self._cache)
            return children[title]

        space_id = self._discover_space_id()
        # 创建方式：先创建文档，再将其作为节点移入 wiki 空间
        doc_id = self._create_standalone_document(title)
        data = self._api_request(
            "POST",
            f"{FEISHU_BASE}/wiki/v2/spaces/{space_id}/nodes",
            json_body={
                "obj_type": "docx",
                "obj_token": doc_id,
                "node_type": 1,
                "title": title,
            },
            params={"parent_node_token": parent_node_token},
        )
        node_token = data.get("node", data).get("node_token", "")
        if node_token:
            self._cache[cache_key] = node_token
            self._save_node_cache(self._cache)
        return node_token

    def ensure_category_node(self, category):
        daily_key = f"_root_/DailyPapers"
        if daily_key not in self._cache:
            children = self.list_child_nodes(self.root_node_token)
            if "DailyPapers" in children:
                self._cache[daily_key] = children["DailyPapers"]
            else:
                self._cache[daily_key] = self.get_or_create_child_node(
                    self.root_node_token, "DailyPapers"
                )
            self._save_node_cache(self._cache)
        daily_node = self._cache[daily_key]

        cat_key = f"{daily_key}/{category}"
        if cat_key not in self._cache:
            self._cache[cat_key] = self.get_or_create_child_node(daily_node, category)
            self._save_node_cache(self._cache)
        return self._cache[cat_key]

    # ── 文档操作 ─────────────────────────────────────────────────

    def _create_standalone_document(self, title):
        """创建独立文档（不指定文件夹），返回 document_id"""
        data = self._api_request(
            "POST",
            f"{FEISHU_BASE}/docx/v1/documents",
            json_body={"title": title},
        )
        doc = data.get("document", data)
        return doc.get("document_id", "")

    def create_document_in_node(self, node_token, title):
        """在指定 wiki 节点下创建文档"""
        data = self._api_request(
            "POST",
            f"{FEISHU_BASE}/docx/v1/documents",
            json_body={"folder_token": node_token, "title": title},
        )
        doc = data.get("document", data)
        document_id = doc.get("document_id", "")
        return document_id, document_id

    def write_document_blocks(self, document_id, block_id, blocks):
        chunk_size = 50
        for i in range(0, len(blocks), chunk_size):
            chunk = blocks[i:i + chunk_size]
            self._api_request(
                "POST",
                f"{FEISHU_BASE}/docx/v1/documents/{document_id}/blocks/{block_id}/children",
                json_body={"children": chunk},
            )
        return True

    # ── 论文信息 → 飞书 Block 转换 ──────────────────────────────

    def _paper_info_to_blocks(self, paper_info):
        blocks = []

        def text_el(content, bold=False, italic=False, link=None):
            style = {}
            if bold:
                style["bold"] = True
            if italic:
                style["italic"] = True
            if link:
                style["link"] = {"url": link}
            el = {"text_run": {"content": content}}
            if style:
                el["text_run"]["text_element_style"] = style
            return el

        def paragraph_block(elements, heading_level=0):
            if heading_level > 0:
                htype = heading_level + 1
                return {
                    "block_type": htype,
                    f"heading{heading_level}": {
                        "elements": elements,
                    },
                }
            return {
                "block_type": 2,
                "text": {"elements": elements},
            }

        def divider_block():
            return {"block_type": 22, "divider": {}}

        # 标题
        blocks.append(paragraph_block([text_el(paper_info.get("title", ""), bold=True)], heading_level=2))

        # 推荐指数
        rec = paper_info.get("recommendation", "值得看")
        blocks.append(paragraph_block([
            text_el("推荐指数：", bold=True),
            text_el(rec, bold=True),
        ]))

        # 作者
        authors = paper_info.get("authors", [])
        if authors:
            blocks.append(paragraph_block([
                text_el("作者：", bold=True),
                text_el(", ".join(authors)),
            ]))

        # arXiv 时间 + 链接
        published = paper_info.get("published", "")
        arxiv_id = paper_info.get("arxiv_id", "")
        if published:
            blocks.append(paragraph_block([
                text_el("arXiv 上传时间：", bold=True),
                text_el(published),
            ]))
        if arxiv_id:
            arxiv_url = f"https://arxiv.org/abs/{arxiv_id}"
            blocks.append(paragraph_block([
                text_el("原文链接：", bold=True),
                text_el(arxiv_url, link=arxiv_url),
            ]))

        blocks.append(divider_block())

        # 一句话摘要
        summary = paper_info.get("summary", "")
        if summary:
            blocks.append(paragraph_block([text_el("一句话摘要", bold=True)], heading_level=3))
            blocks.append(paragraph_block([text_el(summary)]))

        # 深度对比（增量模式）
        comparison = paper_info.get("comparison", "")
        if comparison:
            blocks.append(paragraph_block([text_el("深度差量对比", bold=True)], heading_level=3))
            blocks.append(paragraph_block([text_el(comparison)]))

        blocks.append(divider_block())

        # 核心术语
        concepts = paper_info.get("core_concepts", [])
        if concepts:
            blocks.append(paragraph_block([text_el("核心术语库", bold=True)], heading_level=3))
            blocks.append(paragraph_block([text_el(" | ".join(concepts))]))

        # 方法论
        methodology = paper_info.get("methodology", "")
        if methodology:
            blocks.append(paragraph_block([text_el("核心方法简述", bold=True)], heading_level=3))
            blocks.append(paragraph_block([text_el(methodology)]))

        # 锐评
        sharp_review = paper_info.get("sharp_review", "")
        if sharp_review:
            blocks.append(paragraph_block([text_el("锐评", bold=True)], heading_level=3))
            blocks.append(paragraph_block([text_el(sharp_review, italic=True)]))

        return blocks

    # ── 高层编排 ─────────────────────────────────────────────────

    def mirror_paper_to_wiki(self, category, paper_info):
        category_node = self.ensure_category_node(category)
        title = paper_info.get("title", "Untitled")[:100]
        document_id, block_id = self.create_document_in_node(category_node, title)
        blocks = self._paper_info_to_blocks(paper_info)
        self.write_document_blocks(document_id, block_id, blocks)
        return f"https://my.feishu.cn/wiki/{document_id}"

    # ── 缓存 ─────────────────────────────────────────────────────

    def _load_node_cache(self):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_node_cache(self, cache):
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print(f"⚠️ 飞书节点缓存写入失败: {e}")
