#!/usr/bin/env python3
"""Merge three executed research-skill outputs into a safe, stable RSS 2.0 feed."""
import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import hashlib
import html
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

DEPENDENCIES = {1:'finance-primary-intelligence',2:'finance-market-research',3:'finance-social-sentiment'}
PUBLIC_FIELDS = ('id','layer','source_id','source_name','source_role','original_url','available_url',
 'original_link_status','title','title_type','published_at','published_precision','source_timezone',
 'updated_at','event_at','data_period','observed_at','retrieved_at','feed_published_at',
 'publication_time_note','fact_summary','analysis','confidence','investment_impact','impact_degree',
 'verification_status','gaps','is_background','event_key','revision_id','dedup_keep_separate','account_id',
 'source_record_id','record_kind','window_basis')
SECRET_PARAMS = re.compile(r'^(?:api[_-]?key|api[_-]?token|access_token|refresh_token|authorization|password|passwd|jsc_token|qsid)$', re.I)
TRACKING = {'fbclid','gclid','msclkid','spm','utm_source','utm_medium','utm_campaign','utm_term','utm_content','utm_id'}
ET.register_namespace('atom','http://www.w3.org/2005/Atom')

def clean_text(value):
    value = str(value) if value is not None else ''
    return ''.join(c for c in value if c in '\t\n\r' or 0x20 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF)

def safe_url(value):
    if not value:return None
    p=urlsplit(str(value))
    if p.scheme.lower() not in ('https','http') or not p.hostname or p.username or p.password:
        raise ValueError('Not a safe public HTTP URL')
    if any(SECRET_PARAMS.match(k) for k,v in parse_qsl(p.query,keep_blank_values=True)):
        raise ValueError('Credential/session parameter in public URL')
    return str(value)

