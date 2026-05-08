# Release Notes

This document contains detailed notes for each release of Zotero AI Daily Papers.

## Version 1.0.0 - Initial Release

### Release Date
2025-03-24

### Overview

This is the initial release of Zotero AI Daily Papers, an intelligent academic paper fetching, analysis, and archiving system powered by AI.

### 🎉 Major Features

#### 1. Intelligent Paper Fetching
- **arXiv API Integration**: Automatically fetch papers from arXiv using keyword-based search
- **Multi-category Support**: Manage multiple research areas simultaneously
- **Smart Deduplication**: Avoid duplicate paper processing with history tracking
- **Rate Limiting**: Intelligent delay mechanism to respect API limits

#### 2. AI-Powered Analysis
- **Two-Stage Pipeline**:
  - **Stage 1**: Lightweight relevance filtering (score 0-10)
  - **Stage 2**: Deep comparative analysis with existing papers
- **LLM Support**: Compatible with ModelScope Qwen and OpenAI GPT models
- **Structured Output**: Generate JSON-structured analysis including:
  - Recommendation level (Must Read / Worth Reading / Skip)
  - Methodology overview
  - Core concepts and terminology
  - Critical review and insights
  - Comparative analysis (incremental runs)

#### 3. Zotero Integration
- **Automatic Collection Management**: Create collections for each research category
- **Item Creation**: Automatically create Zotero items with complete metadata
- **Rich HTML Notes**: Generate beautifully formatted notes with structured analysis
- **Direct Links**: Provide clickable links to arXiv papers and Zotero items

#### 4. Multi-Platform Notifications
- **Feishu Bot**: Rich-text notifications with paper details
- **WeChat Work Bot**: Markdown-formatted notifications
- **Workflow Status**: Real-time updates on workflow progress
- **Per-Paper Notifications**: Individual detailed analysis for each new paper

#### 5. Incremental Updates
- **State Management**: Track last run date and processed papers
- **Cold Start**: Initial run fetches latest + relevant papers
- **Incremental Mode**: Only fetch and analyze new papers
- **History Tracking**: Avoid duplicate processing

### 📋 Default Research Categories

The project comes pre-configured with four research areas:

1. **UAV_VLN** - UAV Vision-Language Navigation
2. **MultiAgent_Game_Theory** - Multi-Agent Game Theory
3. **MARL** - Multi-Agent Reinforcement Learning
4. **Humanoid_Manipulation** - Humanoid Robot Manipulation

Users can easily customize or add new categories.

### 🛠️ Technical Features

#### Robust Error Handling
- **Retry Mechanism**: Exponential backoff for API failures
- **Graceful Degradation**: Continue processing even if some papers fail
- **Detailed Logging**: Comprehensive console output for debugging
- **Error Notifications**: Automatic notification on workflow failures

#### Flexible Configuration
- **Environment Variables**: Secure configuration via `.env` file
- **Dry Run Mode**: Test without writing to Zotero
- **Debug Modes**: Verbose output for troubleshooting
- **Custom LLM**: Support for any OpenAI-compatible API

#### Automation Ready
- **GitHub Actions**: Pre-configured workflow for daily runs
- **cron Compatible**: Easy to set up scheduled tasks
- **Docker Support**: Containerized deployment option
- **Cross-Platform**: Works on Windows, macOS, and Linux

### 📊 Performance

- **Efficient Fetching**: Async HTTP requests for concurrent API calls
- **Smart Caching**: Knowledge base indexing for fast relevance checks
- **Rate Limit Aware**: Respects arXiv API rate limits automatically
- **Incremental Processing**: Only process new papers on subsequent runs

### 🔐 Security

- **No Hardcoded Credentials**: All secrets via environment variables
- **Secure by Default**: `.env` and `.gitignore` configured properly
- **Minimal Permissions**: Only request necessary API permissions
- **No Data Sharing**: No third-party data transmission beyond configured APIs

### 📚 Documentation

- **Comprehensive README**: Detailed installation and usage guide
- **API Reference**: Complete API documentation
- **Deployment Guide**: Instructions for various deployment scenarios
- **Contributing Guide**: Guidelines for contributors
- **Code of Conduct**: Community guidelines
- **Issue Templates**: Easy bug reporting and feature requests

### 🐛 Known Limitations

1. **LLM Token Usage**: First run with large knowledge base may consume significant tokens
2. **arXiv API Limits**: Rate limits may slow down initial large-scale fetches
3. **Language Support**: Currently optimized for English and Chinese content
4. **Notification Limits**: Feishu/WeChat Work have rate limits on messages

### 🔮 Future Roadmap

#### Planned for v1.1.0
- [ ] Support for additional LLM providers (Anthropic Claude, Google Gemini)
- [ ] Slack and Discord notification channels
- [ ] Web-based configuration UI
- [ ] Export reports to PDF/CSV formats

#### Planned for v1.2.0
- [ ] Multi-language support (i18n)
- [ ] Advanced filtering options
- [ ] Batch processing improvements
- [ ] Performance optimizations

#### Long-term
- [ ] Mobile app companion
- [ ] Collaboration features
- [ ] Integration with other reference managers (Mendeley, EndNote)
- [ ] Community-curated research categories

### 🙏 Acknowledgments

This project would not be possible without:

- **arXiv** for providing the paper database and API
- **Zotero** for the excellent reference management system
- **ModelScope** for providing accessible LLM APIs
- All open-source library maintainers whose work we build upon

### 📦 Dependencies

Core dependencies:
- `aiohttp>=3.9.0` - Async HTTP client
- `feedparser>=6.0.10` - RSS/Atom parser
- `openai>=1.12.0` - OpenAI API client
- `pyzotero>=1.5.22` - Zotero API wrapper
- `beautifulsoup4>=4.12.0` - HTML parser
- `httpx>=0.26.0` - Async HTTP client
- `requests>=2.31.0` - HTTP client for notifications

### 📖 Migration Guide

If you're upgrading from a development version:

1. **Backup your data**:
   ```bash
   cp state.json state.json.backup
   cp history.json history.json.backup
   cp knowledge_base.json knowledge_base.json.backup
   ```

2. **Update dependencies**:
   ```bash
   pip install --upgrade -r requirements.txt
   ```

3. **Rebuild knowledge base** (optional):
   ```bash
   python zotero_indexer.py
   ```

4. **Run normally**:
   ```bash
   python main.py
   ```

### 📞 Support

- **GitHub Issues**: Report bugs and request features
- **Documentation**: See `docs/` directory for detailed guides
- **Community**: Join our discussions on GitHub

### ✅ Getting Started

1. **Clone and install**:
   ```bash
   git clone https://github.com/otis-XJY/paperRead.git
   cd paperRead
   pip install -r requirements.txt
   ```

2. **Configure**:
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

3. **Run**:
   ```bash
   python zotero_indexer.py  # Build knowledge base
   python main.py             # Fetch and analyze papers
   ```

See [README.md](./README.md) for complete documentation.

---

**Thank you for using Zotero AI Daily Papers!** 🚀

We welcome your feedback and contributions to help improve this project.
