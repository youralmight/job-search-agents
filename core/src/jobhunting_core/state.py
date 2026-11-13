"""Jobs `state` values and which of them an Agent may write; each job's local records."""
from __future__ import annotations

import re
from pathlib import Path

STATE_HEADER = 'state'
LOG_HEADER = 'log'
PROGRESS_HEADER = 'progress'

SELECTED = 'Apply'
AI_SELECTED = 'AI recommends'
REJECTED = 'Skip'
TO_FILL = 'Ready to fill'
CLOSED = 'Closed'
DUPLICATE = 'Duplicate'
SUBMITTED = 'Submitted'
MISSING_JD = 'Missing JD'

# `Apply` and `AI recommends` stay distinct values but behave identically.
CHOSEN = frozenset({SELECTED, AI_SELECTED})

# Agent transitions only: target -> states it may leave from. The user's own
# edits (Apply, Skip, ...) never pass through this table. An Apply worker writes Skip from Ready to fill only
# when the form asks for information it must not give or the user says so in its session; Screen's
# rescreen writes it when a job Screen chose (AI recommends) or that waits for its form fails the screening rules.
# A chosen row without JD waits as Missing JD until someone fetches its JD; dedupe then returns it to AI recommends.
AGENT_TRANSITIONS: dict[str, frozenset[str]] = {
    REJECTED: frozenset({TO_FILL, AI_SELECTED}),
    TO_FILL: CHOSEN,
    SUBMITTED: frozenset({TO_FILL}),
    CLOSED: CHOSEN | {TO_FILL, MISSING_JD},
    DUPLICATE: CHOSEN | {TO_FILL, MISSING_JD},
    MISSING_JD: CHOSEN | {TO_FILL},
    AI_SELECTED: frozenset({MISSING_JD}),
}


def current(row) -> str:
    return row[STATE_HEADER].strip()


def jobs_root() -> Path:
    # Apply's per-job records: browser receipts, form evidence and which private fields a form asked for
    return Path(__file__).resolve().parents[3] / 'apply-job/tmp/jobs'


def job_dir(job_id: str) -> Path:
    if not re.fullmatch(r'[A-Za-z0-9_-]+', job_id):
        raise ValueError(f'invalid job ID: {job_id!r}')
    return jobs_root() / job_id
