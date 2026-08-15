"""
通知推送模块
支持企业微信和飞书推送
"""
import os
import sys
import json
import time
import requests
from feishu_sdk import FeishuOpenAPIClient, FeishuSDKError
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python 3.8 on environments without zoneinfo support
    ZoneInfo = None


DEFAULT_REPORT_TIMEZONE = "Asia/Shanghai"

SOURCE_LABELS = {
    "arxiv": "arXiv",
    "openreview": "OpenReview",
    "acl_anthology": "ACL Anthology",
    "pmlr": "PMLR",
    "cvf": "CVF Open Access",
    "europe_pmc": "Europe PMC",
}


def format_paper_source(paper: Dict[str, Any]) -> str:
    """Return a human-readable source label without assuming arXiv."""
    source = str(paper.get("source") or "").strip()
    source_label = SOURCE_LABELS.get(source.lower(), source or "Unknown source")
    venue = str(paper.get("venue") or "").strip()
    return f"{source_label} | {venue}" if venue else source_label


def get_report_timezone():
    """Return the configured display timezone for notifications."""
    timezone_name = DEFAULT_REPORT_TIMEZONE
    if ZoneInfo is not None:
        try:
            return ZoneInfo(timezone_name)
        except Exception:
            print(
                f"⚠️ 无法识别 REPORT_TIMEZONE={timezone_name!r}，"
                f"回退到 {DEFAULT_REPORT_TIMEZONE}"
            )
            try:
                return ZoneInfo(DEFAULT_REPORT_TIMEZONE)
            except Exception:
                pass

    # Python 3.8 Windows installations may not include the IANA timezone data.
    # Keep the default deployment timezone correct even in that case.
    return timezone(timedelta(hours=8), name="CST")


