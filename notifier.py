"""
通知推送模块
支持企业微信和飞书推送
"""
import os
import json
import requests
from typing import Optional, Dict, List


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
        """发送工作流完成通知"""
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
