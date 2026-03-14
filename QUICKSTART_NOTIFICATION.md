# 快速配置通知推送（5分钟完成）

## 方案一：企业微信（推荐）

### 步骤1：创建机器人（1分钟）
1. 打开你需要接收通知的微信群
2. 点击右上角 `...` → `群机器人` → `添加机器人`
3. 点击 `新建机器人` → 设置名称（如：论文推送）
4. 复制生成的 Webhook URL

### 步骤2：配置 GitHub（2分钟）
1. 打开你的 GitHub 仓库
2. 点击 `Settings` → `Secrets and variables` → `Actions`
3. 点击 `New repository secret`
4. Name 输入：`WXWORK_WEBHOOK_URL`
5. Secret 输入：步骤1复制的 URL
6. 点击 `Add secret`

### 步骤3：完成（0分钟）
配置完成！下次 GitHub Actions 运行时会自动推送通知。

---

## 方案二：飞书

### 步骤1：创建机器人（1分钟）
1. 打开你需要接收通知的飞书群
2. 点击群设置 `...` → `群机器人` → `添加机器人`
3. 选择 `自定义机器人` → 设置名称和描述
4. 复制生成的 Webhook URL

### 步骤2：配置 GitHub（2分钟）
1. 打开你的 GitHub 仓库
2. 点击 `Settings` → `Secrets and variables` → `Actions`
3. 点击 `New repository secret`
4. Name 输入：`FEISHU_WEBHOOK_URL`
5. Secret 输入：步骤1复制的 URL
6. 点击 `Add secret`

### 步骤3：完成（0分钟）
配置完成！下次 GitHub Actions 运行时会自动推送通知。

---

## 测试配置

### 方法1：手动触发 Workflow
1. 进入 GitHub 仓库
2. 点击 `Actions` 标签
3. 选择 `Zotero AI Daily Papers`
4. 点击 `Run workflow` → `Run workflow`

### 方法2：本地测试
```bash
# 安装依赖
pip install requests

# 测试企业微信
python -c "
from notifier import WxWorkNotifier
import os
os.environ['WXWORK_WEBHOOK_URL'] = '你的Webhook URL'
WxWorkNotifier().send_text('🎉 测试消息发送成功！')
"

# 测试飞书
python -c "
from notifier import FeishuNotifier
import os
os.environ['FEISHU_WEBHOOK_URL'] = 'https://open.feishu.cn/open-apis/bot/v2/hook/1f3ce8ee-d1c3-414d-9568-a6f46b056df0'
FeishuNotifier().send_text('🎉 测试消息发送成功！')
"
```

---

## 常见问题

### Q1: 两个平台都配置会怎样？
A: 两个平台都会收到通知，互不影响。

### Q2: 如何只使用一个平台？
A: 只配置对应平台的 Secret 即可，无需修改代码。

### Q3: 如何禁用通知？
A: 在 GitHub Actions 中设置 `ENABLE_NOTIFICATION: "0"`，或本地运行时 `export ENABLE_NOTIFICATION=0`

### Q4: 收不到通知怎么办？
A: 检查以下几点：
1. Webhook URL 是否正确复制
2. GitHub Secrets 是否正确添加
3. 查看 GitHub Actions 日志确认是否有错误

### Q5: 通知频率太高怎么办？
A: 可以在代码中添加条件判断，只在有新论文时才发送通知（已默认实现）。

---

## 下一步

详细配置说明请查看：[NOTIFICATION_SETUP.md](./NOTIFICATION_SETUP.md)
