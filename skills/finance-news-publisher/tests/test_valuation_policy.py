import importlib.util
from pathlib import Path
import unittest
import tempfile

spec=importlib.util.spec_from_file_location('policy_feed',Path(__file__).parents[1]/'scripts/build_feed.py')
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)


def analysis(pct=.1,basis='valuation',**extra):
    reading=dict(value=25,percentile=pct,data_date='2026-11-26',window_ok=True,flags={});reading.update(extra)
    return dict(record_kind='板块低位自分析',fact_values={'basis':basis,'valuation':reading})


class HardThresholdTests(unittest.TestCase):
    def test_boundary_unknown_quality_and_alternate_trigger(self):
        self.assertTrue(f.policy_rules.eligible(analysis(),f.VALUATION_POLICY)[0])
        for row in [analysis(.100001),analysis(None),analysis(float('nan')),analysis(.01,basis='price_drawdown'),analysis(.01,window_ok=False),analysis(.01,flags={'stale':True})]:
            self.assertFalse(f.policy_rules.eligible(row,f.VALUATION_POLICY)[0])
        renamed=dict(analysis(.4468),layer=4,record_kind='other')
        self.assertFalse(f.policy_rules.eligible(renamed,f.VALUATION_POLICY)[0])

    def test_legacy_state_is_filtered_without_deleting_originals(self):
        low=dict(analysis(),guid='low',data_period='2026-11-26')
        high=dict(analysis(.4468),guid='high',data_period='2026-11-26')
        price=dict(analysis(.01,'price_drawdown'),guid='price',data_period='2026-11-26')
        items=[low,high,price];self.assertEqual([low],f.rss_entries(items,{}));self.assertEqual(3,len(items))

    def test_snapshot_requires_qualified_evidence_and_correct_units(self):
        row=dict(record_kind='指数估值快照',data_period='2026-11-26',source_id='M27',source_record_id='399006.SZ',title='创业板',fact_values={'fields':'PE=25、PB=3；PE分位=5、PB分位=8（0—100）'})
        packet={'provider':'funddb_valuation','data':{'rows':[dict(code='399006.SZ',pe=25,pe_pct=5,update_time='2026-11-26',start_date='2016-01-01')]}}
        meta=f.policy_rules.snapshot_metadata(row,[packet],f.VALUATION_POLICY);self.assertEqual(.05,meta['percentile'])
        row['valuation_screen']=meta;self.assertTrue(f.policy_rules.eligible(row,f.VALUATION_POLICY)[0])
        row['valuation_screen']=dict(meta,percentile=.100001);self.assertFalse(f.policy_rules.eligible(row,f.VALUATION_POLICY)[0])
        packet['data']['rows'][0].pop('start_date');row.pop('valuation_screen');meta=f.policy_rules.snapshot_metadata(row,[packet],f.VALUATION_POLICY)
        self.assertFalse(f.policy_rules.qualified(meta,f.VALUATION_POLICY)[0])

    def test_new_snapshot_cannot_self_certify_its_window(self):
        row=dict(record_kind='指数估值快照',source_id='M26',valuation_screen={'window_ok':True,'value':25,'percentile':.01,'data_date':'2026-11-26'})
        with tempfile.TemporaryDirectory() as d:f.annotate_snapshot(row,Path(d))
        self.assertIsNone(row['valuation_screen'])


if __name__=='__main__':unittest.main()
