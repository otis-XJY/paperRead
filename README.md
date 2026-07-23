# Zotero AI Daily Papers

<div align="center">

![Version](https://img.shields.io/badge/Version-1.4.0-blue.svg)
![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![arXiv](https://img.shields.io/badge/arXiv-API-red.svg)
![Zotero](https://img.shields.io/badge/Zotero-Integration-orange.svg)
![LLM](https://img.shields.io/badge/Powered%20by-LLM-purple.svg)

**🚀 智能化学术论文自动化抓取、分析与归档系统**

[English](./README_EN.md) | 简体中文

[功能特性](#-功能特性) • [快速开始](#-快速开始) • [配置说明](#-配置说明) • [使用指南](#-使用指南) • [贡献指南](#-贡献指南)

</div>

---

## 📌 项目近期修改（2026-07-23）

- **日报输出**：`daily_report.py` 增加 ActivityWatch 专注度分析，结合应用/窗口切换、持续使用时长、键盘输入和鼠标点击等指标生成节奏分析。
- **日报解析稳定性**：支持从 Markdown 代码块中提取 JSON，降低模型返回带代码围栏或附加说明时的解析失败风险。
- **模型调用容错**：LLM 模型池支持失败后自动切换备用模型；模型不可用或请求超时时会继续尝试后续模型。
- **时间处理**：飞书运行通知按 `REPORT_TIMEZONE` 显示（默认 `Asia/Shanghai`）；增量游标 `state.json.last_date` 统一保存为 UTC 论文发布时间，不与任务开始时间混用。
- **论文推送触发方式**：`daily_paper.yml` 移除 GitHub 内置 `schedule`，改为接收外部 `repository_dispatch` 事件 `daily-paper`，并保留手动运行入口。
- **配置安全**：外部 cron 调度所需的 GitHub Token 仅保存在 cron-job.org 的凭据配置中，不写入开源仓库；论文推送对象等群聊相关配置应通过 GitHub Secrets 或环境变量管理。

---

## 🆕 最近更新 (v1.4.0 - 2026/05/15)

| 版本 | 日期 | 主要更新 |
|------|------|----------|
| **v1.4.0** | 2026-05-15 | 🎉 **类目配置外部化** — 新增 categories.json 配置文件，支持在 GitHub 页面上快速配置查询类目和关键词 |
| **v1.3.0** | 2026-05-15 | 多模型LLM自动切换、arXiv抓取失败追踪、请求间隔增至30s |
| **v1.2.0** | 2026-05-15 | 新增OAI-PMH备选方案解决arXiv限速、错误收集器、指数退避重试机制 |
| **v1.1.1** | 2026-05-12 | 优化无新论文通知显示，动态展示扫描分类 |
| **v1.1.0** | 2026-05-08 | 🎉 **飞书Wiki同步功能** — 自动镜像Zotero笔记到飞书知识库 |

> 📋 完整更新日志请查看 [CHANGELOG.md](./CHANGELOG.md)

---

## ✨ 功能特性

### 🤖 智能抓取
- 自动从 arXiv 抓取指定领域最新论文
- 支持多关键词、多分类同时抓取
- 智能去重，避免重复处理
- 完善的速率限制处理和重试机制

### 🧠 AI 驱动分析
- **两阶段分析流程**：
  - **阶段一**：轻量级相关性初筛，快速判断论文价值
  - **阶段二**：深度对比分析，与已有论文进行差量对比
- 支持自定义 LLM 模型（ModelScope Qwen / OpenAI GPT）
- 生成结构化分析笔记：方法论、核心概念、锐评等

### 📚 自动归档
- 自动创建 Zotero 集合和条目
- 生成 HTML 格式的结构化笔记
- 支持标签分类和优先级标记
- 生成可直接访问的 Zotero 链接
- **自动下载推荐论文 PDF 并附加到 Zotero 条目**

### 📱 多平台通知
- 支持飞书机器人推送
- 支持企业微信机器人推送
- 实时通知工作流状态
- 每篇论文独立推送详细分析

### 📝 飞书知识库同步
- 自动镜像 Zotero 目录结构到飞书 Wiki
- 每篇推荐论文创建独立飞书文档
- 结构化笔记同步：推荐指数、方法论、核心术语、锐评等
- 支持节点缓存，避免重复创建

### 🔄 增量更新
- 只抓取新论文，节省时间和资源
- 智能状态管理，避免重复处理
- 支持冷启动和增量运行两种模式

### 🎯 高度可配置
- 灵活的研究领域配置
- 可自定义 LLM 模型
- 支持演练模式（DRY_RUN）
- 丰富的调试选项

---

## 📸 效果预览

### Zotero 中的结构化笔记

每篇论文都会生成包含以下信息的结构化笔记：

- 🆕 入库阶段标识
- 🔥 推荐指数（必读/值得看/可跳过）
- 📂 分类信息
- 👤 作者列表
- 🕒 arXiv 上传时间
- 🧧 一句话总结
- 📄 完整摘要
- 🧠 核心术语库
- 🔬 方法论简析
- 💬 批判性锐评
- 🔄 深度差量对比（增量运行时）

### 飞书推送示例

```
📚 新论文推荐 - UAV_VLN

1/1. Vision-Language Navigation for UAVs
🔥 推荐: 必读 | 📂 UAV_VLN

👤 作者: John Doe, Jane Smith

📄 arXiv 论文 | 📚 Zotero 条目

🔬 方法论:
提出了一种基于多模态融合的无人机导航框架，结合视觉感知和语言理解...

🧠 核心概念: #多模态融合 #路径规划 #深度学习

🔄 深度对比:
相比你之前阅读的《VLM for Navigation》，本论文增加了高度信息处理...

💬 锐评:
该论文提出的方法具有创新性，但在复杂环境下的表现仍需验证...
```

---

## 🚀 快速开始

### 前置要求

- **Python 3.8+**
- **Zotero 账号**
- **LLM API Key**（ModelScope 或 OpenAI）
- （可选）**飞书/企业微信 Webhook URL**

### 1️⃣ 安装

克隆仓库并安装依赖：

```bash
git clone https://github.com/otis-XJY/paperRead.git
cd paperRead
pip install -r requirements.txt
```

### 2️⃣ 配置

创建 `.env` 文件（或在环境变量中设置）：

```bash
# 必填项
ZOTERO_USER_ID=你的Zotero用户ID  # 在 Zotero 设置页面可找到
ZOTERO_API_KEY=你的API密钥        # https://www.zotero.org/settings/keys
MODELSCOPE_API_KEY=你的ModelScope_API_Key  # https://modelscope.cn/

# 可选项（用于消息推送）
FEISHU_WEBHOOK_URL=你的飞书机器人Webhook地址
WXWORK_WEBHOOK_URL=你的企业微信机器人Webhook地址

# 可选项（飞书知识库同步）
FEISHU_APP_ID=你的飞书应用ID
FEISHU_APP_SECRET=你的飞书应用密钥
FEISHU_WIKI_ROOT_NODE_TOKEN=目标Wiki节点Token

# 可选配置
ENABLE_NOTIFICATION=1  # 启用通知（1:启用, 0:禁用）
DRY_RUN=0  # 演练模式（1:不写入Zotero, 0:正常模式）
DEBUG_PHASE_ONE=1  # 调试阶段一（1:显示详细输出, 0:简洁模式）
```

### 3️⃣ 获取 Zotero API 密钥

1. 登录 [Zotero 官网](https://www.zotero.org/)
2. 进入 [用户设置](https://www.zotero.org/settings/keys)
3. 创建新的 API 密钥，权限建议选择：
   - ✅ 读取权限 (Read access)
   - ✅ 写入权限 (Write access)
   - ✅ 允许访问笔记 (Allow notes access)

### 4️⃣ 首次构建知识库

如果你的 Zotero 中已经有历史论文，先构建知识库索引：

```bash
python zotero_indexer.py
```

这一步会生成 `knowledge_base.json`，并在 Zotero 里补齐 `DailyPapers` 根集合及其分类子集合。之后 `main.py` 才能基于已有笔记做阶段一/阶段二对比。

### 5️⃣ 运行主程序

```bash
python main.py
```

**首次运行会：**
- 抓取各分类的最新 10 篇论文 + 相关性最高的 10 篇论文
- 使用 LLM 进行深度分析
- 自动创建 Zotero 集合和条目
- 生成结构化的分析笔记

**如果新增了分类，会发生什么：**
- 先更新 `categories.json`
- 再运行 `python zotero_indexer.py`，让 Zotero 的分类树和 `knowledge_base.json` 同步新增分类
- 之后运行 `python main.py`，新分类会被视为该分类的首次运行，走冷启动逻辑并写入 `state.json` 的 `initialized_categories`
 - 如果启用了飞书知识库同步（已配置 `FEISHU_APP_ID/FEISHU_APP_SECRET/FEISHU_WIKI_ROOT_NODE_TOKEN`），
   `main.py` 会在运行开始时为所有 `categories` 预创建飞书知识库目录（DailyPapers → 分类），并在首次运行时把该分类的所有首批文章同步到对应子目录。

**后续运行会：**
- 只抓取比上次更新的新论文
- 与知识库对比判断相关性
- 只保存相关的论文

---

## ⚙️ 配置说明

### 研究领域配置

研究领域配置已外部化到 `categories.json` 文件中，您可以直接在 GitHub 页面上编辑该文件，无需修改 Python 代码。

**配置文件结构：**

```json
{
  "分类名称": {
    "keywords": ["关键词1", "关键词2"],
    "arxiv_categories": ["cs:cs:RO", "cs:cs:AI"],
    "desc": "分类描述"
  }
}
```

**示例配置：**

```json
"UAV_VLN": {
    "keywords": [
        "ti:\"Vision-Language Navigation\"",
        "(abs:UAV AND abs:Navigation)"
    ],
    "arxiv_categories": ["cs:cs:RO", "cs:cs:CV", "cs:cs:AI"],
    "desc": "无人机视觉语言导航、空间感知及指令执行。"
}
```

**在 GitHub 页面上添加新类目：**

1. 打开 `categories.json` 文件
2. 点击编辑按钮（铅笔图标）
3. 添加新的类目配置
4. 提交更改

> 📝 系统会自动检测新添加的类目，并在下次运行时自动补充知识库数据。

**arXiv 搜索语法：**
- `ti:"关键词"` - 搜索标题
- `abs:关键词` - 搜索摘要
- `cat:cs.MA` - 限定分类
- `AND` / `OR` - 逻辑运算符
- 更多语法参见 [arXiv API 文档](https://export.arxiv.org/api_help/)

### LLM 配置

#### 使用 ModelScope（推荐）

```python
CONFIG = {
    "llm_model": "Qwen/Qwen3.5-35B-A3B",
    "base_url": "https://api-inference.modelscope.cn/v1/",
    # 多模型备选：遇到 429 限速时自动切换
    "fallback_models": [
        "Qwen/Qwen3.5-35B-A3B",
        "Qwen/Qwen2.5-72B-Instruct",
        "Qwen/Qwen2.5-32B-Instruct",
        "deepseek-ai/DeepSeek-V3",
    ],
}
```

环境变量：`MODELSCOPE_API_KEY`（ModelScope Token，`ms-` 开头）

> 多模型切换策略：LLM 遇到 429 限速时，第一轮逐个模型尝试（立即切换），全部失败后等待 60s 进入第二轮。

#### 使用 OpenAI

```python
CONFIG = {
    "llm_model": "gpt-4",
    "base_url": "https://api.openai.com/v1/"
}
```

环境变量：`OPENAI_API_KEY`

### 速率限制策略

为避免 arXiv API 速率限制，程序内置了智能延迟机制：

- **请求间隔**：30 秒（增量更新）/ 30-45 秒（首次运行）
- **429 限速**：固定等待 60 秒，最多重试 3 次
- **网络错误**：指数退避重试
- **抓取失败**：区分"真无结果"和"限速失败"，失败时发送告警通知

---

## 🔧 高级功能

### 1. 演练模式 (DRY_RUN)

不写入 Zotero，仅测试抓取逻辑：

```bash
DRY_RUN=1 python main.py
```

适用场景：
- 首次配置测试
- 调试关键词效果
- 估算 LLM Token 消耗

### 2. 调试模式

启用详细的阶段一输出：

```bash
DEBUG_PHASE_ONE=1 python main.py
```

### 3. GitHub Actions 自动化

配置 GitHub Actions，并由外部 cron 服务触发每日运行：

1. **Fork 本仓库**
2. **在仓库设置中添加 Secrets**：
   - `ZOTERO_USER_ID`
   - `ZOTERO_API_KEY`
   - `MODELSCOPE_API_KEY`
   - `FEISHU_WEBHOOK_URL` (可选)
   - `FEISHU_APP_ID` (可选，飞书知识库)
   - `FEISHU_APP_SECRET` (可选，飞书知识库)
   - `FEISHU_WIKI_ROOT_NODE_TOKEN` (可选，飞书知识库)
3. **启用 Actions 工作流**

详见 [`.github/workflows/daily_paper.yml`](./.github/workflows/daily_paper.yml)

工作流不再使用 GitHub 内置 `schedule`。外部 cron 服务应调用 GitHub `repository_dispatch` 接口，事件类型为 `daily-paper`：

```bash
curl -L -X POST https://api.github.com/repos/OWNER/REPO/dispatches \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -d '{"event_type":"daily-paper"}'
```

其中 `GITHUB_TOKEN` 只应保存在外部 cron 服务的凭据配置中，不要写入仓库。也可以通过 GitHub 页面或 API 使用 `workflow_dispatch` 手动运行。

#### 使用 cron-job.org 配置外部触发

在 [cron-job.org 控制台](https://console.cron-job.org/jobs) 新建一个 Job，按以下字段填写：

1. **Title**：例如 `paperRead-daily-paper`。
2. **URL**：`https://api.github.com/repos/OWNER/REPO/dispatches`，将 `OWNER/REPO` 替换为实际仓库路径，例如 `otis-XJY/paperRead`。
3. **Schedule**：选择每天运行，并设置所需的时区和时间。该时间由 cron-job.org 控制，不再受 GitHub Actions 的 UTC 定时规则影响。
4. **Request method**：选择 `POST`。
5. **Request body**：填写以下 JSON，不要添加 Markdown 代码围栏：

   ```json
   {"event_type":"daily-paper"}
   ```

6. **Headers / Custom headers**：添加以下三个请求头：

   | Name | Value |
   |------|-------|
   | `Accept` | `application/vnd.github+json` |
   | `Authorization` | `Bearer <你的 GitHub Token>` |
   | `X-GitHub-Api-Version` | `2022-11-28` |

   Token 建议使用仅授权该仓库、具备 `Contents: write` 权限的 Fine-grained Personal Access Token。不要把 Token 放进 URL、提交到仓库或截图公开。

7. 保存后点击 **Run now / Execute** 测试。GitHub API 成功时通常返回 HTTP `204 No Content`；随后应在仓库的 **Actions** 页面看到 `Zotero AI Daily Papers` 工作流被触发。
8. 若测试成功，再启用 Job，并在 cron-job.org 的执行历史中确认后续请求状态。GitHub Actions 仍可能运行最长 120 分钟，cron-job.org 只负责发起触发请求。

如果控制台提供“保存响应内容”的选项，建议仅在排查问题时临时开启，避免在第三方服务中长期保存 GitHub API 响应。若出现 `401/403`，优先检查 Token 是否过期、仓库授权范围是否正确；若返回 `204` 但没有工作流，检查事件类型是否严格为 `daily-paper` 以及 workflow 文件是否已推送到默认分支。

> 工作流的增量逻辑依赖 `state.json` 的 `last_date` 和 `initialized_categories`，以及 `history.json` 的已处理 arXiv ID。只要这些文件成功提交回仓库，下一次 Actions 就会按增量方式继续跑。

### 4. 消息推送

#### 飞书推送

1. 创建飞书机器人
2. 获取 Webhook URL
3. 设置 `FEISHU_WEBHOOK_URL` 环境变量

#### 企业微信推送

配置 `WXWORK_WEBHOOK_URL` 即可启用。

---

## 📊 使用指南

### 常见使用场景

#### 场景 1：首次使用

```bash
# 1. 构建知识库（如果有 Zotero 中的论文）
python zotero_indexer.py

# 2. 测试配置
DRY_RUN=1 python main.py

# 3. 正式运行
python main.py
```

#### 场景 2：日常更新

```bash
# 每天运行一次，抓取新论文
python main.py
```

#### 场景 3：添加新研究领域

1. 编辑 `main.py` 的 `CONFIG`
2. 重新运行 `zotero_indexer.py`
3. 运行 `main.py`

#### 场景 4：定期更新知识库

当 Zotero 中论文较多时，重新运行：

```bash
python zotero_indexer.py
```

### 输出文件说明

| 文件 | 说明 | 是否自动生成 |
|------|------|-------------|
| `knowledge_base.json` | Zot 论文知识库索引 | 是（由 zotero_indexer.py 生成） |
| `state.json` | 运行状态记录 | 是 |
| `history.json` | 已处理论文历史 | 是 |

---

## 🛠️ 故障排除

### 问题 1：HTTP 429 速率限制错误

**原因**：arXiv API 请求过于频繁

**解决方案**：
- 程序已内置延迟机制，请耐心等待
- 如仍频繁出现，可增加 `fetch_arxiv_single` 函数中的 `base_delay` 参数

### 问题 2：LLM 鉴权失败

**原因**：API Key 配置错误

**解决方案**：
- 检查 `MODELSCOPE_API_KEY` 或 `OPENAI_API_KEY` 是否正确
- 确认 API Key 有足够额度
- 检查网络连接是否正常

### 问题 3：Zotero 写入失败

**原因**：API 权限不足或网络问题

**解决方案**：
- 确认 ZOTERO_API_KEY 有写入权限
- 检查网络连接
- 使用 `DRY_RUN=1` 测试抓取逻辑

### 问题 4：找不到 knowledge_base.json

**原因**：首次运行前未构建知识库

**解决方案**：
```bash
python zotero_indexer.py
```

`main.py` 也会在缺少知识库时尝试自动重建，但对公开仓库和 GitHub Actions 来说，仍然建议显式先跑一次 `zotero_indexer.py`，这样更可控。

### 问题 5：相关性判断不准确

**原因**：知识库中的论文太少

**解决方案**：
- 在 Zotero 中手动添加更多相关论文
- 重新运行 `zotero_indexer.py` 更新知识库
- 调整 LLM 模型参数

---

## 🤝 贡献指南

欢迎贡献代码、报告问题或提出建议！

### 如何贡献

1. **Fork 本仓库**
2. **创建特性分支**：`git checkout -b feature/AmazingFeature`
3. **提交更改**：`git commit -m 'Add some AmazingFeature'`
4. **推送分支**：`git push origin feature/AmazingFeature`
5. **提交 Pull Request**

### 开发规范

- 遵循现有代码风格
- 添加必要的注释和文档
- 确保新功能有相应的测试
- 更新相关文档

### 报告问题

提交 Issue 时请包含：
- Python 版本
- 错误信息和堆栈跟踪
- 复现步骤
- 相关配置信息

---

## 🗺️ 项目结构

```
paperRead/
├── main.py                      # 主程序
├── zotero_indexer.py            # Zotero 索引生成器
├── notifier.py                  # 消息推送模块
├── feishu_wiki.py               # 飞书知识库镜像客户端
├── requirements.txt             # Python 依赖
├── README.md                    # 项目说明（中文）
├── README_EN.md                 # 项目说明（英文）
├── LICENSE                      # MIT 许可证
├── .env.example                 # 环境变量模板
├── .github/
│   └── workflows/
│       └── daily_paper.yml      # GitHub Actions 日常抓取与分析
├── state.json                   # 运行状态（自动生成）
├── history.json                 # 论文历史（自动生成）
└── knowledge_base.json          # 知识库索引（首次运行生成）
```

---

## 🔗 相关资源

- [arXiv API 文档](https://export.arxiv.org/api_help/)
- [Zotero API 文档](https://www.zotero.org/dev/doc/)
- [ModelScope](https://modelscope.cn/)
- [PyZotero](https://github.com/urschrei/pyzotero)
- [飞书开放平台](https://open.feishu.cn/)
- [企业微信机器人](https://developer.work.weixin.qq.com/document/path/91770)

---

## 💡 使用技巧

1. **首次运行**：建议使用 `DRY_RUN=1` 测试配置
2. **定期更新知识库**：当 Zotero 中论文较多时，重新运行 `zotero_indexer.py`
3. **调整分类**：根据研究兴趣调整关键词配置
4. **查看日志**：关注控制台输出，了解抓取进度和结果
5. **优化 Token 消耗**：适当调整知识库大小，平衡分析质量和成本

---

## 📄 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](./LICENSE) 文件

---

## 📋 更新日志

### v2.0.0 (2026-05-08)
- **新功能**：自动下载推荐论文 PDF 并附加到 Zotero 条目
- **新功能**：飞书知识库镜像 — 自动同步论文笔记到飞书 Wiki
  - 镜像 Zotero 目录结构（DailyPapers → 各分类）
  - 每篇推荐论文创建独立飞书文档
  - 支持节点缓存，避免重复创建
- **改进**：新增 `feishu_wiki.py` 飞书 Open API 客户端
- **改进**：GitHub Actions 支持飞书知识库凭证配置

### v1.0.0
- 初始版本
- arXiv 论文自动抓取与两阶段 LLM 分析
- Zotero 自动归档与结构化笔记生成
- 飞书/企业微信消息推送
- GitHub Actions 自动化工作流

---

## ⭐ 致谢

感谢以下开源项目：

- [PyZotero](https://github.com/urschrei/pyzotero) - Python Zotero API 封装
- [feedparser](https://github.com/kurtmckee/feedparser) - RSS/Atom 解析器
- [aiohttp](https://github.com/aio-libs/aiohttp) - 异步 HTTP 客户端

---

## 📧 联系方式

- 提交 [Issue](../../issues)

---

<div align="center">

**如果这个项目对你有帮助，请给个 Star！⭐**

Made with ❤️ by researchers, for researchers

</div>

## Star History

<a href="https://www.star-history.com/?repos=otis-XJY%2FpaperRead&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=otis-XJY/paperRead&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=otis-XJY/paperRead&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=otis-XJY/paperRead&type=date&legend=top-left" />
 </picture>
</a>