def format_report_time(now=None) -> str:
    """Format a notification timestamp in the configured report timezone."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(get_report_timezone()).strftime("%Y-%m-%d %H:%M:%S")


def configure_utf8_stdio():
    """Keep notification logs readable when stdout is redirected on Windows."""
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


configure_utf8_stdio()


class WxWorkNotifier:
    """企业微信机器人推送"""
    
    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or os.getenv("WXWORK_WEBHOOK_URL")
    
    def send_text(self, content: str) -> bool:
        """发送文本消息"""
        if not self.webhook_url:
            return False
        
        try:
            data = {
                "msgtype": "text",
                "text": {
                    "content": content
                }
            }
            response = requests.post(self.webhook_url, json=data, timeout=10)
            result = response.json()
            if result.get("errcode") == 0:
                print("✅ 企业微信推送成功")
                return True
            else:
                print(f"❌ 企业微信推送失败: {result}")
                return False
        except Exception as e:
            print(f"❌ 企业微信推送异常: {e}")
            return False
    
    def send_markdown(self, content: str) -> bool:
        """发送 Markdown 格式消息"""
        if not self.webhook_url:
            return False
        
        try:
            data = {
                "msgtype": "markdown",
                "markdown": {
                    "content": content
                }
            }
            response = requests.post(self.webhook_url, json=data, timeout=10)
            result = response.json()
            if result.get("errcode") == 0:
                print("✅ 企业微信推送成功")
                return True
            else:
                print(f"❌ 企业微信推送失败: {result}")
                return False
        except Exception as e:
            print(f"❌ 企业微信推送异常: {e}")
            return False
    
    def send_paper_summary(self, category: str, papers: List[Dict]) -> bool:
        """推送论文摘要"""
        if not papers:
            return self.send_text(f"📊 {category}\n本次未发现相关论文")
        
        # 构建 Markdown 消息
        lines = [
            f"# 📊 {category} 论文更新",
            f"共发现 {len(papers)} 篇新论文\n"
        ]
        
        for i, paper in enumerate(papers, 1):
            lines.append(f"## {i}. {paper.get('title', '无标题')}")
            
            recommendation = paper.get('recommendation', '值得看')
            emoji = "🔥" if recommendation == "必读" else "📖"
            lines.append(f"**推荐指数**: {emoji} {recommendation}")
            if paper.get('primary_topic'):
                lines.append(
                    f"**论文主题 / Primary Topic**: `{paper['primary_topic']}`"
                )
            
            if paper.get('authors'):
                authors = ", ".join(paper['authors'][:3])  # 只显示前3个作者
                if len(paper['authors']) > 3:
                    authors += " 等"
                lines.append(f"**作者**: {authors}")
            
            if paper.get('url'):
                lines.append(
                    f"**Source / Venue**: "
                    f"[{format_paper_source(paper)}]({paper['url']})"
                )
            elif paper.get('arxiv_id'):
                lines.append(f"**arXiv**: [{paper['arxiv_id']}](https://arxiv.org/abs/{paper['arxiv_id']})")
            
            # 方法论
            if paper.get('methodology'):
                lines.append(f"**方法论**: {paper['methodology'][:100]}...")
            
            # 核心概念
            if paper.get('core_concepts'):
                concepts = " ".join([f"`{c}`" for c in paper['core_concepts'][:5]])
                lines.append(f"**核心概念**: {concepts}")
            
            # 锐评
            if paper.get('sharp_review'):
                lines.append(f"**锐评**: {paper['sharp_review'][:150]}...")
            
            lines.append("")  # 空行
        
        content = "\n".join(lines)
        return self.send_markdown(content)


class FeishuNotifier:
    """飞书机器人推送"""
    
    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or os.getenv("FEISHU_WEBHOOK_URL")
        # The paper bot and daily-report bot share one group in this setup.
        # Keep the dedicated name supported, but reuse the daily chat ID when
        # FEISHU_PAPER_CHAT_ID is not configured.
        self.chat_id = (
            os.getenv("FEISHU_PAPER_CHAT_ID", "").strip()
            or os.getenv("DAILY_REPORT_FEISHU_CHAT_ID", "").strip()
        )
        self.sdk = None
        app_id = os.getenv("FEISHU_APP_ID", "").strip()
        app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
        if app_id and app_secret and self.chat_id:
            try:
                self.sdk = FeishuOpenAPIClient(app_id, app_secret)
            except Exception as exc:
                print(f"⚠️ 飞书官方 SDK 初始化失败，将回退 Webhook: {exc}")
        if not self.webhook_url and not self.sdk:
            print("⚠️ 未配置飞书 Webhook URL")

    def _send_sdk(self, msg_type: str, content: Dict[str, Any]) -> bool:
        if not self.sdk or not self.chat_id:
            return False
        try:
            self.sdk.request(
                "POST",
                "/open-apis/im/v1/messages",
                params={"receive_id_type": "chat_id"},
                json_body={
                    "receive_id": self.chat_id,
                    "msg_type": msg_type,
                    "content": json.dumps(content, ensure_ascii=False),
                },
            )
            print("✅ 飞书 SDK 推送成功")
            return True
        except FeishuSDKError as exc:
            print(f"⚠️ 飞书 SDK 推送失败: {exc}")
            return False
    
    def send_text(self, content: str) -> bool:
        """发送卡片消息；保留原文本接口供通知管理器调用。"""
        return self.send_card("Zotero AI Daily Papers", content)

    def send_card(self, title: str, markdown: str, template: str = "blue") -> bool:
        """Send an interactive Feishu card through SDK or webhook fallback."""
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": str(title)[:100]},
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": str(markdown)[:30000]},
                }
            ],
        }
        if self._send_sdk("interactive", card):
            return True
        if not self.webhook_url:
            return False
        
        try:
            data = {"msg_type": "interactive", "card": card}
            response = requests.post(self.webhook_url, json=data, timeout=10)
            result = response.json()
            if result.get("StatusCode") == 0 or result.get("code") == 0:
                print("✅ 飞书推送成功")
                return True
            else:
                print(f"❌ 飞书推送失败: {result}")
                return False
        except Exception as e:
            print(f"❌ 飞书推送异常: {e}")
            return False
    
    def send_post(self, title: str, content: List[List[Dict]]) -> bool:
        """兼容旧富文本调用，将内容转换为交互式卡片。"""
        parts = []
        for row in content or []:
            for item in row or []:
                tag = item.get("tag")
                text = str(item.get("text", ""))
                if tag == "a" and item.get("href"):
                    parts.append(f"[{text}]({item['href']})")
                else:
                    parts.append(text)
        return self.send_card(title, "".join(parts))

    def send_paper_summary(self, category: str, papers: List[Dict]) -> bool:
        """推送论文摘要"""
        if not papers:
            content = [[{
                "tag": "text",
                "text": f"📊 {category}\n本次未发现相关论文"
            }]]
            return self.send_post(f"{category} 论文更新", content)
        
        # 构建富文本消息
        post_content = [
            [[{
                "tag": "text",
                "text": f"共发现 {len(papers)} 篇新论文\n\n"
            }]]
        ]
        
        for i, paper in enumerate(papers, 1):
            paper_section = [
                [{
                    "tag": "text",
                    "text": f"{i}. {paper.get('title', '无标题')}\n"
                }]
            ]
            
            recommendation = paper.get('recommendation', '值得看')
            emoji = "🔥" if recommendation == "必读" else "📖"
            paper_section.append([{
                "tag": "text",
                "text": f"推荐: {emoji} {recommendation}\n"
            }])
            if paper.get('primary_topic'):
                paper_section.append([{
                    "tag": "text",
                    "text": (
                        "论文主题 / Primary Topic: "
                        f"{paper['primary_topic']}\n"
                    )
                }])
            
            if paper.get('authors'):
                authors = ", ".join(paper['authors'][:3])
                paper_section.append([{
                    "tag": "text",
                    "text": f"作者: {authors}\n"
                }])
            
            if paper.get('url'):
                paper_section.append([{
                    "tag": "a",
                    "text": f"Source / Venue: {format_paper_source(paper)}",
                    "href": paper['url']
                }])
                paper_section.append([{
                    "tag": "text",
                    "text": "\n"
                }])
            elif paper.get('arxiv_id'):
                paper_section.append([{
                    "tag": "a",
                    "text": f"arXiv: {paper['arxiv_id']}",
                    "href": f"https://arxiv.org/abs/{paper['arxiv_id']}"
                }])
                paper_section.append([{
                    "tag": "text",
                    "text": "\n"
                }])
            
            # 方法论
            if paper.get('methodology'):
                paper_section.append([{
                    "tag": "text",
                    "text": f"方法论: {paper['methodology'][:80]}...\n\n"
                }])
            
            post_content.append(paper_section)
        
        return self.send_post(f"{category} 论文更新", post_content)


class NotificationManager:
    """通知管理器"""
    
    def __init__(self):
        self.wxwork = WxWorkNotifier()
        self.feishu = FeishuNotifier()

    def _enabled_platforms(self) -> List[str]:
        platforms = []
        if self.wxwork.webhook_url:
            platforms.append("wxwork")
        if self.feishu.webhook_url or self.feishu.sdk:
            platforms.append("feishu")
        return platforms
    
    def send_text(self, content: str, platforms: Optional[List[str]] = None) -> Dict[str, bool]:
        """发送文本消息到指定平台"""
        platforms = platforms if platforms is not None else self._enabled_platforms()
        results = {}
        
        if "wxwork" in platforms:
            results["wxwork"] = self.wxwork.send_text(content)
        
        if "feishu" in platforms:
            results["feishu"] = self.feishu.send_text(content)
        
        return results
    
    def send_workflow_start(self, is_first_run: bool) -> Dict[str, bool]:
        """发送工作流开始通知"""
        mode = "首次运行（冷启动）" if is_first_run else "增量运行"
        content = f"""
