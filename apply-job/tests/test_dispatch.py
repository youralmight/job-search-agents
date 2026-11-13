import importlib.util
import json
from pathlib import Path

import pytest

from jobhunting_core import state


spec = importlib.util.spec_from_file_location('apply_dispatch', Path(__file__).resolve().parents[1] / 'scripts/apply.py')
apply = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apply)


def row(job, status='Ready to fill', jd='The full posting text.'):
    return {'job_id': job, 'url': 'https://jobs.lever.co/acme/1', 'company': job, 'title': 'Engineer', 'state': status, 'log': '',
            'jd': jd}


class FakePaseo:
    """Unarchived workers keyed by their job label."""
    def __init__(self):
        self.sessions, self.calls, self.workspaces, self.archived = {}, [], [], []

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == 'ls':
            return json.dumps([{'id': agent} for agent in self.sessions.values()])
        if args[:2] == ('workspace', 'ls'):
            return json.dumps(self.workspaces)
        if args[:2] == ('workspace', 'create'):
            self.workspaces.append({'workspaceId': 'wks_new', 'name': args[args.index('--title') + 1], 'cwd': args[args.index('--path') + 1]})
            return json.dumps(self.workspaces[-1])
        if args[0] == 'run':
            job = _job_label(args)
            self.sessions[job] = f'agent-{job}'
            return json.dumps({'agentId': f'agent-{job}'})
        if args[0] == 'archive':
            agent = args[-1]
            del self.sessions[next(key for key, value in self.sessions.items() if value == agent)]
            self.archived.append(agent)
            return ''
        raise AssertionError(f'fill must not call paseo {args[0]}')

    def started(self):
        return [_job_label(args) for args in self.calls if args[0] == 'run']


