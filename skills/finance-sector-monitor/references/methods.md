# 口径与运行

- 默认38个对象：A股22个宽基/行业/风格指数，美股16个宽基/行业ETF；具体名单只有universe.json维护。以指数代表基金持仓方向，ETF/联接/QDII不等于该指数的可交易收益；费用、跟踪误差、溢价和汇率另核对。
- “低于历史90%以上”定义为自身可得历史的估值分位≤10%（含边界），不是价格跌90%、较最高点跌90%，也不是未来上涨概率90%。银行、证券、地产用PB，其余优先PE；负/零估值、样本不足、不适用和过期记录不触发。周期行业盈利高点造成的低PE须列局限。
- 蛋卷分位0—1、韭圈儿0—100分别保留原值，不平均。平台分位标vendor_reported，列接口样本起点、数据日、可见窗口与未独立重算的限制；未核得五年窗口者只能待核线索，不能称长期低估。跨平台阈值结论冲突不标已确认。begin_at只标“接口给出的起点”，不能冒充已取得全历史估值序列。
- 发布硬门槛唯一配置为 references/valuation-policy.json（共享源 skills/shared/valuation-policy.json）。美股或其他对象缺合格历史PE/PB时只保留缺口，不发低位提醒；价格深回撤仅作估值达标消息的背景，不能独立触发或补足估值缺失。所有反弹阶段也须当前合格估值仍在阈值内。
- 回撤序列按各日当时已见峰值计算，无未来数据；分位为(<当前值的样本数+0.5×相同值样本数)/N。反弹候选要求已有低位区间、单日涨幅≥2%、较近20日最低收盘回升≥3%、高于5日均线。盘中条目必须标“未收盘”，收盘后同一反弹条目更新确认，沿用GUID。称“符合规则的反弹”，不称底部已经确认；缺成交量不声称放量。
- 数据超过4自然日、未来日期或源stale/insufficient/not_applicable标记不作有效触发；列实际交易日期及节假日核验缺口。只用已见数据，今日反弹未核得就不能用用户例子或新闻标题补齐行情。
- 首次扫描已在低位者写“首次纳入观察”，不伪称今天刚跌破。低位持续不重复发；相同区间只发进入、首次反弹及盘中→收盘确认修订。分位≥15%连续3个不同数据日后重新布防，轮询次数不能当交易日；历史区间与提醒记录不删。

从该Skill目录运行：

```sh
python scripts/run_logger.py init --root <日志根> --run-id <同总控run_id> \
  --skill finance-sector-monitor --config-file <本轮配置.json>
python scripts/monitor.py --run <已初始化目录> --previous <仓库/data/sector-monitor-state.json>
```

monitor.py只写本轮outputs，不能直接写GitHub或覆盖上轮状态。输出含messages.json、coverage.json、report.md、run-summary.md、sector-monitor.json、sector-monitor-state.next.json、route-observations.json；登记原始返回、解析、计算、状态迁移、排除、去重和错误后由总控复核并封存。总控将本轮公开分析报告写runs/<run_id>/sector-monitor.json，监控状态与feed/state同一成功提交；未结束则保留旧feed和状态。

修复后可用`--replay-run <原监控日志目录>`回放已留证据，或以其暂存状态作为--previous验证重复提醒；该模式不发网络请求，coverage标validation_only，发布门槛拒绝。不能把回放当新一轮采集或先把暂存状态写入data。

网络默认公开蛋卷/韭圈儿估值、新浪指数日线/腾讯盘中报价、Yahoo ETF复权日线。API错误或空壳不算价格历史成功；不使用需交互的Stooq页面，也不绕过认证/限流。脚本没有模型推理，解释来自已记录的确定性计算；发布者仍须核验口径和数据质量。测试样本仅验证规则，不得当作实时消息发布。
