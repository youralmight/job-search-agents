"""The campaign Sheet. Row 1 of each tab holds machine column keys, row 2 human labels, data starts at row 3.

Every column is found by its key in the live row 1, so the user may reorder, hide and relabel columns.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import time
import tomllib
import uuid
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from googleapiclient.errors import HttpError

from jobhunting_core import state
from jobhunting_core.private_identity import reject_for_job

ROOT = Path(__file__).resolve().parents[3]
SPREADSHEET_ID = tomllib.loads((ROOT / "campaign.toml").read_text())["spreadsheet_id"]
FIRST_DATA_ROW = 3
# A cell holds 50,000 characters; longer text goes to a file the cell names.
CELL_LIMIT = 48000


@contextmanager
def workbook_lock():
    """One writer per workbook on this machine at a time. Every workflow and checkout shares this lock file:
    a checkout-local lock would let two writers take the same next row."""
    digest = hashlib.sha256(SPREADSHEET_ID.encode()).hexdigest()[:16]
    path = os.path.join(tempfile.gettempdir(), f"jobhunting-sheet-{os.getuid()}-{digest}.lock")
    with os.fdopen(os.open(path, os.O_CREAT | os.O_RDWR, 0o600), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def call(function, *args):
    """`function(*args)`, retried: Sheets allows 60 reads a minute per user and every workflow shares it."""
    for attempt in range(3):
        try:
            return function(*args)
        except (OSError, HttpError) as error:
            quota = isinstance(error, HttpError) and error.resp.status == 429
            if (isinstance(error, HttpError) and not quota) or attempt == 2:
                raise
            time.sleep(65 if quota else 1)


def column_letter(index: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA."""
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _row(key_row: Sequence[str], raw: Sequence[str]) -> dict[str, str]:
    return dict(zip(key_row, [*raw, *[""] * (len(key_row) - len(raw))]))


def read_rows(service, tab: str) -> tuple[list[str], list[dict[str, str]]]:
    """The live key row and every data row of a tab."""
    values = service.spreadsheets().values()
    key_row = values.get(spreadsheetId=SPREADSHEET_ID, range=f"{tab}!1:1").execute(num_retries=2)["values"][0]
    data = values.get(spreadsheetId=SPREADSHEET_ID, range=f"{tab}!A{FIRST_DATA_ROW}:{column_letter(len(key_row))}"
                      ).execute(num_retries=2).get("values", [])
    return key_row, [_row(key_row, raw) for raw in data]


def _row_hints_path() -> Path:
    return ROOT / "tmp/sheet-rows.json"


def _read_jobs(service) -> tuple[list[str], list[dict[str, str]]]:
    key_row, rows = read_rows(service, "Jobs")
    # Remember each job's row, so a later single-job read fetches just that row
    hints = {row["job_id"]: index + FIRST_DATA_ROW for index, row in enumerate(rows) if row["job_id"]}
    path = _row_hints_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=path.name + ".", delete=False) as temporary:
        json.dump(hints, temporary)
    os.replace(temporary.name, path)
    return key_row, rows


def read_jobs(service) -> list[dict[str, str]]:
    return _read_jobs(service)[1]


def read_searches(service) -> list[dict[str, str]]:
    return read_rows(service, "Searches")[1]


def _job_row(service, job_id: str) -> tuple[list[str], int, dict[str, str]]:
    # Try the remembered row first; a row that moved (inserted, deleted, re-sorted lines) falls back to a full read
    path = _row_hints_path()
    sheet_row = json.loads(path.read_text()).get(job_id) if path.exists() else None
    if sheet_row:
        response = service.spreadsheets().values().batchGet(
            spreadsheetId=SPREADSHEET_ID, ranges=["Jobs!1:1", f"Jobs!{sheet_row}:{sheet_row}"],
        ).execute(num_retries=2)
        key_values, row_values = (item.get("values", [[]]) for item in response["valueRanges"])
        row = _row(key_values[0], row_values[0])
        if row["job_id"] == job_id:
            return key_values[0], sheet_row, row

    key_row, rows = _read_jobs(service)
    matches = [index for index, row in enumerate(rows) if row["job_id"] == job_id]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one job with job_id={job_id!r}, found {len(matches)}")
    return key_row, matches[0] + FIRST_DATA_ROW, rows[matches[0]]


def get_job(service, job_id: str) -> dict[str, str]:
    return _job_row(service, job_id)[2]


