from jobhunting_screen import dedupe

JD_A = ' '.join(f'routing protocol task {n} on our IP stack in C and Python' for n in range(20))
JD_B = ' '.join(f'sales quota item {n} for enterprise accounts and partner channels' for n in range(20))


def row(job_id='x', status='Apply', company='Acme', title='SWE', jd=JD_A, location='Toronto', arrangement='', score='1', collected=''):
    return {'job_id': job_id, 'state': status, 'company': company, 'title': title, 'jd': jd, 'location': location,
            'work_arrangement': arrangement, 'priority_score': score, 'collected_at': collected}


def test_same_job_is_company_title_and_jd():
    same = dedupe.same_job
    # Location does not matter; case, punctuation and spacing in the title do not; wording does
    assert same(row(location='Toronto'), row(location='Ottawa'))
    assert same(row(title='Embedded Software Developer (New Grad)'), row(title='embedded  software developer new grad'))
    assert not same(row(title='Software Engineer 1'), row(title='Software Engineer I'))
    # Company suffixes and a bilingual tail after | or / do not matter
    assert same(row(company='Ciena'), row(company='Ciena Canada Inc.'))
    assert same(row(company='AMD'), row(company='AMD Canada'))
    assert same(row(company='Export Development Canada | Exportation et développement Canada'),
                row(company='Export Development Canada'))
    assert not same(row(company='Acme'), row(company='Apex'))
    # The JDs decide: clearly different, missing or too short is never the same job
    assert not same(row(jd=JD_A), row(jd=JD_B))
    assert same(row(jd=JD_A), row(jd=JD_A + ' Apply now.'))
    assert not same(row(jd=JD_A), row(jd=''))
    assert not same(row(jd='Short JD'), row(jd='Short JD'))


def test_blank_company_or_title_is_left_alone():
    rows = [row('a', company=''), row('b', company=''), row('c', title=' ', jd=''), row('d', title=' ', jd='')]
    assert dedupe.duplicates(rows) == [] and dedupe.plan(rows) == []


def test_kept_copy_prefers_submitted_then_remote_then_bc_then_toronto():
    places = [row('other', location='Halifax, NS', score='9'), row('toronto', location='Toronto, ON'),
              row('bc', location='Burnaby, British Columbia'), row('remote', location='Canada', arrangement='Remote')]
    assert {places[loser]['job_id'] for loser, kept in dedupe.duplicates(places)} == {'other', 'toronto', 'bc'}
    assert {places[kept]['job_id'] for _, kept in dedupe.duplicates(places)} == {'remote'}
    rows = places + [row('done', 'Submitted', location='Halifax')]
    assert {rows[kept]['job_id'] for _, kept in dedupe.duplicates(rows)} == {'done'}


def test_same_place_prefers_ready_resume_then_priority_then_earliest():
    rows = [row('chosen', score='99'), row('ready', 'Ready to fill', score='1')]
    assert dedupe.duplicates(rows) == [(0, 1)]
    rows = [row('late', score='5', collected='2026-11-05'), row('early', score='5', collected='2026-11-04'), row('low', score='1')]
    assert dedupe.duplicates(rows) == [(0, 1), (2, 1)]


def test_rows_already_marked_still_keep_jobs_apart():
    # b was marked Duplicate of c earlier; it still shows a (different JD) is not c's job
    rows = [row('a', jd=JD_B), row('b', 'Duplicate', jd=JD_A), row('c', 'Ready to fill', jd=JD_A)]
    assert dedupe.duplicates(rows) == []


def test_plan_orders_every_change_of_a_run():
    rows = [row('back', 'Missing JD', title='QA'),                      # JD fetched since: returns
            row('waiting', 'Missing JD', title='Ops', jd=''),            # still no JD: stays
            row('no-jd', 'Ready to fill', title='Data', jd=''),           # becomes Missing JD
            row('applied-before', title='PM'), row('old', 'Submitted', title='PM', jd=''),
            row('copy', title='SWE', location='Halifax'), row('best', title='SWE', location='Vancouver, BC'),
            row('closed', 'Closed', title='Data', jd='')]
    changes = [(rows[index]['job_id'], target) for index, target, _ in dedupe.plan(rows)]
    assert changes == [('back', 'AI recommends'), ('applied-before', 'Duplicate'), ('copy', 'Duplicate'), ('no-jd', 'Missing JD')]
    assert dedupe.plan(rows, scope={'no-jd'}) == [(2, 'Missing JD', 'no JD; waiting for it')]


def test_a_title_match_with_a_submitted_jd_still_needs_the_same_jd():
    rows = [row('new', jd=JD_A), row('old', 'Submitted', jd=JD_B)]
    assert dedupe.plan(rows) == []
    rows = [row('new', jd=''), row('old', 'Submitted', jd=JD_B)]
    assert [(index, target) for index, target, _ in dedupe.plan(rows)] == [(0, 'Duplicate')]
