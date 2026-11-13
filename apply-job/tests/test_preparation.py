import json
import subprocess
from pathlib import Path

import pytest

from jobhunting_apply import preparation
from jobhunting_core import sheet, state


def job(job_id, status, **extra):
    return {'job_id': job_id, 'url': f'https://{job_id}', 'company': job_id.upper(), 'title': 'Engineer',
            'summary': '', 'requirements': '', 'remark': '', 'state': status, 'log': '', 'resume': '', **extra}


@pytest.fixture
def writes(monkeypatch):
    """Record transitions instead of writing the Sheet."""
    recorded = []

    def transition(_service, job_id, target, note, expect=None, extra=None, **_):
        recorded.append((job_id, target, note, dict(extra or {})))
    monkeypatch.setattr(sheet, 'transition', transition)
    return recorded


@pytest.fixture(autouse=True)
def output_root(tmp_path, monkeypatch):
    root = tmp_path / 'tailor-output'
    monkeypatch.setenv('JOBHUNTING_TAILOR_OUTPUT', str(root))
    return root


def test_tailored_resume_moves_the_job_to_fill(writes, tmp_path):
    pdf = tmp_path / 'resume.pdf'
    pdf.write_bytes(b'%PDF')
    preparation.prepare(None, job('a', 'Apply'), lambda _, master=False: pdf)
    assert writes == [('a', state.TO_FILL, 'résumé tailored', {'resume': str(pdf)})]


def test_failed_tailoring_falls_back_to_the_tracked_master(writes, tmp_path):
    def broken(_, master=False):
        if master:
            return tmp_path / 'master.pdf'
        raise RuntimeError('JD unavailable')
    preparation.prepare(None, job('a', 'AI recommends'), broken)
    assert writes == [('a', state.TO_FILL, 'tailoring failed, using the master: JD unavailable', {'resume': str(tmp_path / 'master.pdf')})]


def test_a_failing_tracked_master_falls_back_to_the_fixed_master(writes):
    def broken(_, master=False):
        raise RuntimeError('docker unavailable' if master else 'JD unavailable')
    preparation.prepare(None, job('a', 'AI recommends'), broken)
    assert writes == [('a', state.TO_FILL, 'tailoring failed, using the master: JD unavailable; the tracked master also failed, using the fixed master: docker unavailable',
                       {'resume': str(preparation.MASTER)})]


def test_unavailable_model_leaves_the_job_for_the_next_prepare(writes):
    def limited(_, master=False):
        raise preparation.ModelUnavailable('model unavailable')
    with pytest.raises(preparation.ModelUnavailable):
        preparation.prepare(None, job('a', 'AI recommends'), limited)
    assert writes == []


def test_tailor_recognizes_an_unavailable_model(monkeypatch):
    stderr = 'Traceback\nRuntimeError: model unavailable (claude-haiku-5-5): paseo command not found\n'
    monkeypatch.setattr(preparation.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 1, '', stderr))
    with pytest.raises(preparation.ModelUnavailable):
        preparation.tailor(job('a', 'Apply'))


def test_custom_resume_is_kept_without_generation(writes):
    preparation.prepare(None, job('a', 'Apply', resume='/user/chosen.pdf'), lambda _, master=False: pytest.fail('must not generate'))
    assert writes == [('a', state.TO_FILL, "kept the user's resume path", {})]


@pytest.mark.parametrize('column,value', [('resume', '/user/new-choice.pdf'), ('url', 'https://example.com/new-job')])
def test_generation_does_not_overwrite_a_job_changed_by_the_user(monkeypatch, tmp_path, column, value):
    current = job('a', 'Apply')
    monkeypatch.setattr(state, 'jobs_root', lambda: tmp_path)
    monkeypatch.setattr(sheet, '_job_row', lambda *_: (list(current), 3, dict(current)))
    monkeypatch.setattr(sheet, 'write_cells', lambda *_: pytest.fail('changed job must not be written'))

    def generate(_, master=False):
        current[column] = value
        return tmp_path / 'resume.pdf'

    with pytest.raises(ValueError, match=column):
        preparation.prepare(None, dict(current), generate)
    assert current[column] == value and current['state'] == 'Apply'


def test_tailor_gets_the_saved_jd(monkeypatch, tmp_path):
    seen = {}

    def run(command, **kwargs):
        seen.update(json.loads(Path(command[4]).read_text()))
        return subprocess.CompletedProcess(command, 1, '', 'RuntimeError: stop here')
    monkeypatch.setattr(preparation.subprocess, 'run', run)
    with pytest.raises(RuntimeError):
        preparation.tailor(job('a', 'Apply', jd='Full JD text'))
    assert seen['jd'] == 'Full JD text' and set(seen) == {'job_id', 'url', 'company', 'title', 'summary', 'requirements', 'jd'}


def test_tailor_asks_for_the_tracked_master(monkeypatch):
    seen = []

    def run(command, **kwargs):
        seen.append(command)
        return subprocess.CompletedProcess(command, 1, '', 'RuntimeError: stop here')
    monkeypatch.setattr(preparation.subprocess, 'run', run)
    with pytest.raises(RuntimeError):
        preparation.tailor(job('a', 'Apply'), master=True)
    assert seen[0][-1] == '--master'
