import copy
import json
import multiprocessing
import re
import time
from unittest.mock import Mock

import pytest

from jobhunting_core import sheet
from jobhunting_screen import jobs
from jobhunting_screen.jobs import exact_url_job, normalize_arrangement, normalize_level, same_job_group, validate_job_input

JOBS_HEADERS = (
    "job_id", "url", "source", "company", "title", "location",
    "work_arrangement", "summary", "requirements", "jd", "resume",
    "salary_range", "level", "sponsorship", "posted_date", "query",
    "search_id", "priority_score", "external_ref", "remark",
    "collected_at", "state", "log", "progress", "same_job_group",
)
SEARCHES_HEADERS = ("search_id", "source", "query", "prompt", "time", "result_count", "outcome")


@pytest.fixture
def sheet_service():
    service = Mock()
    service.cells = {
        "Jobs": [list(JOBS_HEADERS), list(JOBS_HEADERS)],
        "Searches": [list(SEARCHES_HEADERS), list(SEARCHES_HEADERS)],
    }
    service.grids = {
        "Jobs": {"sheetId": 1, "title": "Jobs", "gridProperties": {"columnCount": 30, "rowCount": 1000}},
        "Searches": {"sheetId": 2, "title": "Searches", "gridProperties": {"columnCount": 26, "rowCount": 1000}},
    }
    values = service.spreadsheets.return_value.values.return_value

    def metadata(*, spreadsheetId):
        return Mock(execute=Mock(return_value={"sheets": [{"properties": copy.deepcopy(properties)} for properties in service.grids.values()]}))

    def batch_update(*, spreadsheetId, body):
        replies = []
        for request in body["requests"]:
            if "addSheet" in request:
                properties = copy.deepcopy(request["addSheet"]["properties"])
                title = properties["title"]
                properties["sheetId"] = max((p["sheetId"] for p in service.grids.values()), default=0) + 1
                properties.setdefault("gridProperties", {"columnCount": 26, "rowCount": 1000})
                service.grids[title] = properties
                service.cells[title] = []
                replies.append({"addSheet": {"properties": properties}})
            elif "appendDimension" in request:
                dimension = request["appendDimension"]
                properties = next(p for p in service.grids.values() if p["sheetId"] == dimension["sheetId"])
                count = {"COLUMNS": "columnCount", "ROWS": "rowCount"}[dimension["dimension"]]
                properties["gridProperties"][count] += dimension["length"]
                replies.append({})
            else:
                replies.append({})
        return Mock(execute=Mock(return_value={"replies": replies}))

    def get(*, spreadsheetId, range):
        title, cell_range = range.split("!")
        rows = service.cells[title]
        selected = rows[:1] if cell_range == "1:1" else rows[2:]
        return Mock(execute=Mock(return_value={"values": copy.deepcopy(selected)}))

    def update(*, spreadsheetId, range, valueInputOption, body):
        title, cell_range = range.split("!")
        match = re.fullmatch(r"([A-Z]+)([0-9]+)(?::[A-Z]+[0-9]+)?", cell_range)
        assert match, range
        letters, row_number = match.groups()
        column = 0
        for letter in letters:
            column = column * 26 + ord(letter) - ord("A") + 1
        width = service.grids[title]["gridProperties"]["columnCount"]
        if any(column - 1 + len(data) > width for data in body["values"]):
            raise RuntimeError(f"{range} exceeds grid column count {width}")
        if int(row_number) - 1 + len(body["values"]) > service.grids[title]["gridProperties"]["rowCount"]:
            raise RuntimeError(f"{range} exceeds grid row count")
        rows = service.cells[title]
        for offset, data in enumerate(body["values"]):
            index = int(row_number) - 1 + offset
            while len(rows) <= index:
                rows.append([])
            rows[index].extend([""] * max(0, column - 1 + len(data) - len(rows[index])))
            rows[index][column - 1:column - 1 + len(data)] = data
        return Mock(execute=Mock(return_value={}))

    def clear(*, spreadsheetId, range, body):
        service.cells[range] = []
        return Mock(execute=Mock(return_value={}))

    values.get.side_effect = get
    values.update.side_effect = update
    def values_batch_update(*, spreadsheetId, body):
        for item in body["data"]:
            update(spreadsheetId=spreadsheetId, range=item["range"], valueInputOption="RAW", body={"values": item["values"]})
        return Mock(execute=Mock(return_value={}))
    values.batchUpdate.side_effect = values_batch_update
    values.clear.side_effect = clear
    service.spreadsheets.return_value.get.side_effect = metadata
    service.spreadsheets.return_value.batchUpdate.side_effect = batch_update
    return service


