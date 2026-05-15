# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned
- Support for additional LLM providers (Anthropic Claude, Google Gemini)
- More notification channels (Slack, Discord, Telegram)
- Web-based configuration interface
- Export reports to multiple formats
- Community-contributed research categories
- Multi-language support

## [1.2.0] - 2026-05-15

### Added
- OAI-PMH备用方案：当arXiv API限速时通过批量接口获取论文数据
- 错误收集器：统一管理运行时错误并发送到飞书通知
- GitHub Actions环境变量FORCE_JAVASCRIPT_ACTIONS_TO_NODE24支持
- 本地关键词过滤功能，支持更灵活的搜索语法解析

### Changed
- 优化arXiv抓取重试策略，采用指数退避机制避免频繁限速
- 优化工作流超时设置为120分钟以适应长时间运行任务
- 增强飞书wiki节点缓存验证机制，避免使用已删除节点的无效token

### Fixed
- 修复首次运行时节点缓存可能失效的问题

## [1.1.1] - 2026-05-12

### Changed
- 优化无新论文通知内容显示
- 动态显示当前扫描的分类信息，替代固定的分类列表
- 使通知内容更准确地反映实际配置的分类

## [1.1.0] - 2026-05-08

### Added
- 飞书Wiki同步功能：将Zotero论文笔记镜像到飞书知识库
- 新增 feishu_wiki.py 模块实现飞书 Wiki 客户端
- 支持创建节点、获取节点、移动节点等Wiki操作
- 在通知消息中添加飞书知识库链接
- 新增 bootstrap_feishu_wiki_layout.py 引导脚本
- 新增 test_feishu_wiki.py 测试文件

### Changed
- 更新 GitHub Actions 工作流以支持飞书 Wiki 配置
- 重构飞书 Wiki 客户端实现，使用正确的 API 接口创建节点

### Removed
- 移除 PDF 下载和附件功能，简化核心逻辑

### Fixed
- 添加缓存验证机制和节点有效性检查

## [1.0.0] - 2025-03-24

### Added
- Initial release
- Core paper fetching and analysis functionality
- Zotero integration
- Basic notification support
- Configuration system

### Features
- arXiv paper fetching with keyword-based search
- LLM-based relevance analysis
- Automatic Zotero collection and item creation
- Structured HTML note generation
- Incremental paper updates
- Rate limiting and retry mechanisms
- Multi-category support

---

[Unreleased]: https://github.com/otis-XJY/paperRead/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/otis-XJY/paperRead/compare/v1.1.0...v1.2.0
[1.1.1]: https://github.com/otis-XJY/paperRead/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/otis-XJY/paperRead/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/otis-XJY/paperRead/releases/tag/v1.0.0
