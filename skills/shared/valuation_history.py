"""Public index-valuation series, with auditable full-window minima.

Anonymous POST wire fields follow the public FundDB web client, as recorded in
AKShare release 1.15.18 (pinned implementation reference retained in run logs).
No login, account token, subscription or access-denial bypass is used.
"""
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
import hashlib,json,math,statistics,time

CHECKSUM_FIELDS=(('tirgkjfs', 0, 2), ('abiokytke', 21, 23), ('u54rg5d', 2, 4), ('kf54ge7', 31, 32), ('tiklsktr4', 1, 2), ('lksytkjh', 17, 21), ('sbnoywr', 23, 25), ('bgd7h8tyu54', 6, 8), ('y654b5fs3tr', 11, 12), ('bioduytlw', 5, 6), ('bd4uy742', 26, 27), ('h67456y', 16, 19), ('bvytikwqjk', 6, 8), ('ngd4uy551', 17, 19), ('bgiuytkw', 9, 11), ('nd354uy4752', 30, 31), ('ghtoiutkmlg', 11, 14), ('bd24y6421f', 24, 26), ('tbvdiuytk', 16, 17), ('ibvytiqjek', 14, 16), ('jnhf8u5231', 9, 11), ('fjlkatj', 2, 5), ('hy5641d321t', 25, 27), ('iogojti', 25, 26), ('ngd4yut78', 12, 14), ('nkjhrew', 26, 27), ('yt447e13f', 8, 9), ('n3bf4uj7y7', 18, 19), ('nbf4uj7y432', 21, 23), ('yi854tew', 29, 31), ('h13ey474', 29, 32), ('quikgdky', 27, 29))

def number(value):
    if isinstance(value,bool):return None
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except (ValueError,TypeError):return None

def provider_code(code):
    if not code:return None
    if code.startswith(('SH','SZ')):return code[2:]+'.'+code[:2]
    if code.startswith('CSI'):return code[3:]+'.CSI'
    return {'NDX':'NDX.GI','SP500':'SPX.GI'}.get(code)

def request_payload(code,metric,year=-1,now_ms=None):
    args=dict(gu_code=code,pe_category=metric.lower(),year=year,ver='new',type='pc',version='2.2.7',authtoken='',act_time=str(now_ms if now_ms is not None else round(time.time()*1000)))
    text=''.join(str(args[k]) for k in ('act_time','authtoken','gu_code','pe_category','type','ver','version','year'))+'EWf45rlv#kfsr@k#gfksgkr'
    digest=hashlib.md5(text.encode()).hexdigest()
    args.update({key:digest[start:end] for key,start,end in CHECKSUM_FIELDS})
    return args

def parse_history(body,code,metric):
    packet=json.loads(body)
    if packet.get('code')!=0:raise ValueError('history business error: '+str(packet.get('message')))
    data=packet.get('data') or {};series=(data.get('tubiao') or {}).get('series') or []
    names={'PE':'市盈率','PB':'市净率'};wanted=names[metric]
    matches=[s for s in series if s.get('name')==wanted]
    if len(matches)!=1:raise ValueError('requested valuation series missing/ambiguous')
    values=matches[0].get('data') or [];prices=next((s.get('data') or [] for s in series if '收盘价' in s.get('name','')),[])
    def convert(rows):
        out={}
        for pair in rows:
            if not isinstance(pair,list) or len(pair)!=2:raise ValueError('bad history row')
            stamp,value=pair;value=number(value)
            if value is None or value<=0:continue
            day=datetime.fromtimestamp(float(stamp)/1000,timezone.utc).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
            if day in out and out[day]!=value:raise ValueError('conflicting history dates')
            out[day]=value
        return out
    points=convert(values);quotes=convert(prices)
    if not points:raise ValueError('empty positive valuation history')
    return dict(provider='funddb',code=code,metric=metric,points=[dict(date=d,value=v,price=quotes.get(d)) for d,v in sorted(points.items())],source_reported_value=number(data.get('new_pe')),raw_rows=len(values),excluded_rows=len(values)-len(points),series_sha256=hashlib.sha256(json.dumps(values,separators=(',',':')).encode()).hexdigest())