🚀 Zotero AI Daily Papers 开始运行

运行模式: {mode}
开始时间: {self._get_current_time()}
"""
        return self.send_text(content)
    
    def send_workflow_complete(self, stats: Dict, platforms: Optional[List[str]] = None) -> Dict[str, bool]:
        """发送工作流完成通知（简化版）"""
        total_papers = sum(stats.get("categories", {}).values())
        
        content = f"""
✅ Zotero AI Daily Papers 运行完成

处理分类数: {len(stats.get('categories', {}))}
发现新论文: {total_papers} 篇
完成时间: {self._get_current_time()}

分类详情:
"""
        for category, count in stats.get("categories", {}).items():
            content += f"  • {category}: {count} 篇\n"
        
        return self.send_text(content, platforms)
    
    def send_papers_detail(self, stats: Dict, is_first_run: bool, platforms: Optional[List[str]] = None) -> Dict[str, bool]:
        """发送详细论文笔记通知"""
        platforms = platforms or ["feishu"]  # 默认只发飞书，因为支持富文本
        
        # 如果有论文，分批发送
        papers_by_category = stats.get("papers", {})
        if not papers_by_category:
            # 没有新论文，发送简短通知
            return self.send_text(
                f"📊 Zotero AI Daily Papers 运行完成\n\n本次未发现新论文\n时间: {self._get_current_time()}",
                platforms
            )
        
        # 发送头部摘要
        total_papers = stats.get("total_papers", 0)
        mode = "首次运行（冷启动）" if is_first_run else "增量运行"
        
        header_content = f"""📚 **Zotero AI Daily Papers** 运行完成

