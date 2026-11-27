# 执行与发布

依赖三层采集及finance-sector-monitor共四个同级Skill。缺依赖时从配置仓库的skills/<名称>/SKILL.md读取并解析其本地引用；不能把SKILL.md当可执行函数、用入口测试或旧验证样本冒充本轮三层采集。

- 同run_id初始化五份日志，读取当前仓库main头、data/feed-state.json及source-availability.json历史可用性基线（仅供逐源复核）。依config的主题/时窗逐一执行三层Skill后调用finance-sector-monitor；源目录全检查，实际内容/子项/时效覆盖分别记录。S09微博范围外；Reddit现有明确自动化阻断不反复试探。免KeyFirecrawl仅作公开动态页面备用，认证/付费/站点拒绝依原协议处理。
- 每层输出messages.json、coverage.json及report/run-summary并封存日志。所有启用来源及required_components须以本轮证据收尾为content_collected、no_new_verified（限明确检索范围）或access_limited_verified；停用项out_of_scope。行内保留checked_at、checked_scope、reason、evidence_call_ids及evidence_paths，coverage.collection_complete仅全部结束才为true。
- 合并前必须通过脚本publication_gate：三层及板块监控日志已封存、无未返回调用，目录/子项无遗漏，每项均有内容级终态和证据。仅入口、待发现、正文未核验、未知或运行中均阻断；先完成全部任务再合并发布，不发布半成品后补。阻断时保留旧feed并交付差集与日志。
- 同一事实事件经核对后填写event_key（机构/实体、事件、报告期）；不同意见/作者、需独立留存的正式版本及数据期不共用去重键。数字/范围冲突先保留双方并标冲突；仅标题相似不自动合并。“原始链接”栏列全合并出处，按URL去重并标来源；原件缺失保留null，实际读取/转载链接明确区分；禁以转载次数抬高置信度。
- 跨轮读取上次成功state：稳定身份+内容指纹识别新增/修订/重复，获取与观测时刻不进入指纹；数值快照优先提供fact_values，正文可提供source_content_sha256。重复不重发，RSS更新时间变化复用原文章身份，同记录修正沿用GUID；独立正式版本用revision_id区分；保留重叠时窗防漏。游标与feed/state同一提交成功后才生效，失败不推进，访问受限不推进对应来源/子项内容游标，成功子项不能推进失败兄弟子项；日志列新增/更新/重复/未取得。
- manifest列run_id、as_of、window_start/window_end及四个依赖的layer/skill/messages/coverage路径（自分析内部编号4，不作为第四权威来源层级）。运行：python scripts/build_feed.py --manifest <manifest.json> --previous <data/feed-state.json或不存在的初始路径> --config references/config.json --output <输出目录>。脚本按标准化原始URL/明确事件键去重，保留各层出处与审计引用；GUID借助历史别名保持稳定。发布前由执行者核验其dedup-map、rejected和warnings；未知首发不补造RSS pubDate，历史材料不得伪装新消息。
- 公开发布仅含原创简述、七字段、引用链接及脱敏覆盖摘要；完整原文/浏览器记录/日志ZIP保留在当前运行工作区并在任务回复中交付，禁止上传凭据、验证码、私密会话或未授权原始日志。持久公开items/state按GUID累积，不因无新增清空。默认RSS不截条数；归档保持完整。
- 估值快照在RSS按data_period每日合成一条七字段汇总，同日更新复用GUID；逐指数保留平台原值、口径、来源链接、时间、评分与异常标记，不平均不同平台数据。items/state仍逐条存储，日期不明者单列。--render-state也须--completion-manifest通过门槛；--preview-only仅生成本地preview.xml，不能作为发布结果。
- 先在本地验证、秘密扫描；GitHub空仓库用contents创建首个文件，之后基于最新tree新建commit，以expected_sha且force=false移动main。连接器不适合大文件时，用已认证gh api的--input文件上传blob并校验SHA；分支仍按上述CAS更新。遇并发冲突回读新头与state后重新合并，绝不强推。发布失败保留产物、错误与上轮feed，不报告成功。
- 回读原始订阅URL解析RSS，并核对新GUID及提交；GitHub页面URL不是订阅URL。定时任务从仓库加载这些Skill，按北京时间07:00/13:00/22:00执行；计划触发时刻不等于采集与发布完成时刻。
- 每次收尾（含失败/无新增）执行references/evolution.md：合并三层及板块监控路径收据，复核历史成功而本轮未得的链路，依真实任务使用固定版SkillOpt-Sleep验证五个Skill。复盘、修复及优化追加同run_id日志；候选通过留出门槛和回归检查后采用，公共规则只维护一处。维护提交基于最新头，不覆盖已更新的feed/state；最后再导出包含复盘/优化的日志ZIP。

- 板块监控按其methods/universe执行：读取data/sector-monitor-state.json（缺省空状态），仅输出符合共享估值硬门槛的低位/反弹自分析消息；不与平台估值快照合并，不把同一底层数据当独立佐证。build_feed同时校验监控状态基线指纹并生成data/sector-monitor-state.json及sector-monitor.json；后者发布至runs/<run_id>/sector-monitor.json。监控状态与feed、items、feed-state同一提交，提交失败不消费提醒。冲突后重读最新监控状态并重算信号；不得沿用基于旧状态的待发提醒。总控与四个依赖均纳入同轮日志、完整性门槛及SkillOpt复盘。
- 自分析的published_at是实际首次计算产出的时间，数据日/报价时点另列；可在本轮started_at至as_of期间产生，不能伪填新闻窗口截止前的时间。盘中→收盘修订保留首次时间及GUID，updated_at为实际重算时间并标material_update。此规则仅用于已登记的第5个Skill，不放宽外部新闻时间核验。

- 板块提醒与估值汇总的所有生成、历史状态渲染路径必须执行 references/valuation-policy.json；超阈值或估值未核合格均不得推送，价格不能替代。被排除的原始记录/历史GUID继续留档，撤回记录及原因公开脱敏；采集器与RSS出口双重校验。
