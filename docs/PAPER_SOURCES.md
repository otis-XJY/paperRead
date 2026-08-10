# 多源论文发现配置

`paper_sources.json` 控制无需 API Key 的公开论文来源。默认开启：arXiv（内置）、OpenReview、ACL Anthology、PMLR、CVF Open Access 和 Europe PMC。

- `enabled`：关闭某一来源时设为 `false`；不会影响其他来源。
- `venues` / `events`：限制对应来源扫描的会议或论文集。可按年度更新，例如将 `CVPR2025` 替换为 `CVPR2026`。
- `categories.json` 的 `discovery_queries` 用于非 arXiv 来源的本地匹配；原来的 `keywords` 仍只服务 arXiv 查询，二者不应混用。

每个来源和分类都有独立游标，首次成功获取回溯 12 个月。来源调用失败不会推进它的游标，因此下次运行会自动重试该来源的回溯数据。每来源每分类最多采集 20 个原始候选，跨来源去重和质量/兴趣排序后，最多 20 篇进入原有 LLM 分析。

OpenReview 可能对自动化请求返回 challenge/403；程序会记录该来源失败并继续处理其余来源，不需要添加 API Key。
