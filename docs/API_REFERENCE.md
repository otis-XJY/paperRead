# API Reference

This document provides detailed information about the APIs and interfaces used in Zotero AI Daily Papers.

## Table of Contents

- [Configuration API](#configuration-api)
- [Notifier API](#notifier-api)
- [Zotero Indexer API](#zotero-indexer-api)
- [Environment Variables](#environment-variables)

## Configuration API

### CONFIG Dictionary

Main configuration dictionary in `main.py`.

```python
CONFIG = {
    "categories": Dict[str, CategoryConfig],
    "llm_model": str,
    "base_url": str
}
```

### CategoryConfig

```python
{
    "keywords": List[str],  # arXiv search keywords
    "desc": str          # Category description
}
```

### Example

```python
CONFIG = {
    "categories": {
        "UAV_VLN": {
            "keywords": [
                'ti:"Vision-Language Navigation"',
                '(abs:UAV AND abs:Navigation)'
            ],
            "desc": "UAV vision-language navigation"
        }
    },
    "llm_model": "Qwen/Qwen3.5-35B-A3B",
    "base_url": "https://api-inference.modelscope.cn/v1/"
}
```

## Notifier API

### NotificationManager

Main class for managing notifications.

```python
class NotificationManager:
    def __init__(self)
    def send_text(self, content: str, platforms: Optional[List[str]] = None) -> Dict[str, bool]
    def send_workflow_start(self, is_first_run: bool) -> Dict[str, bool]
    def send_workflow_complete(self, stats: Dict, platforms: Optional[List[str]] = None) -> Dict[str, bool]
    def send_papers_detail(self, stats: Dict, is_first_run: bool, platforms: Optional[List[str]] = None) -> Dict[str, bool]
    def send_no_papers_notification(self, is_first_run: bool) -> Dict[str, bool]
    def send_workflow_error(self, error: str) -> Dict[str, bool]
```

### Methods

#### send_text()

Send text message to specified platforms.

```python
def send_text(self, content: str, platforms: Optional[List[str]] = None) -> Dict[str, bool]
```

**Parameters:**
- `content` (str): Message content
- `platforms` (Optional[List[str]]): List of platforms, default ["wxwork", "feishu"]

**Returns:**
- `Dict[str, bool]`: Result for each platform

**Example:**
```python
from notifier import notifier

result = notifier.send_text("Test message", platforms=["feishu"])
print(result)  # {"feishu": True}
```

#### send_papers_detail()

Send detailed paper analysis notifications.

```python
def send_papers_detail(self, stats: Dict, is_first_run: bool, platforms: Optional[List[str]] = None) -> Dict[str, bool]
```

**Parameters:**
- `stats` (Dict): Statistics dictionary with paper details
- `is_first_run` (bool): Whether this is first run
- `platforms` (Optional[List[str]]): List of platforms

**Stats Format:**
```python
{
    "categories": Dict[str, int],
    "total_papers": int,
    "papers": Dict[str, List[PaperInfo]]
}
```

### FeishuNotifier

Feishu-specific notification implementation.

```python
class FeishuNotifier:
    def __init__(self, webhook_url: Optional[str] = None)
    def send_text(self, content: str) -> bool
    def send_post(self, title: str, content: List[List[Dict]]) -> bool
```

### WxWorkNotifier

WeChat Work-specific notification implementation.

```python
class WxWorkNotifier:
    def __init__(self, webhook_url: Optional[str] = None)
    def send_text(self, content: str) -> bool
    def send_markdown(self, content: str) -> bool
```

## Zotero Indexer API

### Functions

#### build_knowledge_base()

Build knowledge base from existing Zotero papers.

```python
def build_knowledge_base() -> None
```

**Output:**
- Creates `knowledge_base.json` file

**Example:**
```python
from zotero_indexer import build_knowledge_base

build_knowledge_base()
```

#### get_or_create_collection()

Get existing collection or create new one.

```python
def get_or_create_collection(name: str, parent_key: Optional[str] = None) -> str
```

**Parameters:**
- `name` (str): Collection name
- `parent_key` (Optional[str]): Parent collection key for nested collections

**Returns:**
- `str`: Collection key

#### extract_note_parts()

Extract short review and full note from HTML.

```python
def extract_note_parts(html_content: str) -> Tuple[str, str]
```

**Parameters:**
- `html_content` (str): HTML content of note

**Returns:**
- `Tuple[str, str]`: (short_review, full_note)

## Environment Variables

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `ZOTERO_USER_ID` | Zotero user ID | `12345678` |
| `ZOTERO_API_KEY` | Zotero API key | `abc123xyz789` |
| `MODELSCOPE_API_KEY` | ModelScope API key | `your_key_here` |
| `OPENAI_API_KEY` | OpenAI API key (alternative) | `sk-...` |

### Optional Variables

| Variable | Description | Default |
|----------|-------------|----------|
| `FEISHU_WEBHOOK_URL` | Feishu bot webhook URL | - |
| `WXWORK_WEBHOOK_URL` | WeChat Work webhook URL | - |
| `ENABLE_NOTIFICATION` | Enable notifications (1/0) | `1` |
| `DRY_RUN` | Dry run mode (1/0) | `0` |
| `DEBUG_PHASE_ONE` | Debug mode for phase one (1/0) | `1` |

## arXiv API Integration

### Search Query Format

Queries use arXiv query syntax:

```python
# Title search
'ti:"Vision-Language Navigation"'

# Abstract search
'abs:UAV AND abs:Navigation'

# Category search
'cat:cs.MA'

# Combined
'ti:"Game Theory" AND abs:Multi-agent'
```

### API Endpoint

```
http://export.arxiv.org/api/query?search_query={query}&sortBy={sort}&sortOrder={order}&max_results={max}
```

**Parameters:**
- `search_query`: arXiv search query
- `sortBy`: `submittedDate`, `relevance`, `lastUpdatedDate`
- `sortOrder`: `ascending`, `descending`
- `max_results`: Number of results (max 2000)

## LLM API Integration

### OpenAI-Compatible Format

Uses OpenAI client library:

```python
from openai import OpenAI

client = OpenAI(
    api_key=LLM_API_KEY,
    base_url=CONFIG["base_url"],
    timeout=90.0,
    max_retries=2,
)
```

### JSON Response Format

All LLM responses are expected in JSON format:

```json
{
    "is_relevant": boolean,
    "score": number,
    "matched_titles": [string],
    "reason": string,
    "recommendation": "必读/值得看/可跳过",
    "methodology": string,
    "core_concepts": [string],
    "sharp_review": string
}
```

## Data Structures

### Paper Info

```python
{
    "id": str,           # arXiv ID
    "title": str,        # Paper title
    "summary": str,      # Abstract
    "published": str,    # Publication date
    "authors": List[str] # Author names
}
```

### Analysis Result

```python
{
    "recommendation": str,       # 必读/值得看/可跳过
    "methodology": str,         # Method description
    "core_concepts": List[str], # Key concepts
    "sharp_review": str,        # Critical review
    "comparison": str          # Comparison with existing papers (incremental runs)
}
```

### State File

```json
{
    "is_first_run": boolean,
    "last_date": string  // ISO 8601 format
}
```

### Knowledge Base

```json
{
    "category_name": [
        {
            "title": str,
            "short_review": str,
            "full_note": str
        }
    ]
}
```

## Error Handling

### Retry Mechanism

```python
def retry_sync(operation, operation_name, retries=3, base_delay=1.0):
    """Retry with exponential backoff"""
```

### Rate Limiting

Automatic delays:
- First run: 8-10 seconds
- Incremental: 6 seconds
- 429 errors: 7-19 seconds dynamic

## Customization

### Adding Custom LLM Provider

1. Implement OpenAI-compatible API
2. Update `CONFIG["base_url"]`
3. Set appropriate API key

### Adding Notification Channel

1. Create new notifier class
2. Implement `send_text()` method
3. Add to `NotificationManager`
4. Update documentation

## See Also

- [arXiv API Documentation](https://export.arxiv.org/api_help/)
- [Zotero API Documentation](https://www.zotero.org/dev/doc/)
- [OpenAI API Reference](https://platform.openai.com/docs/api-reference)
