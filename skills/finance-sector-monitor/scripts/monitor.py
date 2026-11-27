#!/usr/bin/env python3
"""Deterministic sector alerts; private evidence and staged state, never direct publication."""
import argparse
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid
from zoneinfo import ZoneInfo

RULE = 'sector-valuation-only-v2'
REPO = 'https://github.com/jeff-A16/investment-research-news/blob/main/'
_spec=importlib.util.spec_from_file_location('monitor_valuation_policy',Path(__file__).with_name('valuation_policy.py'))
policy_rules=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(policy_rules)


def load_config(path=None):
    path=Path(path or Path(__file__).parents[1]/'references/universe.json')
    cfg=json.loads(path.read_text());policy=policy_rules.load_policy(path.with_name(cfg['valuation_policy_file']))
    cfg['valuation_percentile_max']=policy['max_percentile'];cfg['valuation_policy_version']=policy['version']
    return cfg


def number(x):
    if isinstance(x, bool):
        return None
    try:
        x = float(x)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def rank(values, value):
    return (sum(x < value for x in values) + .5 * sum(x == value for x in values)) / len(values)


def date_ok(day, as_of, market, max_age):
    try:
        d = datetime.fromisoformat(day[:10]).date()
        today = as_of.astimezone(ZoneInfo('Asia/Shanghai' if market == 'CN' else 'America/New_York')).date()
        return 0 <= (today - d).days <= max_age
    except (ValueError, TypeError):
        return False


def valuations(sector, snapshots, as_of, cfg):
    result, gaps = [], []
    code = sector.get('valuation_code')
    if not code:
        return None, [], ['本对象未配置可核验历史估值映射；价格信号不等于估值信号。']
    metric = sector['metric'].lower()
    for provider, rows in snapshots.items():
        for row in rows:
            row_code = row.get('index_code') if provider == 'danjuan' else row.get('code')
            canonical = re.sub(r'[^A-Z0-9]', '', str(row_code).upper())
            aliases = {re.sub(r'[^A-Z0-9]', '', code.upper())}
            if code.startswith(('SH', 'SZ')):
                aliases.add(code[2:] + '.' + code[:2])
                aliases.add(code[2:] + code[:2])
            if code == 'NDX':
                aliases.add('NDXGI')
            if code == 'SP500':
                aliases.add('SPXGI')
            if canonical not in aliases:
                continue
            value = number(row.get(metric))
            raw_pct = number(row.get(metric + ('_percentile' if provider == 'danjuan' else '_pct')))
            pct = raw_pct if provider == 'danjuan' else raw_pct / 100 if raw_pct is not None else None
            try:
                day = datetime.fromtimestamp(row['ts'] / 1000, timezone.utc).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat() if provider == 'danjuan' else row['update_time'][:10]
                start = datetime.fromtimestamp(row['begin_at'] / 1000, timezone.utc).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat() if provider == 'danjuan' and row.get('begin_at') else row.get('start_date') or None
            except (KeyError, ValueError, TypeError, OverflowError):
                gaps.append(provider + '：无有效数据日/接口起点。')
                continue
            flags = {k: row.get(k) for k in ('stale', 'insufficient', 'not_applicable')}
            if value is None or value <= 0 or pct is None or not 0 <= pct <= 1 or any(flags.values()) or not date_ok(day, as_of, sector['market'], cfg['max_data_age_days']):
                gaps.append(provider + '：估值/分位/质量标记/时效不合格。')
                continue
            years = (datetime.fromisoformat(day) - datetime.fromisoformat(start)).days / 365.25 if start else None
            window_ok = years is not None and years >= cfg['minimum_history_years']
            result.append(dict(provider=provider, metric=metric.upper(), value=value,
                percentile=pct, raw_percentile=raw_pct, raw_unit='0—1' if provider == 'danjuan' else '0—100',
                data_date=day, interface_start_date=start, window_ok=window_ok,
                method='vendor_reported; not independently recomputed', flags=flags))
    eligible = [x for x in result if x['window_ok']]
    if not eligible:
        return None, result, gaps + ['未核得五年及以上可用估值分位窗口。']
    chosen = sorted(eligible, key=lambda x: (x['data_date'], x['provider']), reverse=True)[0]
    if any((x['percentile'] <= cfg['valuation_percentile_max']) != (chosen['percentile'] <= cfg['valuation_percentile_max']) for x in result if x['data_date'] == chosen['data_date']):
        return None, result, gaps + ['同数据日跨平台阈值结论冲突，未确认低估状态。']
    return chosen, result, gaps + ['使用平台报告分位和接口起点，未取得完整估值序列独立重算；实际样本数/成分处理待核。']


