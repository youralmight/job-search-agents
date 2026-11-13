"""Fill: keep up to `slots` jobs in progress, one Paseo worker each (run through daily.py fill).

The Sheet is the only lasting record. Which jobs are in progress is this run's own memory: fill starts by
discarding every earlier worker and its tab, and those jobs, still Ready to fill, are filled again from scratch.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from jobhunting_core import sheet, state
from jobhunting_core.google_api import build_service
from jobhunting_core.paseo import paseo


ROOT = Path(__file__).resolve().parents[1]
# Model for fill workers, set in the repository .env
PROVIDER = os.environ['JOBHUNTING_FILL_MODEL']


def _close_form(job_id):
    """Close the job's form tab and its agent-browser session; a session whose tab is gone closes nothing else."""
    session = ['agent-browser', '--session', f'job-form-{job_id}', '--cdp', '9223']
    subprocess.run([*session, '--pin-tab', 'tab', 'close'], capture_output=True, timeout=30)
    subprocess.run([*session, 'close'], capture_output=True, timeout=30)


def fill(slots=8, limit=None, job_ids_file=None):
    """Discard earlier workers, then keep up to `slots` Ready to fill jobs in progress, starting at most `limit` workers;
    returns the started job IDs once nothing more may start and every started job has ended.

        uv run python scripts/daily.py fill --concurrency 8 --limit 16
    """
    scope = set(Path(job_ids_file).read_text().split()) if job_ids_file else None
    result = subprocess.run(['uv', 'run', '--no-sync', '--quiet', 'python', 'scripts/start-agent0-chrome.py'],
                            cwd=ROOT, capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise RuntimeError(f"The agent's Chrome failed to start: {result.stderr.strip()}")

    # Discard every earlier worker and its form; those jobs are still Ready to fill and get filled from scratch
    for agent in json.loads(paseo('ls', '--global', '--label', 'app=apply-job', '--json')):
        paseo('archive', '--force', agent['id'])
    sessions = subprocess.run(['agent-browser', 'session', 'list'], capture_output=True, text=True, timeout=30).stdout.split()
    for name in sessions:
        if name.startswith('job-form-'):
            _close_form(name.removeprefix('job-form-'))

    # Jobs in progress live only in this run's memory (job ID -> worker); a job ends when its row leaves Ready to fill
    active, started, workspace = {}, [], None
    title = 'Apply form-fill'
    while True:
        rows = sheet.call(sheet.read_jobs, build_service())
        status = {row['job_id']: state.current(row) for row in rows}
        for job_id in [job_id for job_id in active if status.get(job_id) != state.TO_FILL]:
            _close_form(job_id)
            paseo('archive', '--force', active.pop(job_id))
            print(json.dumps({'ended': job_id, 'state': status.get(job_id)}, ensure_ascii=False), flush=True)

        # Start workers in the free slots, never more than `limit` in this run
        room = slots - len(active) if limit is None else min(slots - len(active), limit - len(started))
        waiting = [row for row in rows if state.current(row) == state.TO_FILL and row['job_id'] not in started
                   and row['jd'].strip() and (scope is None or row['job_id'] in scope)]
        for row in waiting[:max(room, 0)]:
            job_id = row['job_id']
            # Every worker lives in one Paseo workspace; at most `slots` of them are open, so its sidebar stays short
            workspace = workspace or next((item['workspaceId'] for item in json.loads(paseo('workspace', 'ls', '--json'))
                                           if item['name'] == title and item['cwd'] == str(ROOT)), None) \
                or json.loads(paseo('workspace', 'create', '--path', str(ROOT), '--isolation', 'local',
                                     '--title', title, '--json'))['workspaceId']
            output = state.job_dir(job_id)
            output.mkdir(parents=True, exist_ok=True, mode=0o700)
            # The worker's skill, .agents/skills/apply-job/SKILL.md, is not published in this repository
            prompt = f'''You are an Apply worker. First read .agents/skills/apply-job/SKILL.md in full.
Job ID: {job_id}
Task: fill in this job's application form and stop before the final submission, leaving it for the user to review; click final submit only where the skill's "submit on the user's behalf" rule allows it.
Read the job's current URL, résumé and state from the Sheet; read facts from career.
Output directory: {output}. Work only in your own tabs of the agent's dedicated Chrome; do not create a second browser profile, and do not touch other workers' tabs.
'''
            envs = [f'JOB_ID={job_id}',
                    *(f'{key}={os.environ[key]}' for key in ('JOBHUNTING_PRIVATE_DIR', 'JOBHUNTING_TAILOR_OUTPUT') if key in os.environ)]
            launched = paseo('run', '--json', '--no-wait', '--provider', PROVIDER,
                              '--mode', 'bypassPermissions', '--workspace', workspace, '--cwd', str(ROOT),
                              '--title', f"{row['company']} — {row['title']}", '--label', 'app=apply-job', '--label', f'job={job_id}',
                              *(arg for item in envs for arg in ('--env', item)), prompt)
            active[job_id] = json.loads(launched)['agentId']
            started.append(job_id)
            print(json.dumps({'started': job_id}), flush=True)

        # Done once nothing more may start and every started job has ended; otherwise look again in a minute
        more = len(waiting) > max(room, 0) and (limit is None or len(started) < limit)
        if not active and not more:
            return started
        time.sleep(60)
