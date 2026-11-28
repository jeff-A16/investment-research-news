---
name: finance-sector-monitor
description: "监控A股和美股基金板块的历史估值低位、最低值比较及低位原因，以可追溯自分析消息交给投研总控发布。"
---

# 板块低位监控

执行 references/contract.md 与 references/evolution.md；对象和阈值读 references/universe.json，口径见 references/methods.md。

1. 同总控 run_id 初始化日志，运行 scripts/monitor.py；逐对象核验估值、价格和数据日，缺口保留。
2. 严格执行 references/valuation-policy.json；仅估值达标可提醒，价格回撤或反弹不得替代估值门槛。仅提醒估值低位，按methods公开五项；不发反弹阶段通知，不宣称底部已确定。
3. 按观察区间和信号阶段去重，输出七字段 messages、逐对象 coverage、分析报告及待提交监控状态；总控等全部任务结束后统一发布。状态随 feed 原子提交成功才生效。
