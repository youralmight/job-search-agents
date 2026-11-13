"""Apply: `prepare` tailors résumés, `fill` fills forms up to the final Submit.

    uv run python scripts/daily.py prepare --limit 5
    uv run python scripts/daily.py fill --concurrency 8 --limit 16 [--job_ids_file ids.txt]
    (prepare also takes --job_ids_file)
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import apply
from jobhunting_apply import preparation
from jobhunting_core import sheet, state
from jobhunting_core.google_api import build_service
from jobhunting_core.paseo import create_workspace


def wait_for_memory():
    # Below 8 GB free a new step waits: an OOM kill takes Paseo and its tmux down with it.
    while int(re.search(r'^MemAvailable:\s+(\d+)', Path('/proc/meminfo').read_text(), re.M)[1]) < 8 * 1024 * 1024:
        time.sleep(30)


def prepare(limit, concurrency, job_ids_file=None):
    """Carry each chosen job with a JD through tailoring. Run `jobhunting-screen dedupe` before it, so duplicates
    are not tailored and rows without JD are set aside as Missing JD. A stopped run resumes from each row's state."""
    rows = sheet.call(sheet.read_jobs, build_service())
    scope = set(Path(job_ids_file).read_text().split()) if job_ids_file else None
    results = []

    queue = [row for row in rows if row['job_id'] and state.current(row) in state.CHOSEN and row['jd'].strip()
             and (scope is None or row['job_id'] in scope)]
    queue.sort(key=lambda row: (float(row['priority_score'] or 0), row['collected_at']), reverse=True)
    queue = queue[:limit]
    print(f'{len(queue)} jobs queued', flush=True)
    if queue:
        # Every tailoring call of this run is one tab in its own Paseo workspace
        os.environ['JOBHUNTING_PASEO_WORKSPACE'] = create_workspace(apply.ROOT, f"prepare · {datetime.now():%m-%d %H:%M:%S}")

    def prepare_one(row):
        service = build_service()  # httplib2 is not thread-safe: one client per job
        wait_for_memory()
        return {'job_id': row['job_id'], 'prepare': sheet.call(preparation.prepare, service, row)}

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(prepare_one, row): row['job_id'] for row in queue}
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:
                result = {'job_id': futures[future], 'error': f'{type(exc).__name__}: {exc}'}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    return any('error' in item for item in results)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('prepare', 'fill'))
    parser.add_argument('--limit', type=int, help='at most this many jobs this run (prepare: prepared; fill: workers started)')
    parser.add_argument('--concurrency', type=int, default=8, help='at most this many jobs in progress at once')
    parser.add_argument('--job_ids_file', help='only the job IDs listed in this file')
    args = parser.parse_args(argv)

    # One run of each action at a time; a second one exits quietly
    lock_path = Path.home() / f'.cache/jobhunting-apply-{args.action}.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f'{args.action} already running')
            return 0
        if args.action == 'prepare':
            return int(prepare(args.limit, args.concurrency, args.job_ids_file))
        apply.fill(args.concurrency, args.limit, args.job_ids_file)
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
