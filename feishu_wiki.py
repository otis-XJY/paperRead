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
            # 通过父节点实际子列表验证缓存 token，避免使用已删除节点的旧 token
            try:
                real_children = self.list_child_nodes(daily_node)
                real_token = real_children.get(category)
                if real_token and real_token != self._cache[cat_key]:
                    self._cache[cat_key] = real_token
                    self._save_node_cache(self._cache)
                elif not real_token:
                    self._invalidate_cache_prefix(cat_key)
            except (RuntimeError, requests.RequestException):
                self._invalidate_cache_prefix(daily_key)
                self._invalidate_cache_prefix(cat_key)

        if cat_key not in self._cache:
            # 重新获取 daily_node（可能已被清除）
            if daily_key not in self._cache:
                children = self.list_child_nodes(self.root_node_token)
                if self._daily_folder_name in children:
                    self._cache[daily_key] = children[self._daily_folder_name]
                    self._save_node_cache(self._cache)
                else:
                    self._cache[daily_key] = self.get_or_create_child_node(
                        self.root_node_token, self._daily_folder_name
                    )
            daily_node = self._cache[daily_key]
            # 先查父节点已有子节点，避免重复创建
            real_children = self.list_child_nodes(daily_node)
            if category in real_children:
                self._cache[cat_key] = real_children[category]
            else:
                self._cache[cat_key] = self.get_or_create_child_node(daily_node, category)
            self._save_node_cache(self._cache)
        return self._cache[cat_key]

    # ── 推荐级别子目录 ──────────────────────────────────────────────

    RECOMMENDATION_LEVELS = ("必读", "值得看", "可跳过")

    def _ensure_recommendation_node(self, category_node, category, rec_type):
        """确保 category/rec_type 节点存在并缓存，返回 node_token。"""
        rec_key = f"_root_/{self._daily_folder_name}/{category}/{rec_type}"
        if rec_key in self._cache:
            return self._cache[rec_key]
        node = self.get_or_create_child_node(category_node, rec_type)
        if node:
            self._cache[rec_key] = node
            self._save_node_cache(self._cache)
        return node

    def move_wiki_node(self, node_token, new_parent_token):
        """将 wiki 节点移动到新的父节点下。返回 True/False。"""
        space_id = self._discover_space_id()
        try:
            self._api_request(
                "POST",
                f"{FEISHU_BASE}/wiki/v2/spaces/{space_id}/nodes/{node_token}/move",
                json_body={
                    "target_parent_token": new_parent_token,
                    "type": 2,  # 移动到目标节点下
                },
            )
            return True
        except RuntimeError as e:
            print(f"⚠️ 节点移动失败: {e}")
            return False

    def _read_document_blocks(self, document_id):
        """读取指定文档的所有 block，返回 block 列表（dict）。"""
        blocks = []
        page_token = None
        while True:
            params = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            data = self._api_request(
                "GET",
                f"{FEISHU_BASE}/docx/v1/documents/{document_id}/blocks",
                params=params,
            )
            blocks.extend(data.get("items", []))
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
        return blocks

    def _extract_recommendation_from_doc(self, document_id):
        """从飞书文档内容中提取「推荐指数」值，返回推荐类型字符串或 None。"""
        try:
            blocks = self._read_document_blocks(document_id)
        except Exception:
            return None
        for block in blocks:
            # 推荐指数出现在文本块中，格式为「推荐指数：必读」
            text_el = block.get("text", {}).get("elements", [])
            full_text = "".join(
                el.get("text_run", {}).get("content", "") for el in text_el
            )
            if "推荐指数" in full_text:
                for level in self.RECOMMENDATION_LEVELS:
                    if level in full_text:
                        return level
                # 格式异常，尝试冒号后提取
                parts = full_text.split("：", 1)
                if len(parts) > 1:
                    candidate = parts[1].strip()
                    if candidate in self.RECOMMENDATION_LEVELS:
                        return candidate
        return None

    def reallocate_papers_to_recommendation(self, category, category_node):
        """将已有论文从 category 节点移至对应推荐级别子节点。

        读取每篇飞书文档的推荐指数内容，移入对应子目录；
        无法读取或无推荐信息的论文保持原位不动。
        """
        children = self.list_child_nodes(category_node)

        # 过滤掉推荐级别子节点本身
        existing_rec_nodes = set(self.RECOMMENDATION_LEVELS)
        papers_to_move = {t: tok for t, tok in children.items() if t not in existing_rec_nodes}
        if not papers_to_move:
            return 0

        # 预加载各推荐子节点下的已有论文标题，防止重复
        existing_in_rec = {}
        for rec_type in self.RECOMMENDATION_LEVELS:
            rec_node = self._ensure_recommendation_node(category_node, category, rec_type)
            if rec_node:
                existing_in_rec[rec_type] = set(self.list_child_nodes(rec_node).keys())
            else:
                existing_in_rec[rec_type] = set()

        moved_count = 0
        for paper_title, paper_token in papers_to_move.items():
            # 尝试读取文档内容提取推荐类型
            rec_type = self._extract_recommendation_from_doc(paper_token)

            if not rec_type:
                print(f"  ⏭️ [{category}] 无法读取推荐类型，保持原位: {paper_title[:40]}")
                continue

            if paper_title in existing_in_rec.get(rec_type, set()):
                print(f"  ⏭️ [{category}] 论文已存在于 {rec_type} 子目录，跳过: {paper_title[:40]}")
                continue

            rec_node = self._ensure_recommendation_node(category_node, category, rec_type)
            if not rec_node:
                print(f"  ⚠️ [{category}] 无法获取推荐子节点 {rec_type}，跳过: {paper_title[:40]}")
                continue

            if self.move_wiki_node(paper_token, rec_node):
                moved_count += 1
                # 清理旧缓存键并写入新缓存
                old_key_1 = f"{category_node}/{paper_title}"
                old_key_2 = f"_root_/{self._daily_folder_name}/{category}/{paper_title}"
                for old_key in (old_key_1, old_key_2):
                    if old_key in self._cache:
                        del self._cache[old_key]
                new_key = f"{rec_node}/{paper_title}"
                self._cache[new_key] = paper_token
                print(f"  📦 [{category}] {paper_title[:40]} → {rec_type}")
            else:
                print(f"  ⚠️ [{category}] 迁移失败: {paper_title[:40]}")
        if moved_count:
            self._save_node_cache(self._cache)
        return moved_count

    def bootstrap_layout(self, categories):
        """预先创建「根 → 日更目录 → 各分类 → 推荐级别」节点，并迁移已有论文。"""
        for name in categories:
            node_token = self.ensure_category_node(name)
            print(f"  ✅ {self._daily_folder_name}/{name} → {node_token}")

            # 创建推荐级别子节点
            for rec_type in self.RECOMMENDATION_LEVELS:
                rec_token = self._ensure_recommendation_node(node_token, name, rec_type)
                print(f"    ✅ {self._daily_folder_name}/{name}/{rec_type} → {rec_token}")

            # 迁移已有论文到推荐子节点
            moved = self.reallocate_papers_to_recommendation(name, node_token)
            if moved:
                print(f"  📦 [{name}] 已迁移 {moved} 篇论文到「{self.RECOMMENDATION_LEVELS[0]}」子目录")

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
        # 写入前校验节点是否仍然存在，失效则清除缓存并重建
        if not self._node_is_valid(category_node):
            cat_key = f"_root_/{self._daily_folder_name}/{category}"
            self._invalidate_cache_prefix(cat_key)
            category_node = self.ensure_category_node(category)

        # 根据推荐级别路由到子节点
        raw_rec = (paper_info.get("recommendation") or "值得看").strip()
        rec_type = raw_rec if raw_rec in self.RECOMMENDATION_LEVELS else "值得看"
        rec_node = self._ensure_recommendation_node(category_node, category, rec_type)
        if not rec_node or not self._node_is_valid(rec_node):
            rec_key = f"_root_/{self._daily_folder_name}/{category}/{rec_type}"
            self._invalidate_cache_prefix(rec_key)
            rec_node = self._ensure_recommendation_node(category_node, category, rec_type)

        title = paper_info.get("title", "Untitled")[:100]
        # 优先写入推荐级别子节点，失败时回退到分类节点
        try:
            document_id, block_id = self.create_document_in_node(rec_node, title)
        except Exception:
            print(f"⚠️ [{category}] 推荐子节点写入失败，回退到分类节点")
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
