# 飞书配置与更新说明

本项目使用飞书自建应用的 `tenant_access_token`。当前版本通过官方 Python SDK `lark-oapi` 调用飞书 API；`FEISHU_WEBHOOK_URL` 仅保留为旧版通知回退。

## GitHub Actions Repository secrets

在仓库的 `Settings → Secrets and variables → Actions → Secrets` 中配置：

```text
FEISHU_APP_ID
FEISHU_APP_SECRET
FEISHU_WIKI_ROOT_NODE_TOKEN
DAILY_REPORT_FEISHU_WIKI_ROOT_NODE_TOKEN
DAILY_REPORT_FEISHU_CHAT_ID
FEISHU_PAPER_CHAT_ID
ZOTERO_USER_ID
ZOTERO_API_KEY
MODELSCOPE_API_KEY 或 OPENAI_API_KEY
```

变量对应关系：

| 配置 | 用途 |
| --- | --- |
| `FEISHU_APP_ID/SECRET` | 统一飞书自建应用凭证 |
| `FEISHU_WIKI_ROOT_NODE_TOKEN` | PaperRead 论文知识库根节点 |
| `DAILY_REPORT_FEISHU_WIKI_ROOT_NODE_TOKEN` | 工作日报月报知识库的单节点 |
| `DAILY_REPORT_FEISHU_CHAT_ID` | 日报读取和发送的群聊 |
| `FEISHU_PAPER_CHAT_ID` | 论文推送群聊 |
| `DAILY_REPORT_FEISHU_APP_ID/SECRET` | 可选；设置后覆盖统一应用凭证 |
| `FEISHU_WEBHOOK_URL` | 可选；官方 SDK 失败或未配置群 ID 时的旧版回退 |

`REPORT_TIMEZONE`、`ACTIVITYWATCH_URL`、`LLM_MODEL`、`BASE_URL` 和消息识别 ID 属于非密配置，可以放在 Repository variables 或本机 `.env` 中。

由于论文机器人和日报机器人是两个应用，建议设置以下 Repository variables，让日报准确识别论文机器人消息：

```text
DAILY_REPORT_PAPERREAD_APP_ID=<论文机器人对应的 FEISHU_APP_ID>
DAILY_REPORT_PAPERREAD_SENDER_ID=<通常可填同一个 App ID>
```

建议再设置一个 Repository variable：

```text
DAILY_REPORT_FEISHU_EVENT_DB=D:\\paperRead-runtime\\feishu_document_events.sqlite3
```

该路径必须与 Windows 长驻监听器使用的路径一致，并且放在 Git 仓库目录之外，避免 GitHub Actions checkout 清理 SQLite 事件队列。

## 飞书开发者后台

给自建应用开通机器人、云文档/Wiki/Docx 读写和群消息读写能力，并发布应用版本。然后使用以下任一方式授权目标知识库或页面：

1. 将机器人所在群组加入知识库成员或管理员；
2. 在页面的“添加文档应用”中直接添加机器人应用。

事件监听进程需要同时开通云盘文档变更事件，并保持 Windows 长驻进程运行。应用身份只能接收应用作为所有者或管理员的文档变更，这是飞书平台的数据范围限制，不是代码可以绕过的权限。

## Windows 事件监听器

在运行 ActivityWatch 的同一 Windows 用户下配置 `.env`，然后执行：

```powershell
python -m pip install -r requirements.txt
python feishu_event_listener.py
```

监听器会将事件保存到 `DAILY_REPORT_FEISHU_EVENT_DB` 指定的 SQLite 文件。日报读取该事件队列，不再扫描知识库节点、文件夹或文档版本。监听器和日报必须使用同一个绝对路径。

建议在“任务计划程序”中以登录用户启动监听器，并让 ActivityWatch、GitHub self-hosted runner 和监听器使用同一个 Windows 用户会话。

## 三屏焦点采集

```powershell
python focus_watcher.py --active-seconds 60
```

采集鼠标所在显示器、前台窗口、点击/滚轮/键盘次数，不保存键盘内容。日报将结果描述为可观测操作焦点。

## 月报行为

每月创建一个 `工作日报-YYYY-MM` 文档；每天使用 `[DAILY_REPORT:YYYY-MM-DD]` 标记写入一次。重复运行同一天不会重复追加。
