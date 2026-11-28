import importlib.util
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import unittest

spec=importlib.util.spec_from_file_location('sector_monitor',Path(__file__).parents[1]/'scripts/monitor.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
CFG=m.load_config()
SECTOR=CFG['watchlist'][0]
NOW=datetime(2026,11,27,4,tzinfo=timezone.utc)


def snapshot(pct=.05,pe=25,day='2026-11-26',**kw):
    row=dict(index_code='SZ399006',pe=pe,pb=3,pe_percentile=pct,pb_percentile=.05,
        ts=datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()*1000,
        begin_at=datetime(2016,8,11,tzinfo=timezone.utc).timestamp()*1000)
    row.update(kw)
    return {'danjuan':[row]}


def bars(bounce=False,final=True):
    result=[dict(date=(datetime(2026,10,28)+timedelta(days=i)).date().isoformat(),close=100,final=True,basis='index_points') for i in range(30)]
    result[-2]['close']=97
    result[-1].update(close=103 if bounce else 98,final=final)
    return result


def history(value=25,day='2026-11-26',metric='PE'):
    end=datetime.fromisoformat(day)
    points=[dict(date=(end-timedelta(days=2000-i)).date().isoformat(),value=value*4,price=100) for i in range(2001)]
    points[-2].update(value=value*.8);points[-1].update(value=value,price=90)
    return dict(provider='funddb',code='399006.SZ',metric=metric,points=points,series_sha256='a'*64)


def run(snap=None,prices=None,old=None,now=NOW,hist='auto',cause=None):
    snap=snapshot() if snap is None else snap
    if hist=='auto':
        row=(snap.get('danjuan') or [{}])[0];day=datetime.fromtimestamp(row.get('ts',NOW.timestamp()*1000)/1000,timezone.utc).astimezone(m.ZoneInfo('Asia/Shanghai')).date().isoformat()
        value=row.get('pe');hist=history(value if value is not None and value>0 else 25,day)
    return m.process(SECTOR,snap,bars() if prices is None else prices,now,CFG,old,'test-run',[('source','https://example.org/data')],hist,cause)


class MonitorTests(unittest.TestCase):
    def test_rank_boundary_and_ties(self):
        self.assertEqual(.5,m.rank([1,1,1],1))
        self.assertEqual(.1,m.rank(list(range(10)),.5))
        found,_,_=run(snapshot(.10));self.assertEqual(1,len(found))
        self.assertFalse(run(snapshot(.10001))[0])

    def test_zero_negative_nan_or_stale_pe_cannot_trigger(self):
        for value in [0,-1,float('nan')]:self.assertFalse(run(snapshot(pe=value))[0])
        self.assertFalse(run(snapshot(stale=True))[0])
        self.assertFalse(run(snapshot(day='2026-10-01'))[0])

    def test_short_valuation_window_and_conflicting_platforms(self):
        short=snapshot(begin_at=datetime(2025,1,1,tzinfo=timezone.utc).timestamp()*1000)
        self.assertFalse(run(short)[0])
        conflict=snapshot();conflict['funddb']=[dict(code='399006.SZ',pe=25,pe_pct=80,update_time='2026-11-26',start_date='')]
        self.assertFalse(run(conflict)[0])

    def test_financial_pb_does_not_treat_zero_pe_as_cheap(self):
        sector=dict(SECTOR,metric='PB')
        found,_,_=m.process(sector,snapshot(pe=0),bars(),NOW,CFG,None,'r',[],history(3,metric='PB'))
        self.assertEqual(1,len(found));self.assertEqual('PB',found[0]['fact_values']['valuation']['metric'])
        self.assertFalse(run(snapshot(pe=0))[0])

    def test_persistent_low_is_not_repeated_across_three_polls(self):
        first,state,_=run();self.assertEqual(1,len(first))
        for i in range(3):
            found,state,_=run(old=state,now=NOW+timedelta(minutes=i+1));self.assertFalse(found)

    def test_rearm_counts_distinct_dates_and_preserves_episode_history(self):
        _,state,_=run()
        for i in range(3):_,state,_=run(snapshot(.2),old=state,now=NOW+timedelta(minutes=i+1))
        self.assertTrue(state['channels']['valuation']['active'])
        for i,day in enumerate(['2026-11-27','2026-11-28']):
            _,state,_=run(snapshot(.2,day=day),old=state,now=NOW+timedelta(days=i+1))
        self.assertFalse(state['channels']['valuation']['active']);self.assertEqual(1,len(state['history']))

    def test_low_entry_is_only_notification_even_during_rebound_or_close(self):
        first,state,_=run();self.assertEqual(1,len(first));self.assertEqual('entry',first[0]['fact_values']['phase'])
        self.assertNotIn('反弹',first[0]['title']);self.assertNotIn('首次',first[0]['title'])
        for prices in [bars(True,False),bars(True,True),bars(False,True)]:
            found,state,_=run(prices=prices,old=state,now=NOW+timedelta(hours=4));self.assertFalse(found)

    def test_legacy_rebound_record_is_retained_without_close_revision(self):
        _,state,_=run();state['channels']['valuation']['alerts']['rebound']={'published_at':NOW.isoformat(),'confirmed':False,'run_id':'old'}
        before=deepcopy(state['channels']['valuation']['alerts']['rebound'])
        found,state,_=run(prices=bars(True,True),old=state)
        self.assertFalse(found);self.assertEqual(state['channels']['valuation']['alerts']['rebound'],before)

    def test_future_price_is_ignored_and_duplicate_date_rejected(self):
        base=bars();future=base+[dict(date='2026-11-28',close=10000,final=False,basis='index_points')]
        a,_=m.price_metrics(base,NOW,SECTOR,CFG);b,_=m.price_metrics(future,NOW,SECTOR,CFG)
        self.assertEqual(a,b)
        with self.assertRaises(ValueError):m.price_metrics(base+[base[-1]],NOW,SECTOR,CFG)

    def test_does_not_mutate_committed_state(self):
        _,old,_=run();before=deepcopy(old)
        run(prices=bars(True),old=old);self.assertEqual(old,before)

    def test_no_valuation_no_short_history_false_low(self):
        found,_,detail=run(snap={});self.assertFalse(found);self.assertIsNone(detail['valuation'])
        self.assertIsNone(detail['valuation_history'])

    def test_deep_price_drawdown_cannot_replace_valuation_gate(self):
        prices=[dict(date=(datetime(2020,1,1)+timedelta(days=i)).date().isoformat(),close=100,final=True,basis='index_points') for i in range(2521)]
        prices[-1].update(date='2026-11-27',close=30,final=False)
        for data in [{},snapshot(.4468),snapshot(.100001),snapshot(.05,stale=True)]:
            found,_,detail=run(snap=data,prices=prices)
            self.assertFalse(found)
            metrics,_=m.price_metrics(prices,NOW,SECTOR,CFG);self.assertTrue(metrics['history_qualified']);self.assertLessEqual(metrics['drawdown_percentile'],.10);self.assertLessEqual(metrics['drawdown'],-.20)
        legacy={'channels':{'price_drawdown':{'active':True,'release_days':[],'alerts':{}}},'history':[]}
        found,state,_=run(snap={},prices=prices,old=legacy)
        self.assertFalse(found);self.assertFalse(state['channels']['price_drawdown']['active']);self.assertTrue(state['history'])

    def test_rebound_does_not_bypass_current_valuation_threshold(self):
        _,state,_=run(snapshot(.05))
        self.assertFalse(run(snapshot(.11),prices=bars(True,False),old=state)[0])
        self.assertFalse(run(snapshot(.11),prices=bars(True,True),old=state)[0])
        self.assertFalse(run({},prices=bars(True,False),old=state)[0])

    def test_missing_short_conflicting_or_newer_history_does_not_consume_entry(self):
        for hist in [None,dict(history(),metric='PB'),dict(history(),points=history()['points'][-20:]),history(40),history(day='2026-11-27')]:
            found,state,detail=run(hist=hist);self.assertFalse(found);self.assertNotIn('entry',state['channels']['valuation']['alerts']);self.assertEqual('unqualified',detail['history_status'])
        found,_,_=run();self.assertEqual(len(found),1)

    def test_ratio_is_current_divided_by_real_minimum_not_percentile(self):
        found,_,_=run();facts=found[0]['fact_values'];h=facts['valuation_history']
        self.assertEqual(h['minimum'],20);self.assertEqual(h['current'],25);self.assertEqual(h['current_to_min_percent'],125)
        self.assertLess(facts['valuation']['percentile'],.1);self.assertNotEqual(h['current_to_min_percent'],facts['valuation']['percentile']*100)
        self.assertIn('代理',facts['low_cause']);self.assertNotIn('\n',facts['low_cause'])

    def test_returned_full_history_also_must_match_valuation_threshold(self):
        hist=history();hist['points']=[dict(x,value=20 if i<1900 else 25) for i,x in enumerate(hist['points'])]
        self.assertFalse(run(hist=hist)[0])

    def test_history_parser_uses_metric_series_instead_of_mean_or_percentile_band(self):
        stamps=[datetime(2020,1,1,tzinfo=timezone.utc).timestamp()*1000,datetime(2026,11,26,tzinfo=timezone.utc).timestamp()*1000]
        packet={'code':0,'data':{'new_pe':'25','tubiao':{'series':[{'name':'平均值','data':[[x,999] for x in stamps]},{'name':'最低10%','data':[[x,.1] for x in stamps]},{'name':'市盈率','data':[[stamps[0],20],[stamps[1],25]]}]}}}
        parsed=m.history_rules.parse_history(json.dumps(packet).encode(),'399006.SZ','PE')
        self.assertEqual([r['value'] for r in parsed['points']],[20,25])
        with self.assertRaises(ValueError):m.history_rules.parse_history(json.dumps(packet).encode(),'399006.SZ','PB')

    def test_full_series_cannot_override_explicit_provider_quality_flags(self):
        for key in ['stale','insufficient','not_applicable']:
            with self.assertRaises(ValueError):m.history_rules.snapshot_row(history(),NOW,flags={key:True})

    def test_full_history_snapshot_does_not_mix_platform_current_values(self):
        hist=history(25);row=m.history_rules.snapshot_row(hist,NOW)
        self.assertEqual(row['pe'],25);self.assertLess(row['pe_pct'],10);self.assertTrue(row['_from_full_history'])
        local={'danjuan':snapshot()['danjuan'],'funddb':[row]};v,_,_=m.valuations(SECTOR,local,NOW,CFG)
        self.assertEqual(v['provider'],'funddb');self.assertEqual(v['method'],'computed_from_provider_full_series')
        bad=dict(row,pe_pct=30);self.assertIsNone(m.valuations(SECTOR,{'danjuan':local['danjuan'],'funddb':[bad]},NOW,CFG)[0])

    def test_rising_metric_is_not_explained_as_recent_compression(self):
        summary=dict(metric='PE',one_year={'price_change':-.1,'metric_change':.125,'denominator_proxy_change':-.2})
        text=m.history_rules.data_cause(summary);self.assertIn('未继续压缩',text)
        self.assertNotIn('降幅超过',text)

    def test_post_history_replay_keys_distinguish_index_and_metric(self):
        with __import__('tempfile').TemporaryDirectory() as tmp:
            run_path=Path(tmp);folder=run_path/'artifacts/http';folder.mkdir(parents=True)
            for code,metric,value in [('399006.SZ','pe',25),('399975.SZ','pb',1)]:
                url='https://example.org/history';key=url+'?gu_code='+code+'&pe_category='+metric+'&year=-1'
                (folder/(code+'.json')).write_text(json.dumps(dict(url=url,request_key=key,http_status=200,body=json.dumps({'value':value}))))
            audit=m.Audit(run_path,replay=run_path)
            self.assertEqual(len(audit.replay),2)

    def test_yahoo_requires_adjusted_prices_and_correct_instrument(self):
        data={'chart':{'result':[{'meta':{'symbol':'BAD','currency':'USD'},'timestamp':[1],'indicators':{}}],'error':None}}
        sector=next(s for s in CFG['watchlist'] if s['key']=='US-XLK')
        with self.assertRaises(ValueError):m.yahoo(json.dumps(data).encode(),sector,NOW)

    def test_cached_midday_quote_is_not_confirmed_by_later_clock(self):
        fields=['']*35;fields[2]='399006';fields[3]='103';fields[4]='100';fields[30]='20261127120000'
        raw=('v_sz399006="'+'~'.join(fields)+'";').encode()
        quote=m.tencent(raw,NOW+timedelta(hours=10))['sz399006']
        self.assertFalse(quote['final'])

    def test_platform_start_date_uses_its_local_timezone(self):
        row=snapshot(begin_at=datetime(2016,8,10,16,tzinfo=timezone.utc).timestamp()*1000)
        v,_,_=m.valuations(SECTOR,row,NOW,CFG)
        self.assertEqual('2016-08-11',v['interface_start_date'])

    def test_us_early_close_uses_source_session_end(self):
        zone=m.ZoneInfo('America/New_York');end=datetime(2026,11,27,13,tzinfo=zone).timestamp()
        data={'chart':{'result':[{'meta':{'symbol':'XLK','currency':'USD','regularMarketTime':end,'currentTradingPeriod':{'regular':{'end':end}}},'timestamp':[end-12600],'indicators':{'adjclose':[{'adjclose':[100]}]}}],'error':None}}
        sector=next(s for s in CFG['watchlist'] if s['key']=='US-XLK')
        self.assertTrue(m.yahoo(json.dumps(data).encode(),sector,NOW+timedelta(days=1))[0]['final'])
        data['chart']['result'][0]['meta']['regularMarketTime']=end-3600
        self.assertFalse(m.yahoo(json.dumps(data).encode(),sector,NOW+timedelta(days=1))[0]['final'])
        with self.assertRaises(ValueError):m.yahoo(json.dumps(data).encode(),sector,NOW)


if __name__=='__main__':unittest.main()
