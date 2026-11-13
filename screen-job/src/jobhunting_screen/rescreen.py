"""Judge Jobs rows again by the current screening rules: an excluded row becomes Skip with the reason, a recommended
one stays as it is. Only rows still waiting on Agents are judged; the user's Apply is the user's decision."""
from __future__ import annotations

import json
import time
from datetime import datetime

from jobhunting_core import sheet, state
from jobhunting_core.paseo import create_workspace
from jobhunting_screen import screening

# Seconds for every screening tab of one rescreen together.
MAX_TIME = 6 * 3600


def select(rows, scope=None):
    """Rows in Ready to fill or AI recommends that have a JD, limited to the `scope` job IDs."""
    return [row for row in rows if row['job_id'] and state.current(row) in (state.TO_FILL, state.AI_SELECTED)
            and row['jd'].strip() and (scope is None or row['job_id'] in scope)]


def rescreen(service, rows) -> bool:
    """Screen the rows in agent tabs, then move each excluded one to Skip. True when a move failed."""
    now = datetime.now()
    run_dir = screening.ROOT / f'tmp/rescreen/{now:%Y%m%d-%H%M%S}'
    run_dir.mkdir(parents=True, mode=0o700)
    postings = [{'url': row['url'], 'company': row['company'], 'title': row['title'], 'location': row['location'],
                 'posted_date': row['posted_date'], 'description': row['jd']} for row in rows]
    screening.save(run_dir / 'result.model.json', {'candidates': [
        {'url': row['url'], 'company': row['company'], 'title': row['title'], 'decision': 'unread',
         'reason': 'JD pending', 'evidence': 'not judged yet'} for row in rows]})
    workspace = create_workspace(screening.ROOT, f'rescreen · {now:%m-%d %H:%M:%S}')
    screening.judge(run_dir, postings, workspace, 'Rescreen', time.monotonic() + MAX_TIME)

    failed = False
    candidates = json.loads((run_dir / 'result.model.json').read_text(encoding='utf-8'))['candidates']
    for row, candidate in zip(rows, candidates, strict=True):
        if candidate['decision'] != 'exclude':
            continue
        try:
            sheet.call(lambda: sheet.transition(service, row['job_id'], state.REJECTED, f"Rescreen excluded: {candidate['reason']}",
                                                expect=(state.current(row),)))
            result = {'job_id': row['job_id'], 'state': state.REJECTED, 'reason': candidate['reason']}
        except Exception as exc:
            result = {'job_id': row['job_id'], 'error': f'{type(exc).__name__}: {exc}'}
            failed = True
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return failed
