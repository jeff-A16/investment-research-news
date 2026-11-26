# 执行与发布

依赖三个同级Skill。缺依赖时从配置仓库的skills/<名称>/SKILL.md读取并解析其本地引用；不能把SKILL.md当可执行函数、用入口测试或旧验证样本冒充本轮三层采集。

- 同run_id初始化四份日志，读取当前仓库main头和data/feed-state.json。依config的主题/时窗逐一执行三层Skill；源目录全检查，实际内容/子项/时效覆盖分别记录。S09微博范围外；Reddit现有明确自动化阻断不反复试探。免KeyFirecrawl仅作公开动态页面备用，认证/付费/站点拒绝依原协议处理。
- 每层输出完整messages.json、coverage.json及report/run-summary。无合格消息可空数组，失败层不能伪造成功；总控保留其错误后继续其他层，feed中保留先前有效条目。
- 同一事实事件经核对后填写event_key（机构/实体、事件、报告期）；不同意见/作者、修订版本及数据期不共用去重键。数字/范围冲突先保留双方并标冲突；仅标题相似不自动合并。“原始链接”栏列全合并出处，按URL去重并标来源；原件缺失保留null，实际读取/转载链接明确区分；禁以转载次数抬高置信度。
- manifest列run_id、as_of、window_start/window_end及三个layer的skill/messages/coverage路径。运行：python scripts/build_feed.py --manifest <manifest.json> --previous <data/feed-state.json或不存在的初始路径> --config references/config.json --output <输出目录>。脚本按标准化原始URL/明确事件键去重，保留各层出处与审计引用；GUID借助历史别名保持稳定。发布前由执行者核验其dedup-map、rejected和warnings；未知首发不补造RSS pubDate，历史材料不得伪装新消息。
- 公开发布仅含原创简述、七字段、引用链接及脱敏覆盖摘要；完整原文/浏览器记录/日志ZIP保留在当前运行工作区并在任务回复中交付，禁止上传凭据、验证码、私密会话或未授权原始日志。持久公开items/state按GUID累积，不因无新增清空。默认RSS不截条数；归档保持完整。
- 估值快照在RSS按data_period每日合成一条七字段汇总，同日更新复用GUID；逐指数保留平台原值、口径、来源链接、时间、评分与异常标记，不平均不同平台数据。items/state仍逐条存储，日期不明者单列。仅展示调整用--render-state重渲染已发布state，保留原采集时刻和历史日志，不当作新采集。
- 先在本地验证、秘密扫描；GitHub空仓库用contents创建首个文件，之后基于最新tree新建commit，以expected_sha且force=false移动main。遇并发冲突回读新头与state后重新合并，绝不强推。发布失败保留产物、错误与上轮feed，不报告成功。
- 回读原始订阅URL解析RSS，并核对新GUID及提交；GitHub页面URL不是订阅URL。定时任务从仓库加载这些Skill，按北京时间08:00/14:00/23:00执行；计划触发时刻不等于采集与发布完成时刻。
