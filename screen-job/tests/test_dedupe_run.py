import pytest

from jobhunting_core import state
from jobhunting_screen import cli

JD_ROUTING = ' '.join(f'routing protocol task {n} on our IP stack in C and Python' for n in range(20))
JD_SALES = ' '.join(f'sales quota item {n} for enterprise accounts and partner channels' for n in range(20))


def job(job_id, status, url='', score='1', company='Acme', title=None, collected='', location='Toronto', jd=None):
    # By default every row has its own JD, so rows only match when a test gives them the same one
    jd = ' '.join(f'{job_id} duty {n}' for n in range(10)) if jd is None else jd
    return {'job_id': job_id, 'company': company, 'title': title or job_id, 'location': location, 'work_arrangement': '',
            'url': url or f'https://jobs.lever.co/acme/{job_id}', 'same_job_group': '', 'jd': jd,
            'state': status, 'log': '', 'priority_score': score, 'collected_at': collected}


@pytest.fixture
def sheet_rows(monkeypatch, tmp_path):
    """An in-memory Jobs tab; transition checks the expected state like the real one. Returns (rows by ID, moves)."""
    rows, moves = {}, []
    monkeypatch.setattr(cli, 'build_service', lambda: object())
    monkeypatch.setattr(cli.sheet, 'read_jobs', lambda _: [dict(row) for row in rows.values()])
    monkeypatch.setattr(cli.Path, 'home', lambda: tmp_path)

    def transition(_, job_id, target, note, *, expect):
        assert rows[job_id]['state'] in expect
        moves.append((target, job_id))
        rows[job_id].update({'state': target, 'log': note})
        return dict(rows[job_id])
    monkeypatch.setattr(cli.sheet, 'transition', transition)

    def load(*jobs):
        rows.update({row['job_id']: row for row in jobs})
        return rows, moves
    return load


def test_copies_of_one_posting_keep_only_the_best_placed(sheet_rows):
    by_id, moves = sheet_rows(
        job('ciena-a', 'AI recommends', 'https://www.linkedin.com/jobs/view/111/', score='5', company='Ciena',
            title='Routing/IP Software Engineer - New Grad', location='Ottawa', jd=JD_ROUTING),
        job('ciena-b', 'AI recommends', 'https://www.linkedin.com/jobs/view/222/', score='1', company='Ciena Canada Inc.',
            title='Routing/IP Software Engineer – New Grad', location='Vancouver, BC', jd=JD_ROUTING + ' Apply now.'),
        job('ciena-c', 'Apply', 'https://ciena.wd5.myworkdayjobs.com/x/job/R1', score='9', company='CIENA',
            title='routing ip software engineer new grad', location='Ottawa', jd=JD_ROUTING),
        job('other', 'Apply', title='Analyst'))
    assert cli.dedupe() is False
    assert by_id['ciena-b']['state'] == 'AI recommends' and by_id['other']['state'] == 'Apply'
    assert by_id['ciena-a']['state'] == by_id['ciena-c']['state'] == state.DUPLICATE
    assert by_id['ciena-a']['log'] == 'same job as ciena-b (row 4, AI recommends)'


def test_a_submitted_copy_makes_every_candidate_a_duplicate(sheet_rows):
    by_id, _ = sheet_rows(job('ready', 'Ready to fill', score='9', title='SWE', jd=JD_ROUTING),
                          job('chosen', 'Apply', score='9', title='SWE', jd=JD_ROUTING),
                          job('done', 'Submitted', score='0', title='SWE', location='Halifax', jd=JD_ROUTING))
    assert cli.dedupe() is False
    assert by_id['ready']['state'] == by_id['chosen']['state'] == state.DUPLICATE
    assert by_id['done']['state'] == state.SUBMITTED and 'Submitted' in by_id['chosen']['log']


def test_the_job_list_limits_which_rows_change(sheet_rows, tmp_path):
    by_id, _ = sheet_rows(job('a', 'Apply', score='9', title='SWE', jd=JD_ROUTING), job('b', 'Apply', score='1', title='SWE', jd=JD_ROUTING),
                          job('c', 'Apply', score='1', title='Data', jd=JD_SALES), job('d', 'Apply', score='5', title='Data', jd=JD_SALES))
    listed = tmp_path / 'ids.txt'
    listed.write_text('a\nb\nc\n')
    assert cli.dedupe(str(listed)) is False
    # c loses to d, which is outside the list and stays untouched
    assert by_id['b']['state'] == by_id['c']['state'] == state.DUPLICATE and 'd' in by_id['c']['log']
    assert by_id['a']['state'] == by_id['d']['state'] == 'Apply'


def test_rows_without_jd_wait_as_missing_jd_and_return_once_it_is_filled(sheet_rows):
    by_id, _ = sheet_rows(job('no-jd', 'Ready to fill', title='SWE', jd=''), job('with-jd', 'Apply', title='SWE', jd=JD_ROUTING),
                          job('applied-before', 'AI recommends', title='Data', jd=JD_SALES), job('old', 'Submitted', title='Data', jd=''))
    assert cli.dedupe() is False
    assert by_id['no-jd']['state'] == state.MISSING_JD and by_id['no-jd']['log'] == 'no JD; waiting for it'
    # Against a Submitted row without JD the title is enough
    assert by_id['applied-before']['state'] == state.DUPLICATE and 'old' in by_id['applied-before']['log']
    assert by_id['with-jd']['state'] == 'Apply'
    # Someone fetches the JD: the next dedupe returns the row
    by_id['no-jd']['jd'] = JD_SALES
    assert cli.dedupe() is False
    assert by_id['no-jd']['state'] == state.AI_SELECTED and by_id['no-jd']['log'] == 'JD added'


def test_running_dedupe_twice_changes_nothing_the_second_time(sheet_rows):
    # a and b are two jobs under one title; c has no JD; d repeats a, e repeats b
    by_id, moves = sheet_rows(job('a', 'AI recommends', title='SWE', score='3', jd=JD_ROUTING),
                              job('b', 'AI recommends', title='SWE', score='2', jd=JD_SALES),
                              job('c', 'Ready to fill', title='SWE', score='9', jd=''),
                              job('d', 'AI recommends', title='SWE', score='1', jd=JD_ROUTING),
                              job('e', 'Ready to fill', title='SWE', score='1', location='Vancouver, BC', jd=JD_SALES))
    assert cli.dedupe() is False
    first = {job_id: row['state'] for job_id, row in by_id.items()}
    assert first == {'a': 'AI recommends', 'b': state.DUPLICATE, 'c': state.MISSING_JD, 'd': state.DUPLICATE, 'e': 'Ready to fill'}
    moves.clear()
    assert cli.dedupe() is False
    assert moves == [] and {job_id: row['state'] for job_id, row in by_id.items()} == first


def test_a_refused_move_is_reported_and_the_run_goes_on(sheet_rows, monkeypatch, capsys):
    sheet_rows(job('a', 'Ready to fill', jd=''), job('b', 'Apply', jd=''))
    real = cli.sheet.transition
    monkeypatch.setattr(cli.sheet, 'transition', lambda _, job_id, *a, **k: (_ for _ in ()).throw(ValueError('changed'))
                        if job_id == 'a' else real(_, job_id, *a, **k))
    assert cli.main(['dedupe']) == 1
    assert '"error"' in capsys.readouterr().out
