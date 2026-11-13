"""Screen: which jobs to apply to.

    uv run jobhunting-screen screen <run dir> --workspace <id> --seconds 3600   # Discover, once per search task
    uv run jobhunting-screen rescreen [--job_ids_file ids.txt]
    uv run jobhunting-screen dedupe [--job_ids_file ids.txt]
    echo '<job JSON>' | uv run jobhunting-screen append-job                   # screening tabs only
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from pathlib import Path

from jobhunting_core import sheet, state
from jobhunting_core.google_api import build_service
from jobhunting_screen import dedupe as same, jobs, rescreen, screening


def _scope(job_ids_file):
    return set(Path(job_ids_file).read_text().split()) if job_ids_file else None


def screen(run_dir, workspace, seconds):
    """Judge the postings a Discover task collected into listing.json; decisions go to result.model.json and
    recommendations to Jobs. Prints the number of tab sessions and whether a tab ran out of time."""
    run_dir = Path(run_dir).resolve()
    metadata = json.loads((run_dir / 'run.json').read_text(encoding='utf-8'))
    postings = json.loads((run_dir / 'listing.json').read_text(encoding='utf-8'))['jobs']
    sessions, out_of_time = screening.judge(run_dir, postings, workspace, metadata['task']['source'],
                                            time.monotonic() + seconds, metadata)
    print(json.dumps({'sessions': sessions, 'out_of_time': out_of_time}))


def append_job():
    job = json.loads(sys.stdin.read())
    run_dir = os.environ.get('JOBHUNTING_SCREEN_RUN_DIR')
    if run_dir:
        # Agents have retyped and truncated URLs; the posting must be one this task collected
        run = json.loads(Path(run_dir, 'run.json').read_text(encoding='utf-8'))
        candidates = json.loads(Path(run_dir, 'result.model.json').read_text(encoding='utf-8'))['candidates']
        if job.get('search_id') != run['search_id'] or job.get('url') not in [candidate['url'] for candidate in candidates]:
            raise ValueError(f'append-job search_id or URL is not this task\'s: read {run_dir}/run.json and '
                             f'{run_dir}/result.model.json, build search_id and the full literal URL programmatically '
                             'from those files, then retry. Do not retype or truncate the URL.')
    print(json.dumps(jobs.append_job(build_service(), job), ensure_ascii=False))


def rescreen_rows(job_ids_file=None):
    service = build_service()
    rows = rescreen.select(sheet.call(sheet.read_jobs, service), _scope(job_ids_file))
    print(f'{len(rows)} jobs to screen again', flush=True)
    return bool(rows) and rescreen.rescreen(service, rows)


def dedupe(job_ids_file=None):
    """One single-threaded pass over the whole Sheet: Missing JD rows with a JD return, copies of a job become Duplicate,
    chosen rows without JD become Missing JD (see `jobhunting_screen.dedupe.plan`). True when a move failed."""
    service = build_service()
    rows = sheet.call(sheet.read_jobs, service)
    failed = False
    for index, target, note in same.plan(rows, _scope(job_ids_file)):
        row = rows[index]
        try:
            moved = sheet.call(lambda: sheet.transition(service, row['job_id'], target, note, expect=(state.current(row),)))
            row['state'] = moved['state']
            result = {'job_id': row['job_id'], 'state': target, 'note': note}
        except Exception as exc:
            result = {'job_id': row['job_id'], 'error': f'{type(exc).__name__}: {exc}'}
            failed = True
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return failed


def main(argv=None):
    parser = argparse.ArgumentParser(prog='jobhunting-screen')
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('screen', help="judge a Discover task's collected postings")
    command.add_argument('run_dir')
    command.add_argument('--workspace', required=True, help='Paseo workspace for the screening tabs')
    command.add_argument('--seconds', type=int, required=True, help='time for every screening tab together')
    for name, help_text in (('rescreen', 'judge Ready to fill and AI recommends rows again; excluded ones become Skip'),
                            ('dedupe', 'mark copies of one job Duplicate and chosen rows without JD Missing JD')):
        commands.add_parser(name, help=help_text).add_argument('--job_ids_file', help='only the job IDs listed in this file')
    commands.add_parser('append-job', help='append a recommended posting to Jobs (JSON on stdin)')
    args = parser.parse_args(argv)

    if args.command == 'screen':
        screen(args.run_dir, args.workspace, args.seconds)
        return 0
    if args.command == 'append-job':
        try:
            append_job()
            return 0
        except Exception as exc:  # noqa: BLE001 - agent-facing boundary: report and exit non-zero
            print(f'error: {exc}', file=sys.stderr)
            return 1

    # One run of each Sheet-wide command at a time; a second one exits quietly
    lock_path = Path.home() / f'.cache/jobhunting-screen-{args.command}.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f'{args.command} already running')
            return 0
        return int((rescreen_rows if args.command == 'rescreen' else dedupe)(args.job_ids_file))


if __name__ == '__main__':
    raise SystemExit(main())
