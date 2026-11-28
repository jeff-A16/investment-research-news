# 金融投研 RSS 与板块低位监控

订阅地址：**https://raw.githubusercontent.com/jeff-A16/investment-research-news/main/feed.xml**

GitHub文件浏览地址不能直接作为RSS订阅地址。feed.xml为RSS 2.0，UTF-8；普通新闻包含来源及原始链接、标题、发表时间、置信度、投资影响和影响度；板块低位提醒仅五项。RSS采用精简展示，完整核验记录保留在原始数据及日志。

总控Skill：[finance-news-publisher](skills/finance-news-publisher/SKILL.md)。它依次执行[一层](skills/finance-primary-intelligence/SKILL.md)、[二层](skills/finance-market-research/SKILL.md)、[三层](skills/finance-social-sentiment/SKILL.md)，合并重复事实并保留不同观点/数据期/修订和多源出处。共享协议只有skills/shared一份维护源。

更新计划：北京时间每天 **07:00、22:00** 由ChatGPT定时任务启动执行。触发后需要采集、核验和发布时间；外站限制、任务失败或权限中断可能导致延迟，失败时保留上一次有效feed。

每轮成功、无新增或失败后都[复核历史链路并使用微软 SkillOpt-Sleep 进化五个 Skill](skills/shared/evolution.md)：逐来源、子项和实际路径保留旧/新证据，区分无新增、缓存/日历与访问/解析退化；没有证据不宣称恢复。每个 Skill 每轮至多一处短编辑，验证集严格改善且逐任务不退化后采用，失败回退。完整复盘、提示/答案、评分、修改及发布过程保留在同 run_id 私有日志；不把原始日志或凭据上传仓库。

当前XML历史消息已按用户要求清空（0条）；427条原始明细、全部历史GUID及去重状态继续保留。后续仅展示清理界限后新增或实质更新的记录。最近一次完整采集的[逐源记录](runs/20261127T230256Z-scheduled-morning/source-audit.json)和[验证](runs/20261127T230256Z-scheduled-morning/validation.json)保留，采集游标未推进。

合并器使用原始URL、经核对的事件键、数据期及记录ID；不会仅凭标题相似合并。跨轮保存内容指纹，获取时间变化不重发，同记录修正复用GUID；来源/子项游标与feed、state同一提交成功后才生效，受限子项不推进。保留重叠采集窗口防漏，RSS更新其pubDate时仍复用原文章身份及旧GUID；本轮201条合格候选再次输入得到201条重复、0条新增/更新RSS。置信度衡量证据，不是预测胜率；未取得依据的影响度保留待评估。

指数估值在RSS中按数据日期汇总成一条，同日刷新复用GUID；明细保留平台原值、来源、时间、评分及异常标记。完整逐指数记录仍在data/items.json与data/feed-state.json，RSS条数与原始记录数分别统计。--render-state发布也须提供通过门槛的--completion-manifest；--preview-only只生成本地preview.xml。

所有合并条目的“原始链接”栏集中列出全部出处，按URL去重并标明来源名称；实际读取/转载链接与原件缺失情况分别标注。

运行：读取总控Skill的pipeline.md；完成三层及板块监控输出后执行build_feed.py。Skill文档不是可执行函数，Python合并器不能代替研究采集。运行环境为Python 3.9+标准库；验证命令：`python -m unittest discover -s skills/finance-news-publisher/tests -v`。

发布采用GitHub当前分支头校验和普通快进更新，保留已有状态，禁止强推。公开仓库只保存原创简述、引用链接、最终数据和脱敏运行摘要；完整原始返回保留私有ZIP，另交付过程日志与证据索引，凭据、验证码、私密会话与未授权原始日志不上传。

下载：downloads中的.skill是ZIP格式，单包引用已实体化；总控需三层及板块监控Skill配合，五件套可一起导入。

第5个 Skill：[板块低位监控](skills/finance-sector-monitor/SKILL.md)，由总控在三层采集后调用。默认监控[38个A股/美股基金方向](skills/finance-sector-monitor/references/universe.json)，估值分位≤10%进入观察；仅在估值低位进入时提醒，持续低位不重复，不发反弹通知；美股缺历史估值只保留缺口，不改用价格条件。自分析内部保留完整取证；公开仅板块、历史最低估值、当前估值、当前/历史最低百分比及一段低位原因。完整[口径](skills/finance-sector-monitor/references/methods.md)、监控状态与RSS原子提交，五份日志私有归档。未完成任一依赖不能发布。

按用户要求已清空当前XML历史条目；后续仅展示清理界限后新增或实质更新的记录。427条原始明细、历史GUID与去重状态继续保留。RSS时间仅来源发表时间，评分只显示数字，投资影响为资产及原因的一段说明；不再显示摘录标签、核验缺口或独立合并出处段。

板块提醒示意：**🚨【板块低位提醒】创业板**；正文只保留五项。最低值按真实可得区间计算，PE/PB与指数点位不同，当前/最低比例也不等于估值历史分位。普通财经新闻的订阅范围及每天07:00/22:00日程继续保留。