def _job_payload(**overrides):
    job = {
        "url": "https://example/jobs/1",
        "source": "LinkedIn",
        "company": "Example",
        "title": "NLP Engineer",
        "location": "Canada",
        "work_arrangement": "Remote",
        "summary": "Builds language-model evaluation systems.",
        "requirements": "Python; production ML", "jd": "Full JD text.",
    }
    job.update(overrides)
    return job


def _plain_service():
    """A Mock service whose Jobs and Searches grids have room for any write."""
    service = Mock()
    service.spreadsheets.return_value.get.return_value.execute.return_value = {"sheets": [
        {"properties": {"sheetId": index, "title": title, "gridProperties": {"rowCount": 1000}}}
        for index, title in enumerate(("Jobs", "Searches"), 1)]}
    return service


def _install_two_row_get(values_ns, key_row, data_values):
    """Mock `values().get()` so a `!1:1` range returns `key_row` and any
    other range (the data fetch, from row 3 on) returns `data_values`.
    Returns the two distinct result mocks so callers can assert on
    `.execute` calls for each.
    """
    key_result = Mock()
    key_result.execute.return_value = {"values": [key_row]}
    data_result = Mock()
    data_result.execute.return_value = {"values": data_values}

    def _get(*, spreadsheetId, range, **_):
        return key_result if range.endswith("!1:1") else data_result

    values_ns.get.side_effect = _get
    return key_result, data_result


def _job_row_values(**overrides):
    row = {header: "" for header in JOBS_HEADERS}
    row.update(overrides)
    return [row[header] for header in JOBS_HEADERS]


def test_append_preserves_notes_and_existing_manual_progress(sheet_service):
    keys = list(reversed(JOBS_HEADERS)) + ["custom_field"]
    sheet_service.cells["Jobs"] = [keys.copy(), keys.copy()]
    existing = dict(job_id="old-job", url="https://example/jobs/old", remark="human note", state="Filling", progress="OA", custom_field="custom")
    old_row = [existing.get(key, "") for key in keys]
    sheet_service.cells["Jobs"].append(old_row.copy())
    note = "Accepts June 2027 graduates; remote Canada; Python work."

    result = jobs.append_job(sheet_service, _job_payload(discovery_notes=note))

    assert result["inserted"] is True
    assert sheet_service.cells["Jobs"][2] == old_row
    rows = sheet.read_rows(sheet_service, "Jobs")[1]
    assert rows[1]["state"] == "AI recommends"
    assert rows[1]["log"].endswith(" found · Discover · " + note)
    assert rows[1]["remark"] == rows[1]["progress"] == ""


def test_append_without_notes_logs_the_source_and_normalizes_categories(sheet_service):
    keys = list(JOBS_HEADERS)
    sheet_service.cells["Jobs"] = [keys.copy(), keys.copy()]

    result = jobs.append_job(sheet_service, _job_payload(level="2+ years", work_arrangement="Hybrid (3 days)"))

    assert result["inserted"] is True
    row = sheet.read_rows(sheet_service, "Jobs")[1][0]
    assert row["job_id"] == result["job_id"]
    assert (row["level"], row["work_arrangement"]) == ("Senior", "Hybrid")
    assert " found · Discover · " in row["log"]



