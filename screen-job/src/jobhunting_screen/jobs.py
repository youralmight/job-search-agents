"""A recommended posting becomes a Jobs row, once per source job ID, starting at state AI recommends."""
from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from jobhunting_core import sheet, state
from jobhunting_core.postings import job_key

REQUIRED_JOB_INPUT = (
    "url", "source", "company", "title", "location",
    "work_arrangement", "summary", "requirements", "jd",
)
OPTIONAL_JOB_INPUT = (
    "resume", "salary_range", "level", "sponsorship", "posted_date",
    "query", "search_id", "priority_score", "external_ref", "discovery_notes",
)
# `remark` is the user's: never settable at append time.
JOB_INPUT_FIELDS = frozenset((*REQUIRED_JOB_INPUT, *OPTIONAL_JOB_INPUT))

# Normalized categories; the user may correct a cell by hand.
LEVELS = ("Intern", "New grad", "Senior", "Lead", "Unknown")
ARRANGEMENTS = ("Remote", "Hybrid", "Onsite", "Unknown")


def normalize_level(text: str) -> str:
    value = " ".join((text or "").casefold().split())
    if value in {level.casefold() for level in LEVELS}:
        return next(level for level in LEVELS if level.casefold() == value)
    if re.search(r"new ?grad|graduating", value):
        return "New grad"
    if re.search(r"intern|co-?op|student|placement", value):
        return "Intern"
    if re.search(r"graduate|entry|junior|associate|\bearly\b|university|campus|"
                 r"\b0\s*[-–]\s*[1-3]\b|\b1\s*[-–]\s*[23]\b|\b[01]\+|\b(?:engineer|developer|scientist|analyst) i\b", value):
        return "New grad"
    if re.search(r"director|principal|staff|\blead\b|manager|head of|architect|vp\b", value):
        return "Lead"
    if re.search(r"senior|\bsr\b|mid|intermediate|experienced|\b(?:ii|iii|iv)\b|\b[2-9]\+?\s*(?:[-–]\s*\d+\s*)?(?:years|yrs)", value):
        return "Senior"
    return "Unknown"


def normalize_arrangement(text: str) -> str:
    value = " ".join((text or "").casefold().split())
    if value in {item.casefold() for item in ARRANGEMENTS}:
        return next(item for item in ARRANGEMENTS if item.casefold() == value)
    if "hybrid" in value:
        return "Hybrid"
    if re.search(r"(?<!not )(?<!non-)remote|work from home|wfh", value):
        return "Remote"
    if re.search(r"on[- ]?site|in[- ]office|in[- ]person|office-based|\boffice\b", value):
        return "Onsite"
    return "Unknown"


def validate_job_input(job: Mapping[str, str]) -> dict[str, str]:
    # Agents build this payload; a precise message lets them correct it and call again
    unknown = set(job) - JOB_INPUT_FIELDS
    if unknown:
        raise ValueError(f"unexpected job fields: {', '.join(sorted(unknown))}")
    missing = [field for field in REQUIRED_JOB_INPUT if not isinstance(job.get(field), str) or not job[field].strip()]
    if missing:
        raise ValueError(f"missing required job fields: {', '.join(missing)}")
    return dict(job)


def same_job_group(company: str, title: str, location: str) -> str:
    def normalize(value: str) -> str:
        value = unicodedata.normalize("NFKD", value).casefold()
        value = "".join(char for char in value if not unicodedata.combining(char))
        value = "".join(char if char.isalnum() else " " for char in value).strip()
        return " ".join(value.split())

    company_name = re.sub(r"(?:\s+(?:inc|incorporated|ltd|limited|corp|corporation|llc))+$", "", normalize(company))
    parts = (company_name, normalize(title), normalize(location))
    if not all(parts):
        return ""
    return "group-" + hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]


def exact_url_job(rows: Sequence[Mapping[str, str]], url: str) -> Mapping[str, str] | None:
    key = job_key(url)
    return next((row for row in rows if row["url"] and job_key(row["url"]) == key), None)


def append_job(service, job: Mapping[str, str]) -> dict[str, object]:
    """Append a recommended posting unless Jobs already has its source job ID. Rows of other sources with the same
    company, title and location share a `same_job_group` mark; every row is kept."""
    with sheet.workbook_lock():
        key_row, rows = sheet.read_rows(service, "Jobs")
        existing = exact_url_job(rows, job.get("url", ""))
        if existing is not None:
            return {"inserted": False, "job_id": existing["job_id"]}

        row = validate_job_input(job)
        notes = row.pop("discovery_notes", "")
        row["level"] = normalize_level(row.get("level", ""))
        row["work_arrangement"] = normalize_arrangement(row["work_arrangement"])
        now = datetime.now(timezone.utc)
        row["job_id"] = "job-" + uuid.uuid4().hex[:12]
        row["collected_at"] = now.isoformat(timespec="seconds").replace("+00:00", "Z")
        # The state machine starts here; the recommendation is the first log line.
        row[state.STATE_HEADER] = state.AI_SELECTED
        row[state.LOG_HEADER] = f"{now.astimezone():%Y-%m-%d %H:%M} found · Discover · {' '.join((notes or row['source']).split())}"

        group = same_job_group(row["company"], row["title"], row["location"])
        matching = {number: old for number, old in enumerate(rows, sheet.FIRST_DATA_ROW)
                    if group and same_job_group(old["company"], old["title"], old["location"]) == group}
        cross_source = any(old["source"] != row["source"] for old in matching.values())
        if cross_source:
            row["same_job_group"] = group
        sheet.append_row(service, "Jobs", key_row, sheet.FIRST_DATA_ROW + len(rows), row)
        regroup = {number: {"same_job_group": group} for number, old in matching.items() if old["same_job_group"] != group}
        if cross_source and regroup:
            sheet.write_cells(service, "Jobs", key_row, regroup)
    return {"inserted": True, "job_id": row["job_id"], "collected_at": row["collected_at"]}
