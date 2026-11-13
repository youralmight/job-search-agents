"""Which Jobs rows are the same job, which copy to keep, and which rows wait for their JD.

Same job = same company and title (loosely compared) and both JDs on the Sheet sharing at least half their
word 5-grams. Against a Submitted row the title is enough when either JD is missing: that job was applied to.
Otherwise a row without JD is never guessed; it waits as Missing JD until someone fetches the JD.
"""
from __future__ import annotations

import re
import unicodedata

from jobhunting_core import sheet, state

COMPANY_SUFFIXES = {'canada', 'inc', 'incorporated', 'ltd', 'limited', 'llc', 'corp', 'corporation', 'co', 'technologies'}
SAME_JD = 0.5
CANDIDATES = state.CHOSEN | {state.TO_FILL}
WAITING = CANDIDATES | {state.MISSING_JD}


def _words(text):
    return re.findall(r'[^\W_]+', text.lower())


def job_key(row):
    """Company and title compared loosely: case, punctuation, a bilingual `|`/`/` tail and company suffixes ignored."""
    company = _words(re.split(r'[|/]', row['company'], maxsplit=1)[0])
    while len(company) > 1 and company[-1] in COMPANY_SUFFIXES:
        company.pop()
    title = _words(row['title'])
    return (' '.join(company), ' '.join(title)) if company and title else None


def has_jd(row):
    return bool(row['jd'].strip())


def jd_overlap(a, b):
    """Shared word 5-grams over the smaller JD's."""
    def shingles(text):
        tokens = re.findall(r'[a-z0-9]+', unicodedata.normalize('NFKD', text).lower())
        return {tuple(tokens[i:i + 5]) for i in range(len(tokens) - 4)}
    first, second = shingles(a), shingles(b)
    return len(first & second) / min(len(first), len(second)) if first and second else 0.0


def same_job(a, b):
    key = job_key(a)
    return key is not None and key == job_key(b) and has_jd(a) and has_jd(b) and jd_overlap(a['jd'], b['jd']) >= SAME_JD


def location_rank(row):
    """Remote first, then British Columbia, then Toronto, then elsewhere."""
    place = row['location'].lower()
    if row.get('work_arrangement', '') == 'Remote' or 'remote' in place:
        return 0
    if re.search(r'british columbia|\bbc\b|vancouver|burnaby', place):
        return 1
    if 'toronto' in place:
        return 2
    return 3


def duplicates(rows, scope=None):
    """[(loser index, kept index), ...]: chosen rows that are another copy of a job.

    Groups use every row with a JD, whatever its state, so marking a row Duplicate never changes a later run. Two groups
    merge only if every pair across them is the same job. Kept copy: Submitted, else the chosen row with the best
    location, then Ready to fill (résumé ready), then the highest priority_score, then the earliest collected_at.
    """
    by_key = {}
    for index, row in enumerate(rows):
        key = job_key(row)
        if row['job_id'] and key is not None and has_jd(row):
            by_key.setdefault(key, []).append(index)

    def rank(index):
        row = rows[index]
        try:
            score = float(row['priority_score'] or 0)
        except ValueError:
            score = 0.0
        return (state.current(row) != state.SUBMITTED, location_rank(row), state.current(row) != state.TO_FILL,
                -score, row['collected_at'].strip() or '￿', index)

    pairs = []
    for same_key in by_key.values():
        group_of = {index: [index] for index in same_key}
        for position, first in enumerate(same_key):
            for second in same_key[position + 1:]:
                left, right = group_of[first], group_of[second]
                if left is not right and all(same_job(rows[a], rows[b]) for a in left for b in right):
                    left += right
                    for index in right:
                        group_of[index] = left
        for group in {id(group): group for group in group_of.values()}.values():
            keepable = [index for index in group if state.current(rows[index]) in CANDIDATES | {state.SUBMITTED}]
            if len(keepable) < 2:
                continue
            kept = min(keepable, key=rank)
            pairs += [(index, kept) for index in keepable if index != kept
                      and state.current(rows[index]) in CANDIDATES
                      and (scope is None or rows[index]['job_id'] in scope)]
    return sorted(pairs)


def plan(rows, scope=None):
    """Every state change of one dedupe run, in order: [(row index, target, note), ...].

    1. A Missing JD row that now has its JD goes back to AI recommends.
    2. A waiting row with the title of a Submitted row, either of them without JD, is Duplicate of it.
    3. Among rows with JD, every copy of a job but the kept one is Duplicate (see `duplicates`).
    4. A chosen row still without JD becomes Missing JD.
    Rows outside `scope` (job IDs) and rows without company or title are left alone.
    """
    current = [state.current(row) for row in rows]
    changes = []

    def move(index, target, note):
        changes.append((index, target, note))
        current[index] = target

    def mine(index):
        return rows[index]['job_id'] and job_key(rows[index]) is not None and (scope is None or rows[index]['job_id'] in scope)

    def copy_of(index, kept):
        return f"same job as {rows[kept]['job_id']} (row {kept + sheet.FIRST_DATA_ROW}, {current[kept]})"

    for index, row in enumerate(rows):
        if mine(index) and current[index] == state.MISSING_JD and has_jd(row):
            move(index, state.AI_SELECTED, 'JD added')

    submitted = {}
    for index, row in enumerate(rows):
        if row['job_id'] and current[index] == state.SUBMITTED and job_key(row) is not None:
            submitted.setdefault(job_key(row), []).append(index)
    for index, row in enumerate(rows):
        if mine(index) and current[index] in WAITING:
            kept = next((other for other in submitted.get(job_key(row), [])
                         if not has_jd(row) or not has_jd(rows[other])), None)
            if kept is not None:
                move(index, state.DUPLICATE, copy_of(index, kept) + ' (no JD; matched by title)')

    view = [{**row, 'state': current[index]} for index, row in enumerate(rows)]
    for loser, kept in duplicates(view, scope):
        move(loser, state.DUPLICATE, copy_of(loser, kept))

    for index, row in enumerate(rows):
        if mine(index) and current[index] in CANDIDATES and not has_jd(row):
            move(index, state.MISSING_JD, 'no JD; waiting for it')
    return changes
