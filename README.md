# Zotero AI Daily Papers

<div align="center">

![Version](https://img.shields.io/badge/Version-1.2.0-blue.svg)
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

## 🆕 最近更新 (v1.3.0 - 2026/05/15)

| 版本 | 日期 | 主要更新 |
|------|------|----------|
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

### 4️⃣ 构建知识库索引

首次运行前需要从 Zotero 导出现有论文作为知识库：

```bash
python zotero_indexer.py
```

这将生成 `knowledge_base.json` 文件，包含你 Zotero 中已有论文的索引信息。

### 5️⃣ 运行主程序

```bash
python main.py
```

**首次运行会：**
- 抓取各分类的最新 10 篇论文 + 相关性最高的 10 篇论文
- 使用 LLM 进行深度分析
- 自动创建 Zotero 集合和条目
- 生成结构化的分析笔记

**后续运行会：**
- 只抓取比上次更新的新论文
- 与知识库对比判断相关性
- 只保存相关的论文

---

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
    },
    "llm_model": "Qwen/Qwen3.5-35B-A3B",
    "base_url": "https://api-inference.modelscope.cn/v1/"
}
```

**示例配置：**

```python
"UAV_VLN": {
    "keywords": [
        'ti:"Vision-Language Navigation"',
        '(abs:UAV AND abs:Navigation)'
    ],
    "desc": "无人机视觉语言导航、空间感知及指令执行。"
}
```

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

配置 GitHub Actions 实现每日自动运行：

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

详见 [`.github/workflows/daily.yml`](./.github/workflows/daily.yml)

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
│       ├── daily.yml            # GitHub Actions 日常抓取
│       └── daily_paper.yml      # GitHub Actions 论文分析
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