**运行模式**: {mode}
**发现新论文**: {total_papers} 篇
**完成时间**: {self._get_current_time()}

"""
        results = self.feishu.send_post("📚 论文更新通知", [
            [{"tag": "text", "text": header_content}]
        ])
        
        # 按分类发送论文详情
        for category, papers in papers_by_category.items():
            if not papers:
                continue
            
            for idx, paper in enumerate(papers, 1):
                # 构建单篇论文的富文本
                paper_sections = self._build_paper_section(paper, category, idx, len(papers))
                
                # 发送单篇论文
                result = self.feishu.send_post(
                    f"{category} - {idx}/{len(papers)}",
                    paper_sections
                )
                print(f"📤 已发送论文通知: {paper['title'][:30]}... (状态: {'成功' if result else '失败'})")
                
                # 避免发送过快
                time.sleep(0.5)
        
        return {"feishu": True}
    
    def _build_paper_section(self, paper: Dict, category: str, idx: int, total: int) -> List[List[Dict]]:
        """构建单篇论文的富文本内容"""
        sections = []
        
        # 标题和推荐
        recommendation = paper.get('recommendation', '值得看')
        emoji = "🔥" if recommendation == "必读" else "📖"
        
        sections.append([
            {
                "tag": "text",
                "text": f"{idx}/{total}. {paper.get('title', '无标题')}\n"
            }
        ])
        
        sections.append([
            {
                "tag": "text",
                "text": f"{emoji} 推荐: {recommendation} | 📂 {category}\n"
            }
        ])
        primary_topic = paper.get('primary_topic', '')
        if primary_topic:
            sections.append([
                {
                    "tag": "text",
                    "text": f"🏷️ 论文主题 / Primary Topic: {primary_topic}\n"
                }
            ])

        sections.append([
            {
                "tag": "text",
                "text": f"📚 Source / Venue: {format_paper_source(paper)}\n"
            }
        ])
        
        # 作者
        authors = paper.get('authors', [])
        if authors:
            authors_str = ", ".join(authors[:3])
            if len(authors) > 3:
                authors_str += f" 等 {len(authors)} 人"
            sections.append([
                {
                    "tag": "text",
                    "text": f"👤 作者: {authors_str}\n"
                }
            ])
        
        # 链接区域
        arxiv_id = paper.get('arxiv_id')
        paper_url = paper.get('url') or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "")
        if paper_url:
            link_row = [
                {
                    "tag": "a",
                    "text": f"📄 {format_paper_source(paper)} paper",
                    "href": paper_url
                },
                {
                    "tag": "text",
                    "text": " | "
                },
                {
                    "tag": "a",
                    "text": "📚 Zotero 条目",
                    "href": paper.get('zotero_link', '')
                },
            ]
            feishu_wiki_url = paper.get('feishu_wiki_url', '')
            if feishu_wiki_url:
                link_row.append({"tag": "text", "text": " | "})
                link_row.append({"tag": "a", "text": "📝 飞书知识库", "href": feishu_wiki_url})
            link_row.append({"tag": "text", "text": "\n\n"})
            sections.append(link_row)
        
        # 方法论
        methodology = paper.get('methodology', '')
        if methodology:
            sections.append([
                {
                    "tag": "text",
                    "text": f"🔬 方法论:\n{methodology[:200]}"
                }
            ])
            if len(methodology) > 200:
                sections.append([
                    {
                        "tag": "text",
                        "text": "...\n\n"
                    }
                ])
            else:
                sections[-1].append({
                    "tag": "text",
                    "text": "\n\n"
                })
        
        # 核心概念
        concepts = paper.get('core_concepts', [])
        if concepts:
            concepts_text = " ".join([f"#{c}" for c in concepts[:5]])
            sections.append([
                {
                    "tag": "text",
                    "text": f"🧠 核心概念: {concepts_text}\n\n"
                }
            ])
        
        # 深度对比（仅增量）
        if paper.get('comparison'):
            sections.append([
                {
                    "tag": "text",
                    "text": f"🔄 深度对比:\n{paper['comparison'][:150]}"
                }
            ])
            if len(paper['comparison']) > 150:
                sections[-1].append({
                    "tag": "text",
                    "text": "...\n\n"
                })
            else:
                sections[-1].append({
                    "tag": "text",
                    "text": "\n\n"
                })
        
        # 锐评
        sharp_review = paper.get('sharp_review', '')
        if sharp_review:
            sections.append([
                {
                    "tag": "text",
                    "text": f"💬 锐评:\n{sharp_review[:200]}"
                }
            ])
            if len(sharp_review) > 200:
                sections[-1].append({
                    "tag": "text",
                    "text": "...\n"
                })
        
        return sections

    def send_no_papers_notification(self, is_first_run: bool, categories: Dict[str, Any] = None) -> Dict[str, bool]:
        """发送无新论文通知"""
        mode = "首次运行（冷启动）" if is_first_run else "增量更新"
        category_lines = "\n".join(
            f"- {name}: {info.get('desc', '')}" for name, info in (categories or {}).items()
        )
        content = f"""
