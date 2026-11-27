#!/usr/bin/env python3
"""Append-only research audit helper. No network calls or automatic tool interception."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid
import zipfile

SKILLS = ('finance-primary-intelligence', 'finance-market-research', 'finance-social-sentiment', 'finance-news-publisher', 'finance-sector-monitor')
EVENT_TYPES = ('plan', 'tool_start', 'tool_end', 'retry', 'error', 'candidate',
               'filter', 'dedup', 'verification', 'assessment', 'coverage',
               'checkpoint', 'gap', 'resume', 'note')
OUTPUTS = ('outputs/report.md', 'outputs/messages.json', 'outputs/coverage.json',
           'outputs/run-summary.md')
SECRET = re.compile(r'^(authorization|proxy-authorization|cookie|set-cookie|password|passwd|api[_-]?(?:key|token)|x[_-](?:api[_-]key|finnhub[_-]token)|access[_-]?token|refresh[_-]?token|client[_-]?secret)$', re.I)


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def clean(value):
    """Defense in depth; callers must review arbitrary text/binary artifacts themselves."""
    if isinstance(value, dict):
        return {k: '[REDACTED_CREDENTIAL]' if SECRET.fullmatch(str(k)) else clean(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+', 'Bearer [REDACTED_CREDENTIAL]', value)
        return re.sub(r'(?i)([?&](?:authorization|sid_token|securitytoken|access_token|refresh_token|api_key|apikey|api_token|token|signature|x-amz-signature)=)[^&#\s]+', r'\1[REDACTED_CREDENTIAL]', value)
    return value


def load_json(filename):
    if filename == '-':
        return json.load(sys.stdin)
    return json.loads(Path(filename).read_text(encoding='utf-8'))


def save_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as handle:
        handle.write(data)
        handle.flush()
        import os
        os.fsync(handle.fileno())


def target(run, relative):
    p = Path(relative)
    if p.is_absolute() or '..' in p.parts or not p.parts:
        raise ValueError('Artifact name must be a safe relative path')
    result = run / p
    if not result.resolve().is_relative_to(run.resolve()):
        raise ValueError('Artifact path escapes run directory')
    if p.parts[0] not in ('inputs', 'snapshots', 'artifacts', 'outputs', 'checkpoints'):
        raise ValueError('Artifact name must start with inputs/snapshots/artifacts/outputs/checkpoints')
    return result


@contextmanager
def locked(run):
    if not (run / 'manifest.json').is_file():
        raise ValueError('Not an initialized run directory')
    with (run / '.writer.lock').open('a') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_events(run):
    data = (run / 'events.jsonl').read_bytes()
    if data and not data.endswith(b'\n'):
        raise ValueError('Incomplete final event: preserve file and record recovery in a new linked run')
    result, previous = [], '0' * 64
    for n, line in enumerate(data.splitlines(), 1):
        event = json.loads(line)
        recorded = event.pop('sha256')
        if event['seq'] != n or event['prev_sha256'] != previous or digest(canonical(event)) != recorded:
            raise ValueError(f'Event integrity failure at line {n}')
        event['sha256'] = recorded
        result.append(event)
        previous = recorded
    return result


def append_unlocked(run, kind, data):
    events = read_events(run)
    if any(e['type'] == 'run_end' for e in events) and kind not in ('run_export',):
        raise ValueError('Run is closed; create a new run with parent_run for further work')
    meta = json.loads((run / 'manifest.json').read_text())
    event = {'seq': len(events)+1, 'at': now(), 'run_id': meta['run_id'],
             'skill': meta['skill'], 'type': kind, 'data': clean(data),
             'prev_sha256': events[-1]['sha256'] if events else '0'*64}
    event['sha256'] = digest(canonical(event))
    with (run / 'events.jsonl').open('ab') as handle:
        handle.write(canonical(event)+b'\n')
        handle.flush()
        import os
        os.fsync(handle.fileno())
    return event


def capture_unlocked(run, filename, relative, metadata):
    dest = target(run, relative)
    data = Path(filename).read_bytes()
    save_new(dest, data)
    return append_unlocked(run, 'artifact_saved', {
        'path': relative, 'sha256': digest(data), 'bytes': len(data),
        'metadata': clean(metadata), 'credential_review': 'caller-reviewed-safe-input'})


def check(run):
    events = read_events(run)
    artifacts = {e['data']['path']: e['data'] for e in events if e['type']=='artifact_saved'}
    errors = []
    for relative, item in artifacts.items():
        p = target(run, relative)
        if not p.is_file() or digest(p.read_bytes()) != item['sha256']:
            errors.append('Missing or changed artifact: '+relative)
    tracked = set(artifacts) | {'manifest.json', 'events.jsonl', '.writer.lock'}
    for p in run.rglob('*'):
        if p.is_file() and str(p.relative_to(run)) not in tracked:
            errors.append('Untracked artifact: '+str(p.relative_to(run)))
    if not events or events[0]['type'] != 'run_start':
        errors.append('Missing run_start')
    elif digest((run/'manifest.json').read_bytes()) != events[0]['data']['manifest_sha256']:
        errors.append('Changed manifest')
    starts, ends = {}, {}
    for e in events:
        if e['type'] in ('tool_start', 'tool_end'):
            call_id = e['data'].get('call_id')
            if not call_id:
                errors.append('Tool event lacks call_id')
                continue
            group = starts if e['type']=='tool_start' else ends
            if call_id in group:
                errors.append('Duplicate tool event call_id: '+call_id)
            group[call_id] = e
    for call_id, e in starts.items():
        if not e['data'].get('tool') or not e['data'].get('source_ids'):
            errors.append('tool_start lacks tool/source_ids: '+call_id)
    for call_id, e in ends.items():
        data = e['data']
        if call_id not in starts or starts[call_id]['seq'] >= e['seq']:
            errors.append('tool_end lacks earlier tool_start: '+call_id)
        if data.get('status') not in ('success','error','cancelled','timeout'):
            errors.append('tool_end invalid status: '+call_id)
        if 'duration_ms' not in data or 'truncated' not in data:
            errors.append('tool_end lacks duration_ms/truncated: '+call_id)
        if 'result' not in data and not data.get('result_paths'):
            errors.append('tool_end lacks result or result_paths: '+call_id)
        for relative in data.get('result_paths', []):
            if relative not in artifacts:
                errors.append('Unregistered result artifact: '+relative)
    pending = sorted(set(starts)-set(ends))
    missing = [p for p in OUTPUTS if p not in artifacts]
    terminal = [e for e in events if e['type']=='run_end']
    status = terminal[-1]['data']['status'] if terminal else 'running_or_interrupted'
    if status=='completed' and (pending or missing):
        errors.append('Completed run has unfinished calls or missing outputs')
    return {'integrity_ok': not errors, 'status': status,
            'log_completeness':terminal[-1]['data'].get('log_completeness','unknown') if terminal else 'unknown',
            'event_count':len(events),
            'artifact_count':len(artifacts), 'pending_call_ids':pending,
            'missing_outputs':missing, 'errors':errors,
            'limitation':'Checks recorded data only; cannot detect tool calls never submitted to this helper.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('init')
    p.add_argument('--root', default='research-logs')
    p.add_argument('--skill', choices=SKILLS, required=True)
    p.add_argument('--run-id')
    p.add_argument('--parent-run')
    p.add_argument('--config-file', required=True, help='Reviewed JSON; use - for stdin')
    p = sub.add_parser('event')
    p.add_argument('--run', required=True)
    p.add_argument('--type', choices=EVENT_TYPES, required=True)
    p.add_argument('--data-file', required=True, help='Reviewed JSON; use - for stdin')
    p = sub.add_parser('capture')
    p.add_argument('--run', required=True)
    p.add_argument('--file', required=True)
    p.add_argument('--name', required=True)
    p.add_argument('--safe-input', action='store_true', required=True,
                   help='Declare this input has been reviewed for credentials; not an approval prompt')
    p.add_argument('--metadata-file')
    p = sub.add_parser('finalize')
    p.add_argument('--run', required=True)
    p.add_argument('--status', choices=('completed','partial','failed','cancelled'), required=True)
    p.add_argument('--log-completeness', choices=('complete','partial','unknown'), required=True)
    p.add_argument('--notes', required=True)
    p = sub.add_parser('verify')
    p.add_argument('--run', required=True)
    p = sub.add_parser('export')
    p.add_argument('--run', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--allow-incomplete', action='store_true', help='Export damaged/incomplete evidence without repairing it')
    args = parser.parse_args()

    if args.command=='init':
        run_id = args.run_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:12]
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', run_id):
            raise ValueError('Invalid run-id')
        config = clean(load_json(args.config_file))
        run = Path(args.root).resolve()/run_id/args.skill
        run.mkdir(parents=True, exist_ok=False)
        manifest = {'schema_version':1, 'run_id':run_id, 'skill':args.skill,
                    'started_at':now(), 'parent_run':args.parent_run,
                    'retention':'keep-until-user-explicitly-requests-deletion',
                    'config':config}
        save_new(run/'manifest.json', canonical(manifest)+b'\n')
        save_new(run/'events.jsonl', b'')
        with locked(run):
            append_unlocked(run, 'run_start', {'manifest_sha256':digest((run/'manifest.json').read_bytes())})
            skill_root = Path(__file__).resolve().parent.parent
            for p in sorted(skill_root.rglob('*')):
                if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc':
                    capture_unlocked(run, p, 'snapshots/skill/'+str(p.relative_to(skill_root)), {'kind':'skill_version'})
        print(json.dumps({'run_dir':str(run),'run_id':run_id},ensure_ascii=False))
        return

    run = Path(args.run).resolve()
    with locked(run):
        if args.command=='event':
            data = clean(load_json(args.data_file))
            if not isinstance(data,dict):
                raise ValueError('Event data must be a JSON object')
            if args.type in ('tool_start','tool_end') and not data.get('call_id'):
                raise ValueError('Tool events require a unique call_id')
            print(json.dumps(append_unlocked(run,args.type,data),ensure_ascii=False))
        elif args.command=='capture':
            # Fail before copying if closed, to avoid unregistered artifacts.
            if any(e['type']=='run_end' for e in read_events(run)):
                raise ValueError('Run is closed')
            meta = load_json(args.metadata_file) if args.metadata_file else {}
            print(json.dumps(capture_unlocked(run,args.file,args.name,meta),ensure_ascii=False))
        elif args.command=='finalize':
            result = check(run)
            if not result['integrity_ok']:
                raise ValueError('Fix/report integrity errors before finalizing: '+str(result['errors']))
            if args.status=='completed' and (result['pending_call_ids'] or result['missing_outputs']):
                raise ValueError('Cannot complete: pending tool calls or missing report/messages/coverage/run-summary')
            print(json.dumps(append_unlocked(run,'run_end',{'status':args.status,'log_completeness':args.log_completeness,'notes':args.notes,'verification':result}),ensure_ascii=False))
        elif args.command=='verify':
            result = check(run)
            print(json.dumps(result,ensure_ascii=False,indent=2))
            if not result['integrity_ok']:
                sys.exit(1)
        elif args.command=='export':
            try:
                result = check(run)
            except (OSError, ValueError, KeyError) as exc:
                result = {'integrity_ok':False,'errors':[str(exc)],'status':'unknown','log_completeness':'unknown'}
            if not result['integrity_ok'] and not args.allow_incomplete:
                raise ValueError('Integrity check failed: '+str(result['errors']))
            out = Path(args.output).resolve()
            if out.is_relative_to(run):
                raise ValueError('Archive must be outside the run directory')
            if out.exists():
                raise ValueError('Refusing to overwrite archive')
            out.parent.mkdir(parents=True,exist_ok=True)
            if result['integrity_ok']:
                append_unlocked(run,'run_export',{'archive_name':out.name,'verification':result})
            with zipfile.ZipFile(out,'x',zipfile.ZIP_DEFLATED) as archive:
                for p in sorted(run.rglob('*')):
                    if p.is_file() and p.name!='.writer.lock':
                        archive.write(p,Path(run.parent.name)/run.name/p.relative_to(run))
            print(json.dumps({'archive':str(out),'sha256':digest(out.read_bytes()),'verification':result},ensure_ascii=False))


if __name__=='__main__':
    try:
        main()
    except (OSError,ValueError,KeyError,json.JSONDecodeError) as exc:
        print(f'Audit logger error: {exc}',file=sys.stderr)
        sys.exit(1)
