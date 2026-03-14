# 飞书论文通知功能说明

## 功能概述

本项目现已集成飞书推送功能，会在每次运行后将新加入的论文详细笔记发送到飞书群。

## 通知内容

每篇新论文会收到一条独立的消息，包含以下信息：

### 📋 基本信息
- **标题**：论文完整标题
- **推荐指数**：🔥 必读 / 📖 值得看 / ⏭️ 可跳过
- **分类**：所属的分类（如 UAV_VLN、MARL 等）
- **作者**：主要作者列表（最多显示前3位）

### 🔗 快速链接
- **arXiv 论文**：直达 arXiv 论文页面
- **Zotero 条目**：直达 Zotero 中的条目（包含完整笔记）

### 📖 笔记内容
- **方法论**：核心方法简述
- **核心概念**：论文涉及的关键术语和概念
- **深度对比**：与已读论文的对比（仅增量运行）
- **锐评**：批判性分析和评价

### 📊 汇总信息
- **运行模式**：首次运行（冷启动）或 增量运行
- **发现新论文**：本次发现的论文总数
- **完成时间**：运行完成的时间

## 配置步骤

### 1. 创建飞书机器人（1分钟）

1. 打开你需要接收通知的飞书群
2. 点击群设置 `...` → `群机器人` → `添加机器人`
3. 选择 `自定义机器人`
4. 设置机器人名称（如：论文推送）和描述
5. 点击 `添加`
6. 复制生成的 Webhook URL（格式：`https://open.feishu.cn/open-apis/bot/v2/hook/XXXXXXXX`）

### 2. 配置 GitHub Secrets（2分钟）

1. 打开你的 GitHub 仓库
2. 点击 `Settings` → `Secrets and variables` → `Actions`
3. 点击 `New repository secret`
4. 填写以下信息：
   - **Name**: `FEISHU_WEBHOOK_URL`
   - **Secret**: 粘贴步骤1复制的 Webhook URL
5. 点击 `Add secret`

### 3. 完成配置

配置完成！下次 GitHub Actions 自动运行时会自动推送论文笔记到飞书群。

## 消息示例

### 汇总消息
```
📚 Zotero AI Daily Papers 运行完成

运行模式: 增量运行
发现新论文: 3 篇
完成时间: 2024-01-15 10:30:00
```

### 单篇论文消息
```
UAV_VLN - 1/2
1. Vision-Language Navigation for UAVs: A Survey
🔥 推荐: 必读 | 📂 UAV_VLN
👤 作者: Zhang San, Li Si 等 4 人
📄 arXiv 论文 | 📚 Zotero 条目

🔬 方法论:
提出了一种新的视觉语言导航框架，结合了深度强化学习和...

🧠 核心概念: #视觉导航 #多模态融合 #路径规划

🔄 深度对比:
与我们之前索引的论文相比，本文在复杂环境下的导航性能提升...

💬 锐评:
方法创新性强，但在极端天气条件下的鲁棒性仍有待验证...
```

## 特点

### ✅ 优势
- **详细信息**：包含论文的完整笔记内容
- **快速访问**：提供 arXiv 和 Zotero 双链接
- **分类清晰**：按分类发送，方便阅读
- **视觉友好**：使用富文本格式，易于阅读

### 📌 注意事项
- **消息数量**：每篇新论文一条消息，加上汇总消息
- **消息长度**：单条消息会截断过长的内容（方法论、锐评等）
- **发送间隔**：每条消息间隔 0.5 秒，避免触发频率限制
- **完整内容**：如需查看完整笔记，点击 Zotero 条目链接

## 测试通知

### 本地测试
```bash
# 设置环境变量
export FEISHU_WEBHOOK_URL="你的飞书Webhook URL"

# 运行程序（会触发通知）
python -u main.py
```

### GitHub Actions 手动触发
1. 进入 GitHub 仓库
2. 点击 `Actions` 标签
3. 选择 `Zotero AI Daily Papers`
4. 点击 `Run workflow` → `Run workflow`

## 禁用通知

### 本地运行
```bash
export ENABLE_NOTIFICATION=0
python -u main.py
```

### GitHub Actions
修改 `.github/workflows/daily_paper.yml`：
```yaml
env:
  ENABLE_NOTIFICATION: "0"  # 改为 "0" 禁用通知
```

## 高级配置

### 修改通知内容格式

如需自定义通知内容，编辑 `notifier.py` 中的 `_build_paper_section` 方法：

```python
def _build_paper_section(self, paper: Dict, category: str, idx: int, total: int) -> List[List[Dict]]:
    """构建单篇论文的富文本内容"""
    sections = []
    # 自定义你的内容格式
    return sections
```

### 修改发送间隔

如需调整消息发送间隔，修改 `send_papers_detail` 方法：

```python
# 修改这里的间隔时间（秒）
sleep(0.5)  # 改为你想要的值
```

## 故障排查

### 问题1：收不到通知
**可能原因**：
- Webhook URL 配置错误
- GitHub Secrets 未正确设置
- 飞书机器人被群管理员禁用

**解决方法**：
1. 检查 Webhook URL 是否正确复制
2. 查看 GitHub Actions 日志确认是否有错误
3. 确认飞书机器人在群中是否正常显示

### 问题2：消息内容不完整
**可能原因**：
- 单条消息超过飞书长度限制（4096 字节）

**解决方法**：
- 已自动截断，查看完整内容请点击 Zotero 条目链接
- 或在代码中调整截断长度（修改 `notifier.py` 中的长度限制）

### 问题3：发送失败
**可能原因**：
- 网络连接问题
- 飞书 API 限流

**解决方法**：
- 查看 GitHub Actions 日志中的错误信息
- 稍后手动触发 workflow 重试

## 下一步

- 详细配置说明：[NOTIFICATION_SETUP.md](./NOTIFICATION_SETUP.md)
- 快速配置指南：[QUICKSTART_NOTIFICATION.md](./QUICKSTART_NOTIFICATION.md)

## 问题反馈

如有问题或建议，请在 GitHub Issues 中反馈。
