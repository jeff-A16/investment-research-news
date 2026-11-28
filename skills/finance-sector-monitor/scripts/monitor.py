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

REPO = 'https://github.com/jeff-A16/investment-research-news/blob/main/'
_spec=importlib.util.spec_from_file_location('monitor_valuation_policy',Path(__file__).with_name('valuation_policy.py'))
policy_rules=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(policy_rules)
_hist_spec=importlib.util.spec_from_file_location('sector_valuation_history',Path(__file__).with_name('valuation_history.py'))
history_rules=importlib.util.module_from_spec(_hist_spec);_hist_spec.loader.exec_module(history_rules)


def load_config(path=None):
    path=Path(path or Path(__file__).parents[1]/'references/universe.json')
    cfg=json.loads(path.read_text());policy=policy_rules.load_policy(path.parent/cfg['valuation_policy_file'])
    cfg['valuation_percentile_max']=policy['max_percentile'];cfg['valuation_policy_version']=policy['version'];cfg['rule_version']='sector-'+policy['version'];cfg['minimum_valuation_observations']=policy['minimum_valuation_observations'];cfg['minimum_history_years']=policy['minimum_history_years'];cfg['alert_title_prefix']=policy.get('alert_title_prefix','')
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
            if code.startswith('CSI'):aliases.add(code[3:]+'CSI')
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
                method='computed_from_provider_full_series' if row.get('_from_full_history') else 'vendor_reported; not independently recomputed', flags=flags))
    eligible = [x for x in result if x['window_ok']]
    if not eligible:
        return None, result, gaps + ['未核得五年及以上可用估值分位窗口。']
    chosen = sorted(eligible, key=lambda x: (x['data_date'], x['provider']), reverse=True)[0]
    if any((x['percentile'] <= cfg['valuation_percentile_max']) != (chosen['percentile'] <= cfg['valuation_percentile_max']) for x in result if x['data_date'] == chosen['data_date']):
        return None, result, gaps + ['同数据日跨平台阈值结论冲突，未确认低估状态。']
    return chosen, result, gaps + (['分位及样本起止由已读取完整估值序列重算；指数成分和财务口径仍以提供方定义为准。'] if chosen.get('method')=='computed_from_provider_full_series' else ['使用平台报告分位和接口起点，未取得完整估值序列独立重算；实际样本数/成分处理待核。'])


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


