# 工具用法

Python 3.9+、POSIX文件锁；三个脚本均仅用标准库。日志字段与保留要求统一见contract.md。

```sh
python scripts/run_logger.py init --root /项目/research-logs --skill <Skill名> --config-file /已脱敏/config.json
python scripts/run_logger.py event --run <目录> --type tool_start --data-file /已脱敏/start.json
python scripts/run_logger.py capture --run <目录> --file /已检查/返回.json --name artifacts/call-1.json --safe-input
python scripts/run_logger.py event --run <目录> --type tool_end --data-file /已脱敏/end.json
python scripts/run_logger.py verify --run <目录>
python scripts/run_logger.py finalize --run <目录> --status completed --log-completeness partial --notes '据实际结果填写'
python scripts/run_logger.py export --run <目录> --output /归档/本轮.zip
```

start：call_id、source_ids、tool、arguments、started_at、retry_of。end：call_id、status、ended_at、duration_ms、truncated、result或result_paths、gaps。init保存本轮Skill快照；事件枚举见脚本--help。capture的--safe-input是调用者已检查凭据的声明，不是用户审批；助手脱敏只覆盖常见字段，不能代替人工检查。

check_sources.py --skill-dir <Skill目录> --run <运行目录> [--source-ids ID…] [--url 实际链接] [--workers 4] [--timeout 15]：自动留痕HTTP/PDF访问测试，不生成投资结论；PDF文本提取可选pdftotext。HTTP/解码/应用报错均保留，预检环境故障停止批量。资料识别/成功层级须由执行者依协议审核，不能以脚本状态代替。

等价文件工具可替代日志助手。导出损坏证据可加--allow-incomplete，原样保留并交付失败详情；结束运行不得再改历史记录。独立.skill包将公共协议实体化为本地引用，源码中的shared是唯一维护源。

API适配器：scripts/financial_api.py --provider <providers.json中名称> --run <目录> --source-ids ID… [--params-file 参数JSON] [--endpoint news/all]。真实Key仅从命名环境变量读取，缺Key退出2且不发送请求；服务/权限/格式错误退出3并保存响应，HTTP 200不等于成功。Alpha Vantage --demo仅作官方演示，不能冒充私人Key或注册结果。免费额度/覆盖与注册状态见providers.json，未核对的媒体映射不得标成覆盖成功。

环境变量由受控运行器或本地私密配置注入；.env.example仅列变量名，不自动载入。eastmoney_research使用--endpoint report/jg及参数{"qType":2}获取策略元数据，JSONP只解析不执行；匹配机构/原件/日期后才可产出消息。

本轮新增wallstreetcn_rss、fed_monetary_rss、fed_speeches_rss、danjuan_valuation、funddb_valuation免Key适配器；原始字段、数据期与时效缺口随响应保留。check_sources同时保留JSON-LD与time标签候选，不能自动认定为首次发布时间。

Firecrawl备用：总控scripts/firecrawl_mcp.py --run <目录> --source-ids ID… --tool firecrawl_scrape --args-file <含url的JSON>；免Key按实际tools/list能力调用，保留完整返回及缓存时点，HTTP200仍检查工具错误。限额/网络拒绝不切换身份绕过，专业供应商需单独授权。