@pytest.mark.parametrize("kind", ["different_urls", "same_url"])
def test_concurrent_appends_preserve_rows_and_exact_url_dedup(tmp_path, monkeypatch, kind):
    """Two processes contend between snapshot read and next-row update."""
    storage = tmp_path / "rows.json"
    storage.write_text("[]")
    monkeypatch.setattr(sheet.tempfile, "gettempdir", lambda: str(tmp_path))

    def fetch(service, tab):
        rows = json.loads(storage.read_text())
        time.sleep(0.2)  # Without the transaction lock, both read the same next row.
        return list(JOBS_HEADERS), rows

    def write(service, tab, key_row, row_number, row):
        rows = json.loads(storage.read_text())
        index = row_number - sheet.FIRST_DATA_ROW
        row = {key: row.get(key, "") for key in key_row}
        if index == len(rows):
            rows.append(row)
        else:
            rows[index] = row
        storage.write_text(json.dumps(rows))

    monkeypatch.setattr(sheet, "read_rows", fetch)
    monkeypatch.setattr(sheet, "append_row", write)
    context = multiprocessing.get_context("fork")
    start = context.Event()
    ready, results = context.Queue(), context.Queue()

    def append(index):
        ready.put(index)
        assert start.wait(5)
        url = "https://example/job/1" if kind == "same_url" else f"https://example/job/{index}"
        results.put(jobs.append_job(None, _job_payload(url=url)))

    children = [context.Process(target=append, args=(index,)) for index in (1, 2)]
    try:
        for child in children:
            child.start()
        for _ in children:
            ready.get(timeout=5)
        start.set()
        for child in children:
            child.join(5)
            assert child.exitcode == 0
        responses = [results.get(timeout=5) for _ in children]
        rows = json.loads(storage.read_text())
        if kind == "same_url":
            assert len(rows) == 1
            assert sorted(result["inserted"] for result in responses) == [False, True]
        else:
            assert {row["url"] for row in rows} == {"https://example/job/1", "https://example/job/2"}
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(5)



def test_append_skips_literal_duplicate_url():
    service = _plain_service()
    values_ns = service.spreadsheets.return_value.values.return_value
    key_result, data_result = _install_two_row_get(
        values_ns,
        key_row=list(JOBS_HEADERS),
        data_values=[_job_row_values(job_id="job-existing", url="https://example/jobs/1")],
    )

    result = jobs.append_job(service, _job_payload())

    assert result == {"inserted": False, "job_id": "job-existing"}
    values_ns.append.assert_not_called()
    values_ns.update.assert_not_called()


def test_append_keeps_different_url_for_user_review():
    service = _plain_service()
    values_ns = service.spreadsheets.return_value.values.return_value
    _install_two_row_get(
        values_ns,
        key_row=list(JOBS_HEADERS),
        data_values=[_job_row_values(job_id="job-existing", url="https://example/jobs/1")],
    )

    result = jobs.append_job(
        service, _job_payload(url="https://example/jobs/2")
    )

    assert result["inserted"] is True
    assert isinstance(result["job_id"], str) and result["job_id"].startswith("job-")
    assert result["job_id"] != "job-existing"
    assert result["collected_at"]

    # Regression guard: writes go through `.update()` at an explicitly
    # computed row, never `.append()`. `values().append()`'s "detect the
    # existing table" heuristic silently misreads a user-hidden FIRST
    # column as the table's left edge — confirmed live against the
    # production spreadsheet: every new row landed one column to the
    # right of A, with A itself left blank, for as long as column A
    # stayed hidden. An explicit row number sidesteps that entirely.
    values_ns.append.assert_not_called()
    values_ns.update.assert_called_once()
    update_kwargs = values_ns.update.call_args.kwargs
    assert update_kwargs["spreadsheetId"] == sheet.SPREADSHEET_ID
    appended_row = update_kwargs["body"]["values"][0]
    assert "https://example/jobs/2" in appended_row

    # One existing data row (job-existing) means the next row is
    # FIRST_DATA_ROW + 1 = row 4.
    assert update_kwargs["range"].startswith("Jobs!A4:")
    assert update_kwargs["range"].endswith("4")


def test_append_accepts_new_optional_fields_and_never_writes_remark():
    service = _plain_service()
    values_ns = service.spreadsheets.return_value.values.return_value
    _install_two_row_get(values_ns, key_row=list(JOBS_HEADERS), data_values=[])

    result = jobs.append_job(
        service,
        _job_payload(
            url="https://example/jobs/3",
            salary_range="$100k-$120k",
            level="Entry level",
            sponsorship="not mentioned",
            posted_date="2026-09-01",
            query="LLM Engineer",
            priority_score="8",
            external_ref="REQ-999",
        ),
    )

    assert result["inserted"] is True
    appended_row = values_ns.update.call_args.kwargs["body"]["values"][0]
    row_by_header = dict(zip(JOBS_HEADERS, appended_row))
    assert row_by_header["salary_range"] == "$100k-$120k"
    assert row_by_header["external_ref"] == "REQ-999"
    assert row_by_header["remark"] == ""