def price_metrics(bars, as_of, sector, cfg):
    usable = {}
    for bar in bars:
        day = bar.get('date', '')
        try:
            if datetime.fromisoformat(day).date() > as_of.astimezone(ZoneInfo('Asia/Shanghai' if sector['market'] == 'CN' else 'America/New_York')).date():
                continue
        except (ValueError, TypeError):
            continue
        value = number(bar.get('close'))
        if value is not None and value > 0:
            if day in usable:
                raise ValueError('duplicate price date')
            usable[day] = dict(bar, close=value)
    ordered = [usable[d] for d in sorted(usable)]
    cutoff = (as_of - timedelta(days=3653)).date().isoformat()
    ordered = [b for b in ordered if b['date'] >= cutoff]
    if len(ordered) < 21:
        return None, ['有效价格样本少于21日，不能核验反弹。']
    latest = ordered[-1]
    if not date_ok(latest['date'], as_of, sector['market'], cfg['max_data_age_days']):
        return None, ['最新价格数据日过旧；不把旧反弹冒充本轮信号。']
    close = latest['close']
    past = [b['close'] for b in ordered[:-1]]
    closes = [b['close'] for b in ordered]
    peak, drawdowns = 0, []
    for b in ordered:
        peak = max(peak, b['close'])
        if b.get('final', True):
            drawdowns.append(b['close'] / peak - 1)
    dd = close / max(closes) - 1
    comparable = all(b.get('basis') in ('index_points', 'adjusted_close') for b in ordered)
    enough = comparable and len(drawdowns) >= cfg['minimum_price_observations'] and (datetime.fromisoformat(latest['date']) - datetime.fromisoformat(ordered[0]['date'])).days >= 365.25 * cfg['minimum_history_years']
    ret = close / past[-1] - 1
    bounce = close / min(closes[-20:]) - 1
    ma5 = sum(closes[-5:]) / 5
    metrics = dict(data_date=latest['date'], close=close, currency=sector['currency'], final=latest.get('final', True),
        one_day_return=ret, rebound_from_20day_low=bounce, ma5=ma5,
        drawdown=dd, drawdown_percentile=rank(drawdowns, dd) if enough else None,
        history_start=ordered[0]['date'], history_end=next(b['date'] for b in reversed(ordered) if b.get('final',True)), history_observations=len(drawdowns), quote_at=latest.get('quote_at'),
        price_basis=latest.get('basis'), history_qualified=enough,
        rebound=ret >= cfg['rebound_day_return_min'] and bounce >= cfg['rebound_from_20day_low_min'] and close > ma5)
    return metrics, [] if enough else ['价格回撤历史不足五年/1260样本或复权不可比；仍可单独核验短期反弹。']