def process(sector,snapshot,bars,as_of,cfg,old,run_id,links,history=None,cause=None):
    v,readings,gaps=valuations(sector,snapshot,as_of,cfg)
    state=deepcopy(old or {'channels':{},'history':[]});state.setdefault('channels',{});state.setdefault('history',[])
    legacy=state['channels'].get('price_drawdown')
    if legacy and not legacy.get('disabled_by_policy'):
        state['history'].append(dict(deepcopy(legacy),basis='price_drawdown',disabled_by_policy=cfg['valuation_policy_version']))
        legacy.update(active=False,disabled_by_policy=cfg['valuation_policy_version'])
    detail=dict(valuation=v,valuation_readings=readings,valuation_history=None,low_cause=None,history_status='not_required',gaps=gaps)
    if v is None:
        detail['history_status']='prerequisite_unqualified';return [],state,detail
    ch=state['channels'].setdefault('valuation',dict(active=False,release_days=[],alerts={}))
    ch.setdefault('release_days',[]);ch.setdefault('alerts',{});ch.setdefault('active',False)
    ch['rebound_notifications_disabled_by']=cfg['valuation_policy_version']
    low=v['percentile']<=cfg['valuation_percentile_max'];day=v['data_date']
    if ch['active'] and v['percentile']>=cfg['rearm_percentile']:
        if day not in ch['release_days']:ch['release_days'].append(day)
        if len(ch['release_days'])>=cfg['rearm_sessions']:
            state['history'].append(deepcopy(ch));ch.update(active=False,release_days=[],alerts={})
    else:ch['release_days']=[]
    state['last_valuation']=v
    if not low:return [],state,detail
    try:
        summary=history_rules.summarize(history,v,as_of,cfg['minimum_history_years'],cfg['minimum_valuation_observations'])
        detail.update(valuation_history=summary,history_status='verified')
    except (ValueError,TypeError,KeyError) as exc:
        detail['history_status']='unqualified';detail['gaps']=gaps+['历史最低值未核实：'+str(exc)];return [],state,detail
    if summary['current_percentile']>cfg['valuation_percentile_max']:
        detail['gaps']=gaps+['完整历史分位超过配置阈值；不以当前/最低比例替代分位门槛。'];return [],state,detail
    if not ch['active']:ch.update(active=True,episode_day=day,first_seen_at=as_of.isoformat(),release_days=[],alerts={})
    paragraph=history_rules.data_cause(summary);cause_refs=[]
    if isinstance(cause,dict) and cause.get('metric')==v['metric'] and cause.get('data_date')==day and cause.get('analysis') and cause.get('evidence'):
        paragraph=' '.join(str(cause['analysis']).split());cause_refs=cause['evidence']
    detail['low_cause']=paragraph
    if 'entry' in ch['alerts']:return [],state,detail
    aid=sector['key']+'/valuation/'+ch['episode_day']+'/entry';first=as_of.isoformat()
    ch['alerts']['entry']=dict(published_at=first,confirmed=False,finalized=True,price_date=None,run_id=run_id)
    source_url=REPO+'runs/'+run_id+'/sector-monitor.json#'+hashlib.sha256(aid.encode()).hexdigest()[:16]
    facts=dict(rule_version=cfg['rule_version'],sector_key=sector['key'],sector_name=sector['name'],basis='valuation',episode_day=ch['episode_day'],phase='entry',valuation=v,valuation_readings=readings,valuation_history=summary,low_cause=paragraph,cause_evidence=cause_refs,threshold=cfg['valuation_percentile_max'])
    message=dict(id=hashlib.sha256(aid.encode()).hexdigest()[:24],layer=4,source_id=sector['id'],source_name='自分析·板块低位监控',source_role='analysis',original_url=source_url,available_url=REPO+'skills/finance-sector-monitor/references/methods.md',original_link_status='published_with_feed',source_links=[dict(source=n,url=u,kind='original') for n,u in sorted(set(links))],title=policy_rules.alert_title(sector['name'],cfg),title_type='editorial',published_at=first,published_precision='second',source_timezone='UTC',updated_at=None,data_period=ch['episode_day'],material_update=False,observed_at=first,retrieved_at=first,event_key='sector-low/'+aid,source_record_id=aid,record_kind='板块低位自分析',fact_values=facts,fact_summary='同口径历史最低%.4f、当前%.4f，为最低的%.2f%%。'%(summary['minimum'],summary['current'],summary['current_to_min_percent']),analysis=paragraph,confidence=dict(score=80,level='主要证据可核验',reason='估值分位及完整历史序列已核对；原因分析区分观察与推断。',scope='数据与筛选条件，不是触底或收益概率'),investment_impact=dict(assets=[sector['name']],direction='估值处低位，价格方向未知',mechanism=paragraph,horizon='中长期',conditions=['盈利和流动性变化可能改变低位状态'],priced_in='未知',invalidation=['数据、口径或成分修订']),impact_degree=dict(score=2,reason='低估值观察，不能确认市场底部。'),verification_status='partial',gaps=list(dict.fromkeys(gaps)),is_background=False)
    return [message],state,detail


