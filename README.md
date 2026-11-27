# 金融投研 RSS 与板块低位监控

订阅地址：**https://raw.githubusercontent.com/jeff-A16/investment-research-news/main/feed.xml**

GitHub文件浏览地址不能直接作为RSS订阅地址。feed.xml为RSS 2.0，UTF-8；每条包含来源及原始链接、标题、消息产生时间、置信度、对投资的影响、影响度。原件未取得、时间不明、指数样本不足与未读研报正文均明确标注。

总控Skill：[finance-news-publisher](skills/finance-news-publisher/SKILL.md)。它依次执行[一层](skills/finance-primary-intelligence/SKILL.md)、[二层](skills/finance-market-research/SKILL.md)、[三层](skills/finance-social-sentiment/SKILL.md)，合并重复事实并保留不同观点/数据期/修订和多源出处。共享协议只有skills/shared一份维护源。

更新计划：北京时间每天 **07:00、13:00、22:00** 由ChatGPT定时任务启动执行。触发后需要采集、核验和发布时间；外站限制、任务失败或权限中断可能导致延迟，失败时保留上一次有效feed。

每轮成功、无新增或失败后都[复核历史链路并使用微软 SkillOpt-Sleep 进化五个 Skill](skills/shared/evolution.md)：逐来源、子项和实际路径保留旧/新证据，区分无新增、缓存/日历与访问/解析退化；没有证据不宣称恢复。每个 Skill 每轮至多一处短编辑，验证集严格改善且逐任务不退化后采用，失败回退。完整复盘、提示/答案、评分、修改及发布过程保留在同 run_id 私有日志；不把原始日志或凭据上传仓库。

最近阈值纠错已撤回9条未经授权的价格触发消息，板块提醒仅保留17条合格估值消息；估值汇总仅显示20条阈值内且窗口/质量合格的记录，86条原始估值与全部293条明细/GUID继续留档。RSS当前200条。唯一门槛见[valuation-policy.json](skills/shared/valuation-policy.json)，价格/反弹不能替代，未知不发。此轮是基于已完成采集的口径纠错，不推进采集游标。[修订记录](runs/20261127T064252Z-valuation-threshold/validation.json)。

合并器使用原始URL、经核对的事件键、数据期及记录ID；不会仅凭标题相似合并。跨轮保存内容指纹，获取时间变化不重发，同记录修正复用GUID；来源/子项游标与feed、state同一提交成功后才生效，受限子项不推进。保留重叠采集窗口防漏，RSS更新其pubDate时仍复用原文章身份及旧GUID；本轮174条合格候选再次输入得到174条重复、0条新增/更新RSS。置信度衡量证据，不是预测胜率；未取得依据的影响度保留待评估。

指数估值在RSS中按数据日期汇总成一条，同日刷新复用GUID；明细保留平台原值、来源、时间、评分及异常标记。完整逐指数记录仍在data/items.json与data/feed-state.json，RSS条数与原始记录数分别统计。--render-state发布也须提供通过门槛的--completion-manifest；--preview-only只生成本地preview.xml。

所有合并条目的“原始链接”栏集中列出全部出处，按URL去重并标明来源名称；实际读取/转载链接与原件缺失情况分别标注。

运行：读取总控Skill的pipeline.md；完成三个Skill输出后执行build_feed.py。Skill文档不是可执行函数，Python合并器不能代替研究采集。运行环境为Python 3.9+标准库；验证命令：`python -m unittest discover -s skills/finance-news-publisher/tests -v`。

发布采用GitHub当前分支头校验和普通快进更新，保留已有状态，禁止强推。公开仓库只保存原创简述、引用链接、最终数据和脱敏运行摘要；完整可得原始返回/过程日志另行ZIP交付，凭据、验证码、私密会话与未授权原始日志不上传。

下载：downloads中的.skill是ZIP格式，单包引用已实体化；总控需三层及板块监控Skill配合，五件套可一起导入。

第5个 Skill：[板块低位监控](skills/finance-sector-monitor/SKILL.md)，由总控在三层采集后调用。默认监控[38个A股/美股基金方向](skills/finance-sector-monitor/references/universe.json)，估值分位≤10%进入观察；低位后反弹分盘中与收盘，沿用区间身份防重复。缺历史估值的美股行业仅按可核验复权价格发独立深回撤信号。全部自分析保留七字段和原始数据链接；完整[口径](skills/finance-sector-monitor/references/methods.md)、五份日志及监控状态随每轮RSS统一提交。未完成任一依赖不能发布。
