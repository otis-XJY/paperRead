# 多源论文发现配置

`paper_sources.json` 控制无需 API Key 的公开论文来源。默认开启：arXiv（内置）、OpenReview、ACL Anthology、PMLR、CVF Open Access 和 Europe PMC。

- `enabled`：关闭某一来源时设为 `false`；不会影响其他来源。
- `venues` / `events`：限制对应来源扫描的会议或论文集。可按年度更新，例如将 `CVPR2025` 替换为 `CVPR2026`。
- `cvf.ecva_events`：ECCV 论文由 ECVA 官方页面提供，不在 CVF Open Access 域名下；例如保留 `ECCV2024`。
- `filters.excluded_topics`：全局低关注主题词。仅当这类词出现在标题、且标题没有至少两个明确的分类核心方法信号时才过滤；摘要中仅提及安全/攻击不会过滤。例如 `Communication Topology ... under Adversarial Interference` 会保留，而以 attack/security/safety 为主要标题主题的论文会跳过。默认词包括 security、attack、adversarial、jailbreak、safety、privacy；如有需要可直接增删英文词组。
- `categories.json` 的 `discovery_queries` 用于非 arXiv 来源的本地匹配；原来的 `keywords` 仍只服务 arXiv 查询，二者不应混用。

每个来源和分类都有独立游标，首次成功获取回溯 12 个月。来源调用失败不会推进它的游标，因此下次运行会自动重试该来源的回溯数据。每来源每分类最多采集 20 个原始候选，跨来源去重和质量/兴趣排序后，最多 20 篇进入原有 LLM 分析。

OpenReview 可能对自动化请求返回 challenge/403；程序会记录第一次失败、在该次 Action 的后续分类中熔断该来源，并继续处理其余来源。该状态不会持久化，下一次 Action 会重新尝试，不需要添加 API Key。

所有公开来源请求采用与 arXiv 相同的 Action 重试策略：网络错误、超时、5xx 和 OpenReview 的 403 最多尝试 3 次，并在下一轮前按 6 秒、12 秒指数退避；429 每次固定等待 60 秒再继续。OpenReview 的 403 完成 3 次尝试后才在本次 Action 中熔断。确定的 404 地址错误不重试。
