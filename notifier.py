"""
通知推送模块
支持企业微信和飞书推送
"""
import os
import json
import requests
from typing import Optional, Dict, List, Any


class WxWorkNotifier:
    """企业微信机器人推送"""
    
    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or os.getenv("WXWORK_WEBHOOK_URL")
        if not self.webhook_url:
            print("⚠️ 未配置企业微信 Webhook URL")
    
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
            
            if paper.get('authors'):
                authors = ", ".join(paper['authors'][:3])  # 只显示前3个作者
                if len(paper['authors']) > 3:
                    authors += " 等"
                lines.append(f"**作者**: {authors}")
            
            if paper.get('arxiv_id'):
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
        if not self.webhook_url:
            print("⚠️ 未配置飞书 Webhook URL")
    
    def send_text(self, content: str) -> bool:
        """发送文本消息"""
        if not self.webhook_url:
            return False
        
        try:
            data = {
                "msg_type": "text",
                "content": {
                    "text": content
                }
            }
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
        """发送富文本消息"""
        if not self.webhook_url:
            return False
        
        try:
            data = {
                "msg_type": "post",
                "content": {
                    "post": {
                        "zh_cn": {
                            "title": title,
                            "content": content
                        }
                    }
                }
            }
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
            
            if paper.get('authors'):
                authors = ", ".join(paper['authors'][:3])
                paper_section.append([{
                    "tag": "text",
                    "text": f"作者: {authors}\n"
                }])
            
            if paper.get('arxiv_id'):
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
    
    def send_text(self, content: str, platforms: Optional[List[str]] = None) -> Dict[str, bool]:
        """发送文本消息到指定平台"""
        platforms = platforms or ["wxwork", "feishu"]
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
                from asyncio import sleep
                sleep(0.5)
        
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
        
        # arXiv 链接
        arxiv_id = paper.get('arxiv_id')
        if arxiv_id:
            sections.append([
                {
                    "tag": "a",
                    "text": "📄 arXiv 论文",
                    "href": f"https://arxiv.org/abs/{arxiv_id}"
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
                {
                    "tag": "text",
                    "text": "\n\n"
                }
            ])
        
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
    
    def send_workflow_error(self, error: str) -> Dict[str, bool]:
        """发送工作流错误通知"""
        content = f"""
❌ Zotero AI Daily Papers 运行失败

错误信息: {error}
发生时间: {self._get_current_time()}
"""
        return self.send_text(content)
    
    def _get_current_time(self) -> str:
        """获取当前时间字符串"""
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 全局通知管理器实例
notifier = NotificationManager()
