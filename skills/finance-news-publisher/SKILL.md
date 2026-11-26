---
name: finance-news-publisher
description: "依次执行三层金融投研Skill，合并去重、保留证据，发布七字段RSS到指定GitHub仓库。"
---

# 投研 RSS 总控

先读references/pipeline.md与config.json；复用references/contract.md，不重复定义消息与日志语义。

1. 以同一run_id、主题和时窗依次执行finance-primary-intelligence、finance-market-research、finance-social-sentiment；微博停用，逐源保留实际结果。
2. 等三层及全部逐源核验结束、日志封存并通过发布门槛后，依pipeline.md核对事件身份、时间及估值按日汇总，再运行scripts/build_feed.py；脚本不能代替采集。
3. 检查七字段、全部合并出处、跨层去重、历史状态、XML和凭据排除；用GitHub连接器按当前分支头原子发布feed.xml及公开状态。
4. 回读远端XML并保留提交号、覆盖表、去重映射和完整可得日志ZIP；任务已授权发布，每轮无需再询问。