def canonical_url(value):
    if not value:return None
    p=urlsplit(safe_url(value));host=p.hostname.lower()
    port=(':'+str(p.port)) if p.port and not (p.scheme=='https' and p.port==443 or p.scheme=='http' and p.port==80) else ''
    pairs=sorted((k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower() not in TRACKING and not k.lower().startswith('utm_'))
    return urlunsplit((p.scheme.lower(),host+port,p.path or '/',urlencode(pairs,doseq=True),''))

def dt(value):
    if not isinstance(value,str) or len(value)<=10:return None
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:return None

def normalized(value):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',str(value or '')).casefold())

def role_kind(m):
    r=m.get('source_role','').lower()
    if 'sentiment' in r:return 'sentiment'
    if any(x in r for x in ('opinion','forecast','prediction')):return 'opinion'
    if 'implied' in r:return 'market-implied'
    if any(x in r for x in ('proxy','vendor_data','data')):return 'data'
    return 'fact'

def period(m):
    value=m.get('data_period') or m.get('event_at')
    if value:return normalized(value)
    p=m.get('published_at') or m.get('feed_published_at')
    return str(p)[:10] if p else 'undated'

def identity_aliases(m):
    kind=role_kind(m);version=normalized(m.get('revision_id'))
    discriminator=''
    if kind in ('opinion','sentiment'):
        discriminator='|'+normalized(m.get('account_id') or m.get('source_name'))
    suffix='|'+period(m)+'|'+kind+discriminator+'|'+version+'|record:'+normalized(m.get('source_record_id'))
    aliases=[]
    # URL identity uses the actual content owner URL; a secondary reading URL is only a fallback.
    u=canonical_url(m.get('original_url') or m.get('available_url'))
    if u:aliases.append('url:'+u+suffix)
    if m.get('event_key'):
        aliases.append('event:'+normalized(m['event_key'])+suffix)
    if not aliases:
        aliases.append('title:'+normalized(m['source_name'])+'|'+normalized(m['title'])+suffix)
    if m.get('dedup_keep_separate'):
        aliases=[a+'|separate:'+normalized(m.get('id') or m['source_name']) for a in aliases]
    return sorted(set(aliases))

def validate(m):
    for key in ('source_id','source_name','title','confidence','investment_impact','impact_degree','published_at'):
        if key not in m:raise ValueError('Missing seven-field property: '+key)
    if not m['source_name'] or not m['title']:raise ValueError('Empty source/title')
    if not (m.get('original_url') or m.get('available_url')):raise ValueError('Neither original nor reading URL available')
    for k in ('original_url','available_url'):safe_url(m.get(k))
    confidence=m['confidence'];impact=m['impact_degree'];analysis=m['investment_impact']
    if not isinstance(confidence,dict) or not confidence.get('reason') or not confidence.get('scope'):raise ValueError('Confidence needs reason and scope')
    score=confidence.get('score')
    if score is not None and (isinstance(score,bool) or not isinstance(score,(int,float)) or not 0<=score<=100):raise ValueError('Confidence must be 0..100 or null')
    if not isinstance(impact,dict) or not impact.get('reason'):raise ValueError('Impact degree needs reason')
    score=impact.get('score')
    if score is not None and (isinstance(score,bool) or not isinstance(score,(int,float)) or not 0<=score<=5):raise ValueError('Impact degree must be 0..5 or null')
    if not isinstance(analysis,dict) or not analysis.get('mechanism'):raise ValueError('Investment impact needs mechanism')

def window_status(m,start,end,cfg,as_of=None):
    if m.get('is_background') and not cfg.get('include_background',False):return False,'historical_background'
    published=dt(m.get('published_at'))
    if published:
        if published>end+timedelta(minutes=5):return False,'future_publication'
        if published<start and not (m.get('material_update') and dt(m.get('updated_at')) and start<=dt(m['updated_at'])<=end):return False,'outside_window'
        return True,'first_publication'
    date_value=m.get('published_at') or m.get('data_period')
    # A known old calendar date must not become current merely because we fetched it now.
    if isinstance(date_value,str) and re.match(r'^\d{4}-\d{2}-\d{2}',date_value):
        try:
            known_day=datetime.fromisoformat(date_value[:10]).replace(tzinfo=ZoneInfo(cfg.get('timezone','Asia/Shanghai')))
            if (known_day+timedelta(days=1)).astimezone(timezone.utc)<=start:return False,'outside_date_window'
        except ValueError:pass
    for field in ('feed_published_at','updated_at'):
        value=dt(m.get(field))
        if value:
            if value>end+timedelta(minutes=5):return False,'future_'+field
            if start<=value<=end:return True,field+'_not_first_publication'
    observed=dt(m.get('observed_at'))
    if observed:
        # Observation occurs during the run, after the fixed news-window boundary.
        # It is never substituted for first publication in RSS.
        if observed>(as_of or end)+timedelta(minutes=5):return False,'future_observation'
        if start<=observed<=(as_of or end):return True,'observation_during_run_not_first_publication'
    if isinstance(date_value,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',date_value):
        try:
            day=datetime.fromisoformat(date_value).replace(tzinfo=ZoneInfo(cfg.get('timezone','Asia/Shanghai')))
            if day.astimezone(timezone.utc)<=end and (day+timedelta(days=1)).astimezone(timezone.utc)>start:return True,'date_precision_window_overlap'
        except ValueError:pass
        return False,'outside_date_window'
    # Retrieval alone never makes an undated or old source a current message.
    return False,'no_reliable_window_timestamp'

def public_message(m):
    result={k:deepcopy(m[k]) for k in PUBLIC_FIELDS if k in m}
    audit=m.get('audit_refs',{})
    result['audit_refs']={k:deepcopy(audit[k]) for k in ('run_id','call_ids','event_seqs') if k in audit}
    return result

def rank(m):
    return (m.get('layer',9), {'verified':0,'partial':1,'unverified':2}.get(m.get('verification_status'),3), -(m.get('confidence',{}).get('score') or 0), normalized(m.get('source_name')), normalized(m.get('title')))

def provenance(m):
    return {k:deepcopy(m.get(k)) for k in ('layer','source_id','source_name','source_role','title','fact_summary','original_url','available_url','published_at','feed_published_at','data_period','retrieved_at','audit_refs') if m.get(k) is not None}

def merge_members(members):
    # A correction/re-observation from the same owner replaces that owner's older text,
    # even when the corrected confidence is lower. Authority ranks different owners.
    latest={}
    for m in members:
        key=(m.get('source_id'),canonical_url(m.get('original_url') or m.get('available_url')),normalized(m.get('revision_id')),normalized(m.get('source_record_id')))
        freshness=dt(m.get('retrieved_at')) or dt(m.get('updated_at')) or datetime.min.replace(tzinfo=timezone.utc)
        old=latest.get(key)
        if old is None or freshness>(dt(old.get('retrieved_at')) or dt(old.get('updated_at')) or datetime.min.replace(tzinfo=timezone.utc)):latest[key]=m
    ordered=sorted(latest.values(),key=rank);chosen=deepcopy(ordered[0]);provs={}
    for m in members:
        for p in m.get('provenance',[provenance(m)]):
            key=json.dumps({k:v for k,v in p.items() if k not in ('audit_refs','audit_runs','retrieved_at','first_retrieved_at')},ensure_ascii=False,sort_keys=True)
            prior=provs.get(key)
            if prior:
                runs=prior.get('audit_runs',[])+p.get('audit_runs',[])
                for ref in (prior.get('audit_refs',{}),p.get('audit_refs',{})):
                    if ref.get('run_id'):runs.append(ref['run_id'])
                merged=deepcopy(p if str(p.get('retrieved_at',''))>str(prior.get('retrieved_at','')) else prior)
                merged['audit_runs']=sorted(set(runs));merged['first_retrieved_at']=min(str(prior.get('first_retrieved_at') or prior.get('retrieved_at','')),str(p.get('first_retrieved_at') or p.get('retrieved_at','')))
                provs[key]=merged
            else:provs[key]=deepcopy(p)
    all_prov=list(provs.values())
    chosen['provenance']=all_prov
    chosen['layers']=sorted(set(p['layer'] for p in all_prov if p.get('layer') in DEPENDENCIES))
    chosen['gaps']=list(dict.fromkeys(g for m in ordered for g in m.get('gaps',[])))
    return chosen

def merge(candidates,previous,as_of):
    existing=previous.get('items',[]);candidates=deepcopy(candidates)
    # A verified event/date can enrich a prior article whose only date was its
    # RSS timestamp. Bridge that one prior identity, retaining its GUID/aliases.
    for m in candidates:
        url=canonical_url(m.get('original_url'))
        if not url or not m.get('event_key') or not m.get('data_period') or role_kind(m)!='fact' or m.get('dedup_keep_separate'):continue
        prior=[p for p in existing if canonical_url(p.get('original_url'))==url
               and role_kind(p)=='fact' and not p.get('data_period') and not p.get('event_key')
               and not p.get('dedup_keep_separate')
               and normalized(p.get('revision_id'))==normalized(m.get('revision_id'))
               and normalized(p.get('source_record_id'))==normalized(m.get('source_record_id'))]
        if len(prior)==1:
            m['_aliases']=sorted(set(m.get('_aliases',[])+prior[0].get('_aliases',[])+identity_aliases(prior[0])))
    records=existing+candidates
    parent=list(range(len(records)));owner={}
    def find(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    def union(a,b):
        a,b=find(a),find(b)
        if a!=b:parent[max(a,b)]=min(a,b)
    aliases=[]
    for i,m in enumerate(records):
        keys=sorted(set(m.get('_aliases',[])+identity_aliases(m)));aliases.append(keys)
        for key in keys:
            if key in owner:union(i,owner[key])
            else:owner[key]=i
    groups={}
    for i,m in enumerate(records):groups.setdefault(find(i),[]).append((i,m))
    items=[];mapping=[]
    for rows in groups.values():
        member=[m for i,m in rows];item=merge_members(member);keys=sorted(set(k for i,m in rows for k in aliases[i]))
        previous_rows=sorted([m for i,m in rows if i<len(existing)],key=lambda m:(m.get('first_seen',''),m.get('guid','')))
        guid=previous_rows[0]['guid'] if previous_rows else 'urn:investment-research-news:'+hashlib.sha256(keys[0].encode()).hexdigest()
        item.update(guid=guid,_aliases=keys,first_seen=previous_rows[0].get('first_seen',as_of) if previous_rows else as_of)
        # Keep updates under the same GUID, without relabeling first publication.
        item['last_seen']=as_of if any(i>=len(existing) for i,m in rows) else item.get('last_seen',as_of)
        items.append(item)
        mapping.append({'guid':guid,'aliases':keys,'input_ids':[m.get('id') for i,m in rows],'layers':item.get('layers',[]),'merged_members':len(rows)})
    def sort_key(m):
        when=dt(m.get('published_at')) or dt(m.get('feed_published_at')) or dt(m.get('observed_at')) or dt(m.get('first_seen'))
        return (-(m.get('impact_degree',{}).get('score') or 0),-(when.timestamp() if when else 0),m['guid'])
    return sorted(items,key=sort_key),mapping

def escaped(value):return html.escape(clean_text(value),quote=True)
def link(url,label):return '<a href="'+escaped(safe_url(url))+'">'+escaped(label)+'</a>' if url else escaped(label)

def source_links(m):
    """List all attributed URLs once, keeping missing originals explicit."""
    links={}
    def add(url,source,kind):
        if not url:return
        key=canonical_url(url);entry=links.setdefault(key,{'url':url,'labels':[],'original':False})
        label=source+'（'+kind+'）'
        if label not in entry['labels']:entry['labels'].append(label)
        entry['original']=entry['original'] or kind=='原始出处'
    records=[m,*m.get('provenance',[])]
    for row in m.get('valuation_members',[]):records.extend([row,*row.get('provenance',[])])
    for p in records:
        source=p.get('source_name','来源');add(p.get('original_url'),source,'原始出处')
        if p.get('available_url') and p['available_url']!=p.get('original_url'):
            add(p['available_url'],source,'完整明细' if p.get('valuation_members') else '实际读取/转载')
    for p in m.get('source_links',[]):add(p['url'],p['source'],'实际读取/转载' if p.get('kind')=='available' else '原始出处')
    return list(links.values())

def description(m):
    c=m['confidence'];degree=m['impact_degree'];impact=m['investment_impact']
    first=m.get('published_at') or '首次发布时间未知'
    times=[first]
    for key,label in [('source_timezone','原始时区'),('updated_at','更新'),('feed_published_at','来源RSS发布时间'),('event_at','事件'),('data_period','数据期'),('observed_at','观测'),('retrieved_at','获取')]:
        if m.get(key):times.append(label+'：'+str(m[key]))
    links=source_links(m)
    url_html=('原始链接未取得；' if not any(x['original'] for x in links) else '')+'<ul>'+''.join('<li>'+link(x['url'],'；'.join(x['labels']))+'</li>' for x in links)+'</ul>'
    impact_text='；'.join(str(x) for x in [impact.get('direction'),impact.get('mechanism'),'期限：'+str(impact.get('horizon','未知')),'条件：'+'；'.join(impact.get('conditions',[])),'已计价：'+str(impact.get('priced_in','未知')),'失效条件：'+'；'.join(impact.get('invalidation',[]))] if x)
    confidence_text=('逐条见明细' if c.get('score') is None else str(c['score'])+'/100')+'；'+str(c.get('reason'))+'；支持范围：'+str(c.get('scope'))
    fields=[('来源',escaped(m['source_name'])),('原始链接',url_html),('标题',escaped(m['title'])),('消息产生时间',escaped('；'.join(times))),('置信度',escaped(confidence_text)),('对投资的影响',escaped(impact_text)),('影响度',escaped(('待评估' if degree.get('score') is None else str(degree['score'])+'/5')+'；'+degree['reason']))]
    result='<dl>'+''.join('<dt><strong>'+k+'</strong></dt><dd>'+v+'</dd>' for k,v in fields)+'</dl>'
    if m.get('fact_summary'):result+='<p>'+escaped(m['fact_summary'])+'</p>'
    if m.get('analysis'):result+='<p>'+escaped(m['analysis'])+'</p>'
    if m.get('gaps'):result+='<p><strong>核验缺口：</strong>'+escaped('；'.join(m['gaps']))+'</p>'
    result+='<p><strong>合并出处：</strong></p><ul>'
    for p in m.get('provenance',[]):
        result+='<li>'+escaped(p.get('source_name','来源'))+'；'+escaped(p.get('title',''))+'；'+escaped(p.get('published_at') or '首发未知')+'</li>'
    result+='</ul>'
    if m.get('valuation_members'):
        result+='<h3>逐指数明细（平台原值，分位单位各自保留）</h3><table><thead><tr>'+''.join('<th>'+s+'</th>' for s in ['指数/标题','来源及原始链接','估值/分位/质量标记','消息时间','置信度','对投资的影响','影响度'])+'</tr></thead><tbody>'
        for row in m['valuation_members']:
            c=row['confidence'];d=row['impact_degree'];impact=row['investment_impact']
            times='；'.join(label+'：'+str(row[k]) for k,label in [('published_at','首发'),('data_period','数据期'),('updated_at','更新'),('observed_at','观测'),('retrieved_at','获取')] if row.get(k))
            if not row.get('published_at'):times='首发未知；'+times
            investment='；'.join([str(impact.get('direction','待评估')),impact['mechanism'],'期限：'+str(impact.get('horizon','未知')),'条件：'+'；'.join(impact.get('conditions',[])),'已计价：'+str(impact.get('priced_in','未知')),'失效条件：'+'；'.join(impact.get('invalidation',[]))])
            cells=[escaped(row['title']),link(row.get('original_url') or row['available_url'],row['source_name']),escaped(row.get('fact_summary','')+'；'+'；'.join(row.get('gaps',[]))),escaped(times),escaped(str(c.get('score'))+'/100；'+c['reason']+'；支持范围：'+c['scope']),escaped(investment),escaped(('待评估' if d.get('score') is None else str(d['score'])+'/5')+'；'+d['reason'])]
            result+='<tr>'+''.join('<td>'+cell+'</td>' for cell in cells)+'</tr>'
        result+='</tbody></table>'
    return result

def rss_entries(items,cfg):
    """Bundle valuation display only; underlying records and identities stay intact."""
    groups={}
    for m in items:
        day=m.get('data_period')
        if m.get('record_kind')=='指数估值快照' and isinstance(day,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',day):
            try:datetime.fromisoformat(day)
            except ValueError:continue
            groups.setdefault(day,[]).append(m)
    result=[];emitted=set()
    for m in items:
        day=m.get('data_period')
        if m.get('record_kind')!='指数估值快照' or day not in groups:
            result.append(m);continue
        if day in emitted:continue
        emitted.add(day);members=sorted(groups[day],key=lambda x:(x['source_id'],x.get('source_record_id',''),x['guid']))
        sources=sorted({(x['source_name'],x.get('original_url') or x['available_url'],'original' if x.get('original_url') else 'available') for x in members})
        degrees=[x['impact_degree']['score'] for x in members if x['impact_degree'].get('score') is not None]
        digest={'guid':'urn:investment-research-news:valuation:'+day,'layer':2,'layers':[2],'source_name':'；'.join(sorted({x[0] for x in sources})),'source_role':'vendor_data','source_links':[{'source':name,'url':url,'kind':kind} for name,url,kind in sources],'original_url':None,'available_url':'https://github.com/'+cfg['repository']+'/blob/main/data/items.json','title':'指数估值汇总 · '+day+'（'+str(len(members))+'条平台记录）','published_at':None,'data_period':day,'record_kind':'指数估值汇总','confidence':{'score':None,'reason':'保留各原记录评分，不平均不同平台置信度。','scope':'平台显示值及质量标记，不是未来收益概率。'},'investment_impact':{'direction':'估值背景参考，方向待评估','mechanism':'按平台原口径比较，结合盈利与风险预算；异常、过期和不适用记录不解释为低估信号。','horizon':'中长期','conditions':['核对样本期、成分和亏损公司处理'],'priced_in':'未知'},'impact_degree':{'score':max(degrees) if degrees else None,'reason':'沿用明细中的最高已评估影响度，未评估项仍单列待评估；打包不增加影响。'},'gaps':['各平台分位单位不同，保留原值，不混合求平均。','逐指数首发、更新、观测与获取时刻见明细，不把汇总时刻当数据首发。'],'valuation_members':members}
        result.append(digest)
    return result

def rss_group_map(entries):
    return [{'guid':m['guid'],'data_period':m['data_period'],'member_guids':[x['guid'] for x in m['valuation_members']]} for m in entries if m.get('valuation_members')]

def rss(items,cfg,as_of):
    root=ET.Element('rss',version='2.0');channel=ET.SubElement(root,'channel')
    for tag,text in [('title',cfg['channel_title']),('link','https://github.com/'+cfg['repository']),('description','三层金融投研汇总。每条含来源、原始链接、标题、消息产生时间、置信度、投资影响和影响度；保留转载、未知时间与核验缺口。'),('language','zh-CN'),('generator','finance-news-publisher/1.0'),('ttl','60')]:ET.SubElement(channel,tag).text=text
    ET.SubElement(channel,'{http://www.w3.org/2005/Atom}link',href=cfg['feed_url'],rel='self',type='application/rss+xml')
    ET.SubElement(channel,'lastBuildDate').text=format_datetime(dt(as_of),usegmt=True)
    for m in rss_entries(items,cfg):
        node=ET.SubElement(channel,'item');ET.SubElement(node,'title').text=clean_text(m['title']);ET.SubElement(node,'link').text=m.get('original_url') or m['available_url'];ET.SubElement(node,'guid',isPermaLink='false').text=m['guid']
        published=dt(m.get('published_at'))
        if published:ET.SubElement(node,'pubDate').text=format_datetime(published,usegmt=True)
        for layer in m.get('layers',[m['layer']]):ET.SubElement(node,'category').text={1:'一层·权威与一手',2:'二层·媒体数据研报',3:'三层·社交与情绪'}[layer]
        if not published:ET.SubElement(node,'category').text='首次时间待核实'
        if m.get('verification_status')=='unverified':ET.SubElement(node,'category').text='待核验线索'
        if m.get('record_kind'):ET.SubElement(node,'category').text=m['record_kind']
        ET.SubElement(node,'description').text=description(m)
    ET.indent(root,space='  ')
    return ET.tostring(root,encoding='utf-8',xml_declaration=True).decode()+'\n'

def build(manifest,previous,cfg):
    end=dt(manifest['window_end']);start=dt(manifest['window_start']);as_of=dt(manifest['as_of'])
    if not end or not start or not as_of or start>end:raise ValueError('Window/as_of must be timezone-aware ISO times')
    if set(x['layer'] for x in manifest['layers'])!=set(DEPENDENCIES) or len(manifest['layers'])!=3:raise ValueError('Manifest must contain exactly the three executed layers')
    accepted=[];rejected=[];coverage=[]
    for layer in manifest['layers']:
        if layer['skill']!=DEPENDENCIES[layer['layer']]:raise ValueError('Layer/skill mismatch')
        messages=json.loads(Path(layer['messages']).read_text());cov=json.loads(Path(layer['coverage']).read_text())
        if not isinstance(messages,list) or not isinstance(cov,dict):raise ValueError('Malformed layer outputs')
        coverage.append({'layer':layer['layer'],'skill':layer['skill'],'status':layer.get('status','partial'),'candidate_count':len(messages),'coverage':cov})
        for source in messages:
            m=deepcopy(source);reason=None
            try:
                validate(m)
                if m.get('layer')!=layer['layer']:raise ValueError('Message/layer mismatch')
                if m['source_id'] in cfg.get('excluded_source_ids',[]):reason='user_disabled_source'
                else:
                    allowed,basis=window_status(m,start,end,cfg,as_of)
                    if not allowed:reason=basis
                    else:m['window_basis']=basis
            except (ValueError,TypeError,KeyError) as exc:reason='invalid: '+str(exc)
            if reason:rejected.append({'id':m.get('id'),'source_id':m.get('source_id'),'reason':reason})
            else:accepted.append(public_message(m))
    old=deepcopy(previous or {'items':[]});old['items']=[m for m in old.get('items',[]) if m.get('source_id') not in cfg.get('excluded_source_ids',[])]
    items,mapping=merge(accepted,old,manifest['as_of'])
    state={'version':1,'run_id':manifest['run_id'],'updated_at':manifest['as_of'],'items':items}
    public=[{k:v for k,v in m.items() if not k.startswith('_')} for m in items]
    entries=rss_entries(items,cfg)
    summary={'run_id':manifest['run_id'],'as_of':manifest['as_of'],'window_start':manifest['window_start'],'window_end':manifest['window_end'],'layer_count':3,'input_messages':sum(x['candidate_count'] for x in coverage),'accepted_messages':len(accepted),'published_items':len(entries),'stored_records':len(items),'rejected':rejected,'dedup_groups':len(mapping),'scope':'Current collected messages only; no exhaustive all-source/current-content claim. Source failures retained in coverage.','layers':coverage}
    xml=rss(items,cfg,manifest['as_of']);parsed=ET.fromstring(xml)
    assert len(parsed.findall('./channel/item'))==len(entries)
    assert len({m['guid'] for m in items})==len(items)
    return {'feed.xml':xml,'data/items.json':json.dumps(public,ensure_ascii=False,indent=2)+'\n','data/feed-state.json':json.dumps(state,ensure_ascii=False,indent=2)+'\n','summary.json':json.dumps(summary,ensure_ascii=False,indent=2)+'\n','dedup-map.json':json.dumps(mapping,ensure_ascii=False,indent=2)+'\n','rss-groups.json':json.dumps(rss_group_map(entries),ensure_ascii=False,indent=2)+'\n'}

def main():
    p=argparse.ArgumentParser(description=__doc__);mode=p.add_mutually_exclusive_group(required=True);mode.add_argument('--manifest');mode.add_argument('--render-state');p.add_argument('--previous');p.add_argument('--as-of');p.add_argument('--config',required=True);p.add_argument('--output',required=True);a=p.parse_args();cfg=json.loads(Path(a.config).read_text())
    if a.render_state:
        state=json.loads(Path(a.render_state).read_text());items=state['items'];as_of=a.as_of or datetime.now(timezone.utc).isoformat()
        if not dt(as_of):p.error('--as-of must have a timezone')
        for m in items:validate(m)
        if len({m['guid'] for m in items})!=len(items):raise ValueError('Duplicate state GUID')
        entries=rss_entries(items,cfg);groups=rss_group_map(entries)
        summary={'mode':'presentation_only','source_run_id':state.get('run_id'),'source_state_updated_at':state.get('updated_at'),'rendered_at':as_of,'stored_records':len(items),'published_items':len(entries),'valuation_records':sum(len(g['member_guids']) for g in groups),'valuation_digest_count':len(groups),'new_collection':False}
        outputs={'feed.xml':rss(items,cfg,as_of),'summary.json':json.dumps(summary,ensure_ascii=False,indent=2)+'\n','rss-groups.json':json.dumps(groups,ensure_ascii=False,indent=2)+'\n'}
    else:
        if not a.previous:p.error('--previous is required with --manifest')
        manifest=json.loads(Path(a.manifest).read_text());previous=json.loads(Path(a.previous).read_text()) if Path(a.previous).exists() else {'items':[]};outputs=build(manifest,previous,cfg)
    root=Path(a.output);root.mkdir(parents=True,exist_ok=True)
    for relative,value in outputs.items():
        target=root/relative;target.parent.mkdir(parents=True,exist_ok=True);tmp=target.with_suffix(target.suffix+'.tmp');tmp.write_text(value);tmp.replace(target)
    summary=json.loads(outputs['summary.json']);print(json.dumps({k:summary[k] for k in ('run_id','mode','input_messages','accepted_messages','published_items','stored_records','dedup_groups','valuation_digest_count') if k in summary},ensure_ascii=False))

if __name__=='__main__':main()
