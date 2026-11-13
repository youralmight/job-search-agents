import copy
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit

from jobhunting_core import sheet
from jobhunting_core.postings import job_key
from jobhunting_discover.schema import validate_search_input


def verify_result(model: dict, metadata: dict, jobs: list[dict[str, str]]) -> dict:
    # Validate the model contract; the model does not own counts or resource evidence.
    fields = {"search_id", "status", "summary", "coverage", "candidates", "remaining", "materials"}
    if not isinstance(model, dict):
        raise ValueError("model must be an object")
    missing, unexpected = fields - model.keys(), model.keys() - fields
    if missing or unexpected:
        raise ValueError(f"model missing fields: {sorted(missing)}; unexpected fields: {sorted(unexpected)}")
    for field in ("search_id", "status", "summary"):
        if not isinstance(model[field], str) or not model[field].strip():
            raise ValueError(f"model {field} must be nonempty text")
    if model["search_id"] != metadata["search_id"]:
        raise ValueError(f"search_id mismatch: {model['search_id']!r} != {metadata['search_id']!r}")
    if model["status"] not in {"completed", "blocked", "partial", "failed"}:
        raise ValueError(f"invalid status: {model['status']!r}")
    task = metadata["task"]
    if task["kind"] not in {"board", "company", "material"}:
        raise ValueError(f"invalid task kind: {task['kind']!r}")
    if not isinstance(task["source"], str) or not task["source"].strip():
        raise ValueError("task source must be nonempty text")
    for field in ("coverage", "candidates", "remaining", "materials"):
        if not isinstance(model[field], list):
            raise ValueError(f"model {field} must be an array")
    if any(not isinstance(item, str) or not item.strip() for item in model["remaining"]):
        raise ValueError("remaining must contain nonempty text")

    def text_fields(record, expected, context):
        if not isinstance(record, dict):
            raise ValueError(f"{context} must be an object")
        missing, unexpected = set(expected) - record.keys(), record.keys() - set(expected)
        if missing or unexpected:
            raise ValueError(f"{context} missing fields: {sorted(missing)}; unexpected fields: {sorted(unexpected)}")
        empty = [field for field in expected if not isinstance(record[field], str) or not record[field].strip()]
        if empty:
            raise ValueError(f"{context} needs nonempty text: {', '.join(empty)}")

    # Only check that coverage and clue records are complete; eligibility is not judged in Python.
    for index, coverage in enumerate(model["coverage"]):
        text_fields(coverage, ("query", "url", "scope", "stop_reason"), f"coverage[{index}]")
    for index, material in enumerate(model["materials"]):
        text_fields(material, ("clue", "status", "reason"), f"materials[{index}]")
        if material["status"] not in {"completed", "unresolved"}:
            raise ValueError(f"materials[{index}] invalid status: {material['status']!r}")
    seen_urls = set()
    for index, candidate in enumerate(model["candidates"]):
        text_fields(candidate, ("url", "company", "title", "decision", "reason", "evidence"), f"candidates[{index}]")
        if candidate["decision"] not in {"recommend", "exclude", "unread"}:
            raise ValueError(f"candidates[{index}] invalid decision: {candidate['decision']!r}")
        url = candidate["url"]
        # One candidate per posting: tracking-parameter variants share a source key.
        if job_key(url) in seen_urls:
            raise ValueError(f"duplicate candidate posting: {url!r}")
        seen_urls.add(job_key(url))
        if candidate["decision"] == "recommend":
            try:
                parsed = urlsplit(url)
                valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username and not parsed.password
            except ValueError:
                valid = False
            if not valid or any(character.isspace() for character in url):
                raise ValueError(f"recommended URL must be HTTP(S): {url!r}")

    # Jobs is the sole source of truth for new rows and exact duplicates.
    new_jobs = [row for row in jobs if row.get("search_id") == metadata["search_id"]]
    counts = {"inserted": len(new_jobs), "excluded": 0, "exact_duplicates": 0, "unread": 0}
    recommended_urls = set()
    recommended_keys = set()
    for candidate in model["candidates"]:
        decision = candidate["decision"]
        if decision != "recommend":
            counts[{"exclude": "excluded", "unread": "unread"}[decision]] += 1
            continue
        url = candidate["url"]
        matches = [row for row in jobs if row.get("url") and job_key(row["url"]) == job_key(url)]
        if not matches:
            raise ValueError(f"recommended URL absent from actual Jobs: {url!r}")
        if len({row["url"] for row in matches}) != len(matches):
            raise ValueError(f"multiple actual Jobs for the same literal URL: {url!r}")
        current_matches = [row for row in matches if row.get("search_id") == metadata["search_id"]]
        if len(current_matches) > 1:
            raise ValueError(f"multiple new Jobs for recommended source ID: {url!r}")
        row = current_matches[0] if current_matches else matches[0]
        if row.get("search_id") == metadata["search_id"]:
            for field in ("company", "title"):
                actual = row.get(field)
                if not isinstance(actual, str) or " ".join(actual.split()) != " ".join(candidate[field].split()):
                    raise ValueError(f"recommended {field} differs from actual Jobs for URL: {url!r}")
        recommended_urls.add(url)
        key = job_key(url)
        if key in recommended_keys or row.get("search_id") != metadata["search_id"]:
            counts["exact_duplicates"] += 1
        recommended_keys.add(key)
    for row in new_jobs:
        if not any(job_key(row.get("url", "")) == job_key(url) for url in recommended_urls):
            raise ValueError(f"unaccounted new Jobs row: {row.get('url')!r}")

    # Unfinished work cannot be reported as complete.
    if model["status"] == "completed":
        if model["remaining"]:
            raise ValueError("completed result has remaining work")
        if counts["unread"]:
            raise ValueError("completed result has unread candidates")
        if not model["coverage"]:
            raise ValueError("completed result needs coverage")
        if any(item["status"] == "unresolved" for item in model["materials"]):
            raise ValueError("completed result has unresolved materials")
        if task["kind"] == "material" and not model["materials"]:
            raise ValueError("completed material task needs materials clues")
    return {**copy.deepcopy(model), "source": task["source"], "counts": counts, "new_jobs": copy.deepcopy(new_jobs)}


