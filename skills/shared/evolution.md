# 每轮复盘与 SkillOpt 进化

这是五个 Skill 共用的规则。无论发布成功、无新增或失败，都执行收尾复盘；中断时保存检查点，恢复后补齐。采集和 RSS 的完整性门槛保持有效，优化失败不能清空旧 feed、推进失败游标或声称链路修复。

## 链路复核

1. 各层在自己的同 run_id 日志输出 `route-observations.json`：逐来源、必检子项和实际 adapter/端点模板记录稳定 `route_id`、URL、`checked_at`、`outcome`、`root_cause`、`evidence_call_ids`、原始材料路径、重试及修复前后证据。结果用 readable / no_new / transport_error / blocked / empty_shell / parse_error / stale / data_unqualified / not_tested / policy_excluded；原因用 network / rate_limit / endpoint_changed / authentication / subscription / site_policy / dynamic_rendering / parser / stale_cache / calendar / data_quality / history_unavailable / unknown。不能仅凭 HTTP 200 判 readable；没有证据填 unknown。
2. 总控读取历史 `runs/*/source-audit.json`、`data/route-history.json`、共享历史样本及本轮三层及板块监控收尾输出，运行 `scripts/review_run.py`。比较最近可比成功证据，不能把其他子项、转载、旧样本或首页通当成原路径成功。source/component 候选只是待核验差集，route receipt 才能证明具体路径退化。无新增、休市/节假日与发布日历单列。
3. 对每个“以前可读、本次未得”的候选沿原 adapter/端点有限复核，依次排查：共同网络故障、429/Retry-After、404迁移、认证/订阅/政策、动态壳、解析变更、缓存/数据日、检索窗口/采集遗漏。相同路径和替代路径分别记结果；禁止为了恢复数字而降低内容门槛、删除来源或绕过限制。微博停用，Reddit已禁路径仅复核既有政策证据，不重试。未定位如实写 unknown，并保留下一步。
4. 生成新修订目录 `review/<revision>/review.json` 与 `diagnoses.json`；每个候选写旧/新证据、已证实原因或未知、尝试、修复结果、剩余缺口和负责 Skill。修复后重跑受影响内容核验/子项，再重建相关覆盖和封存输出；查不到新内容不能填“恢复”。保留每版，不覆盖已封存日志。

示例（从仓库根目录运行，路径均指本轮真实输出）：

```sh
python skills/finance-news-publisher/scripts/review_run.py \
  --current <source-audit.json> --history <历史audit...> \
  --availability skills/shared/source-availability.json \
  --observations <合并route-observations.json> --ledger data/route-history.json \
  --output <本轮总控日志/review/001>
```

## 使用微软 SkillOpt-Sleep

上游：[microsoft/SkillOpt](https://github.com/microsoft/SkillOpt)，固定提交 `dc0b5e654cafa1073a2819086151c1e0d13012f0`；使用其 `plugins/codex/skills/skillopt-sleep/SKILL.md`。升级依赖须记录差异和验证，不能静默拉最新版。

从本轮复盘及历史真实成功/失败任务生成五份 `skillopt_sleep.tasks.v1` JSON（文件名 `<skill>.json`），经脱敏检查设 `reviewed: true`。只输入最小事实、错误与证据引用，不输入全会话、原始新闻全文、Cookie、验证码或密钥。训练、验证、最终测试按真实任务 ID/事件分开；验证/测试不进入反思提示，不拿合成样本冒充真实留出集。保留所有任务文件及哈希。

```sh
python skills/finance-news-publisher/scripts/skillopt_cycle.py \
  --engine-root <固定版本SkillOpt目录> --repo . \
  --run-dir <本轮总控日志/skillopt> --tasks-dir <已审阅任务目录> \
  --backend handoff --adopt
```

handoff 无需单独 API：按上游规则用全新上下文回答 `pending.json` 中每个提示，只能看到该提示；不带本轮其他任务、历史或评分答案。回答存 `answers/<id>.md` 后重复同命令；退出码 3 表示等待答案，不能称优化完成。平台允许时使用不继承历史的独立执行上下文；否则记录隔离能力缺口并暂存候选，不伪造独立验证。已配置可用 Codex CLI 时可用 `--backend codex`。mock 只测接线，成绩不得作为真实改进或发布依据。

每个 Skill 每轮至多一处有证据的 add/delete/replace；入口原则上不超过1200字符，单轮增长最多20%且不超过160字符（极短文档保留40字符预算），公共语义只改共享引用文件一处。五者均需跑，确无改进可保持原版。上游 gate=on、逐任务不退化、独立留出得分严格改善后，复核 report、具体 diff 及未参与优化的最终测试结果；用上游 adopt 备份并采用，用户已授权该范围的自动进化，无需每轮询问。保留拒绝编辑及原因，禁止手写“通过”或为过门槛改测试。

采用后运行相关真实回放及仓库既有测试，核对七字段、全出处、日期、GUID、完整性门槛与凭据排除；失败用备份回退并记录。更新五份 `.skill` 包。基于 GitHub 最新头 expected_sha/force=false 提交 Skills、脚本、验证和脱敏复盘；如状态已被新轮更新，重读合并，不能覆盖 feed/state。链路账本和复盘可独立提交，**不推进采集游标**。拒绝优化不能阻止本轮原有有效 RSS 发布。

完整查询、原始返回、原因分析、修复尝试、任务拆分、提示/答案、旧/新版本、候选 diff、逐任务分数、拒绝/采纳、备份、回滚、测试和提交记录都归入同 run_id 总控日志，层内取证留在对应层日志并互引。追加优化前/后日志分卷，保留已封存卷；最后完整性校验并导出私有 ZIP。公开仅脱敏原因、变更及评分摘要；回复写明恢复/未恢复数量和 Skill 变更或未采纳原因。

板块监控也纳入进化：估值与价格分开回放，不得为提高通过率降低用户10%阈值、减少对象/子项或把未知窗口改为已验证。
