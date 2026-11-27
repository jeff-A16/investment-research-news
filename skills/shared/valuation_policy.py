"""One publication policy shared by collector, publisher and packaged Skills."""
from datetime import datetime,timezone
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo


def number(x):
    if isinstance(x,bool):return None
    try:
        v=float(x)
        return v if math.isfinite(v) else None
    except (TypeError,ValueError):return None


def load_policy(path):
    policy=json.loads(Path(path).read_text())
    limit=number(policy.get('max_percentile'))
    if limit is None or not 0<=limit<=1:raise ValueError('Invalid valuation percentile policy')
    if policy.get('required_analysis_basis')!='valuation' or policy.get('independent_price_alerts') is not False:
        raise ValueError('Only valuation-qualified alerts are authorized')
    policy['max_percentile']=limit
    return policy


def qualified(reading,policy):
    if not isinstance(reading,dict):return False,'valuation_missing'
    value,pct=number(reading.get('value')),number(reading.get('percentile'))
    if value is None or value<=0 or pct is None or not 0<=pct<=1:return False,'invalid_valuation_or_percentile'
    if reading.get('window_ok') is not True:return False,'history_window_unqualified'
    flags=reading.get('flags') or {}
    if not isinstance(flags,dict) or any(flags.values()):return False,'valuation_quality_flag'
    try:datetime.fromisoformat(reading['data_date'])
    except (ValueError,TypeError,KeyError):return False,'valuation_date_unknown'
    if pct>policy['max_percentile']:return False,'above_configured_valuation_threshold'
    return True,'valuation_threshold_matched'


def eligible(message,policy):
    if message.get('publication_status')=='withdrawn':return False,'withdrawn_by_policy_correction'
    if message.get('layer')==4 or message.get('record_kind')=='板块低位自分析':
        facts=message.get('fact_values') or {}
        if facts.get('basis')!=policy['required_analysis_basis']:return False,'alternate_price_trigger_not_authorized'
        return qualified(facts.get('valuation'),policy)
    if message.get('record_kind')=='指数估值快照' or (message.get('source_id') in ('M26','M27') and message.get('source_role')=='vendor_data'):
        reading=message.get('valuation_screen') or {}
        if reading.get('data_date')!=message.get('data_period'):return False,'snapshot_data_date_unqualified'
        return qualified(message.get('valuation_screen'),policy)
    return True,'not_a_valuation_alert_or_snapshot'


def code(value):
    text=re.sub('[^A-Z0-9]','',str(value).upper())
    match=re.fullmatch(r'(\d{6})(SH|SZ)',text)
    return match[2]+match[1] if match else text


def snapshot_metadata(message,packets,policy):
    """Use the record's own API evidence, with matching code, date and values."""
    if message.get('valuation_screen'):return message['valuation_screen']
    if message.get('source_id') not in ('M26','M27'):return None
    text=(message.get('fact_values') or {}).get('fields','')
    metric='pb' if re.search('银行|证券|地产',message.get('title','')) else 'pe'
    val=re.search(r'(?<![A-Z])'+metric.upper()+r'\s*=\s*([0-9.eE+\-]+)',text)
    pct=re.search(metric.upper()+r'分位\s*=\s*([0-9.eE+\-]+)',text)
    if not val or not pct:return None
    expected_value,expected_pct=number(val[1]),number(pct[1])
    provider='danjuan_valuation' if message['source_id']=='M26' else 'funddb_valuation'
    for packet in packets:
        if packet.get('provider')!=provider:continue
        data=packet.get('data') or {};rows=data.get('data',{}).get('items',[]) if provider=='danjuan_valuation' else data.get('rows',[])
        for row in rows:
            if code(row.get('index_code') or row.get('code'))!=code(message.get('source_record_id')):continue
            value=number(row.get(metric));raw_pct=number(row.get(metric+('_percentile' if provider=='danjuan_valuation' else '_pct')))
            if value!=expected_value or raw_pct!=expected_pct:continue
            try:
                day=datetime.fromtimestamp(row['ts']/1000,timezone.utc).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat() if provider=='danjuan_valuation' else row['update_time'][:10]
                start=datetime.fromtimestamp(row['begin_at']/1000,timezone.utc).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat() if provider=='danjuan_valuation' and row.get('begin_at') else row.get('start_date')
                years=(datetime.fromisoformat(day)-datetime.fromisoformat(start)).days/365.25 if start else None
            except (KeyError,ValueError,TypeError,OverflowError):continue
            if day!=message.get('data_period'):continue
            return dict(provider=provider,metric=metric.upper(),value=value,percentile=raw_pct if provider=='danjuan_valuation' else raw_pct/100 if raw_pct is not None else None,raw_percentile=raw_pct,raw_unit='0—1' if provider=='danjuan_valuation' else '0—100',data_date=day,interface_start_date=start,window_ok=years is not None and years>=policy['minimum_history_years'],flags={k:row.get(k) for k in ('stale','insufficient','not_applicable')},method='vendor_reported; matched original API evidence, not independently recomputed')
    return None
