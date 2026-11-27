import importlib.util,json,tempfile,unittest
from copy import deepcopy
from unittest.mock import patch
from pathlib import Path
import xml.etree.ElementTree as ET

spec=importlib.util.spec_from_file_location('feed',Path(__file__).resolve().parents[1]/'scripts/build_feed.py');feed=importlib.util.module_from_spec(spec);spec.loader.exec_module(feed)
NOW='2026-11-26T12:00:00+00:00'
def qualified_valuation(day='2026-11-26',pct=.05):
 return dict(value=25,percentile=pct,data_date=day,window_ok=True,flags={})
def message(**kw):
 m={'id':'a','layer':1,'source_id':'P02','source_name':'央行','source_role':'fact','original_url':'https://example.org/news?id=7&utm_source=foo','available_url':'https://example.org/news?id=7','title':'央行公告 & <观察>','published_at':'2026-11-26T01:00:00+00:00','data_period':'2026-11-26','retrieved_at':NOW,'verification_status':'verified','fact_summary':'数值需核对','confidence':{'score':95,'reason':'原表','scope':'公告内容'},'investment_impact':{'mechanism':'条件成立时影响资金面','conditions':['补齐到期量'],'invalidation':['数据修订']},'impact_degree':{'score':2,'reason':'局部影响'},'audit_refs':{'run_id':'r','call_ids':['call'],'artifact_paths':['/private/file']}}
 m.update(kw);return m