📊 Zotero AI Daily Papers 运行报告

运行模式: {mode}
扫描时间: {self._get_current_time()}
结果: ✅ 暂无新论文

本次扫描了以下分类:
{category_lines}

系统运行正常，等待下一篇新论文...
"""
        return self.send_text(content)

    def send_workflow_error(self, error: str) -> Dict[str, bool]:
        """发送工作流错误通知"""
        content = f"""
❌ Zotero AI Daily Papers 运行失败

错误信息: {error}
发生时间: {self._get_current_time()}
"""
        return self.send_text(content)

    def send_structured_error_report(self, errors: List[Dict], failed_keywords: List[tuple] = None, stats: Dict = None) -> Dict[str, bool]:
        """发送结构化错误报告，按类型分类展示"""
        by_type: Dict[str, List[Dict]] = {}
        for err in errors:
            t = err.get("type", "runtime")
            by_type.setdefault(t, []).append(err)

        type_labels = {
            "arxiv_fetch": "arXiv 抓取失败",
            "llm_phase_one": "LLM 阶段一分析失败",
            "llm_phase_two": "LLM 阶段二分析失败",
            "zotero_write": "Zotero 写入失败",
            "knowledge_base_build": "知识库构建失败",
            "feishu_sync": "飞书同步失败",
            "runtime": "其他运行时错误",
        }

        lines = ["❌ Zotero AI Daily Papers 错误报告\n"]

        # arXiv 抓取失败（来自 failed_keywords）
        if failed_keywords:
            lines.append(f"📡 arXiv 抓取失败 ({len(failed_keywords)} 个关键词):")
            for cat, kw in failed_keywords:
                lines.append(f"  [{cat}] {kw}")
            lines.append("")

        # LLM 失败 — 区分全部失败 vs 部分失败
        llm_errors = by_type.get("llm_phase_one", []) + by_type.get("llm_phase_two", [])
        if llm_errors:
            total_attempted = stats.get("total_attempted_analysis", 0) if stats else 0
            if total_attempted > 0 and len(llm_errors) >= total_attempted:
                lines.append(f"🤖 LLM 分析全部失败，共 {len(llm_errors)} 篇论文未能分析")
            else:
                lines.append(f"🤖 LLM 分析失败: {len(llm_errors)} 篇")
            # 按分类统计
            by_cat: Dict[str, int] = {}
            for err in llm_errors:
                cat = err.get("category", "未知")
                by_cat[cat] = by_cat.get(cat, 0) + 1
            for cat, count in by_cat.items():
                lines.append(f"  [{cat}] {count} 篇")
            lines.append("")

        # 其他错误类型
        for err_type in ["zotero_write", "knowledge_base_build", "feishu_sync", "runtime"]:
            errs = by_type.get(err_type, [])
            if errs:
                label = type_labels.get(err_type, err_type)
                lines.append(f"⚠️ {label} ({len(errs)} 次):")
                for err in errs[:5]:
                    lines.append(f"  {err['message'][:100]}")
                if len(errs) > 5:
                    lines.append(f"  ... 还有 {len(errs) - 5} 条")
                lines.append("")

        lines.append(f"发生时间: {self._get_current_time()}")
        return self.send_text("\n".join(lines))

    def _get_current_time(self) -> str:
        """获取当前时间字符串"""
        return format_report_time()


# 全局通知管理器实例
notifier = NotificationManager()
