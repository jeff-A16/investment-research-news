#!/usr/bin/env python3
"""Run pinned upstream SkillOpt-Sleep against four explicit, reviewed skill tasks."""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import json
from pathlib import Path
import subprocess
import sys

PIN = 'dc0b5e654cafa1073a2819086151c1e0d13012f0'
SKILLS = ('finance-primary-intelligence', 'finance-market-research',
          'finance-social-sentiment', 'finance-news-publisher')


def load_tasks(path):
    from skillopt_sleep.tasks_file import load_tasks_file
    tasks, meta = load_tasks_file(str(path))
    if meta.get('reviewed') is not True:
        raise ValueError('tasks must be reviewed and redacted before replay')
    if len({t.id for t in tasks}) != len(tasks):
        raise ValueError('duplicate task IDs')
    if not all(any(t.split == split for t in tasks) for split in ('train', 'val', 'test')):
        raise ValueError('need disjoint real train, validation and test tasks')
    if any(t.origin != 'real' for t in tasks if t.split in ('val', 'test')):
        raise ValueError('held-out tasks must come from real runs')
    return tasks


def candidate_ok(before, after):
    # The shared protocol is the single rule owner; keep the entry documents compact.
    if len(after) > min(max(1200, len(before)), len(before) + min(160, max(40, len(before) // 5))):
        raise ValueError('candidate exceeds compact skill budget')
    for prefix in ('name:', 'description:'):
        if [x for x in before.splitlines() if x.startswith(prefix)] != [x for x in after.splitlines() if x.startswith(prefix)]:
            raise ValueError('candidate changed skill identity')
    if 'references/contract.md' not in after:
        raise ValueError('candidate lost shared contract')
    lines = [s.strip() for s in after.splitlines() if len(s.strip()) > 20]
    if len(lines) != len(set(lines)):
        raise ValueError('duplicate rule lines')


def test_measure(staging_dir, expected):
    events = [json.loads(line) for line in (Path(staging_dir) / 'evidence.jsonl').read_text().splitlines()]
    measured = [e for e in events if e.get('stage') == 'test' and e.get('event') == 'held_out_score']
    if not measured or measured[-1].get('n_test') != expected:
        raise ValueError('final test replay is incomplete')
    return measured[-1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--engine-root', required=True)
    p.add_argument('--repo', required=True)
    p.add_argument('--run-dir', required=True, help='private directory under the publisher run')
    p.add_argument('--tasks-dir', required=True)
    p.add_argument('--backend', choices=('handoff', 'codex', 'mock'), default='handoff')
    p.add_argument('--skill', choices=SKILLS, action='append')
    p.add_argument('--adopt', action='store_true', help='apply only accepted, reviewed proposals through upstream adopt')
    a = p.parse_args()
    engine = Path(a.engine_root).resolve()
    sha = subprocess.check_output(['git', '-C', str(engine), 'rev-parse', 'HEAD'], text=True).strip()
    if sha != PIN:
        raise ValueError('upstream revision differs; review and explicitly repin before use')
    if subprocess.check_output(['git', '-C', str(engine), 'status', '--porcelain', '--untracked-files=no'], text=True).strip():
        raise ValueError('upstream tracked source has local modifications')
    sys.path.insert(0, str(engine))
    from skillopt_sleep.config import DEFAULTS, SleepConfig
    from skillopt_sleep.cycle import run_sleep_cycle
    from skillopt_sleep.backend import build_backend
    from skillopt_sleep.handoff_backend import HandoffBackend, PendingCalls
    from skillopt_sleep.staging import adopt
    repo, root = Path(a.repo).resolve(), Path(a.run_dir).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    results, pending = [], False
    for skill in a.skill or SKILLS:
        work = root / skill
        work.mkdir(exist_ok=True, mode=0o700)
        # A completed night is immutable. A retry reads the receipt instead of retraining.
        done = work / 'result.json'
        if done.exists():
            results.append(json.loads(done.read_text()))
            continue
        target = repo / 'skills' / skill / 'SKILL.md'
        tasks = load_tasks(Path(a.tasks_dir) / (skill + '.json'))
        cfg = SleepConfig(dict(DEFAULTS, invoked_project=str(work),
            state_dir=str(work / 'state'), target_skill_path=str(target),
            managed_skill_name=skill, skill_roots=[str(repo / 'skills')],
            transcript_source='codex', backend=a.backend, replay_mode=a.backend, evolve_memory=False,
            evolve_skill=True, auto_adopt=False, llm_mine=False,
            multi_skill_fanout=False, gate_mode='on', gate_no_regression=True,
            gate_metric='mixed', edit_budget=1, max_tokens_per_night=80000,
            evidence_max_chars=1000000, redact_secrets=True,
            preferences='Keep Chinese skills concise. Shared rules belong in skills/shared/evolution.md; reference, do not duplicate. Preserve source coverage, seven fields, full private logs, entitlements, completion gate and stable GUIDs. Propose at most one bounded edit; do not copy evaluator expressions.'))
        backend = HandoffBackend(handoff_dir=str(work / 'handoff')) if a.backend == 'handoff' else build_backend(backend=a.backend)
        before = target.read_text()
        number = len(list(work.glob('engine-*.log'))) + 1
        with (work / f'engine-{number:03}.log').open('x') as log, redirect_stdout(log), redirect_stderr(log):
            try:
                outcome = run_sleep_cycle(cfg, seed_tasks=tasks, backend=backend)
            except PendingCalls:
                if not isinstance(backend, HandoffBackend) or not backend.pending:
                    raise
                backend.flush_pending()
                pending = True
                results.append({'skill': skill, 'status': 'handoff_pending',
                                'pending': len(backend.pending), 'path': str(work / 'handoff' / 'pending.json')})
                continue
            if isinstance(backend, HandoffBackend) and backend.pending:
                backend.flush_pending()
                pending = True
                results.append({'skill': skill, 'status': 'handoff_pending', 'pending': len(backend.pending)})
                continue
            report = outcome.report
            report_path = Path(outcome.staging_dir) / 'report.md'
            if not report_path.is_file():
                raise ValueError('engine did not stage a report')
            measured = test_measure(outcome.staging_dir, sum(t.split == 'test' for t in tasks))
            # Persist reviewable report before adoption. No synthetic/mock gain is deployable.
            proposed = Path(outcome.staging_dir) / 'proposed_SKILL.md'
            adopted = []
            if report.accepted and a.adopt and a.backend != 'mock':
                if report.holdout_leaked or not report.candidate_score > report.baseline_score:
                    raise ValueError('candidate lacks a clean held-out gain')
                if measured['hard'] != 1.0 or measured['soft'] != 1.0:
                    raise ValueError('candidate did not pass every final test check')
                candidate_ok(before, proposed.read_text())
                adopted = adopt(outcome.staging_dir, cfg=cfg)
            result = {'skill': skill, 'status': 'completed', 'engine_sha': sha,
                'backend': a.backend, 'tasks': len(tasks), 'baseline': report.baseline_score,
                'candidate': report.candidate_score, 'gate': report.gate_action,
                'accepted': report.accepted, 'holdout_leaked': report.holdout_leaked,
                'report': str(report_path), 'staging_dir': outcome.staging_dir,
                'adopted': adopted, 'final_test': measured,
                'before_sha256': hashlib.sha256(before.encode()).hexdigest(),
                'after_sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
            done.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
            results.append(result)
    print(json.dumps({'results': results, 'pending': pending}, ensure_ascii=False))
    return 3 if pending else 0


if __name__ == '__main__':
    sys.exit(main())