class FeedTests(unittest.TestCase):
 def test_rss_refresh_retains_verified_period_through_merged_source_alias(self):
  original=message(source_id='P03',original_url='https://official.org/profits',event_key='profits',data_period='2026-10')
  media=message(source_id='M08',layer=2,original_url='https://media.org/profits',event_key='profits',data_period='2026-10')
  prior,_=feed.merge([original,media],{},NOW)
  refresh=dict(media,data_period=None,published_at=None,feed_published_at=NOW)
  feed.retain_article_period(refresh,{'items':prior})
  self.assertEqual(refresh['data_period'],'2026-10')
  merged,_=feed.merge([refresh],{'items':prior},NOW)
  self.assertEqual(len(merged),1);self.assertEqual(merged[0]['guid'],prior[0]['guid'])
  explicit=dict(refresh,data_period='2026-11');feed.retain_article_period(explicit,{'items':prior})
  self.assertEqual(explicit['data_period'],'2026-11')
 def test_reviewed_event_group_keeps_all_links_and_refuses_opinion(self):
  a=message(original_url='https://one.org/result');b=message(id='b',layer=2,original_url='https://two.org/result')
  group={'urls':[a['original_url'],b['original_url']],'event_key':'results-Q3','data_period':'2026-Q3'}
  feed.annotate_events([a,b],[group]);rows,_=feed.merge([a,b],{},NOW)
  self.assertEqual(len(rows),1);self.assertEqual(len(feed.source_links(rows[0])),3)
  opinion=dict(a,source_role='opinion')
  with self.assertRaises(ValueError):feed.annotate_events([opinion],[group])
 def test_rss_clock_change_migrates_legacy_state_and_reuses_article_guid(self):
  old=message(source_id='M08',layer=2,source_name='RSS',published_at=None,data_period=None,feed_published_at='2026-11-26T10:00:00+08:00')
  legacy=[a for a in feed.identity_aliases(old) if not a.startswith('rss-url:')]
  previous={'items':[dict(old,guid='legacy-guid',_aliases=legacy)],'source_records':{'M08|'+a:{'source_id':'M08','fingerprint':feed.content_fingerprint(old)} for a in legacy}}
  same=dict(old,feed_published_at='2026-11-27T06:33:10+08:00',retrieved_at='2026-11-27T00:00:00Z')
  changes,records,stats=feed.incremental_records([same],previous)
  self.assertFalse(changes);self.assertEqual(stats['duplicate_source_records'],1)
  revised=dict(same,fact_summary='经原文核对的新收盘数据')
  changes,records,stats=feed.incremental_records([revised],previous)
  self.assertEqual(stats['updated_source_records'],1);self.assertEqual(stats['new_source_records'],0)
  items,_=feed.merge(changes,previous,'2026-11-27T00:00:00Z')
  self.assertEqual(len(items),1);self.assertEqual(items[0]['guid'],'legacy-guid')
  self.assertEqual(items[0]['fact_summary'],revised['fact_summary'])
 def test_successful_component_does_not_advance_failed_sibling_cursor(self):
  prior={'source_cursors':{'P02':{'resume_from':'old','components':{'MLF':{'resume_from':'older'}}}}}
  coverage=[{'coverage':{'sources':[{'source_id':'P02','status':'content_collected','checked_at':NOW,'components':[{'component':'OMO','status':'content_collected','checked_at':NOW},{'component':'MLF','status':'access_limited_verified','checked_at':NOW}]}]}}]
  current=feed.advance_cursors(prior,coverage,NOW)['P02']
  self.assertEqual(current['resume_from'],'old');self.assertEqual(current['components']['MLF']['resume_from'],'older');self.assertEqual(current['components']['OMO']['resume_from'],NOW)
  self.assertNotIn('OMO',prior['source_cursors']['P02']['components'])
 def test_unzoned_source_clock_preserves_date_overlap_and_current_rss_monthly_release(self):
  start=feed.dt('2026-11-25T13:14:32Z');end=feed.dt('2026-11-26T13:14:32Z')
  m=message(published_at='2026-11-26T09:20:30')
  self.assertEqual(feed.window_status(m,start,end,{})[1],'date_window_overlap_timezone_unknown')
  items,_=feed.merge([m],{},NOW)
  self.assertIsNone(ET.fromstring(feed.rss(items,{'repository':'a/b','channel_title':'x','feed_url':'https://e.org/rss'},NOW)).find('./channel/item/pubDate'))
  m=message(published_at=None,data_period='2026-10',feed_published_at='2026-11-25T21:52:55+08:00')
  self.assertEqual(feed.window_status(m,start,end,{})[1],'feed_published_at_not_first_publication')
 def test_canonical_tracking_keeps_material_parameters(self):
  self.assertEqual(feed.canonical_url('https://EXAMPLE.org/x?id=7&utm_source=a#part'),'https://example.org/x?id=7')
  self.assertNotEqual(feed.canonical_url('https://e.org/?id=1'),feed.canonical_url('https://e.org/?id=2'))
 def test_repost_and_primary_merge_keep_provenance_no_score_inflation(self):
  a=message();b=message(id='b',layer=2,source_id='M01',source_name='媒体',confidence={'score':80,'reason':'转载','scope':'转载存在'})
  items,mapping=feed.merge([a,b],{},NOW);self.assertEqual(len(items),1);self.assertEqual(len(items[0]['provenance']),2);self.assertEqual(items[0]['confidence']['score'],95)
 def test_event_key_can_link_distinct_urls(self):
  a=message(event_key='PBOC/OMO/2026-11-26');b=message(id='b',layer=2,source_id='M01',original_url='https://media.org/1',event_key='PBOC/OMO/2026-11-26')
  self.assertEqual(len(feed.merge([a,b],{},NOW)[0]),1)
 def test_distinct_periods_and_opinions_do_not_merge(self):
  a=message();b=message(data_period='2026-11-25');self.assertEqual(len(feed.merge([a,b],{},NOW)[0]),2)
  a=message(source_role='opinion',source_name='机构甲',event_key='gold');b=message(source_role='opinion',source_name='机构乙',event_key='gold');self.assertEqual(len(feed.merge([a,b],{},NOW)[0]),2)
 def test_guid_survives_more_authoritative_source_later(self):
  old=message(id='m',layer=2,source_id='M01',original_url='https://media.org/x',event_key='event')
  first,_=feed.merge([old],{},NOW);new=message(original_url='https://official.org/x',event_key='event')
  second,_=feed.merge([new],{'items':first},'2026-11-26T13:00:00+00:00');self.assertEqual(first[0]['guid'],second[0]['guid']);self.assertEqual(second[0]['layer'],1)
 def test_corrected_owner_text_replaces_old_lower_confidence(self):
  old=message(fact_summary='旧值',retrieved_at='2026-11-26T10:00:00Z');first,_=feed.merge([old],{},NOW)
  new=message(fact_summary='修正值',confidence={'score':90,'reason':'修正','scope':'修正值'})
  second,_=feed.merge([new],{'items':first},NOW);self.assertEqual(second[0]['fact_summary'],'修正值');self.assertEqual(second[0]['guid'],first[0]['guid'])
 def test_verified_event_date_enrichment_preserves_legacy_rss_guid(self):
  old=message(id='old',layer=2,source_id='M01',original_url='https://media.org/beige',data_period=None,published_at=None,feed_published_at='2026-11-26T01:00:00Z',retrieved_at='2026-11-26T10:00:00Z')
  first,_=feed.merge([old],{},NOW)
  enriched=dict(old,id='new',data_period='2026-11-25',event_key='BEIGE_2026-11-25',retrieved_at=NOW)
  primary=message(original_url='https://official.org/beige',data_period='2026-11-25',event_key='BEIGE_2026-11-25')
  second,_=feed.merge([enriched,primary],{'items':first},NOW)
  self.assertEqual(len(second),1);self.assertEqual(second[0]['guid'],first[0]['guid']);self.assertEqual(second[0]['layer'],1)
  self.assertTrue(set(first[0]['_aliases'])<=set(second[0]['_aliases']))
  only_media,_=feed.merge([enriched],{'items':first},NOW);self.assertEqual(only_media[0]['data_period'],'2026-11-25')
 def test_enrichment_does_not_override_explicit_period_or_conflict(self):
  old=message(data_period='2026-11-24');first,_=feed.merge([old],{},NOW)
  enriched=message(data_period='2026-11-25',event_key='event')
  self.assertEqual(len(feed.merge([enriched],{'items':first},NOW)[0]),2)
  old=message(data_period=None,dedup_keep_separate=True);first,_=feed.merge([old],{},NOW)
  self.assertEqual(len(feed.merge([enriched],{'items':first},NOW)[0]),2)
 def test_unknown_time_not_forged_and_historical_rejected(self):
  start=feed.dt('2026-11-25T12:00:00Z');end=feed.dt(NOW);cfg={}
  self.assertFalse(feed.window_status(message(published_at='2023-01-01T00:00:00Z'),start,end,cfg)[0])
  self.assertFalse(feed.window_status(message(published_at=None,data_period=None),start,end,cfg)[0])
  self.assertTrue(feed.window_status(message(published_at=None,observed_at=NOW),start,end,cfg)[0])
  items,_=feed.merge([message(published_at=None,observed_at=NOW)],{},NOW)
  xml=feed.rss(items,{'channel_title':'x','repository':'a/b','feed_url':'https://e.org/feed.xml'},NOW)
  self.assertIsNone(ET.fromstring(xml).find('./channel/item/pubDate'))
 def test_xml_html_and_secrets(self):
  m=message(title='<script>alert(1)</script> & \x00',fact_summary='<img src=x onerror=bad>');items,_=feed.merge([m],{},NOW);x=ET.fromstring(feed.rss(items,{'channel_title':'x','repository':'a/b','feed_url':'https://e.org/rss'},NOW));body=x.findtext('./channel/item/description')
  self.assertNotIn('<script>',body);self.assertNotIn('<img src=x',body)
  for url in ['javascript:alert(1)','https://u:p@example.org/a','https://e.org/?api_key=private']:
   with self.assertRaises(ValueError):feed.safe_url(url)
  self.assertNotIn('artifact_paths',feed.public_message(message())['audit_refs'])
 def test_empty_layers_preserve_previous_and_disabled_source_is_excluded(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);layers=[]
   for layer,sk in feed.DEPENDENCIES.items():
    (p/f'{layer}.json').write_text('[]');(p/f'c{layer}.json').write_text('{"sources":[]}');layers.append({'layer':layer,'skill':sk,'messages':str(p/f'{layer}.json'),'coverage':str(p/f'c{layer}.json')})
   baseline=feed.hashlib.sha256(json.dumps({'version':1,'sectors':{}},ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
   monitor={'run_id':'r','rule_version':'sector-'+feed.VALUATION_POLICY['version'],'base_state_sha256':baseline,'sectors':{}}
   (p/'sector-monitor-state.next.json').write_text(json.dumps(monitor));(p/'sector-monitor.json').write_text('{"run_id":"r","messages":[]}')
   manifest={'run_id':'r','as_of':NOW,'window_start':'2026-11-25T12:00:00Z','window_end':NOW,'layers':layers};cfg={'repository':'a/b','channel_title':'x','feed_url':'https://e.org/rss','excluded_source_ids':['S09']}
   old,_=feed.merge([message(),message(id='weibo',layer=3,source_id='S09',original_url='https://weibo.com/x')],{},NOW)
   with patch.object(feed,'publication_gate',return_value={'passed':True}):
    corrections=[{'reason':'User correction','guid':old[0]['guid']}]
    previous={'items':old,'editorial_corrections':corrections}
    out=feed.build(manifest,previous,cfg);self.assertEqual(len(json.loads(out['data/items.json'])),2)
    self.assertEqual(len(ET.fromstring(out['feed.xml']).findall('./channel/item')),1)
    self.assertEqual(json.loads(out['data/feed-state.json'])['editorial_corrections'],corrections)
    self.assertEqual(previous['items'],old)
    self.assertEqual(json.loads(out['data/sector-monitor-state.json']),json.loads(out['data/feed-state.json'])['sector_monitor_state'])
    monitor['sectors']={'legacy':{'channels':{'price_drawdown':{'active':True}}}};(p/'sector-monitor-state.next.json').write_text(json.dumps(monitor))
    with self.assertRaises(feed.PublicationBlocked):feed.build(manifest,{'items':old},cfg)
    monitor['sectors']={};monitor['rule_version']='sector-low-v1';(p/'sector-monitor-state.next.json').write_text(json.dumps(monitor))
    with self.assertRaises(feed.PublicationBlocked):feed.build(manifest,{'items':old},cfg)
    monitor['rule_version']='sector-'+feed.VALUATION_POLICY['version']
    monitor['base_state_sha256']='wrong';(p/'sector-monitor-state.next.json').write_text(json.dumps(monitor))
    with self.assertRaises(feed.PublicationBlocked):feed.build(manifest,{'items':old},cfg)
    manifest['layers'].pop()
    with self.assertRaises(ValueError):feed.build(manifest,{},cfg)
 def test_refresh_deduplicates_same_source_in_attribution(self):
  a=message(retrieved_at='2026-11-26T10:00:00Z');first,_=feed.merge([a],{},NOW)
  again,_=feed.merge([message()],{'items':first},NOW);self.assertEqual(len(again[0]['provenance']),1)
 def test_api_records_and_conflicting_claims_are_not_collapsed(self):
  a=message(source_record_id='index-A');b=message(source_record_id='index-B')
  self.assertEqual(len(feed.merge([a,b],{},NOW)[0]),2)
 def test_collection_after_window_does_not_invent_publication_or_revive_old_article(self):
  start=feed.dt('2026-11-25T12:00:00Z');end=feed.dt(NOW);later=feed.dt('2026-11-26T12:30:00Z')
  m=message(published_at=None,observed_at='2026-11-26T12:20:00Z')
  self.assertTrue(feed.window_status(m,start,end,{},later)[0])
  m=message(published_at='2023-01-01T09:00:00',observed_at='2026-11-26T12:20:00Z')
  self.assertFalse(feed.window_status(m,start,end,{},later)[0])
  a=message(id='claim-A',event_key='event',dedup_keep_separate=True);b=message(id='claim-B',event_key='event',dedup_keep_separate=True)
  self.assertEqual(len(feed.merge([a,b],{},NOW)[0]),2)
 def test_valuation_digest_retains_platform_records_fields_and_unknown_times(self):
  a=message(id='dj',layer=2,source_id='M26',source_name='蛋卷',original_url='https://dj.org/valuations',source_record_id='A',record_kind='指数估值快照',source_role='vendor_data',published_at=None,fact_summary='PE=25；分位0.05（0—1）',valuation_screen=qualified_valuation())
  b=message(id='jx',layer=2,source_id='M27',source_name='韭圈儿',original_url='https://jx.org/valuations',source_record_id='A',record_kind='指数估值快照',source_role='vendor_data',published_at=None,fact_summary='分位5（0—100）；stale=false',valuation_screen=qualified_valuation(),impact_degree={'score':None,'reason':'影响待评估'})
  news=message(id='news',original_url='https://news.org/one')
  items,_=feed.merge([a,b,news],{},NOW);before=deepcopy(items);cfg={'repository':'a/b','channel_title':'x','feed_url':'https://e.org/rss'}
  entries=feed.rss_entries(items,cfg);self.assertEqual(len(entries),2);self.assertEqual(items,before)
  digest=next(x for x in entries if x.get('valuation_members'));self.assertEqual(len(digest['valuation_members']),2);self.assertIsNone(digest['confidence']['score'])
  xml=ET.fromstring(feed.rss(items,cfg,NOW));bundle=next(n for n in xml.findall('./channel/item') if n.findtext('guid')==digest['guid']);self.assertIsNone(bundle.find('pubDate'))
  body=bundle.findtext('description')
  for text in ['0—1','0—100','stale=false','待评估','蛋卷','韭圈儿']:self.assertIn(text,body)
  self.assertEqual(len(ET.fromstring('<root>'+body+'</root>').findall('.//tbody/tr')),2)
  for label in ['来源','原始链接','标题','消息产生时间','置信度','对投资的影响','影响度']:self.assertIn('<strong>'+label+'</strong>',body)
  self.assertIn(next(x['guid'] for x in items if x['id']=='news'),[n.findtext('guid') for n in xml.findall('./channel/item')])
 def test_valuation_digest_guid_is_stable_per_data_day(self):
  a=message(layer=2,source_id='M26',source_record_id='A',record_kind='指数估值快照',source_role='vendor_data',valuation_screen=qualified_valuation())
  b=dict(a,id='b',source_record_id='B');c=dict(a,id='c',data_period='2026-11-25',valuation_screen=qualified_valuation('2026-11-25'))
  first,_=feed.merge([a],{},NOW);second,_=feed.merge([a,b,c],{},NOW);cfg={'repository':'a/b'}
  one=feed.rss_entries(first,cfg);two=feed.rss_entries(list(reversed(second)),cfg)
  self.assertEqual(len(two),2);self.assertEqual(one[0]['guid'],next(x['guid'] for x in two if x['data_period']=='2026-11-26'))
  self.assertEqual(sum(len(x['valuation_members']) for x in two),3)
 def test_valuation_without_reliable_day_stays_separate_and_html_is_escaped(self):
  a=message(layer=2,record_kind='指数估值快照',source_role='vendor_data',data_period=None,valuation_screen=qualified_valuation())
  b=dict(a,id='b',data_period='2026-02-30',source_record_id='B')
  c=dict(a,id='c',data_period='2026-11-26',source_record_id='C',title='<script>bad</script>',fact_summary='<img src=x onerror=bad>')
  items,_=feed.merge([a,b,c],{},NOW);entries=feed.rss_entries(items,{'repository':'a/b'})
  self.assertEqual(len(entries),1);self.assertEqual(len(feed.rss_group_map(entries)),1)
  body=feed.description(next(x for x in entries if x.get('valuation_members')))
  self.assertNotIn('<script>',body);self.assertNotIn('<img src=x',body)
 def test_original_link_field_contains_all_merged_sources_once(self):
  a=message(event_key='event',original_url='https://official.org/one',available_url='https://official.org/one')
  b=message(id='media',layer=2,source_id='M01',source_name='媒体',event_key='event',original_url='https://media.org/one',available_url='https://media.org/one')
  items,_=feed.merge([a,b,b],{},NOW);body=ET.fromstring('<root>'+feed.description(items[0])+'</root>');links=body.findall('./dl/dd')[1].findall('.//a')
  self.assertEqual({a.attrib['href'] for a in links},{'https://official.org/one','https://media.org/one'});self.assertEqual(len(links),2)
  self.assertTrue(any('媒体' in a.text for a in links))
  row=deepcopy(items[0]);row.update(layer=2,record_kind='指数估值快照',source_role='vendor_data',valuation_screen=qualified_valuation());digest=feed.rss_entries([row],{'repository':'a/b'})[0]
  grouped=ET.fromstring('<root>'+feed.description(digest)+'</root>').findall('./dl/dd')[1].findall('.//a')
  self.assertTrue({'https://official.org/one','https://media.org/one'}<={a.attrib['href'] for a in grouped})
 def test_missing_original_is_not_relabelled_as_original_source(self):
  m=message(original_url=None,available_url='https://metadata.org/report');body=ET.fromstring('<root>'+feed.description(m)+'</root>');field=body.findall('./dl/dd')[1]
  self.assertIn('原始链接未取得',''.join(field.itertext()));self.assertIn('实际读取/转载',''.join(field.itertext()))
 def test_publication_barrier_runs_before_any_merge(self):
  with patch.object(feed,'publication_gate',side_effect=feed.PublicationBlocked('unfinished')),patch.object(feed,'merge') as merge:
   with self.assertRaises(feed.PublicationBlocked):feed.build({}, {}, {})
   merge.assert_not_called()
 def test_incremental_fingerprint_ignores_fetch_times_but_detects_value_change(self):
  a=message(fact_values={'pe':12.3});changes,records,counts=feed.incremental_records([a],{});self.assertEqual(counts['new_source_records'],1)
  b=dict(a,retrieved_at='2026-11-26T15:00:00Z',observed_at='2026-11-26T15:00:00Z',updated_at='2026-11-26T15:00:00Z')
  changes,records,counts=feed.incremental_records([b],{'source_records':records});self.assertEqual(changes,[]);self.assertEqual(counts['duplicate_source_records'],1)
  c=dict(b,fact_values={'pe':12.4});changes,records,counts=feed.incremental_records([c],{'source_records':records});self.assertEqual(len(changes),1);self.assertEqual(counts['updated_source_records'],1)
  first,_=feed.merge([a],{},NOW);second,_=feed.merge(changes,{'items':first},NOW);self.assertEqual(first[0]['guid'],second[0]['guid'])
 def test_verified_update_can_publish_an_older_data_period_without_reviving_old_articles(self):
  m=message(published_at=None,data_period='2026-11-24',updated_at='2026-11-25T21:16:00Z',material_update=True)
  self.assertTrue(feed.window_status(m,feed.dt('2026-11-25T13:00:00Z'),feed.dt(NOW),{})[0])
  m['material_update']=False;self.assertFalse(feed.window_status(m,feed.dt('2026-11-25T13:00:00Z'),feed.dt(NOW),{})[0])
 def test_market_snapshot_update_during_run_is_not_future_article_publication(self):
  start=feed.dt('2026-11-25T12:00:00Z');end=feed.dt(NOW);as_of=feed.dt('2026-11-26T13:00:00Z')
  m=message(published_at=None,source_role='market-implied',updated_at='2026-11-26T12:30:00Z',observed_at='2026-11-26T12:31:00Z')
  self.assertTrue(feed.window_status(m,start,end,{},as_of)[0]);m['source_role']='reporting';self.assertFalse(feed.window_status(m,start,end,{},as_of)[0])
 def test_old_month_is_not_revived_by_observation_but_current_release_is_allowed(self):
  start=feed.dt('2026-11-25T12:00:00Z');end=feed.dt(NOW);m=message(published_at=None,data_period='2026-10',observed_at=NOW)
  self.assertFalse(feed.window_status(m,start,end,{})[0]);m['published_at']='2026-11-26';self.assertTrue(feed.window_status(m,start,end,{})[0])
 def test_publication_gate_requires_closed_source_and_component_evidence(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);layers=[];catalog={};events={}
   for number,skill in feed.DEPENDENCIES.items():
    run=p/str(number);(run/'outputs').mkdir(parents=True);(run/'artifacts').mkdir();(run/'artifacts/evidence.json').write_text('{}');sid='T'+str(number)
    component=dict(source_id=sid,status='no_new_verified',checked_at=NOW,checked_scope='fixture listing',reason='fixture has no records',evidence_call_ids=['call'],evidence_paths=['artifacts/evidence.json'])
    row=dict(component,components=[dict(component,component='detail')] if number==1 else [])
    (run/'outputs/messages.json').write_text('[]');(run/'outputs/coverage.json').write_text(json.dumps({'run_id':'r','collection_complete':True,'sources':[row]}))
    if number==4:
     for name in ['sector-monitor-state.next.json','sector-monitor.json']:(run/'outputs'/name).write_text('{"run_id":"r"}')
    catalog[skill]={sid:['detail'] if number==1 else []};events[run]=[{'type':'tool_start','data':{'call_id':'call','source_ids':[sid]}},{'type':'tool_end','data':{'call_id':'call'}}]
    if number==4:events[run]+=[{'type':'artifact_saved','data':{'path':'outputs/'+name}} for name in ['sector-monitor-state.next.json','sector-monitor.json']]
    layers.append({'layer':number,'skill':skill,'messages':str(run/'outputs/messages.json'),'coverage':str(run/'outputs/coverage.json')})
   manifest={'run_id':'r','started_at':'2026-11-26T11:00:00Z','as_of':NOW,'window_start':'2026-11-25T12:00:00Z','window_end':NOW,'layers':layers}
   check={'integrity_ok':True,'status':'completed','pending_call_ids':[],'missing_outputs':[]}
   with patch.object(feed,'required_sources',side_effect=lambda sk:catalog[sk]),patch.object(feed,'collection_log',side_effect=lambda run:(check,events[run])):
    passed=feed.publication_gate(manifest,{});self.assertEqual(passed['completed_sources'],len(feed.DEPENDENCIES));self.assertEqual(passed['completed_components'],1)
    with self.assertRaises(feed.PublicationBlocked):feed.publication_gate(dict(manifest,layers=layers[:3]),{})
    monitor_cov=Path(layers[3]['coverage']);monitor_orig=monitor_cov.read_text();changed=json.loads(monitor_orig);changed['validation_only']=True;monitor_cov.write_text(json.dumps(changed))
    with self.assertRaises(feed.PublicationBlocked):feed.publication_gate(manifest,{})
    monitor_cov.write_text(monitor_orig)
    path=Path(layers[0]['coverage']);original=json.loads(path.read_text())
    for mutation in ['entry_only','running','no_evidence','missing_component','not_sealed']:
     coverage=deepcopy(original)
     if mutation=='entry_only':coverage['sources'][0]['status']='entry_readable'
     elif mutation=='running':coverage['collection_complete']=False
     elif mutation=='no_evidence':coverage['sources'][0]['evidence_call_ids']=[]
     elif mutation=='missing_component':coverage['sources'][0]['components']=[]
     else:check['status']='running_or_interrupted'
     path.write_text(json.dumps(coverage))
     with self.assertRaises(feed.PublicationBlocked,msg=mutation):feed.publication_gate(manifest,{})
     check['status']='completed'
 def test_analysis_is_separate_from_valuation_evidence(self):
  raw=message(layer=2,source_id='M26',source_role='vendor_data',event_key='same-event')
  analysis=message(id='analysis',layer=4,source_id='A01',source_name='自分析',source_role='analysis',event_key='same-event',record_kind='板块低位自分析',fact_values={'basis':'valuation','valuation':qualified_valuation()})
  rows,_=feed.merge([raw,analysis],{},NOW);self.assertEqual(2,len(rows))
  root=ET.fromstring(feed.rss(rows,{'repository':'a/b','channel_title':'x','feed_url':'https://e.org/rss'},NOW))
  self.assertIn('自分析·板块低位监控',[x.text for x in root.findall('.//category')])
 def test_rebound_close_revision_preserves_analysis_guid(self):
  a=message(layer=4,source_id='A01',source_role='analysis',source_record_id='CN/valuation/day/rebound',record_kind='板块低位自分析',fact_values={'confirmed':False})
  first,_=feed.merge([a],{},NOW)
  b=dict(a,fact_values={'confirmed':True},updated_at=NOW,material_update=True)
  changed,_,_=feed.incremental_records([b],{'items':first});second,_=feed.merge(changed,{'items':first},NOW)
  self.assertEqual(len(second),1);self.assertEqual(first[0]['guid'],second[0]['guid'])
 def test_own_analysis_can_be_generated_after_fixed_news_window(self):
  start=feed.dt('2026-11-25T12:00:00Z');end=feed.dt(NOW);asof=feed.dt('2026-11-26T14:00:00Z')
  m=message(layer=4,source_id='A01',source_role='analysis',record_kind='板块低位自分析',published_at='2026-11-26T13:00:00Z')
  self.assertEqual('generated_analysis_during_run',feed.window_status(m,start,end,{},asof)[1])
  self.assertFalse(feed.window_status(dict(m,source_role='reporting'),start,end,{},asof)[0])
  old=dict(m,published_at='2026-11-20T00:00:00Z',updated_at='2026-11-26T13:00:00Z',material_update=True)
  self.assertEqual('material_analysis_revision',feed.window_status(old,start,end,{},asof)[1])
  self.assertFalse(feed.window_status(dict(m,published_at='2026-11-26T15:00:00Z'),start,end,{},asof)[0])
if __name__=='__main__':unittest.main()