def process(sector, snapshot, bars, as_of, cfg, old, run_id, links):
    v, readings, vgaps = valuations(sector, snapshot, as_of, cfg)
    p, pgaps = price_metrics(bars, as_of, sector, cfg)
    state = deepcopy(old or {'channels': {}, 'history': []})
    messages = []
    # Price observations are context only; an old price channel cannot authorize output.
    legacy=state['channels'].get('price_drawdown')
    if legacy and not legacy.get('disabled_by_policy'):
        state['history'].append(dict(deepcopy(legacy),basis='price_drawdown',disabled_by_policy=cfg['valuation_policy_version']))
        legacy.update(active=False,disabled_by_policy=cfg['valuation_policy_version'])
    choices = []
    if v:
        choices.append(('valuation', v['percentile'], v['data_date'], v['percentile'] <= cfg['valuation_percentile_max']))
    for basis, percentile, day, low in choices:
        ch = state['channels'].setdefault(basis, {'active': False, 'release_days': [], 'alerts': {}})
        # Older observations can never rewind committed episode state.
        if ch.get('last_data_date') and day < ch['last_data_date']:
            continue
        ch['last_data_date'] = day
        if ch['active'] and percentile >= cfg['rearm_percentile'] and (not p or p['final'] or basis == 'valuation'):
            if day not in ch['release_days']:
                ch['release_days'].append(day)
            if len(ch['release_days']) >= cfg['rearm_sessions']:
                state['history'].append(deepcopy(ch))
                ch.update(active=False, release_days=[], alerts={})
        else:
            ch['release_days'] = []
        if low and not ch['active']:
            ch.update(active=True, episode_day=day, first_seen_at=as_of.isoformat(), release_days=[], alerts={})
        if not ch['active'] or not low:
            continue
        phases = ['entry'] if 'entry' not in ch['alerts'] else []
        previous_rebound = ch['alerts'].get('rebound')
        if p and p['data_date'] >= ch['episode_day']:
            if (p['rebound'] and not previous_rebound) or (previous_rebound and p['final'] and not previous_rebound.get('finalized', previous_rebound['confirmed']) and p['data_date'] >= previous_rebound.get('price_date', ch['episode_day'])):
                phases.append('rebound')
        for phase in phases:
            alert = ch['alerts'].get(phase)
            first = alert['published_at'] if alert else as_of.isoformat()
            aid = sector['key'] + '/' + basis + '/' + ch['episode_day'] + '/' + phase
            ch['alerts'][phase] = dict(published_at=first, confirmed=bool(p and p['final'] and p['rebound']), finalized=bool(p and p['final']), price_date=p['data_date'] if p else None, run_id=alert['run_id'] if alert else run_id)
            source_url = REPO + 'runs/' + ch['alerts'][phase]['run_id'] + '/sector-monitor.json#' + hashlib.sha256(aid.encode()).hexdigest()[:16]
            label = '估值低位观察'
            title = sector['name'] + '｜' + (('低位后反弹·收盘规则确认' if p['final'] and p['rebound'] else '盘中反弹·收盘未确认' if p['final'] else '低位后反弹·盘中待确认') if phase == 'rebound' else '首次纳入' + label + ('（盘中）' if basis=='price_drawdown' and not p['final'] else ''))
            explanation = '平台' + v['metric'] + '=%.4f，分位 %.2f%%，满足配置上限%.2f%%；数据日%s，接口给出的起点%s，未独立重算。' % (v['value'],v['percentile'] * 100,cfg['valuation_percentile_max'] * 100,v['data_date'],v['interface_start_date'])
            if p:explanation += '价格截至'+(p.get('quote_at') or p['data_date'])+'。'
            if phase == 'rebound':
                explanation += '价格单日 %.2f%%，距近20日最低收盘回升 %.2f%%；%s。' % (p['one_day_return'] * 100, p['rebound_from_20day_low'] * 100, '按收盘数据符合全部反弹规则' if p['final'] and p['rebound'] else '收盘未符合全部反弹规则，撤回盘中确认预期' if p['final'] else '盘中结果，未收盘确认')
            gaps = list(dict.fromkeys(vgaps + pgaps + ['低分位和反弹均不能证明市场底部或未来收益；历史窗口不代表全部市场历史。']))
            facts = dict(rule_version=cfg['rule_version'], sector_key=sector['key'], basis=basis, episode_day=ch['episode_day'], phase=phase, valuation=v, valuation_readings=readings, price=p, threshold=cfg['valuation_percentile_max'])
            messages.append(dict(id=hashlib.sha256(aid.encode()).hexdigest()[:24], layer=4, source_id=sector['id'],
                source_name='自分析·板块低位监控', source_role='analysis', original_url=source_url,
                available_url=REPO+'skills/finance-sector-monitor/references/methods.md', original_link_status='published_with_feed',
                source_links=[dict(source=n,url=u,kind='original') for n,u in sorted(set(links))],
                title=title, title_type='editorial', published_at=first, published_precision='second', source_timezone='UTC',
                updated_at=as_of.isoformat() if alert else None, data_period=ch['episode_day'],
                material_update=bool(alert),
                observed_at=as_of.isoformat(), retrieved_at=as_of.isoformat(), event_key='sector-low/'+aid,
                source_record_id=aid, record_kind='板块低位自分析', fact_values=facts,
                fact_summary=explanation, analysis='首次发现时间与数据日分别保留；未声称该信号首次在市场出现。',
                confidence=dict(score=80 if basis=='valuation' else 85, level='主要证据可核验',
                    reason='按所列平台原值/复权价格与固定规则筛查，缺失及口径限制另列。', scope='本次数据是否满足筛查规则；不是触底或上涨概率'),
                investment_impact=dict(assets=[sector['name'],sector['fund_exposure']], direction='进入观察；短期持续性待核',
                    mechanism='低估值可减轻估值约束，价格回撤/反弹可提示风险偏好变化；盈利下修、成分变化和流动性仍可能令低位持续或再创新低。',
                    horizon='数周至数季', conditions=['核对盈利/净资产质量、持仓和交易成本','反弹需后续交易日检验'],
                    priced_in='未知', invalidation=['历史口径或数据修订','盈利/净资产恶化','反弹失败并创新低']),
                impact_degree=dict(score=3 if phase=='rebound' else 2, asset_horizon=sector['name']+'；数周至数季', reason='规则触发的板块研究线索，不代表系统性拐点'),
                verification_status='partial' if basis=='valuation' or gaps else 'verified', gaps=gaps, is_background=False))
    state['last_valuation'] = v if v else state.get('last_valuation')
    state['last_price'] = p if p else state.get('last_price')
    return messages, state, dict(valuation=v, valuation_readings=readings, price=p, gaps=list(dict.fromkeys(vgaps+pgaps)))


