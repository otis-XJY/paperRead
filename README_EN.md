# Zotero AI Daily Papers

<div align="center">

![Version](https://img.shields.io/badge/Version-1.4.0-blue.svg)
![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![arXiv](https://img.shields.io/badge/arXiv-API-red.svg)
![Zotero](https://img.shields.io/badge/Zotero-Integration-orange.svg)
![LLM](https://img.shields.io/badge/Powered%20by-LLM-purple.svg)

**🚀 AI-Powered Automated Academic Paper Fetching, Analysis, and Archiving System**

简体中文 | [English](./README_EN.md)

[Features](#-features) • [Quick Start](#-quick-start) • [Configuration](#-configuration) • [Usage Guide](#-usage-guide) • [Contributing](#-contributing)

</div>

---

## 🆕 Recent Updates (v1.4.0 - 2026/05/15)

| Version | Date | Highlights |
|---------|------|------------|
| **v1.4.0** | 2026-05-15 | 🎉 **Externalized Category Configuration** — New categories.json config file for easy category and keyword management via GitHub |
| **v1.3.0** | 2026-05-15 | Multi-model LLM failover, arXiv failure tracking, 30s request intervals |
| **v1.2.0** | 2026-05-15 | OAI-PMH fallback for arXiv rate limiting, error collector, exponential backoff retry |
| **v1.1.1** | 2026-05-12 | Optimized no-paper notification with dynamic category display |
| **v1.1.0** | 2026-05-08 | 🎉 **Feishu Wiki Sync** — Mirror Zotero notes to Feishu knowledge base |

> 📋 Full changelog: [CHANGELOG.md](./CHANGELOG.md)

---

## ✨ Features

### 🤖 Intelligent Fetching
- Automatically fetch latest papers from arXiv in specific research areas
- Support multiple keywords and categories simultaneously
- Smart deduplication to avoid duplicate processing
- Comprehensive rate limiting and retry mechanisms

### 🧠 AI-Driven Analysis
- **Two-stage analysis pipeline**:
  - **Stage 1**: Lightweight relevance filtering for quick paper value assessment
  - **Stage 2**: Deep comparative analysis with existing papers
- Support custom LLM models (ModelScope Qwen / OpenAI GPT)
- Generate structured analysis notes: methodology, core concepts, critical reviews

### 📚 Automatic Archiving
- Automatically create Zotero collections and items
- Generate HTML-formatted structured notes
- Support tag classification and priority marking
- Generate accessible Zotero links
- **Auto-download recommended paper PDFs and attach to Zotero items**

### 📱 Multi-Platform Notifications
- Support Feishu bot notifications
- Support WeChat Work bot notifications
- Real-time workflow status updates
- Individual detailed analysis for each paper

### 📝 Feishu Wiki Sync
- Automatically mirror Zotero collection structure to Feishu Wiki
- Create a dedicated Feishu document for each recommended paper
- Structured note sync: recommendation, methodology, core concepts, critical review
- Node caching to avoid duplicate creation

### 🔄 Incremental Updates
- Only fetch new papers, saving time and resources
- Smart state management to avoid duplicate processing
- Support both cold-start and incremental run modes

### 🎯 Highly Configurable
- Flexible research area configuration
- Customizable LLM models
- Support dry-run mode (DRY_RUN)
- Rich debugging options

---

## 📸 Preview

### Structured Notes in Zotero

Each paper generates structured notes including:

- 🆕 Ingestion stage indicator
- 🔥 Recommendation score (Must Read / Worth Reading / Skip)
- 📂 Category information
- 👤 Author list
- 🕒 arXiv upload time
- 🧧 One-line summary
- 📄 Complete abstract
- 🧠 Core terminology
- 🔬 Methodology overview
- 💬 Critical review
- 🔄 Deep comparative analysis (incremental runs)

### Feishu Notification Example

```
📚 New Paper Recommendation - UAV_VLN

1/1. Vision-Language Navigation for UAVs
🔥 Recommendation: Must Read | 📂 UAV_VLN

👤 Authors: John Doe, Jane Smith

📄 arXiv Paper | 📚 Zotero Entry

🔬 Methodology:
Proposed a multi-modal fusion-based UAV navigation framework combining visual perception and language understanding...

🧠 Core Concepts: #MultiModalFusion #PathPlanning #DeepLearning

🔄 Comparative Analysis:
Compared to your previously read "VLM for Navigation", this paper adds altitude information processing...

💬 Critical Review:
The proposed method in this paper is innovative, but its performance in complex environments still needs verification...
```

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.8+**
- **Zotero Account**
- **LLM API Key** (ModelScope or OpenAI)
- (Optional) **Feishu/WeChat Work Webhook URL**

### 1️⃣ Installation

Clone the repository and install dependencies:

```bash
git clone https://github.com/otis-XJY/paperRead.git
cd paperRead
pip install -r requirements.txt
```

### 2️⃣ Configuration

Create a `.env` file (or set in environment variables):

```bash
# Required
ZOTERO_USER_ID=your_zotero_user_id  # Found in Zotero settings
ZOTERO_API_KEY=your_api_key          # https://www.zotero.org/settings/keys
MODELSCOPE_API_KEY=your_modelscope_api_key  # https://modelscope.cn/

# Optional (for notifications)
FEISHU_WEBHOOK_URL=your_feishu_bot_webhook_url
WXWORK_WEBHOOK_URL=your_wechat_work_bot_webhook_url

# Optional (Feishu Wiki sync)
FEISHU_APP_ID=your_feishu_app_id
FEISHU_APP_SECRET=your_feishu_app_secret
FEISHU_WIKI_ROOT_NODE_TOKEN=target_wiki_node_token

# Optional configuration
ENABLE_NOTIFICATION=1  # Enable notifications (1: enable, 0: disable)
DRY_RUN=0  # Dry run mode (1: don't write to Zotero, 0: normal mode)
DEBUG_PHASE_ONE=1  # Debug stage one (1: verbose output, 0: concise mode)
```

### 3️⃣ Get Zotero API Key

1. Login to [Zotero](https://www.zotero.org/)
2. Go to [User Settings](https://www.zotero.org/settings/keys)
3. Create a new API key with recommended permissions:
   - ✅ Read access
   - ✅ Write access
   - ✅ Allow notes access

### 4️⃣ Build Knowledge Base Index

For the first run, export existing papers from Zotero as a knowledge base:

```bash
python zotero_indexer.py
```

This generates `knowledge_base.json` containing index information of your existing Zotero papers.

### 5️⃣ Run Main Program

```bash
python main.py
```

**First run will:**
- Fetch latest 10 papers + top 10 most relevant papers for each category
- Use LLM for deep analysis
- Automatically create Zotero collections and items
- Generate structured analysis notes

**Subsequent runs will:**
- Only fetch papers newer than last update
- Compare with knowledge base for relevance
- Only save relevant papers

---

## ⚙️ Configuration

### Research Area Configuration

Research area configuration is externalized to the `categories.json` file. You can edit this file directly on GitHub without modifying Python code.

**Configuration File Structure:**

```json
{
  "CategoryName": {
    "keywords": ["keyword1", "keyword2"],
    "arxiv_categories": ["cs:cs:RO", "cs:cs:AI"],
    "desc": "Category description"
  }
}
```

**Example Configuration:**

```json
"UAV_VLN": {
    "keywords": [
        "ti:\"Vision-Language Navigation\"",
        "(abs:UAV AND abs:Navigation)"
    ],
    "arxiv_categories": ["cs:cs:RO", "cs:cs:CV", "cs:cs:AI"],
    "desc": "UAV vision-language navigation, spatial perception, and instruction execution."
}
```

**Adding New Categories on GitHub:**

1. Open the `categories.json` file
2. Click the edit button (pencil icon)
3. Add new category configuration
4. Commit changes

> 📝 The system automatically detects newly added categories and supplements knowledge base data on the next run.

**arXiv Search Syntax:**
- `ti:"keyword"` - Search in title
- `abs:keyword` - Search in abstract
- `cat:cs.MA` - Limit by category
- `AND` / `OR` - Logical operators
- See [arXiv API Documentation](https://export.arxiv.org/api_help/) for more

### LLM Configuration

#### Using ModelScope (Recommended)

```python
CONFIG = {
    "llm_model": "Qwen/Qwen3.5-35B-A3B",
    "base_url": "https://api-inference.modelscope.cn/v1/",
    # Multi-model fallback: auto-switch on 429 rate limit
    "fallback_models": [
        "Qwen/Qwen3.5-35B-A3B",
        "Qwen/Qwen2.5-72B-Instruct",
        "Qwen/Qwen2.5-32B-Instruct",
        "deepseek-ai/DeepSeek-V3",
    ],
}
```

Environment variable: `MODELSCOPE_API_KEY` (ModelScope Token, starts with `ms-`)

> Multi-model strategy: on 429 rate limit, round 1 cycles through all models immediately. If all fail, waits 60s then retries round 2.

#### Using OpenAI

```python
CONFIG = {
    "llm_model": "gpt-4",
    "base_url": "https://api.openai.com/v1/"
}
```

Environment variable: `OPENAI_API_KEY`

### Rate Limiting Strategy

To avoid arXiv API rate limits, the program includes intelligent delay mechanisms:

- **Request interval**: 30 seconds (incremental) / 30-45 seconds (first run)
- **429 errors**: Fixed 60s wait, max 3 retries
- **Network errors**: Exponential backoff retry
- **Fetch failures**: Distinguished from empty results; error notification sent on failure

---

## 🔧 Advanced Features

### 1. Dry Run Mode (DRY_RUN)

Don't write to Zotero, only test fetching logic:

```bash
DRY_RUN=1 python main.py
```

Use cases:
- First-time configuration testing
- Debug keyword effectiveness
- Estimate LLM token consumption

### 2. Debug Mode

Enable detailed stage one output:

```bash
DEBUG_PHASE_ONE=1 python main.py
```

### 3. GitHub Actions Automation

Configure GitHub Actions for daily automatic runs:

1. **Fork this repository**
2. **Add Secrets in repository settings**:
   - `ZOTERO_USER_ID`
   - `ZOTERO_API_KEY`
   - `MODELSCOPE_API_KEY`
   - `FEISHU_WEBHOOK_URL` (optional)
   - `FEISHU_APP_ID` (optional, for Feishu Wiki)
   - `FEISHU_APP_SECRET` (optional, for Feishu Wiki)
   - `FEISHU_WIKI_ROOT_NODE_TOKEN` (optional, for Feishu Wiki)
3. **Enable Actions workflow**

See [`.github/workflows/daily.yml`](./.github/workflows/daily.yml) for details

### 4. Message Notifications

#### Feishu Notifications

1. Create a Feishu bot
2. Get Webhook URL
3. Set `FEISHU_WEBHOOK_URL` environment variable

#### WeChat Work Notifications

Configure `WXWORK_WEBHOOK_URL` to enable.

---

## 📊 Usage Guide

### Common Use Cases

#### Scenario 1: First-Time Use

```bash
# 1. Build knowledge base (if you have papers in Zotero)
python zotero_indexer.py

# 2. Test configuration
DRY_RUN=1 python main.py

# 3. Run normally
python main.py
```

#### Scenario 2: Daily Updates

```bash
# Run once a day to fetch new papers
python main.py
```

#### Scenario 3: Adding New Research Areas

1. Edit `CONFIG` in `main.py`
2. Re-run `zotero_indexer.py`
3. Run `main.py`

#### Scenario 4: Periodic Knowledge Base Updates

When you have many papers in Zotero, re-run:

```bash
python zotero_indexer.py
```

### Output File Description

| File | Description | Auto-generated |
|------|-------------|----------------|
| `knowledge_base.json` | Zotero paper knowledge base index | Yes (by zotero_indexer.py) |
| `state.json` | Run state record | Yes |
| `history.json` | Processed paper history | Yes |

---

## 🛠️ Troubleshooting

### Issue 1: HTTP 429 Rate Limit Error

**Cause**: Too frequent arXiv API requests

**Solution**:
- The program includes built-in delay mechanisms, please wait patiently
- If it still occurs frequently, increase the `base_delay` parameter in `fetch_arxiv_single`

### Issue 2: LLM Authentication Failed

**Cause**: API Key configuration error

**Solution**:
- Check if `MODELSCOPE_API_KEY` or `OPENAI_API_KEY` is correct
- Confirm the API Key has sufficient quota
- Check network connection

### Issue 3: Zotero Write Failed

**Cause**: Insufficient API permissions or network issues

**Solution**:
- Confirm ZOTERO_API_KEY has write permissions
- Check network connection
- Use `DRY_RUN=1` to test fetching logic

### Issue 4: knowledge_base.json Not Found

**Cause**: Knowledge base not built before first run

**Solution**:
```bash
python zotero_indexer.py
```

### Issue 5: Relevance Judgment Inaccurate

**Cause**: Too few papers in knowledge base

**Solution**:
- Manually add more relevant papers to Zotero
- Re-run `zotero_indexer.py` to update knowledge base
- Adjust LLM model parameters

---

## 🤝 Contributing

We welcome code contributions, bug reports, and suggestions!

### How to Contribute

1. **Fork this repository**
2. **Create a feature branch**: `git checkout -b feature/AmazingFeature`
3. **Commit changes**: `git commit -m 'Add some AmazingFeature'`
4. **Push to branch**: `git push origin feature/AmazingFeature`
5. **Submit a Pull Request**

### Development Guidelines

- Follow existing code style
- Add necessary comments and documentation
- Ensure new features have corresponding tests
- Update relevant documentation

### Reporting Issues

When submitting an Issue, please include:
- Python version
- Error message and stack trace
- Steps to reproduce
- Relevant configuration information

---

## 🗺️ Project Structure

```
paperRead/
├── main.py                      # Main program
├── zotero_indexer.py            # Zotero index generator
├── notifier.py                  # Notification module
├── feishu_wiki.py               # Feishu Wiki mirror client
├── requirements.txt             # Python dependencies
├── README.md                    # Project documentation (Chinese)
├── README_EN.md                 # Project documentation (English)
├── LICENSE                      # MIT License
├── .env.example                 # Environment variable template
├── .github/
│   └── workflows/
│       ├── daily.yml            # GitHub Actions daily fetch
│       └── daily_paper.yml      # GitHub Actions paper analysis
├── state.json                   # Run state (auto-generated)
├── history.json                 # Paper history (auto-generated)
└── knowledge_base.json          # Knowledge base index (generated on first run)
```

---

## 🔗 Related Resources

- [arXiv API Documentation](https://export.arxiv.org/api_help/)
- [Zotero API Documentation](https://www.zotero.org/dev/doc/)
- [ModelScope](https://modelscope.cn/)
- [PyZotero](https://github.com/urschrei/pyzotero)
- [Feishu Open Platform](https://open.feishu.cn/)
- [WeChat Work Bot](https://developer.work.weixin.qq.com/document/path/91770)

---

## 💡 Tips

1. **First run**: Recommended to use `DRY_RUN=1` to test configuration
2. **Periodically update knowledge base**: When you have many papers in Zotero, re-run `zotero_indexer.py`
3. **Adjust categories**: Adjust keyword configuration based on research interests
4. **View logs**: Pay attention to console output to understand fetching progress and results
5. **Optimize token consumption**: Adjust knowledge base size appropriately to balance analysis quality and cost

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](./LICENSE) file for details

---

## 📋 Changelog

### v2.0.0 (2026-05-08)
- **New**: Auto-download recommended paper PDFs and attach to Zotero items
- **New**: Feishu Wiki mirror — auto-sync paper notes to Feishu Wiki
  - Mirror Zotero collection structure (DailyPapers → categories)
  - Create a dedicated Feishu document for each recommended paper
  - Node caching to avoid duplicate creation
- **Added**: `feishu_wiki.py` — Feishu Open API client
- **Improved**: GitHub Actions support for Feishu Wiki credentials

### v1.0.0
- Initial release
- arXiv paper fetching with two-stage LLM analysis
- Zotero auto-archiving with structured notes
- Feishu/WeChat Work notifications
- GitHub Actions automation

---

## ⭐ Acknowledgments

Thanks to these open source projects:

- [PyZotero](https://github.com/urschrei/pyzotero) - Python Zotero API wrapper
- [feedparser](https://github.com/kurtmckee/feedparser) - RSS/Atom parser
- [aiohttp](https://github.com/aio-libs/aiohttp) - Asynchronous HTTP client

---

## 📧 Contact

- Submit [Issue](../../issues)

---

<div align="center">

**If this project helps you, please give it a Star! ⭐**

Made with ❤️ by researchers, for researchers

</div>
