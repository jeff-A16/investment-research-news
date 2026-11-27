import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('review_run', Path(__file__).parents[1] / 'scripts/review_run.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def source(sid, status, at, **kw):
    return dict(source_id=sid, status=status, checked_at=at,
                evidence_call_ids=['call-' + sid], reason='evidence in run log', **kw)


class ReviewTests(unittest.TestCase):
    def test_no_new_is_not_failed_route(self):
        old = {'run_id': 'old', 'sources': [source('S13', 'content_collected', '2026-11-26T01:00:00Z')]}
        now = {'run_id': 'now', 'sources': [source('S13', 'no_new_verified', '2026-11-27T01:00:00Z')]}
        r = m.review(now, [old])
        self.assertEqual([], r['source_component_candidates'])
        self.assertEqual(1, len(r['verified_no_new']))

    def test_failed_child_is_detected_despite_successful_parent(self):
        old = {'run_id': 'old', 'sources': [source('P02', 'content_collected', '2026-11-26T01:00:00Z',
            components=[dict(component='MLF', status='content_collected', checked_at='2026-11-26T01:00:00Z')])]}
        now = {'run_id': 'now', 'sources': [source('P02', 'content_collected', '2026-11-27T01:00:00Z',
            components=[dict(component='MLF', status='access_limited_verified', checked_at='2026-11-27T01:00:00Z')])]}
        r = m.review(now, [old])
        self.assertEqual(['MLF'], [x['component'] for x in r['source_component_candidates']])
        self.assertEqual('unknown', r['source_component_candidates'][0]['root_cause'])

    def test_sample_and_future_runs_cannot_prove_previous_chain_success(self):
        now = {'run_id': 'now', 'sources': [source('P43', 'access_limited_verified', '2026-11-27T01:00:00Z')]}
        future = {'run_id': 'future', 'sources': [source('P43', 'content_collected', '2026-11-28T01:00:00Z')]}
        sample = {'sources': [dict(source_id='P43', previous_sample_tier='A')]}
        r = m.review(now, [future], sample)
        self.assertEqual('historical_sample_only', r['source_component_candidates'][0]['baseline_type'])
        self.assertEqual([], r['route_regressions'])

    def test_same_route_cache_regression_preserves_last_readable(self):
        now = {'run_id': 'now', 'sources': [source('P43', 'access_limited_verified', '2026-11-27T01:00:00Z')]}
        before = dict(run_id='old', source_id='P43', route_id='official-matrix', checked_at='2026-11-26T01:00:00Z', evidence_call_ids=['old'], outcome='readable')
        key = m.json.dumps(('P43', '', 'official-matrix'), ensure_ascii=False, separators=(',', ':'))
        obs = dict(before, run_id='now', outcome='stale', root_cause='stale_cache', checked_at='2026-11-27T01:00:00Z', evidence_call_ids=['new'])
        r = m.review(now, [], observations=[obs], ledger={'routes': {key: {'last_readable': before}}})
        self.assertEqual('freshness_regression', r['route_regressions'][0]['classification'])
        self.assertEqual(before, r['ledger']['routes'][key]['last_readable'])

    def test_alternate_adapter_cannot_repair_original_route(self):
        now = {'run_id': 'now', 'sources': [source('M05', 'content_collected', '2026-11-27T01:00:00Z')]}
        original = dict(run_id='old', source_id='M05', route_id='http', checked_at='2026-11-26T01:00:00Z', evidence_call_ids=['old'], outcome='readable')
        key = m.json.dumps(('M05', '', 'http'), ensure_ascii=False, separators=(',', ':'))
        other = dict(original, run_id='now', route_id='firecrawl', checked_at='2026-11-27T01:00:00Z', evidence_call_ids=['fc'])
        r = m.review(now, [], observations=[other], ledger={'routes': {key: {'last_readable': original}}})
        self.assertEqual(original, r['ledger']['routes'][key]['last_readable'])
        self.assertEqual(2, len(r['ledger']['routes']))

    def test_unscoped_or_unevidenced_receipt_is_rejected(self):
        now = {'run_id': 'now', 'sources': [source('M05', 'content_collected', '2026-11-27T01:00:00Z')]}
        bad = dict(run_id='now', source_id='M05', component='missing', route_id='http', checked_at='2026-11-27T01:00:00Z', evidence_call_ids=['x'], outcome='readable')
        with self.assertRaises(ValueError):
            m.review(now, [], observations=[bad])

    def test_omitted_successful_child_is_not_silently_lost(self):
        old = {'run_id': 'old', 'sources': [source('P02', 'content_collected', '2026-11-26T01:00:00Z',
            components=[dict(component='MLF', status='content_collected', checked_at='2026-11-26T01:00:00Z')])]}
        now = {'run_id': 'now', 'sources': [source('P02', 'content_collected', '2026-11-27T01:00:00Z', components=[])]}
        r = m.review(now, [old])
        self.assertEqual('MLF', r['source_component_candidates'][0]['component'])
        self.assertEqual('missing_current_result', r['source_component_candidates'][0]['current_status'])


if __name__ == '__main__':
    unittest.main()