class Audit:
    def __init__(self, run, replay=None):
        self.run = Path(run)
        self.replay = {} if replay else None
        if replay:
            for p in (Path(replay)/'artifacts/http').glob('*.json'):
                packet=json.loads(p.read_text())
                if packet.get('url') and ('body_base64' in packet or 'body' in packet):self.replay[packet.get('request_key') or packet['url']] = (p,packet)
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

    def fetch(self, url, ids, parser, payload=None):
        key=url if payload is None else url+'?'+urlencode({k:payload[k] for k in ('gu_code','pe_category','year') if k in payload})
        cid='monitor-'+uuid.uuid4().hex[:12];start=time.monotonic()
        self.event('tool_start',dict(call_id=cid,source_ids=ids,tool='sector_monitor.replay_evidence' if self.replay is not None else 'sector_monitor.public_GET',arguments=dict(url=url,method='GET' if payload is None else 'POST',json=payload,timeout=20,request_sent=self.replay is None)))
        body=b'';status=None;headers={};data=None;error=None
        try:
            if self.replay is not None:
                origin,packet=self.replay[key];status=packet['http_status'];headers=packet.get('headers',{})
                body=base64.b64decode(packet['body_base64']) if 'body_base64' in packet else packet['body'].encode()
                self.event('note',dict(kind='validation_only_replay',origin=str(origin),sha256=hashlib.sha256(origin.read_bytes()).hexdigest()))
            else:
                try:
                    response=urlopen(Request(url,data=json.dumps(payload).encode() if payload is not None else None,headers={'User-Agent':'FinancialResearchMonitor/1.0 (public data research)','Accept':'application/json,text/plain','Content-Type':'application/json'}),timeout=20)
                except HTTPError as e:
                    response=e
                with response:
                    status=response.status;body=response.read();headers={k:v for k,v in response.headers.items() if k.lower() in ('date','content-type','retry-after')}
            if status!=200:
                raise ValueError('HTTP '+str(status))
            data=parser(body)
        except Exception as e:
            error=type(e).__name__+': '+str(e)
        path=self.save('artifacts/http/'+cid+'.json',dict(url=url,request_key=key,request_payload=payload,http_status=status,headers=headers,body_base64=base64.b64encode(body).decode(),body=body.decode('utf-8',errors='replace'),error=error,parsed=data))
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


