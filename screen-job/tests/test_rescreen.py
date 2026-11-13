import json

import pytest

from jobhunting_core import state
from jobhunting_screen import cli, rescreen, screening


def job(job_id, status, jd='A full posting text.', company='Acme'):
    return {'job_id': job_id, 'url': f'https://jobs.lever.co/acme/{job_id}', 'company': company, 'title': 'Engineer',
            'location': 'Toronto', 'posted_date': '', 'jd': jd, 'state': status, 'log': ''}


def test_only_rows_waiting_on_agents_with_a_jd_are_selected():
    rows = [job('ai', 'AI recommends'), job('ready', 'Ready to fill'), job('user', 'Apply'), job('dropped', 'Skip'),
            job('done', 'Submitted'), job('dup', 'Duplicate'), job('wait', 'Missing JD'), job('no-jd', 'Ready to fill', jd=' '), job('', 'Ready to fill')]
    assert [row['job_id'] for row in rescreen.select(rows)] == ['ai', 'ready']


def test_a_job_id_list_limits_the_selection():
    rows = [job('a', 'AI recommends'), job('b', 'Ready to fill'), job('c', 'Apply')]
    assert [row['job_id'] for row in rescreen.select(rows, {'b', 'c', 'missing'})] == ['b']


@pytest.fixture
def run(tmp_path, monkeypatch):
    """Jobs rows in memory and screening tabs that exclude every posting of company 'Exclude Me'."""
    rows, moves = {}, []
    monkeypatch.setattr(screening, 'ROOT', tmp_path)
    monkeypatch.setattr(rescreen, 'create_workspace', lambda path, title: 'wks')
    monkeypatch.setattr(cli, 'build_service', lambda: object())
    monkeypatch.setattr(cli.Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(cli.sheet, 'read_jobs', lambda _: [dict(row) for row in rows.values()])
    monkeypatch.setattr(screening, 'wait_for_agent', lambda agent, seconds: 'idle')

    def paseo(*args):
        prompt = args[-1]
        assert 'do not call append-job' in prompt and 'screening-rules.md' in prompt
        chunk_path = prompt.split('Read ', 2)[2].split('. It holds')[0]
        chunk = json.loads(open(chunk_path).read())
        model_file = chunk_path.rsplit('/', 1)[0] + '/result.model.json'
        model = json.loads(open(model_file).read())
        for posting in chunk:
            exclude = posting['company'] == 'Exclude Me'
            model['candidates'][posting['index']].update(
                decision='exclude' if exclude else 'recommend',
                reason='JD requires 5 years of experience' if exclude else 'new-grad role', evidence=posting['description'][:20])
        open(model_file, 'w').write(json.dumps(model))
        return json.dumps({'agentId': 'agent-1'})
    monkeypatch.setattr(screening, 'paseo', paseo)

    def transition(_, job_id, target, note, *, expect):
        # The real transition checks the move against the live row and the agent table
        if rows[job_id]['state'] not in expect:
            raise ValueError(f"{job_id} is in state {rows[job_id]["state"]}")
        assert rows[job_id]['state'] in state.AGENT_TRANSITIONS[target]
        moves.append((job_id, target, note))
        rows[job_id]['state'] = target
        return dict(rows[job_id])
    monkeypatch.setattr(cli.sheet, 'transition', transition)

    def load(*jobs):
        rows.update({row['job_id']: row for row in jobs})
        return rows, moves
    return load


def test_excluded_rows_become_not_filled_with_the_reason_and_recommended_rows_stay(run):
    rows, moves = run(job('keep-ai', 'AI recommends'), job('drop-ai', 'AI recommends', company='Exclude Me'),
                      job('drop-ready', 'Ready to fill', company='Exclude Me'), job('keep-ready', 'Ready to fill'),
                      job('user', 'Apply', company='Exclude Me'), job('no-jd', 'AI recommends', jd='', company='Exclude Me'))
    assert cli.main(['rescreen']) == 0
    assert moves == [('drop-ai', 'Skip', 'Rescreen excluded: JD requires 5 years of experience'),
                     ('drop-ready', 'Skip', 'Rescreen excluded: JD requires 5 years of experience')]
    assert {job_id: row['state'] for job_id, row in rows.items()} == {
        'keep-ai': 'AI recommends', 'drop-ai': 'Skip', 'drop-ready': 'Skip', 'keep-ready': 'Ready to fill',
        'user': 'Apply', 'no-jd': 'AI recommends'}


def test_the_job_id_file_limits_what_is_screened(run, tmp_path):
    rows, moves = run(job('a', 'AI recommends', company='Exclude Me'), job('b', 'Ready to fill', company='Exclude Me'))
    listed = tmp_path / 'ids.txt'
    listed.write_text('b\n')
    assert cli.main(['rescreen', '--job_ids_file', str(listed)]) == 0
    assert [job_id for job_id, _, _ in moves] == ['b'] and rows['a']['state'] == 'AI recommends'


def test_a_row_the_user_changed_meanwhile_is_reported_and_left(run, monkeypatch, capsys):
    rows, moves = run(job('a', 'AI recommends', company='Exclude Me'), job('b', 'AI recommends', company='Exclude Me'))
    real = cli.sheet.read_jobs

    def user_picks_a(service):
        selected = real(service)
        rows['a']['state'] = 'Apply'  # the user chose it while the tabs were screening
        return selected
    monkeypatch.setattr(cli.sheet, 'read_jobs', user_picks_a)
    assert cli.main(['rescreen']) == 1
    assert rows['a']['state'] == 'Apply' and rows['b']['state'] == 'Skip'
    assert '"error"' in capsys.readouterr().out


def test_nothing_to_screen_opens_no_tab(run, monkeypatch, capsys):
    run(job('a', 'Apply'), job('b', 'Submitted'))
    monkeypatch.setattr(rescreen, 'create_workspace', lambda *a: pytest.fail('no workspace without rows'))
    assert cli.main(['rescreen']) == 0
    assert '0 jobs to screen again' in capsys.readouterr().out