class Audit:
    def __init__(self, run, replay=None):
        self.run = Path(run)
        self.replay = {} if replay else None
        if replay:
            for p in (Path(replay)/'artifacts/http').glob('*.json'):
                packet=json.loads(p.read_text())
                if packet.get('url') and ('body_base64' in packet or 'body' in packet):self.replay[packet['url']] = (p,packet)
        spec = importlib.util.spec_from_file_location('sector_audit',Path(__file__).with_name('run_logger.py'))
        self.log = importlib.util.module_from_spec(spec);spec.loader.exec_module(self.log)

    def event(self, kind, data):
        with self.log.locked(self.run):
            return self.log.append_unlocked(self.run, kind, data)

    def save(self, path, data):
        body = self.log.canonical(data)+b'\n'
        with self.log.locked(self.run):
            self.log.save_new(self.log.target(self.run,path),body)
            self.log.append_unlocked(self.run,'artifact_saved',dict(path=path,sha256=self.log.digest(body),bytes=len(body),metadata={'kind':'monitor evidence'},credential_review='public data / deterministic analysis'))
        return path

    def fetch(self, url, ids, parser):
        cid='monitor-'+uuid.uuid4().hex[:12];start=time.monotonic()
        self.event('tool_start',dict(call_id=cid,source_ids=ids,tool='sector_monitor.replay_evidence' if self.replay is not None else 'sector_monitor.public_GET',arguments=dict(url=url,timeout=20,request_sent=self.replay is None)))
        body=b'';status=None;headers={};data=None;error=None
        try:
            if self.replay is not None:
                origin,packet=self.replay[url];status=packet['http_status'];headers=packet.get('headers',{})
                body=base64.b64decode(packet['body_base64']) if 'body_base64' in packet else packet['body'].encode()
                self.event('note',dict(kind='validation_only_replay',origin=str(origin),sha256=hashlib.sha256(origin.read_bytes()).hexdigest()))
            else:
                try:
                    response=urlopen(Request(url,headers={'User-Agent':'FinancialResearchMonitor/1.0 (public data research)','Accept':'application/json,text/plain'}),timeout=20)
                except HTTPError as e:
                    response=e
                with response:
                    status=response.status;body=response.read();headers={k:v for k,v in response.headers.items() if k.lower() in ('date','content-type','retry-after')}
            if status!=200:
                raise ValueError('HTTP '+str(status))
            data=parser(body)
        except Exception as e:
            error=type(e).__name__+': '+str(e)
        path=self.save('artifacts/http/'+cid+'.json',dict(url=url,http_status=status,headers=headers,body_base64=base64.b64encode(body).decode(),body=body.decode('utf-8',errors='replace'),error=error,parsed=data))
        self.event('tool_end',dict(call_id=cid,status='error' if error else 'success',duration_ms=round((time.monotonic()-start)*1000),truncated=False,result_paths=[path]))
        return data,cid,path,error

    def skipped(self, url, ids, reason):
        cid='suspended-'+uuid.uuid4().hex[:12]
        self.event('tool_start',dict(call_id=cid,source_ids=ids,tool='sector_monitor.provider_suspension',arguments=dict(url=url,request_sent=False)))
        path=self.save('artifacts/http/'+cid+'.json',dict(request_sent=False,reason=reason))
        self.event('tool_end',dict(call_id=cid,status='error',duration_ms=0,truncated=False,result_paths=[path]))
        return None,cid,path,reason


