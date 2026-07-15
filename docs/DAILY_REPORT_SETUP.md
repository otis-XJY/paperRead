# 每日日报自动化配置

本功能每天北京时间 23:30 运行一次：在安装 ActivityWatch 的 Windows 电脑上，通过 GitHub self-hosted runner 执行 `daily_report.py`，读取当天 01:00 至当前时刻的 ActivityWatch 数据和指定飞书群聊历史，然后使用项目已有的 ModelScope/OpenAI 配置生成日报并发回群聊。

## 1. 仓库与分支建议

推荐把此功能放在私有仓库，或把当前公开仓库复制为一个私有部署仓库。self-hosted runner 会执行仓库中的代码；公开仓库如果有人获得写权限，恶意代码可能接触 runner 上的本地文件和 GitHub Secrets。功能 workflow 只配置了 `schedule` 和 `workflow_dispatch`，没有 `pull_request` 触发器，也不会上传 artifact 或 ActivityWatch 快照，但这不能替代私有仓库和分支保护。

在确认代码前可使用独立分支：

```powershell
git switch -c feat/daily-report
python -m unittest test_daily_report.py
git add daily_report.py test_daily_report.py .github/workflows/daily_report.yml docs/DAILY_REPORT_SETUP.md
git commit -m "feat: add scheduled daily work report"
git push -u origin feat/daily-report
```

合并到默认分支后，定时 workflow 才会按 GitHub 的规则运行。若保留公开仓库，请至少启用分支保护、禁止直接推送默认分支，并不要在任何 Pull Request workflow 中使用该 self-hosted runner。

## 2. 配置 Windows self-hosted runner

1. 在 GitHub 仓库打开 `Settings → Actions → Runners → New self-hosted runner`，选择 `Windows` 和 `x64`。
2. 按页面显示的版本下载 runner。下面命令中的 URL、token 和仓库地址必须替换成 GitHub 页面给出的值；token 是一次性短期 token，不要提交到仓库。

```powershell
mkdir C:\actions-runner
cd C:\actions-runner
# 按 GitHub 页面下载并解压 actions-runner-win-x64-*.zip
.\config.cmd --url https://github.com/OWNER/REPO --token GITHUB_TEMP_TOKEN --name daily-report-pc --labels self-hosted,windows,x64
```

配置时选择将 runner 安装为 Windows 服务，并让服务使用能访问 ActivityWatch 的同一用户。验证：

```powershell
Get-Service "actions.runner*"
Invoke-WebRequest http://127.0.0.1:5600/api/0/buckets
```

runner 只需要向 GitHub 发起出站 HTTPS 连接，不需要公网 IP 或入站端口。若使用公开仓库，建议把 runner 放在专用 Windows 用户或虚拟机中，并只给它必要的本地权限。

当前日报 workflow 直接使用 runner 上已安装的 Python，不再通过 `actions/setup-python` 下载 Python，因此不会触发 PowerShell 安装脚本的执行策略限制。请在 runner 机器安装 Python 3.10 或更高版本，并在安装器中勾选 `Add Python to PATH`：

```powershell
python --version
python -m pip --version
```

如果是在安装 Python 后才配置 Runner 服务，请重启服务，使新的 PATH 生效：

```powershell
Restart-Service "actions.runner*"
```

如果 `python -m pip --version` 仍报 `WinError 2` 或找不到 `LocalAppData`，请把 Runner 服务改为使用你日常登录 Windows 的账号，而不是 `LocalSystem`：打开 `services.msc`，找到名称以 `GitHub Actions Runner` 开头的服务，在“属性 → 登录”中选择“此账户”，填写你的 Windows 用户名和密码，应用后重启服务。这样 Runner 才能使用和 ActivityWatch 相同的用户配置目录。workflow 还会为 pip 设置临时的 `LOCALAPPDATA`、`APPDATA`、`USERPROFILE` 和缓存目录作为兜底。

## 3. 创建飞书应用机器人

在[飞书开放平台](https://open.feishu.cn/app)新建“企业自建应用”，名称例如“每日日报”，然后：

1. `应用能力 → 添加应用能力 → 机器人`。
2. `权限管理 → API 权限` 申请：
   - `im:message` 或 `im:message:readonly`
   - `im:message.group_msg`（读取群聊全部历史消息，敏感权限）
   - `im:message:send_as_bot`（发送日报）
   - `im:chat:read`（可选，用于运行 `--list-chats` 查找群 ID）
3. 发布一个应用版本，并把自己的账号和目标群聊加入可用范围。
4. 在飞书客户端把“每日日报”添加到目标群聊。
5. 记录 App ID、App Secret 和目标群的 `chat_id`（通常以 `oc_` 开头）。应用必须已经在该群中，才能读取群历史和发送消息。若申请了 `im:chat:read`，可在本机设置 App ID/Secret 后运行 `python daily_report.py --list-chats` 查找群 ID。

本方案不依赖实时事件订阅；定时任务直接调用[获取会话历史消息接口](https://open.feishu.cn/document/server-docs/im-v1/message/list)，因此不需要配置公网回调地址。若未来改成收到 @ 即时处理，再订阅 `im.message.receive_v1`。

## 4. 配置 GitHub Secrets

在 `Settings → Secrets and variables → Actions` 添加以下 Repository secrets：

```text
DAILY_REPORT_FEISHU_APP_ID
DAILY_REPORT_FEISHU_APP_SECRET
DAILY_REPORT_FEISHU_CHAT_ID
MODELSCOPE_API_KEY       # 或 OPENAI_API_KEY
OPENAI_API_KEY           # 可选备用
```

可选 Repository variables：`LLM_MODEL`、`BASE_URL`、`REPORT_TIMEZONE`。ActivityWatch 默认使用 `http://127.0.0.1:5600`，workflow 已设置 `Asia/Shanghai` 和 23:30 定时。手动运行 workflow 前，确认 ActivityWatch、`aw-watcher-window`、`aw-watcher-afk` 和 `aw-watcher-input` 都在记录数据。

## 5. 本地验证

在仓库根目录准备 `.env`，或在 PowerShell 设置同名环境变量后运行：

```powershell
python -m unittest test_daily_report.py
python daily_report.py --preview
```

`--preview` 会调用飞书和大模型但不发送群消息；它仍会把完整内容发送给已配置的大模型服务。正式 workflow 不打印聊天内容、窗口标题或 ActivityWatch 原始数据，也不保存中间文件。