def _job_label(args):
    return next(args[i + 1][4:] for i, arg in enumerate(args) if arg == '--label' and args[i + 1].startswith('job='))


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """fill against an in-memory Jobs list; each sleep runs the next scripted change, so a run cannot loop forever."""
    rows, paseo, browser, steps = [], FakePaseo(), {'sessions': [], 'closed': []}, []

    def run(command, **_):
        if command[:3] == ['agent-browser', 'session', 'list']:
            return type('Done', (), {'returncode': 0, 'stdout': '\n'.join(browser['sessions']), 'stderr': ''})()
        if command[0] == 'agent-browser' and 'tab' in command:
            browser['closed'].append(command[2].removeprefix('job-form-'))
        return type('Done', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

    def sleep(_seconds):
        if not steps:
            pytest.fail('fill kept waiting with nothing left to change')
        steps.pop(0)()

    monkeypatch.setattr(state, 'jobs_root', lambda: tmp_path / 'jobs')
    monkeypatch.setattr(apply, 'paseo', paseo)
    monkeypatch.setattr(apply.subprocess, 'run', run)
    monkeypatch.setattr(apply.time, 'sleep', sleep)
    monkeypatch.setattr(apply, 'build_service', lambda: object())
    monkeypatch.setattr(apply.sheet, 'read_jobs', lambda *_: [dict(item) for item in rows])
    return rows, paseo, browser, steps


def result(rows, job, status):
    return lambda: next(item for item in rows if item['job_id'] == job).update({'state': status})


def test_worker_runs_in_the_shared_workspace_with_configured_directories(harness, tmp_path, monkeypatch):
    rows, paseo, _, steps = harness
    rows.append(row('job-a'))
    paseo.workspaces.append({'workspaceId': 'wks_1', 'name': 'Apply form-fill', 'cwd': str(apply.ROOT)})
    monkeypatch.setenv('JOBHUNTING_PRIVATE_DIR', str(tmp_path / 'private'))
    monkeypatch.setenv('JOBHUNTING_TAILOR_OUTPUT', str(tmp_path / 'tailor output'))
    steps.append(result(rows, 'job-a', 'Submitted'))
    assert apply.fill() == ['job-a']
    run = next(call for call in paseo.calls if call[0] == 'run')
    assert run[run.index('--workspace') + 1] == 'wks_1'
    assert run[run.index('--mode') + 1] == 'bypassPermissions'
    assert run[run.index('--provider') + 1] == apply.PROVIDER
    assert run[run.index('--cwd') + 1] == str(apply.ROOT)
    assert [run[i + 1] for i, arg in enumerate(run) if arg == '--label'] == ['app=apply-job', 'job=job-a']
    envs = [run[i + 1] for i, arg in enumerate(run) if arg == '--env']
    assert set(envs) == {'JOB_ID=job-a',
                         f'JOBHUNTING_PRIVATE_DIR={tmp_path}/private', f'JOBHUNTING_TAILOR_OUTPUT={tmp_path}/tailor output'}


def test_start_discards_earlier_workers_and_fills_their_jobs_again(harness):
    rows, paseo, browser, steps = harness
    rows.append(row('old'))
    paseo.sessions['old'] = 'agent-from-last-run'
    browser['sessions'] += ['job-form-old', 'canvas-lab3-submit']
    steps.append(result(rows, 'old', 'Submitted'))
    assert apply.fill() == ['old']
    assert paseo.archived[0] == 'agent-from-last-run'
    assert browser['closed'][0] == 'old' and 'canvas-lab3-submit' not in browser['closed']


def test_a_slot_is_held_until_the_sheet_records_the_job_ended(harness):
    rows, paseo, browser, steps = harness
    rows += [row(f'job-{index}') for index in range(3)]
    steps += [
        lambda: None,                       # job-0 waits for the user: no slot frees
        result(rows, 'job-0', 'Submitted'),     # submitted: its slot goes to job-2
        result(rows, 'job-1', 'Skip'),       # the user gave it up on the Sheet
        result(rows, 'job-2', 'Closed'),
    ]
    assert apply.fill(slots=2) == ['job-0', 'job-1', 'job-2']
    assert paseo.archived == ['agent-job-0', 'agent-job-1', 'agent-job-2'] and paseo.sessions == {}
    assert browser['closed'] == ['job-0', 'job-1', 'job-2']


def test_limit_caps_the_workers_started_in_one_run(harness):
    rows, paseo, _, steps = harness
    rows += [row(f'job-{index}') for index in range(5)]
    steps.append(lambda: [item.update({'state': 'Submitted'}) for item in rows[:2]])
    assert apply.fill(slots=8, limit=2) == ['job-0', 'job-1']
    assert paseo.started() == ['job-0', 'job-1']


def test_an_archive_failure_stops_fill(harness, monkeypatch):
    rows, paseo, _, _ = harness
    rows.append(row('job-a'))
    paseo.sessions['old'] = 'agent-old'

    def broken(*args):
        if args[0] == 'archive':
            raise RuntimeError('paseo archive failed: exit 1; daemon not running')
        return paseo(*args)
    monkeypatch.setattr(apply, 'paseo', broken)
    with pytest.raises(RuntimeError, match='daemon not running'):
        apply.fill()
    assert paseo.started() == []


def test_only_listed_to_fill_jobs_are_started(harness, tmp_path):
    rows, _, _, steps = harness
    rows += [row('job-a'), row('job-b'), row('done', 'Submitted')]
    ids = tmp_path / 'ids.txt'
    ids.write_text('job-a\nlater\ndone\n')
    steps.append(result(rows, 'job-a', 'Submitted'))
    assert apply.fill(job_ids_file=str(ids)) == ['job-a']


def test_chrome_failure_starts_no_worker(harness, monkeypatch):
    rows, paseo, _, _ = harness
    rows.append(row('a'))
    monkeypatch.setattr(apply.subprocess, 'run', lambda *_a, **_k: type('Failed', (), {'returncode': 1, 'stderr': 'no chrome'})())
    with pytest.raises(RuntimeError, match='no chrome'):
        apply.fill()
    assert paseo.calls == []


def test_the_shared_workspace_is_created_once(harness):
    rows, paseo, _, steps = harness
    rows += [row('job-a'), row('job-b')]
    steps += [result(rows, 'job-a', 'Submitted'), result(rows, 'job-b', 'Submitted')]
    apply.fill(slots=1)
    assert [call[:2] for call in paseo.calls if call[0] == 'workspace'].count(('workspace', 'create')) == 1
    assert {call[call.index('--workspace') + 1] for call in paseo.calls if call[0] == 'run'} == {'wks_new'}


def test_a_job_waiting_for_its_jd_is_not_filled(harness):
    rows, _, _, steps = harness
    rows += [row('job-a', jd=''), row('job-b')]
    steps.append(lambda: [item.update({'state': 'Submitted'}) for item in rows])
    assert apply.fill() == ['job-b']