def write_cells(service, tab: str, key_row: Sequence[str], changes: Mapping[int, Mapping[str, str]]) -> None:
    """Named cells of several rows in one request: {sheet row: {column key: value}}."""
    data = [{"range": f"{tab}!{column_letter(key_row.index(name) + 1)}{sheet_row}", "values": [[value]]}
            for sheet_row, cells in changes.items() for name, value in cells.items()]
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID, body={"valueInputOption": "RAW", "data": data},
    ).execute(num_retries=2)


def cell(value: str, tab: str) -> str:
    """The value, or for text past CELL_LIMIT its head and the path of a file holding all of it."""
    if len(value.encode("utf-16-le")) // 2 <= CELL_LIMIT:
        return value
    directory = ROOT / "tmp/sheet-overflow"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f"{tab}-cell-{hashlib.sha256(value.encode()).hexdigest()[:20]}.txt"
    if not path.exists():
        temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
        with temporary.open("x", encoding="utf-8") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(value)
        temporary.replace(path)
    marker = f"\n[truncated; full text: {path}]"
    return value.encode("utf-16-le")[: (CELL_LIMIT - len(marker)) * 2].decode("utf-16-le", errors="ignore") + marker


def append_row(service, tab: str, key_row: Sequence[str], sheet_row: int, row: Mapping[str, str]) -> None:
    """Write a new row at `sheet_row`, growing a full grid first. Hold `workbook_lock` from the read that gave
    `sheet_row` until this returns.

    Never `values().append()`: its table detection took a user-hidden column A as the table's left edge and wrote
    every row one column to the right. No read-back either: `values().get()` drops trailing empty cells, so a
    read-back never matched and its retries once turned a 20-row import into 200 rows.
    """
    metadata = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute(num_retries=2)
    properties = next(item["properties"] for item in metadata["sheets"] if item["properties"]["title"] == tab)
    missing_rows = sheet_row - properties["gridProperties"]["rowCount"]
    if missing_rows > 0:
        service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{"appendDimension": {
                "sheetId": properties["sheetId"], "dimension": "ROWS", "length": missing_rows,
            }}]},
        ).execute(num_retries=2)
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID, range=f"{tab}!A{sheet_row}:{column_letter(len(key_row))}{sheet_row}",
        valueInputOption="RAW", body={"values": [[cell(row.get(key, ""), tab) for key in key_row]]},
    ).execute(num_retries=2)


def _reject_progress_columns(changes: Mapping[str, str]) -> None:
    if set(changes) & {state.STATE_HEADER, state.LOG_HEADER, state.PROGRESS_HEADER}:
        raise ValueError("state, log and progress can only be written via transition or by the user")


def transition(service, job_id: str, target: str, note: str, *,
               actor: str = "Agent", expect: Sequence[str] | None = None,
               expect_fields: Mapping[str, str] | None = None,
               extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The only way an Agent writes state: check the move against the live row and prepend one log line."""
    reject_for_job((note, dict(extra or {})), state.jobs_root() / job_id)
    _reject_progress_columns(extra or {})

    with workbook_lock():
        # Check the move against the live state
        key_row, sheet_row, row = _job_row(service, job_id)
        source = state.current(row)
        if expect is not None and source not in expect:
            raise ValueError(f"{job_id} is in state {source}, not one of {'/'.join(expect)}")
        changed = [name for name, value in (expect_fields or {}).items() if row[name] != value]
        if changed:
            raise ValueError(f"{job_id} fields changed: {', '.join(changed)}; result not written")
        if target not in state.AGENT_TRANSITIONS:
            raise ValueError(f"An Agent cannot set the state to {target!r}")
        if source not in state.AGENT_TRANSITIONS[target]:
            raise ValueError(f"Transition not allowed: {source} → {target}")

        # Write the state and the new log line in one request
        line = f"{datetime.now().astimezone():%Y-%m-%d %H:%M} {source}→{target} · {actor} · {' '.join(note.split())}"
        log = row[state.LOG_HEADER]
        changes = {**(extra or {}), state.STATE_HEADER: target, state.LOG_HEADER: line + ("\n" + log if log.strip() else "")}
        write_cells(service, "Jobs", key_row, {sheet_row: changes})
    return {**row, **changes}


def update_job(service, job_id: str, changes: Mapping[str, str]) -> dict[str, str]:
    """Ordinary columns of one job; state, log and progress are not among them."""
    reject_for_job(changes, state.jobs_root() / job_id)
    _reject_progress_columns(changes)
    with workbook_lock():
        key_row, sheet_row, row = _job_row(service, job_id)
        write_cells(service, "Jobs", key_row, {sheet_row: changes})
    return {**row, **changes}
