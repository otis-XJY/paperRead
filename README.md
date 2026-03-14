# Zotero AI Daily Papers

一个智能化的学术论文自动化抓取、分析和归档系统。通过 arXiv API 自动抓取最新论文，使用 LLM 进行相关性分析和深度评估，并自动归档到 Zotero 中。

## ✨ 主要功能

- 🤖 **智能抓取**: 自动从 arXiv 抓取指定领域最新论文
- 🧠 **AI 分析**: 使用 LLM 进行相关性判断和深度分析
- 📚 **自动归档**: 将论文和相关分析笔记自动存入 Zotero
- 📱 **消息推送**: 支持飞书/企业微信推送每日论文报告
- 🔄 **增量更新**: 只抓取新论文，避免重复处理
- 🎯 **多分类支持**: 同时管理多个研究领域的论文

## 🚀 快速开始

### 前置要求

- Python 3.8+
- Zotero 账号
- ModelScope API Key (用于 LLM 分析)
- (可选) 飞书/企业微信 Webhook URL (用于消息推送)

### 安装步骤

1. **克隆仓库**
```bash
git clone https://github.com/your-username/paperRead.git
cd paperRead
```

2. **安装依赖**
```bash
pip install -r requirements.txt
```

### 配置

#### 1. 获取 Zotero API 密钥