def yahoo(body, sector, as_of):
    j=json.loads(body);result=(j.get('chart',{}).get('result') or [None])[0]
    if not result or j['chart'].get('error'):
        raise ValueError('Yahoo chart unavailable')
    if result['meta'].get('symbol','').upper()!=sector['price_symbol'].upper() or result['meta'].get('currency')!=sector['currency']:
        raise ValueError('price identity/currency mismatch')
    values=result['indicators'].get('adjclose',[{}])[0].get('adjclose',[])
    if len(values)!=len(result.get('timestamp',[])):
        raise ValueError('adjusted price history missing')
    local=as_of.astimezone(ZoneInfo('America/New_York'));out=[]
    trade=datetime.fromtimestamp(result['meta']['regularMarketTime'],timezone.utc).astimezone(ZoneInfo('America/New_York')) if result['meta'].get('regularMarketTime') else None
    if trade and trade>as_of+timedelta(minutes=5):raise ValueError('future quote timestamp')
    end_stamp=result['meta'].get('currentTradingPeriod',{}).get('regular',{}).get('end')
    session_end=datetime.fromtimestamp(end_stamp,timezone.utc).astimezone(ZoneInfo('America/New_York')) if end_stamp else None
    for i,(stamp,value) in enumerate(zip(result['timestamp'],values)):
        day=datetime.fromtimestamp(stamp,timezone.utc).astimezone(ZoneInfo('America/New_York')).date().isoformat()
        end=session_end if session_end and session_end.date().isoformat()==day else datetime.fromisoformat(day).replace(hour=16,tzinfo=ZoneInfo('America/New_York'))
        final=i<len(values)-1 or bool(trade and trade>=end)
        if number(value) is not None:
            out.append(dict(date=day,close=value,final=final,basis='adjusted_close',quote_at=trade.isoformat() if i==len(values)-1 and trade else None))
    return out


def sina(body, as_of):
    data=json.loads(body)
    if not isinstance(data,list) or not data:
        raise ValueError('Sina daily history unavailable')
    local=as_of.astimezone(ZoneInfo('Asia/Shanghai'))
    return [dict(date=r['day'][:10],close=r['close'],final=r['day'][:10]<local.date().isoformat(),basis='index_points') for r in data]


