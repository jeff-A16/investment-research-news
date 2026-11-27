import importlib.util
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import unittest

spec=importlib.util.spec_from_file_location('sector_monitor',Path(__file__).parents[1]/'scripts/monitor.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
CFG=json.loads((Path(__file__).parents[1]/'references/universe.json').read_text())
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


def run(snap=None,prices=None,old=None,now=NOW):
    return m.process(SECTOR,snapshot() if snap is None else snap,bars() if prices is None else prices,now,CFG,old,'test-run',[('source','https://example.org/data')])


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
        found,_,_=m.process(sector,snapshot(pe=0),bars(),NOW,CFG,None,'r',[])
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

    def test_intraday_confirmation_reuses_identity_and_first_time(self):
        _,state,_=run()
        found,state,_=run(prices=bars(True,False),old=state)
        self.assertEqual(1,len(found));first=found[0]
        final,state,_=run(prices=bars(True,True),old=state,now=NOW+timedelta(hours=4))
        self.assertEqual(1,len(final));self.assertEqual(first['source_record_id'],final[0]['source_record_id'])
        self.assertEqual(first['published_at'],final[0]['published_at']);self.assertTrue(final[0]['material_update'])
        self.assertFalse(run(prices=bars(True,True),old=state)[0])

    def test_failed_close_revises_pending_rebound_once(self):
        _,state,_=run()
        _,state,_=run(prices=bars(True,False),old=state)
        found,state,_=run(prices=bars(False,True),old=state)
        self.assertEqual(1,len(found));self.assertIn('收盘未确认',found[0]['title'])
        self.assertFalse(run(prices=bars(False,True),old=state)[0])

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
        self.assertIsNone(detail['price']['drawdown_percentile'])

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


if __name__=='__main__':unittest.main()