def snapshot_row(history,as_of,max_age_days=4,minimum_years=5,minimum_observations=1000,flags=None):
    if any((flags or {}).values()):raise ValueError('history provider quality flags')
    rows=[r for r in history['points'] if r['date']<=as_of.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()]
    if not rows:raise ValueError('current history observation missing')
    day=rows[-1]['date'];age=(as_of.astimezone(ZoneInfo('Asia/Shanghai')).date()-datetime.fromisoformat(day).date()).days
    if age<0 or age>max_age_days or (datetime.fromisoformat(day)-datetime.fromisoformat(rows[0]['date'])).days/365.25<minimum_years or len(rows)<minimum_observations:raise ValueError('current history date/window unqualified')
    value=rows[-1]['value'];rank=(sum(x['value']<value for x in rows)+.5*sum(x['value']==value for x in rows))/len(rows)
    metric=history['metric'].lower();row=dict(code=history['code'],update_time=day,start_date=rows[0]['date'],_from_full_history=True)
    row.update({metric:value,metric+'_pct':rank*100});row.update(flags or {})
    return row

def summarize(history,valuation,as_of,minimum_years=5,minimum_observations=1000):
    if not history or history.get('metric')!=valuation['metric']:raise ValueError('history metric mismatch')
    rows=[r for r in history['points'] if r['date']<=as_of.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()]
    if not rows or rows[-1]['date']!=valuation['data_date']:raise ValueError('history/current data day mismatch')
    latest=rows[-1];first=rows[0];years=(datetime.fromisoformat(latest['date'])-datetime.fromisoformat(first['date'])).days/365.25
    if years<minimum_years or len(rows)<minimum_observations:raise ValueError('valuation history too short')
    if abs(latest['value']/valuation['value']-1)>.005:raise ValueError('current/history source values conflict')
    low=min(rows,key=lambda r:r['value']);pct=(sum(r['value']<latest['value'] for r in rows)+.5*sum(r['value']==latest['value'] for r in rows))/len(rows)
    target=datetime.fromisoformat(latest['date'])-timedelta(days=365)
    baseline=min(rows,key=lambda r:abs((datetime.fromisoformat(r['date'])-target).days))
    year=None
    if abs((datetime.fromisoformat(baseline['date'])-target).days)<=7:
        year=dict(date=baseline['date'],metric_value=baseline['value'],metric_change=latest['value']/baseline['value']-1)
        if latest.get('price') and baseline.get('price'):
            year.update(price=baseline['price'],current_price=latest['price'],price_change=latest['price']/baseline['price']-1,denominator_proxy_change=(latest['price']/latest['value'])/(baseline['price']/baseline['value'])-1)
    return dict(provider=history['provider'],code=history['code'],metric=history['metric'],window_start=first['date'],window_end=latest['date'],observations=len(rows),minimum=low['value'],minimum_date=low['date'],current=latest['value'],current_date=latest['date'],current_to_min_percent=100*latest['value']/low['value'],current_percentile=pct,series_sha256=history['series_sha256'],verified_from_full_series=True,one_year=year)

def data_cause(summary):
    metric=summary['metric'];year=summary.get('one_year');noun='盈利' if metric=='PE' else '净资产'
    if year and 'price_change' in year:
        def move(v):return ('上升' if v>=0 else '下降')+'%.2f%%'%abs(v*100)
        price,ratio,denom=year['price_change'],year['metric_change'],year['denominator_proxy_change']
        if ratio>=0:reason='当前估值虽处历史低区间，近一年却未继续压缩，不能把低位状态直接归因于近期下跌'
        elif price<0 and denom>=0:reason='价格回落与'+noun+'分母代理改善共同压低估值'
        elif price<0 and denom<0:reason='价格降幅超过'+noun+'分母代理的降幅，使估值走低'
        elif ratio<0:reason=noun+'分母代理增长快于价格，使估值走低'
        else:reason='当前估值虽处历史低区间，近一年却未继续压缩，低位状态不能直接归因于近期下跌'
        return '近一年指数价格'+move(price)+'，'+metric+move(ratio)+'；按同口径价格/'+metric+'反推的'+noun+'代理'+move(denom)+'，因此'+reason+'。该代理受指数成分和口径调整影响，不能当作企业实际'+noun+'增减，也不能据此确认更深层驱动因素。'
    if year:return '近一年'+metric+('下降' if year['metric_change']<0 else '上升')+'%.2f%%，当前低位反映估值倍数处于历史较低区间；缺少同口径价格和财务序列，尚不能确认价格回落、'+noun+'变化及成分调整各自的贡献。'%abs(year['metric_change']*100)
    return '当前'+metric+'低于样本区间的大多数观察值；仅凭估值序列无法确认价格、'+noun+'或成分调整谁是主因，原因仍待对应经营及市场证据核实。'