def tencent(body, as_of):
    out={};local=as_of.astimezone(ZoneInfo('Asia/Shanghai'))
    for symbol,text in re.findall(r'v_([a-z]{2}\d{6})="([^"]*)"',body.decode('gb18030',errors='replace')):
        fields=text.split('~')
        if len(fields)<35 or fields[2]!=symbol[-6:] or not re.fullmatch(r'\d{14}',fields[30]):continue
        stamp=datetime.strptime(fields[30],'%Y%m%d%H%M%S').replace(tzinfo=ZoneInfo('Asia/Shanghai'))
        if stamp>as_of.astimezone(ZoneInfo('Asia/Shanghai'))+timedelta(minutes=5):continue
        close=number(fields[3]);prev=number(fields[4])
        if close is None or close<=0 or prev is None or prev<=0:continue
        day=stamp.date().isoformat();final=(stamp.hour,stamp.minute)>=(15,0)
        out[symbol]=dict(date=day,close=close,previous_close=prev,quote_at=stamp.isoformat(),final=final,basis='index_points')
    if not out:raise ValueError('Tencent quote timestamp/identity unavailable')
    return out


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True);parser.add_argument('--previous',required=True)
    parser.add_argument('--universe',default=str(Path(__file__).resolve().parents[1]/'references/universe.json'))
    parser.add_argument('--replay-run',help='Offline validation only; its coverage is blocked from publication')
    args=parser.parse_args();cfg=load_config(args.universe);audit=Audit(args.run,args.replay_run)
    meta=json.loads((Path(args.run)/'manifest.json').read_text());run_id=meta['run_id']
    previous=json.loads(Path(args.previous).read_text()) if Path(args.previous).exists() else {'version':1,'sectors':{}}
    audit.save('inputs/previous-monitor-state.json',previous);audit.save('inputs/universe.json',cfg)
    ids=[s['id'] for s in cfg['watchlist']];snapshots={};evidence=[];links=[]
    urls={'danjuan':'https://danjuanfunds.com/djapi/index_eva/dj','funddb':'https://ai2.jiucaishuo.com/api/index-valuation?category=jx'}
    for provider,url in urls.items():
        def parse(body,provider=provider):
            value=json.loads(body)
            rows=value.get('data',{}).get('items') if provider=='danjuan' else value.get('rows')
            if not isinstance(rows,list):raise ValueError('valuation schema unavailable')
            return rows
        rows,cid,path,error=audit.fetch(url,ids,parse);snapshots[provider]=rows or [];evidence.append((cid,path));links.append((provider,url))
    cn=[s for s in cfg['watchlist'] if s['market']=='CN']
    quote_url='https://qt.gtimg.cn/q='+','.join(s['price_symbol'] for s in cn)
    quotes,qcid,qpath,qerror=audit.fetch(quote_url,[s['id'] for s in cn],lambda b:tencent(b,datetime.now(timezone.utc)))
    messages=[];coverage=[];diagnostics=[];routes=[];next_state=deepcopy(previous);suspended={}
    for sector in cfg['watchlist']:
        now=datetime.now(timezone.utc)
        if sector['market']=='CN':
            url='https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?'+urlencode(dict(symbol=sector['price_symbol'],scale=240,ma='no',datalen=1970))
            parse=lambda body:sina(body,datetime.now(timezone.utc))
        else:
            url='https://query1.finance.yahoo.com/v8/finance/chart/'+sector['price_symbol']+'?'+urlencode(dict(range='10y',interval='1d',events='div,splits'))
            parse=lambda body,sector=sector:yahoo(body,sector,datetime.now(timezone.utc))
        provider='sina' if sector['market']=='CN' else 'yahoo'
        bars,pcid,ppath,perror=audit.skipped(url,[sector['id']],suspended[provider]) if provider in suspended else audit.fetch(url,[sector['id']],parse)
        if perror and any(text in perror.lower() for text in ('http 429','http 403','http 401','proxy','certificate verify failed')):
            suspended[provider]='该提供方已限流/拒绝/共同网络失败，本轮停止扩大请求；原错误：'+perror
        if bars and sector['market']=='CN' and quotes and sector['price_symbol'] in quotes:
            quote=quotes[sector['price_symbol']]
            prior=[b for b in bars if b['date']<quote['date']]
            if quote['date']>=bars[-1]['date'] and not (quote['date']==bars[-1]['date'] and bars[-1].get('final') and not quote['final']) and prior and abs(float(prior[-1]['close'])/quote['previous_close']-1)<.005:
                bars=[b for b in bars if b['date']<quote['date']]+[quote]
        cid='calc-'+uuid.uuid4().hex[:12];start=time.monotonic()
        audit.event('tool_start',dict(call_id=cid,source_ids=[sector['id']],tool='sector_monitor.compute',arguments=dict(rule_version=cfg['rule_version'],sector=sector,valuation_artifacts=[p for _,p in evidence],price_artifact=ppath,quote_artifact=qpath if sector['market']=='CN' else None,previous_state='inputs/previous-monitor-state.json')))
        observed=datetime.now(timezone.utc)
        try:
            sector_links=links+[(('新浪指数日线' if sector['market']=='CN' else 'Yahoo复权ETF日线'),url)]
            if sector['market']=='CN':sector_links.append(('腾讯指数报价',quote_url))
            found,state,detail=process(sector,snapshots,bars or [],observed,cfg,previous.get('sectors',{}).get(sector['key']),run_id,sector_links)
            if sector['market']=='CN' and (not quotes or sector['price_symbol'] not in quotes):
                detail['gaps'].append('本对象本轮盘中报价未取得；反弹仅按实际日线截至日期判断。')
            next_state.setdefault('sectors',{})[sector['key']]=state
            error=None
        except Exception as e:
            found=[];detail={'valuation':None,'price':None,'gaps':['计算失败：'+str(e)]};error=str(e)
        path=audit.save('artifacts/calculations/'+cid+'.json',dict(sector=sector,observed_at=observed.isoformat(),detail=detail,messages=found,error=error))
        audit.event('tool_end',dict(call_id=cid,status='error' if error else 'success',duration_ms=round((time.monotonic()-start)*1000),truncated=False,result_paths=[path]))
        all_calls=[x for x,_ in evidence]+[pcid,cid];all_paths=[x for _,x in evidence]+[ppath,path]
        if sector['market']=='CN':all_calls.append(qcid);all_paths.append(qpath)
        for m in found:m['audit_refs']=dict(run_id=run_id,call_ids=all_calls,artifact_paths=all_paths)
        messages.extend(found);checked=observed.isoformat()
        components=[]
        for name,ok in [('估值历史分位',detail.get('valuation') is not None),('价格历史与反弹',detail.get('price') is not None)]:
            components.append(dict(component=name,status='content_collected' if found and ok else 'no_new_verified' if ok else 'access_limited_verified',checked_at=checked,checked_scope={'sector':sector['key'],'rule':cfg['rule_version']},reason='本轮规则、数据日与样本已核验；信号与缺口见计算证据。' if ok else '缺合格数据/历史窗口或计算失败；详见证据，不宣称未达低位。',evidence_call_ids=all_calls,evidence_paths=all_paths))
        status='content_collected' if found else 'no_new_verified' if all(c['status']=='no_new_verified' for c in components) else 'access_limited_verified'
        coverage.append(dict(source_id=sector['id'],source_name='自分析·'+sector['name'],status=status,checked_at=checked,checked_scope={'sector':sector['key'],'valuation_code':sector.get('valuation_code'),'price_symbol':sector['price_symbol']},reason='输出%d条规则触发；持续低位不重复；缺口保留。'%len(found),evidence_call_ids=all_calls,evidence_paths=all_paths,components=components))
        diagnostics.append(dict(source_id=sector['id'],sector=sector['name'],detail=detail,emitted=len(found)))
        routes.append(dict(run_id=run_id,source_id=sector['id'],component='价格历史与反弹',route_id=('sina:index-daily' if sector['market']=='CN' else 'yahoo:adjusted-chart')+':'+sector['price_symbol'],checked_at=checked,outcome='readable' if detail.get('price') else 'parse_error' if not perror else 'transport_error',root_cause='unknown',evidence_call_ids=[pcid,cid],evidence_paths=[ppath,path]))
        routes.append(dict(run_id=run_id,source_id=sector['id'],component='估值历史分位',route_id='valuation:platform-snapshots:'+str(sector.get('valuation_code')),checked_at=checked,outcome='readable' if detail.get('valuation') else 'data_unqualified',root_cause='unknown' if sector.get('valuation_code') else 'history_unavailable',evidence_call_ids=[x for x,_ in evidence]+[cid],evidence_paths=[x for _,x in evidence]+[path]))
    finished=datetime.now(timezone.utc).isoformat();next_state.update(version=1,rule_version=cfg['rule_version'],run_id=run_id,updated_at=finished,base_state_sha256=hashlib.sha256(audit.log.canonical(previous)).hexdigest())
    report='# 板块低位监控\n\n共核验%d个对象，输出%d条自分析消息。估值、价格、历史窗口与获取缺口逐项保留；这不是底部或收益预测。\n\n'%(len(coverage),len(messages))+'\n\n'.join(m['title']+'\n'+m['fact_summary'] for m in messages)
    for filename,payload in [('messages.json',messages),('coverage.json',dict(run_id=run_id,collection_complete=True,validation_only=bool(args.replay_run),sources=coverage)),('sector-monitor.json',dict(run_id=run_id,rule_version=cfg['rule_version'],validation_only=bool(args.replay_run),as_of=finished,analysis=diagnostics,messages=messages)),('sector-monitor-state.next.json',next_state),('route-observations.json',routes)]:
        audit.save('outputs/'+filename,payload)
    for filename,text in [('report.md',report),('run-summary.md','run_id='+run_id+'；全部对象收尾，未知/受限不等于无低位；监控状态尚未提交。')]:
        body=text.encode();path='outputs/'+filename
        with audit.log.locked(audit.run):
            audit.log.save_new(audit.log.target(audit.run,path),body)
            audit.log.append_unlocked(audit.run,'artifact_saved',dict(path=path,sha256=audit.log.digest(body),bytes=len(body),metadata={'kind':'monitor report'},credential_review='reviewed derived metrics'))
    print(json.dumps(dict(run_id=run_id,objects=len(coverage),alerts=len(messages),state_staged=True,run_dir=str(Path(args.run).resolve())),ensure_ascii=False))


if __name__=='__main__':
    main()
