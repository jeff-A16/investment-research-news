# 金融投研 RSS

订阅地址：**https://raw.githubusercontent.com/jeff-A16/investment-research-news/main/feed.xml**

GitHub文件浏览地址不能直接作为RSS订阅地址。feed.xml为RSS 2.0，UTF-8；每条包含来源及原始链接、标题、消息产生时间、置信度、对投资的影响、影响度。原件未取得、时间不明、指数样本不足与未读研报正文均明确标注。

总控Skill：[finance-news-publisher](skills/finance-news-publisher/SKILL.md)。它依次执行[一层](skills/finance-primary-intelligence/SKILL.md)、[二层](skills/finance-market-research/SKILL.md)、[三层](skills/finance-social-sentiment/SKILL.md)，合并重复事实并保留不同观点/数据期/修订和多源出处。共享协议只有skills/shared一份维护源。

更新计划：北京时间每天 **08:00、14:00、23:00** 由ChatGPT定时任务启动执行。触发后需要采集、核验和发布时间；外站限制、任务失败或权限中断可能导致延迟，失败时保留上一次有效feed。

首轮193条候选，36条不在时窗，157条合并为155项；其中包含76条指数估值快照和14条未读正文的研报索引。目录并不等于当天全量内容覆盖；每轮真实范围和缺口见[runs](runs)。历史可读样本不能填充当日消息。微博已停用，Reddit当前受限。

合并器使用原始URL、经核对的事件键、数据期及记录ID；不会仅凭标题相似合并。GUID在后续更新保持稳定，避免重复推送。置信度衡量证据，不是预测胜率；未取得依据的影响度保留待评估。

指数估值在RSS中按数据日期汇总成一条，同日刷新复用GUID；明细保留平台原值、来源、时间、评分及异常标记。完整逐指数记录仍在data/items.json与data/feed-state.json，RSS条数与原始记录数分别统计。纯展示调整可用build_feed.py --render-state配合--config及--output重渲染，不作为新采集。

所有合并条目的“原始链接”栏集中列出全部出处，按URL去重并标明来源名称；实际读取/转载链接与原件缺失情况分别标注。

运行：读取总控Skill的pipeline.md；完成三个Skill输出后执行build_feed.py。Skill文档不是可执行函数，Python合并器不能代替研究采集。运行环境为Python 3.9+标准库；验证命令：`python -m unittest discover -s skills/finance-news-publisher/tests -v`。

发布采用GitHub当前分支头校验和普通快进更新，保留已有状态，禁止强推。公开仓库只保存原创简述、引用链接、最终数据和脱敏运行摘要；完整可得原始返回/过程日志另行ZIP交付，凭据、验证码、私密会话与未授权原始日志不上传。

下载：downloads中的.skill是ZIP格式，单包引用已实体化；总控需另三层Skill配合，四件套可一起导入。
