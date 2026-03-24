# Contributing to Zotero AI Daily Papers

Thank you for your interest in contributing to Zotero AI Daily Papers! This document provides guidelines and instructions for contributing to the project.

## 🤝 How to Contribute

### Reporting Bugs

Before creating bug reports, please check the existing issues as you might find that the problem has already been reported. When creating a bug report, please include as many details as possible:

- **Clear and descriptive title**
- **Steps to reproduce** the issue
- **Expected behavior** vs. **actual behavior**
- **Screenshots** if applicable
- **Environment information** (OS, Python version, etc.)
- **Relevant logs** and error messages

Use the [Bug Report template](.github/ISSUE_TEMPLATE/bug_report.md) when reporting bugs.

### Suggesting Enhancements

Enhancement suggestions are tracked as GitHub issues. When creating an enhancement suggestion, please:

- **Use a clear and descriptive title**
- **Provide a detailed description** of the suggested enhancement
- **Explain why** this enhancement would be useful
- **List any alternatives** you've considered

Use the [Feature Request template](.github/ISSUE_TEMPLATE/feature_request.md) for suggestions.

### Pull Requests

Pull requests are the best way to propose changes to the codebase. We actively welcome your pull requests:

1. **Fork the repository** and create your branch from `main`
2. **Make your changes** following our coding standards
3. **Write tests** for new functionality
4. **Update documentation** as needed
5. **Ensure all tests pass**
6. **Submit your pull request**

## 📝 Development Guidelines

### Code Style

- Follow PEP 8 style guide for Python code
- Use meaningful variable and function names
- Add docstrings to functions and classes
- Keep functions focused and concise

### Commit Messages

Follow these guidelines for commit messages:

- **Use the present tense** ("Add feature" not "Added feature")
- **Use the imperative mood** ("Move cursor to..." not "Moves cursor to...")
- **Limit the first line to 72 characters or less**
- **Reference issues and pull requests liberally** (e.g., "Fixes #123")

Example:
```
Add support for custom LLM models

- Allow users to specify custom model endpoints
- Update configuration documentation
- Add validation for model parameters

Fixes #45
```

### Testing

- Write tests for new features and bug fixes
- Ensure all existing tests pass
- Test on Python 3.8, 3.9, 3.10, and 3.11 if possible
- Add test data in `tests/` directory

## 🏗️ Project Structure

```
paperRead/
├── main.py                 # Main application logic
├── zotero_indexer.py      # Zotero indexing
├── notifier.py            # Notification system
├── tests/                 # Test files
├── docs/                  # Additional documentation
└── .github/               # GitHub configuration
    ├── workflows/
    └── ISSUE_TEMPLATE/
```

## 🐛 Debugging

When debugging issues:

1. Enable debug mode: `DEBUG_PHASE_ONE=1`
2. Use dry-run mode: `DRY_RUN=1`
3. Check logs carefully
4. Verify API keys and permissions
5. Test with minimal configuration first

## 📚 Documentation

Documentation is crucial for a healthy project:

- Keep README.md and README_EN.md in sync
- Update docstrings when modifying functions
- Add examples for new features
- Document configuration options
- Maintain CHANGELOG.md for version history

## 🎯 Areas Where We Need Help

We welcome contributions in these areas:

- **Additional LLM providers** (Anthropic, Google, etc.)
- **More notification channels** (Slack, Discord, Telegram)
- **Enhanced analysis capabilities**
- **Performance optimizations**
- **Better error handling**
- **Internationalization**
- **Comprehensive test coverage**
- **Documentation improvements**

## 📬 Getting in Touch

- Open an issue for bugs or questions
- Join our discussions for general topics
- Contact maintainers via email for sensitive issues

## 📜 Code of Conduct

Please be respectful and constructive in all interactions. We're committed to providing a welcoming and inclusive environment for all contributors.

## ⏱️ Time Commitment

There's no minimum time commitment. Even small contributions like:

- Fixing a typo
- Updating documentation
- Reporting a bug
- Sharing feedback

Are greatly appreciated!

---

Thank you for contributing to Zotero AI Daily Papers! 🙏