def load_causes(path,run,run_id):
    if not path or not Path(path).is_file():return {}
    supplied=json.loads(Path(path).read_text());messages={}
    for skill in ('finance-primary-intelligence','finance-market-research','finance-social-sentiment'):
        file=Path(run).parent/skill/'outputs/messages.json'
        if file.is_file():
            for row in json.loads(file.read_text()):
                if row.get('audit_refs',{}).get('run_id')==run_id:messages[row['id']]=row
    reviewed={}
    for key,row in supplied.items():
        evidence=row.get('evidence') or [];valid=[]
        for ref in evidence:
            msg=messages.get(ref.get('message_id'))
            if not msg:continue
            urls={msg.get('original_url'),msg.get('available_url')}|{r.get('url') for r in msg.get('source_links',[])}
            if ref.get('url') in urls:valid.append(dict(message_id=msg['id'],source_id=msg['source_id'],url=ref['url'],audit_refs=msg['audit_refs']))
        if valid and row.get('analysis') and row.get('metric') in ('PE','PB') and row.get('data_date'):
            reviewed[key]=dict(row,evidence=valid,analysis=' '.join(str(row['analysis']).split()))
    return reviewed


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True);parser.add_argument('--previous',required=True)
    parser.add_argument('--universe',default=str(Path(__file__).resolve().parents[1]/'references/universe.json'))
    parser.add_argument('--causes-file',help="Optional sector-specific analysis grounded in this run's three-tier messages")
    parser.add_argument('--replay-run',help='Offline validation only; blocked from production publication')
    args=parser.parse_args();cfg=load_config(args.universe);audit=Audit(args.run,args.replay_run)
    meta=json.loads((Path(args.run)/'manifest.json').read_text());run_id=meta['run_id']
    previous=json.loads(Path(args.previous).read_text()) if Path(args.previous).exists() else {'version':1,'sectors':{}}
    audit.save('inputs/previous-monitor-state.json',previous);audit.save('inputs/universe.json',cfg)
    cause_path=args.causes_file or str(Path(args.run)/'inputs/low-causes.json')
    causes=load_causes(cause_path,args.run,run_id);audit.save('inputs/low-causes-reviewed.json',causes)
    ids=[s['id'] for s in cfg['watchlist']];snapshots={};evidence=[];links=[]
    urls={'danjuan':'https://danjuanfunds.com/djapi/index_eva/dj','funddb':'https://ai2.jiucaishuo.com/api/index-valuation?category=jx'}
    for provider,url in urls.items():
        def parse(body,provider=provider):
            value=json.loads(body);rows=value.get('data',{}).get('items') if provider=='danjuan' else value.get('rows')
            if not isinstance(rows,list):raise ValueError('valuation schema unavailable')
            return rows
        rows,cid,path,error=audit.fetch(url,ids,parse);snapshots[provider]=rows or [];evidence.append((cid,path));links.append((provider,url))
    messages=[];coverage=[];diagnostics=[];routes=[];next_state=deepcopy(previous);suspended=None
    history_url='https://api.jiucaishuo.com/v2/guzhi/newtubiaolinedata'
    for sector in cfg['watchlist']:
        now=datetime.now(timezone.utc);v,_,_=valuations(sector,snapshots,now,cfg);history=None;hevidence=[];herror=None
        code=history_rules.provider_code(sector.get('valuation_code'));requested=bool(code);local_snapshots=deepcopy(snapshots)
        if requested and code:
            if suspended:
                _,hid,hpath,herror=audit.skipped(history_url,[sector['id']],suspended);hevidence.append((hid,hpath))
            else:
                payload=history_rules.request_payload(code,sector['metric'])
                history,hid,hpath,herror=audit.fetch(history_url,[sector['id']],lambda b:history_rules.parse_history(b,code,sector['metric']),payload);hevidence.append((hid,hpath))
                if herror and any(x in herror.lower() for x in ('http 429','http 403','http 401','proxy','certificate verify failed')):suspended='该历史提供方本轮已拒绝/限流/共同网络失败，停止扩大请求；'+herror
                if herror and any(x in herror.lower() for x in ('登录','权限','订阅','unauthorized','forbidden')):suspended='该历史提供方存在认证/授权限制，本轮停止；'+herror
                if herror and not suspended and 'requested valuation series missing' in herror:
                    payload=history_rules.request_payload(code,sector['metric'],year=10)
                    history,hid,hpath,herror=audit.fetch(history_url,[sector['id']],lambda b:history_rules.parse_history(b,code,sector['metric']),payload);hevidence.append((hid,hpath))
        if history:
            try:
                prior_row=next((r for r in snapshots.get('funddb',[]) if str(r.get('code','')).upper()==code),{})
                flags={key:prior_row.get(key) for key in ('stale','insufficient','not_applicable')}
                derived=history_rules.snapshot_row(history,now,cfg['max_data_age_days'],cfg['minimum_history_years'],cfg['minimum_valuation_observations'],flags)
                local_snapshots['funddb']=[r for r in snapshots.get('funddb',[]) if str(r.get('code','')).upper()!=code]+[derived]
            except (ValueError,TypeError,KeyError) as exc:history=None;herror='history/current qualification: '+str(exc)
        if herror and any(x in herror.lower() for x in ('http 429','http 403','http 401','登录','权限','订阅','unauthorized','forbidden')):suspended='该历史提供方存在拒绝/限流/权限限制，本轮停止；'+herror
        cid='calc-'+uuid.uuid4().hex[:12];start=time.monotonic();audit.event('tool_start',dict(call_id=cid,source_ids=[sector['id']],tool='sector_monitor.compute',arguments=dict(rule_version=cfg['rule_version'],sector=sector,valuation_artifacts=[p for _,p in evidence],history_artifacts=[p for _,p in hevidence],cause_input='inputs/low-causes-reviewed.json',previous_state='inputs/previous-monitor-state.json')))
        observed=datetime.now(timezone.utc)
        try:
            sector_links=links+([('韭圈儿同口径完整估值/价格历史 '+str(code)+' '+sector['metric'],history_url)] if hevidence else [])
            found,state,detail=process(sector,local_snapshots,[],observed,cfg,previous.get('sectors',{}).get(sector['key']),run_id,sector_links,history,causes.get(sector['key']));next_state.setdefault('sectors',{})[sector['key']]=state;error=None
        except Exception as exc:
            found=[];detail={'valuation':v,'valuation_history':None,'low_cause':None,'history_status':'unqualified','gaps':['计算失败：'+str(exc)]};error=str(exc)
        path=audit.save('artifacts/calculations/'+sector['id']+'.json',dict(sector=sector,detail=detail,messages=found,error=error))
        audit.event('tool_end',dict(call_id=cid,status='error' if error else 'success',duration_ms=round((time.monotonic()-start)*1000),truncated=False,result_paths=[path]))
        all_calls=[x for x,_ in evidence+hevidence]+[cid];all_paths=[x for _,x in evidence+hevidence]+[path]
        for m in found:m['audit_refs']=dict(run_id=run_id,call_ids=all_calls,artifact_paths=all_paths)
        messages.extend(found);checked=observed.isoformat();has_v=detail.get('valuation') is not None;has_h=detail.get('history_status') in ('verified','not_required') and has_v
        components=[]
        for name,ok in [('估值历史分位',has_v),('历史最低值与原因分析',has_h)]:
            components.append(dict(component=name,status='content_collected' if found and ok else 'no_new_verified' if ok else 'access_limited_verified',checked_at=checked,checked_scope={'sector':sector['key'],'rule':cfg['rule_version'],'minimum_comparison_required':requested},reason='已核真实全历史估值及其独立分位；不足/冲突不发。低位时需最低值、同口径当前值与原因完整，持续低位不重复。' if ok else '缺合格当前估值、同口径完整历史或原因依据；保留实际错误/缺口，不称没有低位。',evidence_call_ids=all_calls,evidence_paths=all_paths))
        status='content_collected' if found else 'no_new_verified' if all(c['status']=='no_new_verified' for c in components) else 'access_limited_verified'
        coverage.append(dict(source_id=sector['id'],source_name='自分析·'+sector['name'],status=status,checked_at=checked,checked_scope={'sector':sector['key'],'valuation_code':sector.get('valuation_code')},reason='仅估值低位进入提醒；最低值/当前值/比例/原因完整才可发布，不发反弹或收盘阶段通知。',evidence_call_ids=all_calls,evidence_paths=all_paths,components=components))
        diagnostics.append(dict(source_id=sector['id'],sector=sector['name'],detail=detail,emitted=len(found)))
        routes.append(dict(run_id=run_id,source_id=sector['id'],component='估值历史分位',route_id='valuation:platform-snapshots:'+str(sector.get('valuation_code')),checked_at=checked,outcome='readable' if has_v else 'data_unqualified',root_cause='unknown' if sector.get('valuation_code') else 'history_unavailable',evidence_call_ids=[x for x,_ in evidence]+[cid],evidence_paths=[x for _,x in evidence]+[path]))
        routes.append(dict(run_id=run_id,source_id=sector['id'],component='历史最低值与原因分析',route_id='valuation:funddb-full-history:'+str(code)+':'+sector['metric'],url=history_url,adapter='public_anonymous_POST',checked_at=checked,outcome='no_new' if has_h and not requested else 'readable' if has_h else 'data_unqualified' if herror and herror.startswith('history/current qualification:') else 'transport_error' if herror else 'data_unqualified',root_cause='data_quality' if herror and herror.startswith('history/current qualification:') else 'unknown' if code else 'history_unavailable',evidence_call_ids=[x for x,_ in hevidence]+[cid],evidence_paths=[x for _,x in hevidence]+[path]))
    finished=datetime.now(timezone.utc).isoformat();next_state.update(version=1,validation_only=bool(args.replay_run),rule_version=cfg['rule_version'],run_id=run_id,updated_at=finished,base_state_sha256=hashlib.sha256(audit.log.canonical(previous)).hexdigest())
    report='# 板块低位监控\n\n共核验%d个对象，输出%d条低位自分析；不产生反弹阶段提醒。\n\n'%(len(coverage),len(messages))+'\n\n'.join(m['title']+'\n'+m['fact_summary']+'\n'+m['analysis'] for m in messages)
    for filename,payload in [('messages.json',messages),('coverage.json',dict(run_id=run_id,collection_complete=True,validation_only=bool(args.replay_run),sources=coverage)),('sector-monitor.json',dict(run_id=run_id,rule_version=cfg['rule_version'],validation_only=bool(args.replay_run),as_of=finished,analysis=diagnostics,messages=messages)),('sector-monitor-state.next.json',next_state),('route-observations.json',routes)]:audit.save('outputs/'+filename,payload)
    for filename,text in [('report.md',report),('run-summary.md','run_id='+run_id+'；全部对象收尾；最低值/当前值/比例/原因需完整；状态尚未提交。')]:
        body=text.encode();path='outputs/'+filename
        with audit.log.locked(audit.run):
            audit.log.save_new(audit.log.target(audit.run,path),body)
            audit.log.append_unlocked(audit.run,'artifact_saved',dict(path=path,sha256=audit.log.digest(body),bytes=len(body),metadata={'kind':'monitor report'},credential_review='reviewed derived metrics'))
    print(json.dumps(dict(run_id=run_id,objects=len(coverage),alerts=len(messages),state_staged=True,run_dir=str(Path(args.run).resolve())),ensure_ascii=False))


if __name__=='__main__':main()