1. 登录 [Zotero 官网](https://www.zotero.org/)
2. 进入 [用户设置](https://www.zotero.org/settings/keys)
3. 创建新的 API 密钥，建议权限选择：
   - ✅ 读取权限 (Read access)
   - ✅ 写入权限 (Write access)

#### 2. 配置环境变量

创建 `.env` 文件或直接在环境变量中设置：

```bash
# 必填项
ZOTERO_USER_ID=你的Zotero用户ID  # 在 Zotero 设置页面可找到
ZOTERO_API_KEY=你的API密钥
MODELSCOPE_API_KEY=你的ModelScope_API_Key  # 用于 LLM 分析

# 可选项（用于消息推送）
FEISHU_WEBHOOK_URL=你的飞书机器人Webhook地址
WXWORK_WEBHOOK_URL=你的企业微信机器人Webhook地址

# 可选配置
ENABLE_NOTIFICATION=1  # 启用通知（1:启用, 0:禁用）
DRY_RUN=0  # 演练模式（1:不写入Zotero, 0:正常模式）
DEBUG_PHASE_ONE=1  # 调试阶段一（1:显示详细输出, 0:简洁模式）
```

#### 3. 首次运行 - 构建知识库索引

首次运行前需要从 Zotero 导出现有论文作为知识库：

```bash
python zotero_indexer.py
```

这将生成 `knowledge_base.json` 文件，包含你 Zotero 中已有论文的索引信息。

#### 4. 运行主程序

```bash
python main.py
```

首次运行会：
- 抓取各分类的最新 10 篇论文
- 抓取相关性最高的 10 篇论文
- 使用 LLM 进行深度分析
- 自动创建 Zotero 集合和条目
- 生成结构化的分析笔记

后续运行会：
- 只抓取比上次更新的新论文
- 与知识库对比判断相关性
- 只保存相关的论文

## 📁 项目结构

```
paperRead/
├── main.py                      # 主程序
├── zotero_indexer.py            # Zotero 索引生成器
├── notifier.py                  # 消息推送模块
├── requirements.txt             # Python 依赖
├── state.json                   # 运行状态（自动生成）
├── history.json                 # 论文历史（自动生成）
├── knowledge_base.json          # 知识库索引（首次运行生成）
└── .github/workflows/
    └── daily_paper.yml          # GitHub Actions 配置
```

## ⚙️ 配置说明

### 研究领域配置

在 `main.py` 的 `CONFIG` 中配置你关注的研究领域：

```python
CONFIG = {
    "categories": {
        "分类名称": {
            "keywords": ["关键词1", "关键词2"],
            "desc": "分类描述"
        }
    }
}
```

示例：
```python
"UAV_VLN": {
    "keywords": [
        'ti:"Vision-Language Navigation"',
        '(abs:UAV AND abs:Navigation)'
    ],
    "desc": "无人机视觉语言导航、空间感知及指令执行。"
}
```

### LLM 配置

默认使用 ModelScope 的 Qwen 模型：

```python
"llm_model": "Qwen/Qwen3.5-35B-A3B",
"base_url": "https://api-inference.modelscope.cn/v1/"
```

如需使用 OpenAI，只需设置 `OPENAI_API_KEY` 环境变量即可。

### 速率限制策略

为避免 arXiv API 速率限制，程序内置了延迟机制：
- 首次运行：请求间隔 8-10 秒
- 增量更新：请求间隔 6 秒
- 429 错误：动态等待 7-19 秒

## 🔧 高级功能

### 1. 演练模式 (DRY_RUN)

不写入 Zotero，仅测试抓取逻辑：

```bash
DRY_RUN=1 python main.py
```

### 2. GitHub Actions 自动化

配置 GitHub Actions 实现每日自动运行：

1. Fork 本仓库
2. 在仓库设置中添加 Secrets：
   - `ZOTERO_USER_ID`
   - `ZOTERO_API_KEY`
   - `MODELSCOPE_API_KEY`
   - `FEISHU_WEBHOOK_URL` (可选)
3. 启用 Actions 工作流

### 3. 消息推送

#### 飞书推送

1. 创建飞书机器人
2. 获取 Webhook URL
3. 设置 `FEISHU_WEBHOOK_URL` 环境变量

推送内容包括：
- 工作流开始通知
- 新论文详细笔记（每篇独立推送）
- 无新论文提醒
- 错误通知

#### 企业微信推送

配置 `WXWORK_WEBHOOK_URL` 即可启用。

## 📊 输出示例

### Zotero 中的论文笔记

每篇论文会生成结构化笔记，包含：

- 🆕 入库阶段（首次运行/增量）
- 🔥 推荐指数（必读/值得看/可跳过）
- 📂 分类信息
- 👤 作者列表
- 🕒 arXiv 上传时间
- 🧧 一句话总结
- 📄 完整摘要
- 🧠 核心术语库
- 🔬 方法论简析
- 💬 批判性锐评
- 🔄 深度差量对比（增量运行）

### 飞书推送示例

```
📚 新论文推荐 - UAV_VLN

📖 论文标题: Vision-Language Navigation for UAVs
👤 作者: John Doe, Jane Smith
🔗 arXiv: https://arxiv.org/abs/2403.xxxxx

🔥 推荐指数: 必读

🔬 方法论:
提出了一种基于多模态融合的无人机导航框架...

🧠 核心概念: #多模态融合 #路径规划 #深度学习

💬 锐评:
该论文提出的方法具有创新性，但在复杂环境下的表现...

🔗 Zotero条目: https://www.zotero.org/users/xxx/items/yyy
```

## 🛠️ 故障排除

### 问题 1: HTTP 429 速率限制错误

**原因**: arXiv API 请求过于频繁

**解决方案**:
- 程序已内置延迟机制，请耐心等待
- 如仍频繁出现，可增加 `fetch_arxiv_single` 函数中的 `base_delay` 参数

### 问题 2: LLM 鉴权失败

**原因**: API Key 配置错误

**解决方案**:
- 检查 `MODELSCOPE_API_KEY` 是否正确
- 确认 API Key 有足够额度

### 问题 3: Zotero 写入失败

**原因**: API 权限不足或网络问题

**解决方案**:
- 确认 ZOTERO_API_KEY 有写入权限
- 检查网络连接
- 使用 `DRY_RUN=1` 测试抓取逻辑

### 问题 4: 网络连接超时

**原因**: 网络不稳定或服务器响应慢

**解决方案**:
- 增加 `fetch_arxiv_single` 中的 timeout 参数
- 检查网络连接
- 程序会自动重试，请耐心等待

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

### 开发建议

1. 遵循现有代码风格
2. 添加必要的注释
3. 测试新功能
4. 更新文档

## 📝 许可证

MIT License

## 🔗 相关链接

- [arXiv API 文档](https://export.arxiv.org/api_help/)
- [Zotero API 文档](https://www.zotero.org/dev/doc/)
- [ModelScope](https://modelscope.cn/)
- [飞书机器人文档](https://open.feishu.cn/document/ukTMukTMukTM/ucTM5YjL3ETO24yNxkjN)

## 💡 使用技巧

1. **首次运行**: 建议使用 `DRY_RUN=1` 测试配置
2. **定期更新知识库**: 当 Zotero 中论文较多时，重新运行 `zotero_indexer.py`
3. **调整分类**: 根据研究兴趣调整关键词配置
4. **查看日志**: 关注控制台输出，了解抓取进度和结果

## 📧 联系方式

如有问题或建议，欢迎提交 Issue 或通过邮件联系。

---

⭐ 如果这个项目对你有帮助，请给个 Star！
