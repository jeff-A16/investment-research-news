#!/usr/bin/env python3
"""Compare content audits and route receipts without equating freshness with access."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path


READABLE = {'readable', 'no_new'}
FAILURES = {'transport_error', 'blocked', 'empty_shell', 'parse_error', 'stale'}
CAUSES = {'network', 'rate_limit', 'endpoint_changed', 'authentication', 'subscription',
          'site_policy', 'dynamic_rendering', 'parser', 'stale_cache', 'calendar', 'unknown'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def rows(audit):
    result = {}
    for source in audit.get('sources', []):
        sid = source['source_id']
        if (sid, '') in result:
            raise ValueError('duplicate source: ' + sid)
        result[sid, ''] = source
        for comp in source.get('components', []):
            name = comp['component']
            if (sid, name) in result:
                raise ValueError('duplicate component: ' + sid + '/' + name)
            result[sid, name] = dict(comp, source_id=sid,
                evidence_call_ids=comp.get('evidence_call_ids', source.get('evidence_call_ids', [])))
    return result


def instant(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def route_key(row):
    # route_id describes a stable adapter + endpoint/template, NOT a source-wide alias.
    return (row['source_id'], row.get('component', ''), row['route_id'])


def review(current, history, availability=None, observations=None, ledger=None):
    run_id = current['run_id']
    present = rows(current)
    previous = {}
    for audit in history:
        if audit['run_id'] == run_id:
            continue
        for key, row in rows(audit).items():
            if row.get('status') not in {'content_collected', 'no_new_verified'}:
                continue
            if not row.get('checked_at') or not row.get('evidence_call_ids'):
                continue
            if key in present and present[key].get('checked_at') and instant(row['checked_at']) >= instant(present[key]['checked_at']):
                continue  # do not use a later run to explain an earlier one
            old = previous.get(key)
            if old is None or instant(row['checked_at']) > instant(old['checked_at']):
                previous[key] = dict(row, run_id=audit['run_id'])
    samples = {s['source_id']: s for s in (availability or {}).get('sources', [])
               if s.get('enabled', True) and s.get('previous_sample_tier') in {'A', 'B', 'C'}}
    disabled = {s['source_id'] for s in (availability or {}).get('sources', []) if not s.get('enabled', True)}
    # Interrupted collectors can omit a whole source or child, not merely mark it failed.
    for key in set(previous) | {(sid, '') for sid in samples}:
        sid, component = key
        if key not in present and sid not in disabled and present.get((sid, ''), {}).get('status') != 'out_of_scope':
            present[key] = dict(source_id=sid, status='missing_current_result',
                reason='Previously observed source/component is absent from the current audit.', evidence_call_ids=[])
    cases, no_new = [], []
    for key, row in present.items():
        sid, component = key
        status = row.get('status')
        if status == 'no_new_verified':
            no_new.append({'source_id': sid, 'component': component,
                           'reason': row.get('reason'), 'evidence_call_ids': row.get('evidence_call_ids', [])})
        if status in {'content_collected', 'no_new_verified', 'out_of_scope'}:
            continue
        old = previous.get(key)
        sample = samples.get(sid) if not component else None
        if not old and not sample:
            continue
        cases.append({'source_id': sid, 'component': component,
            'classification': 'needs_route_retest', 'root_cause': 'unknown',
            'baseline_type': 'scoped_content_audit' if old else 'historical_sample_only',
            'baseline_run': old.get('run_id') if old else None,
            'baseline_checked_at': old.get('checked_at') if old else None,
            'baseline_evidence_call_ids': old.get('evidence_call_ids', []) if old else [],
            'baseline_sample_url': sample.get('previous_sample_url') if sample else None,
            'current_status': status, 'current_checked_at': row.get('checked_at'),
            'current_reason': row.get('reason'),
            'evidence_call_ids': row.get('evidence_call_ids', []),
            'decision': 'Compare the former adapter/endpoint with current receipts; unknown is not a proven outage.'})
    old_routes = dict((ledger or {}).get('routes', {}))
    route_findings, seen = [], set()
    for obs in observations or []:
        if obs.get('run_id') != run_id or route_key(obs)[:2] not in present:
            raise ValueError('route observation is not scoped to this run/source/component')
        if not obs.get('checked_at') or not obs.get('evidence_call_ids'):
            raise ValueError('route observation requires timestamp and evidence IDs')
        if obs.get('outcome') not in READABLE | FAILURES | {'not_tested', 'policy_excluded'}:
            raise ValueError('unknown route outcome')
        cause = obs.get('root_cause', 'unknown')
        if cause not in CAUSES:
            raise ValueError('unknown root cause')
        key = json.dumps(route_key(obs), ensure_ascii=False, separators=(',', ':'))
        stamp = (key, obs['checked_at'], tuple(obs['evidence_call_ids']))
        if stamp in seen:
            raise ValueError('duplicate route receipt')
        seen.add(stamp)
        entry = dict(old_routes.get(key, {}))
        before = entry.get('last_readable')
        if before and instant(before['checked_at']) >= instant(obs['checked_at']):
            continue
        if before and obs['outcome'] in FAILURES:
            confirmed = cause != 'unknown'
            route_findings.append({'source_id': obs['source_id'], 'component': obs.get('component', ''),
                'route_id': obs['route_id'], 'baseline': before, 'current': obs,
                'classification': 'freshness_regression' if obs['outcome'] == 'stale' else 'route_regression',
                'root_cause': cause, 'cause_status': 'evidenced' if confirmed else 'unresolved'})
        if obs['outcome'] in READABLE:
            entry['last_readable'] = obs
        entry['last_observation'] = obs
        old_routes[key] = entry
    return {'format': 'finance-route-review.v1', 'run_id': run_id,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'source_component_candidates': cases, 'route_regressions': route_findings,
        'verified_no_new': no_new,
        'counts': {'candidates': len(cases), 'route_regressions': len(route_findings),
                   'verified_no_new': len(no_new), 'route_receipts': len(observations or [])},
        'limitations': [] if observations else ['No structured route receipts supplied; source status alone cannot establish route failure or recovery.'],
        'ledger': {'format': 'finance-route-history.v1', 'last_review_run': run_id, 'routes': old_routes}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--current', required=True)
    p.add_argument('--history', nargs='*', default=[])
    p.add_argument('--availability')
    p.add_argument('--observations')
    p.add_argument('--ledger')
    p.add_argument('--output', required=True)
    a = p.parse_args()
    result = review(read(a.current), [read(x) for x in a.history],
        read(a.availability) if a.availability else None,
        read(a.observations) if a.observations else None,
        read(a.ledger) if a.ledger and Path(a.ledger).exists() else None)
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    for filename, payload in [('route-history.next.json', result.pop('ledger')), ('review.json', result)]:
        path = out / filename
        if path.exists():
            raise ValueError('use a new review revision directory; never overwrite evidence')
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result['counts']))


if __name__ == '__main__':
    main()