def test_append_groups_matching_existing_rows_without_merging(sheet_service):
    first = jobs.append_job(sheet_service, _job_payload(url="https://example/jobs/1", company="Example Inc.", location="Toronto, ON"))
    second = jobs.append_job(sheet_service, _job_payload(url="https://another/jobs/2", source="Employer Careers", company="example ltd", location="Toronto ON"))
    rows = sheet.read_rows(sheet_service, "Jobs")[1]
    assert first["inserted"] and second["inserted"]
    assert rows[0]["same_job_group"] == rows[1]["same_job_group"]
    assert rows[0]["same_job_group"].startswith("group-")


def test_same_source_rows_are_not_grouped(sheet_service):
    jobs.append_job(sheet_service, _job_payload(url="https://one.example/job", source="Indeed Canada"))
    jobs.append_job(sheet_service, _job_payload(url="https://two.example/job", source="Indeed Canada"))
    assert all(not row["same_job_group"] for row in sheet.read_rows(sheet_service, "Jobs")[1])


def test_exact_url_only():
    rows = [{"job_id": "j1", "url": "https://example/jobs/1"}]
    assert exact_url_job(rows, "https://example/jobs/1")["job_id"] == "j1"
    assert exact_url_job(rows, "https://example/jobs/1?ref=linkedin") is None


def test_validate_job_input_requires_visible_fields():
    job = validate_job_input({
        "url": "https://example/jobs/1",
        "source": "LinkedIn",
        "company": "Example",
        "title": "NLP Engineer",
        "location": "Canada",
        "work_arrangement": "Remote; hours unknown",
        "summary": "Builds language-model evaluation systems.",
        "requirements": "Python; production ML", "jd": "Full JD text.",
    })
    assert job["title"] == "NLP Engineer"


def test_validate_job_input_rejects_missing_and_unknown_fields():
    with pytest.raises(ValueError, match="summary"):
        validate_job_input({"url": "https://example/jobs/1"})

    with pytest.raises(ValueError, match="unexpected"):
        validate_job_input({
            "url": "https://example/jobs/1",
            "source": "Employer",
            "company": "Example",
            "title": "Engineer",
            "location": "Canada",
            "work_arrangement": "Remote",
            "summary": "Build systems.",
            "requirements": "Python", "jd": "Full JD text.",
            "unexpected": "value",
        })


@pytest.mark.parametrize("text,expected", [
    ("Internship", "Intern"), ("Co-op / Internship", "Intern"), ("New Grad (2026)", "New grad"), ("Entry level", "New grad"),
    ("Early career; 0-7 years", "New grad"), ("1-3 years", "New grad"), ("Software Engineer I", "New grad"),
    ("2+ years experience", "Senior"), ("Mid-Senior level", "Senior"), ("Software Engineer II", "Senior"),
    ("Staff", "Lead"), ("New Grad / graduating student", "New grad"), ("Early Professional", "New grad"),
    ("Graduate-level Research Intern", "Intern"), ("Senior Lead", "Lead"), ("Director", "Lead"), ("", "Unknown"), ("Full-time", "Unknown"), ("New grad", "New grad"),
])
def test_level_is_normalized_to_one_category(text, expected):
    assert normalize_level(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("Remote", "Remote"), ("Canada (remote-eligible)", "Remote"), ("Hybrid, 3 days/week in Toronto", "Hybrid"),
    ("Remote or hybrid", "Hybrid"), ("On-site", "Onsite"), ("Full time; on site", "Onsite"), ("In office 5 days", "Onsite"), ("Not remote", "Unknown"),
    ("", "Unknown"), ("Hybrid", "Hybrid"),
])
def test_work_arrangement_is_normalized(text, expected):
    assert normalize_arrangement(text) == expected


def test_group_normalization():
    assert same_job_group("  Acme, Inc. ", "ML Engineer!", "Toronto, ON") == same_job_group("acme LTD", "ml  engineer", "Toronto ON")
    assert same_job_group("Acme", "ML Engineer", "Toronto") != same_job_group("Acme", "ML Engineer", "Vancouver")
    assert same_job_group("", "ML Engineer", "Toronto") == ""
    assert same_job_group("Société Générale Inc.", "Ingénieur logiciel", "Montréal") == same_job_group("Société Générale Ltd", "Ingénieur logiciel", "Montréal")