def search_entry(result: dict, metadata: dict, prompt: str) -> dict[str, str]:
    if result["search_id"] != metadata["search_id"] or result["source"] != metadata["task"]["source"]:
        raise ValueError("search_id or source differs from verified result")
    queries = list(dict.fromkeys(item["query"] for item in result["coverage"]))
    outcome = {field: result[field] for field in ("status", "summary", "counts", "coverage", "remaining", "materials")}
    return validate_search_input({
        "search_id": result["search_id"], "source": result["source"],
        "query": "\n".join(queries) if queries else "no query run", "prompt": prompt,
        "result_count": str(result["counts"]["inserted"]),
        "outcome": json.dumps(outcome, ensure_ascii=False),
    })


def log_search(service, entry: dict[str, str]) -> dict[str, str]:
    """One Searches row per search task. A retry returns the row its first attempt wrote: a dropped connection
    can hide a write that landed."""
    row = {key: sheet.cell(value, "Searches") for key, value in entry.items()}
    with sheet.workbook_lock():
        key_row, rows = sheet.read_rows(service, "Searches")
        written = next((old for old in rows if old["search_id"] == row["search_id"]), None)
        if written is not None:
            return {"search_id": written["search_id"], "time": written["time"]}
        row["time"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        sheet.append_row(service, "Searches", key_row, sheet.FIRST_DATA_ROW + len(rows), row)
    return {"search_id": row["search_id"], "time": row["time"]}


def render_batch_report(results: list[dict]) -> str:
    if not results:
        return "No search results were recorded."
    labels = {"completed": "Completed", "blocked": "Blocked", "partial": "Partial", "failed": "Failed"}
    displayed_counts = []
    for result in results:
        counts = result["counts"].copy()
        verification = result.get("verification", {})
        if verification.get("model") is False:
            for field in ("excluded", "exact_duplicates", "unread"):
                counts[field] = "unverified"
        if verification.get("jobs") is False:
            counts["inserted"] = "unverified"
        displayed_counts.append(counts)
    totals = {field: "unverified" if any(counts[field] == "unverified" for counts in displayed_counts)
              else sum(counts[field] for counts in displayed_counts)
              for field in ("inserted", "excluded", "exact_duplicates", "unread")}
    lines = ["## Job discovery report", "",
             f"New jobs: {totals['inserted']}; exact-duplicate recommendations: {totals['exact_duplicates']}; "
             f"excluded: {totals['excluded']}; unread: {totals['unread']}."]
    for result, counts in zip(results, displayed_counts):
        # Unverified counts are shown as unknown, never recomputed from an invalid model.
        lines.extend(["", f"### {result['source']}: {labels[result['status']]}", "", result["summary"], "",
                      f"New {counts['inserted']}; exact duplicates {counts['exact_duplicates']}; "
                      f"excluded {counts['excluded']}; unread {counts['unread']}.", "", "**Source coverage**", ""])
        if counts["excluded"] == "unverified":
            lines.append(f"Model evidence is unverified; the excluded, exact-duplicate and unread counts cannot be confirmed. The raw model is saved for inspection (search_id: {result['search_id']}); task evidence is under Outstanding.")
        if result["coverage"]:
            for coverage in result["coverage"]:
                lines.append(f"- Query: {coverage['query']}; entry: {coverage['url']}; "
                             f"scope checked: {coverage['scope']}; stopped because: {coverage['stop_reason']}")
        else:
            lines.append("No checked source scope was recorded.")

        # Show new jobs and recommendations of already-known exact URLs separately so duplicates are not reported as new.
        new_by_url = {row["url"]: row for row in result["new_jobs"]}
        recommendations = [candidate for candidate in result["candidates"] if candidate["decision"] == "recommend"]
        lines.extend(["", "**Recommended jobs**", ""])
        if not recommendations:
            lines.append("No verified recommended jobs this run.")
        for candidate in recommendations:
            row = new_by_url.get(candidate["url"])
            state = f"new · {row['job_id']}" if row is not None else "existing job · exact URL duplicate"
            lines.extend([f"- **{candidate['company']} · {candidate['title']}** ({state})",
                          f"  - Job: <{candidate['url']}>",
                          f"  - Reason: {candidate['reason']}", f"  - Page evidence: {candidate['evidence']}"])
            if row is not None:
                lines.extend([f"  - Location and arrangement: {row['location']}; {row['work_arrangement']}",
                              f"  - Saved reason: {row['log'].splitlines()[0]}"])

        # Unread jobs, remaining scope and material clues keep their specific reasons.
        if result["remaining"] or counts["unread"]:
            lines.extend(["", "**Outstanding**", ""])
            lines.extend(f"- {item}" for item in result["remaining"])
            for candidate in result["candidates"]:
                if candidate["decision"] == "unread":
                    lines.append(f"- Unread: {candidate['company']} · {candidate['title']}; {candidate['url']}; "
                                 f"reason: {candidate['reason']}; seen so far and gaps: {candidate['evidence']}")
        if result["materials"]:
            lines.extend(["", "**Material clues**", ""])
            for material in result["materials"]:
                state = "checked" if material["status"] == "completed" else "unresolved"
                lines.append(f"- {material['clue']}: {state}; {material['reason']}")
    return "\n".join(lines)
