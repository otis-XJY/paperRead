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
# 浏览器打开云文档用 /docx/{document_id}；/wiki/{token} 只能是知识库节点 token，不能与 document_id 混用
FEISHU_WEB_BASE_DEFAULT = "https://my.feishu.cn"


class FeishuWikiClient:
    def __init__(
        self,
        app_id,
        app_secret,
        root_node_token,
        daily_folder_name="DailyPapers",
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.root_node_token = root_node_token
        self._daily_folder_name = daily_folder_name or "DailyPapers"
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
        # 直接在 Wiki 空间中创建节点（不创建独立文档）
        data = self._api_request(
            "POST",
            f"{FEISHU_BASE}/wiki/v2/spaces/{space_id}/nodes",
            json_body={
                "obj_type": "docx",
                "node_type": "origin",
                "parent_node_token": parent_node_token,
                "title": title,
            },
        )
        node_token = data.get("node", data).get("node_token", "")
        if node_token:
            self._cache[cache_key] = node_token
            self._save_node_cache(self._cache)
        return node_token

    def _node_is_valid(self, node_token):
        """检查缓存的节点是否仍然存在于飞书 Wiki 中。"""
        try:
            self.list_child_nodes(node_token)
            return True
        except (RuntimeError, requests.RequestException):
            return False

    def _invalidate_cache_prefix(self, prefix):
        """移除缓存中以指定前缀开头的所有条目。"""
        keys_to_remove = [k for k in self._cache if k == prefix or k.startswith(prefix + "/")]
        for k in keys_to_remove:
            del self._cache[k]
        if keys_to_remove:
            self._save_node_cache(self._cache)

    def ensure_category_node(self, category):
        daily_key = f"_root_/{self._daily_folder_name}"
        if daily_key in self._cache:
            # 验证缓存的 daily 节点是否仍然有效
            if not self._node_is_valid(self._cache[daily_key]):
                self._invalidate_cache_prefix(daily_key)

        if daily_key not in self._cache:
            children = self.list_child_nodes(self.root_node_token)
            if self._daily_folder_name in children:
                self._cache[daily_key] = children[self._daily_folder_name]
            else:
                self._cache[daily_key] = self.get_or_create_child_node(
                    self.root_node_token, self._daily_folder_name
                )
            self._save_node_cache(self._cache)
        daily_node = self._cache[daily_key]

        cat_key = f"{daily_key}/{category}"
        if cat_key in self._cache:
            # 验证缓存的分类节点是否仍然有效
            if not self._node_is_valid(self._cache[cat_key]):
                self._invalidate_cache_prefix(cat_key)

        if cat_key not in self._cache:
            self._cache[cat_key] = self.get_or_create_child_node(daily_node, category)
            self._save_node_cache(self._cache)
        return self._cache[cat_key]

    def bootstrap_layout(self, categories):
        """预先创建「根 → 日更目录 → 各分类」节点，无需跑主流程即可在知识库中看到目录。"""
        for name in categories:
            node_token = self.ensure_category_node(name)
            print(f"  ✅ {self._daily_folder_name}/{name} → {node_token}")

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

    def write_document_blocks(self, document_id, parent_block_id, blocks):
        """向父块追加内容，使用「创建嵌套块」descendant 接口（旧版 /children 已不可用）。"""
        chunk_size = 50
        for i in range(0, len(blocks), chunk_size):
            chunk = blocks[i : i + chunk_size]
            children_id = []
            descendants = []
            for j, raw in enumerate(chunk):
                bid = f"_tmp_{i}_{j}"
                children_id.append(bid)
                node = dict(raw)
                node["block_id"] = bid
                node.setdefault("children", [])
                descendants.append(node)
            self._api_request(
                "POST",
                f"{FEISHU_BASE}/docx/v1/documents/{document_id}/blocks/{parent_block_id}/descendant",
                params={"document_revision_id": -1},
                json_body={
                    "index": -1,
                    "children_id": children_id,
                    "descendants": descendants,
                },
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
                # 飞书枚举：heading1=3 … heading9=11，故 headingN → block_type = N + 2
                return {
                    "block_type": heading_level + 2,
                    f"heading{heading_level}": {
                        "elements": elements,
                    },
                }
            return {
                "block_type": 2,
                "text": {"elements": elements},
            }

        def divider_block():
            return {"block_type": 22, "divider": {}, "children": []}

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
        base = os.getenv("FEISHU_WEB_BASE", FEISHU_WEB_BASE_DEFAULT).rstrip("/")
        return f"{base}/docx/{document_id}"

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
