# 日报中的 PaperRead 研究建议

日报会把指定时间范围内识别为 PaperRead 机器人的消息单独提取出来。消息中的 `docx` 或 `wiki` 链接会被读取，并作为论文原文补充材料交给 LLM，用于分析论文与当前研究的关系以及未来研究方向。

## 可选配置

在 GitHub Actions 的 Repository variables 中配置：

```text
DAILY_REPORT_PAPERREAD_SENDER_ID       # 推荐：PaperRead 机器人的 sender id
DAILY_REPORT_PAPERREAD_APP_ID          # 如果 API 返回的是 app id，也可配置
DAILY_REPORT_QUESTION_SENDER_NAME       # 可选；不要写入源代码，建议优先使用 sender id
DAILY_REPORT_QUESTION_SENDER_ID        # 可选：sender id；如果历史消息没有返回姓名则配置此项
DAILY_REPORT_KNOWLEDGE_BASE_ENABLED=1  # 默认开启，设为 0 可关闭知识库读取
DAILY_REPORT_MAX_KNOWLEDGE_DOCUMENTS=8
DAILY_REPORT_DOCUMENT_ACTIVITY_ENABLED=1 # 默认开启文档版本活动读取
DAILY_REPORT_MAX_DOCUMENT_ACTIVITY=30
DAILY_REPORT_FEISHU_WIKI_ROOT_NODE_TOKEN  # 可选：扫描整个知识库子树
DAILY_REPORT_MAX_WIKI_DOCUMENTS=200
```

如果没有配置 PaperRead 的 sender id，程序会使用“app/bot 消息 + 分类计数格式 + arXiv 链接 + 推荐/方法论/锐评字段”进行兜底识别。

`DAILY_REPORT_QUESTION_SENDER_ID` 和 `DAILY_REPORT_QUESTION_SENDER_NAME` 用于指定唯一的群聊提问对象，应在 GitHub `Settings → Secrets and variables → Actions → Variables` 中设置。两者都为空时不会识别任何个人问题；代码不再内置个人姓名。目标群 `DAILY_REPORT_FEISHU_CHAT_ID` 仍应放在 GitHub Repository secret 中。

## 飞书权限

日报使用已有的 `DAILY_REPORT_FEISHU_APP_ID` 和 `DAILY_REPORT_FEISHU_APP_SECRET` 读取知识库文档，不需要新增密钥。飞书应用需要：

- 查看知识库或查看知识空间节点信息；
- 查看新版文档；
- 对 PaperRead 写入的知识库文档具有可见/阅读权限。

若日报日志出现 HTTP 403，通常是应用权限或知识库文档授权不足。可以先将 `DAILY_REPORT_KNOWLEDGE_BASE_ENABLED` 设为 `0`，这样仍会分析 PaperRead 消息本身，但不会读取关联知识库。

文档变更部分读取聊天中出现的 `docx/wiki` 链接对应的版本列表，并筛选当天 01:00 到运行时刻的版本活动。它可以识别新增版本、修改版本以及版本处于回收/删除状态的记录，但不能从版本元数据恢复具体被删除或修改的句子。飞书的实时编辑/删除事件需要另行配置事件订阅和回调服务；当前 GitHub Actions 日报不承担实时事件接收。

## 分析边界

时间投入中的项目、网页和事项来自 ActivityWatch 的窗口标题、URL 及飞书聊天证据。无法从这些证据确定的内容，日报应标记为无法确定，而不是推断为已完成。论文原文事实、聊天中明确计划和模型推断也应分别表述。
