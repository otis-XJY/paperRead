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

`ACTIVITYWATCH_URL`、`LLM_MODEL`、`BASE_URL` 和消息识别 ID 属于非密配置，可以放在 Repository variables 或本机 `.env` 中。日报时区已固定写入代码，为 `Asia/Shanghai`。

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

监听器会将事件保存到 `DAILY_REPORT_FEISHU_EVENT_DB` 指定的 SQLite 文件，并将诊断信息写入同目录的 `feishu_event_listener.log`（最多保留约 3 MB 的滚动日志）。日报读取该事件队列，不再扫描知识库节点、文件夹或文档版本。监听器和日报必须使用同一个绝对路径。

建议在“任务计划程序”中以登录用户启动监听器，并让 ActivityWatch、GitHub self-hosted runner 和监听器使用同一个 Windows 用户会话。

## 权限诊断

项目提供只读诊断脚本，可分别检查日报应用身份、群聊历史消息和知识库节点权限：

```powershell
python daily_report.py --list-chats

$wikiToken = Read-Host "输入 DAILY_REPORT_FEISHU_WIKI_ROOT_NODE_TOKEN"
python feishu_permission_check.py `
  --chat-id oc_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx `
  --wiki-token $wikiToken
Remove-Variable wikiToken
```

知识库检查返回 `auth_result=true` 才表示当前日报应用身份确实拥有对应节点权限。API 权限开通但资源权限不足时，仍可能返回 403 或 `auth_result=false`。该脚本不创建、修改或删除飞书内容。

## 三屏焦点采集

```powershell
python focus_watcher.py --active-seconds 60
```

采集鼠标所在显示器、前台窗口、点击/滚轮/键盘次数，不保存键盘内容。日报将结果描述为可观测操作焦点。

## 月报行为

每月创建一个 `工作日报-YYYY-MM` 文档；每天使用 `[DAILY_REPORT:YYYY-MM-DD]` 标记写入一次。重复运行同一天不会重复追加。

## 云文档变更的新实现

日报应用的长连接监听器使用应用身份（`DAILY_REPORT_FEISHU_APP_ID/SECRET`，没有专用值时回退到 `FEISHU_APP_ID/SECRET`）。飞书事件订阅本身是按具体云文档 token 创建的，并不存在一个可以直接覆盖所有知识库节点和所有云空间文件的全局订阅。因此项目增加了 `feishu_subscription_manager.py`：它使用一次性的用户 OAuth 定期发现当前用户可访问的资源，然后为 docx、sheet、bitable 创建订阅；知识库节点先解析 `node_token` 得到真正的 `obj_token`，不会把知识库节点 token 直接当作文档 token。这个任务只发现当前资源和创建订阅，不回放电脑离线期间遗漏的事件，也不读取正文。

官方接口参考：

- [获取知识空间节点信息](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/get_node)
- [获取知识空间节点列表](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/list)
- [获取知识空间列表](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space/list)
- [获取空间根目录](https://open.feishu.cn/document/server-docs/docs/drive-v1/folder/get-root-folder-meta)
- [获取文件夹中的文件清单](https://open.feishu.cn/document/server-docs/docs/drive-v1/folder/list)
- [订阅云文档事件](https://open.feishu.cn/document/server-docs/docs/drive-v1/event/subscribe?lang=zh-CN)
- [创建用户授权访问凭证](https://open.feishu.cn/document/server-docs/authentication-management/access-token/create-2)
- [刷新用户授权访问凭证](https://open.feishu.cn/document/server-docs/authentication-management/access-token/create)

### 一次性本地 OAuth 配置

在日报应用的开放平台后台完成以下事项：开启用户身份的云空间、文档、电子表格、多维表格和知识库读取/编辑相关权限；在事件订阅中保留应用身份的 `drive.file.created_in_folder_v1`、`drive.file.edit_v1`、`drive.file.title_updated_v1`、`drive.file.deleted_v1`、`drive.file.trashed_v1`，并添加用户身份的 bitable 字段/记录变更事件；发布最新版本。

在本地 `.env` 中设置日报应用凭证和以下值（路径必须是绝对路径）：

```text
DAILY_REPORT_FEISHU_APP_ID=<日报机器人应用 App ID>
DAILY_REPORT_FEISHU_APP_SECRET=<日报机器人应用 App Secret>
DAILY_REPORT_FEISHU_OAUTH_STORE=D:/paperRead-runtime/feishu_oauth_tokens.json
DAILY_REPORT_FEISHU_EVENT_DB=D:/paperRead-runtime/feishu_document_events.sqlite3
FEISHU_OAUTH_REDIRECT_URI=http://127.0.0.1:8765/feishu/oauth/callback
DAILY_REPORT_FEISHU_USER_OPEN_ID=<授权用户 open_id，可先留空>
```

只在本地首次运行：

```powershell
python -m pip install -r requirements.txt
python feishu_oauth.py --authorize
python feishu_oauth.py --show
python feishu_subscription_manager.py --discover
```

授权浏览器必须使用拥有这些文档访问权限的用户账号。成功后 OAuth 文件只保存令牌元数据，不保存文档内容；不要把它提交 Git，也不要放进 GitHub Secrets。可用 Windows ACL 仅允许当前账户读取：

```powershell
icacls D:\paperRead-runtime\feishu_oauth_tokens.json /inheritance:r /grant:r "$env:USERNAME:(R,W)"
```

### 周期发现任务

事件监听器保持常驻；发现/订阅任务每天运行一次即可。它会自动发现用户“我的空间”根目录下的文件夹和文件，并遍历用户可访问的知识库空间。共享云空间的额外文件夹，把文件夹 token 写入 `FEISHU_DISCOVERY_FOLDER_TOKENS`，多个 token 用英文逗号分隔。`FEISHU_DISCOVERY_WIKI_SPACE_IDS` 留空表示遍历用户可见的全部知识空间；只想限制范围时再填写 space_id。

建议把以下命令设置为 Windows 任务计划程序，每天运行一次，启动程序填写 `D:\ProgramData\anaconda3\envs\paperRead\python.exe`，参数填写 `feishu_subscription_manager.py --discover`，起始位置填写 `D:\桌面\徐君仪\0博士\paperRead`：

```powershell
python feishu_subscription_manager.py --discover
```

### 本地数据上限

SQLite 只保存事件元数据和资源元数据。事件正文 raw payload 默认最多 1000 个字符，事件最多 5000 条；成功生成日报后会删除日报时间范围内的事件并压缩 WAL。资源注册表最多 10000 条，已删除资源默认保留 7 天。日志最多约 3 MB。可通过 `DAILY_REPORT_FEISHU_EVENT_MAX_ROWS`、`DAILY_REPORT_FEISHU_RESOURCE_MAX_ROWS` 等变量进一步调小，不建议把上限设置为无限。

注意：事件内容通常只提供“发生了哪类变化、哪个资源、操作者”等元数据，不提供可直接用于总结的正文差异。日报因此按操作类型、标题、资源类型和来源进行总结；若需要正文级差异，必须另行读取版本/内容接口，这会增加权限、请求量和本地数据风险，本次实现不启用。
