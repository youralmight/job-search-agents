import fcntl
import importlib.util
from pathlib import Path

import pytest

from googleapiclient.errors import HttpError

from jobhunting_core import state


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


@pytest.fixture
def daily(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location('apply_daily', SCRIPTS / 'daily.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.apply, 'ROOT', tmp_path)
    (tmp_path.parent / 'docs').mkdir(exist_ok=True)
    monkeypatch.setattr(module.Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(module, 'build_service', lambda: object())
    monkeypatch.setattr(module, 'wait_for_memory', lambda: None)
    return module


def job(job_id, status, url='', score='1', company='Acme', title=None, collected='', location='Toronto', jd=None):
    # By default every row has its own JD, so rows only match when a test gives them the same one
    jd = ' '.join(f'{job_id} duty {n}' for n in range(10)) if jd is None else jd
    return {'job_id': job_id, 'company': company, 'title': title or job_id, 'location': location, 'work_arrangement': '',
            'url': url or f'https://jobs.lever.co/acme/{job_id}', 'same_job_group': '', 'jd': jd,
            'state': status, 'log': '', 'priority_score': score, 'collected_at': collected}


def _pipeline(daily, monkeypatch, rows, *, fail_prepare=()):
    """Fake each step by moving the row's state the way the real step would."""
    by_id = {row['job_id']: row for row in rows}
    calls = []
    monkeypatch.setattr(daily.sheet, 'read_jobs', lambda *_: [dict(row) for row in rows])
    monkeypatch.setattr(daily, 'create_workspace', lambda *_: 'wks_test')
    monkeypatch.delenv('JOBHUNTING_PASEO_WORKSPACE', raising=False)

    def prepare(_, row):
        calls.append(('prepare', row['job_id']))
        if row['job_id'] in fail_prepare:
            raise RuntimeError('tailor crashed')
        by_id[row['job_id']]['state'] = state.TO_FILL
        return 'résumé tailored'
    monkeypatch.setattr(daily.preparation, 'prepare', prepare)
    return by_id, calls


def test_prepare_carries_each_job_from_its_current_state(daily, monkeypatch):
    rows = [job('recommended', 'AI recommends', score='5'), job('chosen', 'Apply', score='5', company='C'),
            job('ready', 'Ready to fill', score='5', company='T'), job('dropped', 'Skip', score='5', company='N'),
            job('done', 'Submitted', score='100', company='D')]
    by_id, calls = _pipeline(daily, monkeypatch, rows)
    assert daily.prepare(None, 8) is False
    assert {job_id: row['state'] for job_id, row in by_id.items()} == {
        'recommended': state.TO_FILL, 'chosen': state.TO_FILL, 'ready': state.TO_FILL,
        'dropped': state.REJECTED, 'done': state.SUBMITTED}
    assert sorted(calls) == [('prepare', 'chosen'), ('prepare', 'recommended')]


def test_prepare_limit_takes_highest_priority_first(daily, monkeypatch):
    rows = [job('low', 'Apply', score='1', company='A', collected='2026-03-01'),
            job('high', 'AI recommends', score='10', company='B', collected='2026-01-01'),
            job('mid-new', 'Apply', score='5', company='C', collected='2026-02-01'),
            job('mid-old', 'Apply', score='5', company='D', collected='2026-01-01')]
    by_id, calls = _pipeline(daily, monkeypatch, rows)
    assert daily.prepare(2, 8) is False
    assert {job_id for _, job_id in calls} == {'high', 'mid-new'}
    assert by_id['low']['state'] == by_id['mid-old']['state'] == state.SELECTED


def test_prepare_continues_after_one_job_fails(daily, monkeypatch):
    rows = [job('bad', 'Apply', score='9', company='A'), job('good', 'Apply', score='1', company='B')]
    by_id, _ = _pipeline(daily, monkeypatch, rows, fail_prepare={'bad'})
    assert daily.main(['prepare']) == 1
    assert by_id['good']['state'] == state.TO_FILL and by_id['bad']['state'] == state.SELECTED


def test_prepare_step_retries_sheets_rate_limit(daily, monkeypatch):
    rows = [job('job-a', 'Apply')]
    by_id, _ = _pipeline(daily, monkeypatch, rows)
    real = daily.preparation.prepare
    attempts = []

    class Resp(dict):
        status = 429
        reason = 'Too Many Requests'

    def flaky(*args):
        attempts.append(1)
        if len(attempts) == 1:
            raise HttpError(Resp(), b'quota')
        return real(*args)
    monkeypatch.setattr(daily.preparation, 'prepare', flaky)
    monkeypatch.setattr(daily.sheet.time, 'sleep', lambda _: None)
    assert daily.prepare(None, 8) is False
    assert len(attempts) == 2 and by_id['job-a']['state'] == state.TO_FILL


def test_prepare_alone_does_not_dedupe(daily, monkeypatch):
    jd = ' '.join(f'routing protocol task {n} on our IP stack in C and Python' for n in range(20))
    rows = [job('a', 'Apply', score='9', title='SWE', jd=jd), job('b', 'Apply', score='1', title='SWE', jd=jd)]
    by_id, calls = _pipeline(daily, monkeypatch, rows)
    assert daily.prepare(None, 8) is False
    assert by_id['a']['state'] == by_id['b']['state'] == state.TO_FILL


def test_prepare_skips_rows_without_jd(daily, monkeypatch):
    rows = [job('no-jd', 'Apply', jd=''), job('with-jd', 'AI recommends')]
    by_id, calls = _pipeline(daily, monkeypatch, rows)
    assert daily.prepare(None, 8) is False
    assert calls == [('prepare', 'with-jd')] and by_id['no-jd']['state'] == 'Apply'
