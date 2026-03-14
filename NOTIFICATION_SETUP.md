# 通知推送配置说明

本项目支持通过企业微信和飞书推送 GitHub Actions 运行结果通知。

## 配置步骤

### 1. 企业微信推送

#### 1.1 创建企业微信机器人
1. 在企业微信群中，点击群设置 -> 群机器人 -> 添加机器人
2. 设置机器人名称，获取 Webhook URL
3. URL 格式示例：`https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=XXXXXXXX`

#### 1.2 配置 GitHub Secrets
在 GitHub 仓库中添加以下 Secret：
- **Secret 名称**: `WXWORK_WEBHOOK_URL`
- **Secret 值**: 你的企业微信 Webhook URL

### 2. 飞书推送

#### 2.1 创建飞书机器人
1. 在飞书群中，点击群设置 -> 群机器人 -> 添加机器人 -> 自定义机器人
2. 设置机器人名称、描述，获取 Webhook URL
3. URL 格式示例：`https://open.feishu.cn/open-apis/bot/v2/hook/XXXXXXXX`

#### 2.2 配置 GitHub Secrets
在 GitHub 仓库中添加以下 Secret：
- **Secret 名称**: `FEISHU_WEBHOOK_URL`
- **Secret 值**: 你的飞书 Webhook URL

### 3. 启用通知功能

在 GitHub Actions 中，`ENABLE_NOTIFICATION` 环境变量默认设置为 `"1"`，表示启用通知。

如果需要禁用通知：
- 本地运行：`export ENABLE_NOTIFICATION=0`
- GitHub Actions：修改 `.github/workflows/daily_paper.yml` 中的 `ENABLE_NOTIFICATION: "0"`

## 通知内容

### 工作流开始通知
```
🚀 Zotero AI Daily Papers 开始运行

运行模式: 首次运行（冷启动）/ 增量运行
开始时间: 2024-01-01 02:00:00
```

### 工作流完成通知
```
✅ Zotero AI Daily Papers 运行完成

处理分类数: 4
发现新论文: 5 篇
完成时间: 2024-01-01 03:00:00

分类详情:
  • UAV_VLN: 2 篇
  • MultiAgent_Game_Theory: 1 篇
  • MARL: 1 篇
  • Humanoid_Manipulation: 1 篇
```

### 工作流错误通知
```
❌ Zotero AI Daily Papers 运行失败

错误信息: HTTP 429 (速率限制)
发生时间: 2024-01-01 02:30:00
```

## 通知消息格式

### 企业微信
- 支持文本消息和 Markdown 格式
- Markdown 支持标题、列表、链接等格式

### 飞书
- 支持文本消息和富文本消息
- 富文本支持标题、列表、链接、@提人等格式

## 注意事项

1. **安全性**：Webhook URL 是敏感信息，请妥善保管，不要泄露到公开的代码仓库中
2. **频率限制**：企业微信和飞书都有消息频率限制，请注意不要频繁调用
3. **消息长度**：单条消息有长度限制，过长的内容会被截断
4. **多平台推送**：如果同时配置了企业微信和飞书，两个平台都会收到通知

## 测试通知

### 本地测试
```bash
# 测试企业微信推送
python -c "from notifier import WxWorkNotifier; WxWorkNotifier().send_text('测试消息')"

# 测试飞书推送
python -c "from notifier import FeishuNotifier; FeishuNotifier().send_text('测试消息')"
```

### GitHub Actions 测试
1. 在 GitHub 仓库页面，点击 `Actions` 标签
2. 选择 `Zotero AI Daily Papers` workflow
3. 点击 `Run workflow` 按钮手动触发

## 禁用通知

### 本地运行
```bash
export ENABLE_NOTIFICATION=0
python -u main.py
```

### GitHub Actions
修改 `.github/workflows/daily_paper.yml`：
```yaml
env:
  ENABLE_NOTIFICATION: "0"  # 改为 "0" 禁用通知
```

## 高级配置

### 自定义通知内容

修改 `notifier.py` 中的方法来自定义通知内容：

```python
def send_workflow_complete(self, stats: Dict, platforms: Optional[List[str]] = None) -> Dict[str, bool]:
    # 自定义通知内容
    total_papers = sum(stats.get("categories", {}).values())
    content = f"✅ 我的自定义通知：共发现 {total_papers} 篇论文"
    return self.send_text(content, platforms)
```

### 仅使用特定平台

```python
# 仅使用企业微信
from notifier import notifier
notifier.send_text("测试消息", platforms=["wxwork"])

# 仅使用飞书
notifier.send_text("测试消息", platforms=["feishu"])
```

## 故障排查

### 问题1：收不到通知
- 检查 Webhook URL 是否正确配置
- 检查 GitHub Secrets 是否正确设置
- 检查网络连接是否正常
- 查看 GitHub Actions 日志确认是否有错误

### 问题2：通知内容不完整
- 检查消息长度是否超过限制
- 企业微信：文本消息最大 4096 字节，Markdown 最大 4096 字节
- 飞书：文本消息最大 4096 字节，富文本最大 4096 字节

### 问题3：频繁发送通知
- 检查 workflow 调度频率
- 如果不需要每次都通知，可以在代码中添加条件判断
