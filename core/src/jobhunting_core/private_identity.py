"""Sensitive identity values (ID numbers, security-question answers): read only when a form asks, and never written to the Sheet."""
import json
import os
import re
from pathlib import Path

FIELDS = ('passport_number', 'mother_maiden_name', 'sin')


def private_values(fields):
    root = Path(os.environ['JOBHUNTING_PRIVATE_DIR'])
    identity = json.loads((root / 'identity.json').read_text()) if set(fields) - {'sin'} else {}
    return [(root / 'sin').read_text().strip() if field == 'sin' else identity[field] for field in fields]


def used_fields(directory):
    marker = Path(directory) / 'private-fields-used.json'
    return json.loads(marker.read_text()) if marker.exists() else []


def reject_for_job(data, directory):
    # Only jobs whose form asked for a private field can leak one; spaces and dashes are ignored, so "123-456-789" still matches
    fields = used_fields(directory)
    if not fields:
        return
    squash = lambda text: re.sub(r'[\s-]', '', text).lower()
    rendered = squash(json.dumps(data, ensure_ascii=False))
    if any(squash(value) in rendered for value in private_values(fields)):
        raise ValueError('private identity value in output')
