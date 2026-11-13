import json
import multiprocessing
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest

from jobhunting_core import sheet
from jobhunting_core.sheet import get_job, update_job

JOBS_HEADERS = ("job_id", "url", "company", "title", "jd", "resume", "remark", "state", "log", "progress")


def _install_two_row_get(values_ns, key_row, data_values):
    key_result = Mock()
    key_result.execute.return_value = {"values": [key_row]}
    data_result = Mock()
    data_result.execute.return_value = {"values": data_values}

    def _get(*, spreadsheetId, range, **_):
        return key_result if range.endswith("!1:1") else data_result

    values_ns.get.side_effect = _get
    # A second read of the same job asks only for its remembered row (the first data row here).
    values_ns.batchGet.return_value.execute.return_value = {"valueRanges": [
        {"values": [key_row]}, {"values": data_values[:1]}]}


def _job_row_values(**overrides):
    row = {header: "" for header in JOBS_HEADERS}
    row.update(overrides)
    return [row[header] for header in JOBS_HEADERS]


def test_update_job_uses_live_key_row_order_not_python_order():
    service = Mock()
    values_ns = service.spreadsheets.return_value.values.return_value
    reordered_key_row = ["title", "job_id", "url", *[h for h in JOBS_HEADERS if h not in ("title", "job_id", "url")]]
    reordered_row_values = ["NLP Engineer", "job-1", "https://example/jobs/1"] + [
        "" for h in JOBS_HEADERS if h not in ("title", "job_id", "url")
    ]
    _install_two_row_get(values_ns, key_row=reordered_key_row, data_values=[reordered_row_values])

    result = update_job(service, "job-1", {"title": "Senior NLP Engineer"})

    assert result["job_id"] == "job-1" and result["title"] == "Senior NLP Engineer"
    values_ns.batchUpdate.assert_called_once()
    assert values_ns.batchUpdate.call_args.kwargs["body"]["data"] == [
        {"range": "Jobs!A3", "values": [["Senior NLP Engineer"]]}]


def test_get_job_requires_exactly_one_job_id():
    service = Mock()
    values_ns = service.spreadsheets.return_value.values.return_value

    _install_two_row_get(values_ns, key_row=list(JOBS_HEADERS), data_values=[])
    with pytest.raises(ValueError):
        get_job(service, "missing")

    _install_two_row_get(
        values_ns,
        key_row=list(JOBS_HEADERS),
        data_values=[
            _job_row_values(job_id="job-1", url="url-a"),
            _job_row_values(job_id="job-1", url="url-b"),
        ],
    )
    with pytest.raises(ValueError):
        get_job(service, "job-1")


@pytest.mark.parametrize("column", ["state", "log", "progress"])
def test_update_job_never_writes_state_log_or_progress(column):
    service = Mock()
    _install_two_row_get(service.spreadsheets.return_value.values.return_value,
                         key_row=list(JOBS_HEADERS), data_values=[_job_row_values(job_id="job-1")])
    with pytest.raises(ValueError, match="transition"):
        update_job(service, "job-1", {column: "x"})


def _transition_service(**row):
    service = Mock()
    values_ns = service.spreadsheets.return_value.values.return_value
    _install_two_row_get(values_ns, key_row=list(JOBS_HEADERS), data_values=[_job_row_values(job_id="job-1", **row)])
    return service, values_ns


def test_transition_writes_state_and_prepends_log_in_one_request(tmp_path, monkeypatch):
    monkeypatch.setattr(sheet.state, "jobs_root", lambda: tmp_path)
    service, values_ns = _transition_service(state="Ready to fill", log="2026-10-13 09:00 AI recommends→Ready to fill · Agent · résumé generated")
    result = sheet.transition(service, "job-1", "Submitted", "user confirmed submission")
    data = values_ns.batchUpdate.call_args.kwargs["body"]["data"]
    assert len(data) == 2 and all(item["range"].endswith("3") for item in data)
    written = {item["range"]: item["values"][0][0] for item in data}
    assert result["state"] == "Submitted" in written.values()
    log = result["log"].splitlines()
    assert log[0].endswith(" Ready to fill→Submitted · Agent · user confirmed submission") and log[1].endswith("résumé generated")


def test_transition_refuses_illegal_or_unexpected_source():
    service, values_ns = _transition_service(state="To screen")
    with pytest.raises(ValueError, match="not allowed"):
        sheet.transition(service, "job-1", "Submitted", "x")
    with pytest.raises(ValueError, match="not allowed"):
        sheet.transition(service, "job-1", "Submitted", "x")
    with pytest.raises(ValueError, match="cannot"):
        sheet.transition(service, "job-1", "Apply", "x")
    service, values_ns = _transition_service(state="Skip")
    with pytest.raises(ValueError, match="not one of"):
        sheet.transition(service, "job-1", "Submitted", "x", expect=("Ready to fill",))
    values_ns.batchUpdate.assert_not_called()


@pytest.mark.parametrize("source,allowed", [("AI recommends", True), ("Ready to fill", True), ("Apply", False), ("Submitted", False)])
def test_an_agent_drops_only_jobs_it_chose_or_that_wait_for_their_form(tmp_path, monkeypatch, source, allowed):
    monkeypatch.setattr(sheet.state, "jobs_root", lambda: tmp_path)
    service, values_ns = _transition_service(state=source)
    if allowed:
        assert sheet.transition(service, "job-1", "Skip", "Rescreen excluded: requires 5 years of experience")["state"] == "Skip"
    else:
        with pytest.raises(ValueError, match="not allowed"):
            sheet.transition(service, "job-1", "Skip", "x")
        values_ns.batchUpdate.assert_not_called()


def _hinted_service(hint_row, row_at_hint, full_rows):
    service = Mock()
    values_ns = service.spreadsheets.return_value.values.return_value
    _install_two_row_get(values_ns, key_row=list(JOBS_HEADERS), data_values=full_rows)
    values_ns.batchGet.return_value.execute.return_value = {"valueRanges": [
        {"values": [list(JOBS_HEADERS)]}, {"values": [row_at_hint]}]}
    sheet._row_hints_path().write_text(json.dumps({"job-1": hint_row}))
    return service, values_ns


def test_transition_reads_only_the_remembered_row_when_it_still_matches():
    row = _job_row_values(job_id="job-1", state="Ready to fill")
    service, values_ns = _hinted_service(7, row, [row])
    sheet.transition(service, "job-1", "Submitted", "user confirmed submission")
    values_ns.get.assert_not_called()
    ranges = values_ns.batchGet.call_args.kwargs["ranges"]
    assert ranges == ["Jobs!1:1", "Jobs!7:7"]
    assert all(item["range"].endswith("7") for item in values_ns.batchUpdate.call_args.kwargs["body"]["data"])


def test_moved_row_is_detected_and_found_again_by_a_full_read():
    mine = _job_row_values(job_id="job-1", state="Ready to fill")
    other = _job_row_values(job_id="job-2", state="Ready to fill")
    # A line was inserted above: row 3 (the hint) now holds job-2, job-1 moved to row 4.
    service, values_ns = _hinted_service(3, other, [other, mine])
    sheet.transition(service, "job-1", "Submitted", "user confirmed submission")
    assert all(item["range"].endswith("4") for item in values_ns.batchUpdate.call_args.kwargs["body"]["data"])
    assert json.loads(sheet._row_hints_path().read_text())["job-1"] == 4


def test_concurrent_reads_do_not_share_a_temporary_cache_file(monkeypatch):
    service, _ = _transition_service(state="Ready to fill")
    barrier, replace = Barrier(8), sheet.os.replace

    def replace_together(source, target):
        barrier.wait(timeout=5)
        replace(source, target)

    monkeypatch.setattr(sheet.os, "replace", replace_together)
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda _: sheet.read_jobs(service), range(8)))
    assert all(result[0]["job_id"] == "job-1" for result in rows)
    assert json.loads(sheet._row_hints_path().read_text()) == {"job-1": 3}


def test_two_processes_cannot_both_make_the_same_move(tmp_path, monkeypatch):
    store = tmp_path / "row.json"
    store.write_text(json.dumps(dict(zip(JOBS_HEADERS, _job_row_values(job_id="a", state="Ready to fill")))))
    monkeypatch.setattr(sheet.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(sheet.state, "jobs_root", lambda: tmp_path)
    monkeypatch.setattr(sheet, "_read_jobs", lambda *_: (list(JOBS_HEADERS), [json.loads(store.read_text())]))

    def write(_service, _tab, _keys, changes):
        current = json.loads(store.read_text())
        time.sleep(.1)
        current.update(changes[3])
        store.write_text(json.dumps(current))
    monkeypatch.setattr(sheet, "write_cells", write)
    context = multiprocessing.get_context("fork")
    outcomes = context.Queue()

    def move():
        try:
            sheet.transition(None, "a", "Submitted", "user confirmed submission", expect=("Ready to fill",))
            outcomes.put("ok")
        except ValueError:
            outcomes.put("rejected")

    workers = [context.Process(target=move) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(5)
        assert worker.exitcode == 0
    assert sorted(outcomes.get(timeout=2) for _ in workers) == ["ok", "rejected"]


def _grid_service(row_count=1000):
    service = Mock()
    service.spreadsheets.return_value.get.return_value.execute.return_value = {"sheets": [
        {"properties": {"sheetId": 1, "title": "Jobs", "gridProperties": {"rowCount": row_count}}}]}
    return service


def test_append_row_writes_once_at_the_given_row_without_reading_back():
    # A read-back never matched (trailing empty cells are dropped) and its retries once made 20 rows into 200
    service = _grid_service()
    values_ns = service.spreadsheets.return_value.values.return_value
    sheet.append_row(service, "Jobs", ["job_id", "url", "remark"], 5, {"url": "https://example/mine", "job_id": "job-mine"})
    values_ns.update.assert_called_once()
    values_ns.get.assert_not_called()
    values_ns.append.assert_not_called()
    assert values_ns.update.call_args.kwargs["range"] == "Jobs!A5:C5"
    assert values_ns.update.call_args.kwargs["body"]["values"] == [["job-mine", "https://example/mine", ""]]
    service.spreadsheets.return_value.batchUpdate.assert_not_called()


def test_append_row_grows_a_full_grid_first():
    service = _grid_service(row_count=4)
    sheet.append_row(service, "Jobs", ["job_id"], 6, {"job_id": "job-new"})
    request = service.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]["requests"][0]
    assert request["appendDimension"] == {"sheetId": 1, "dimension": "ROWS", "length": 2}


def test_text_past_the_cell_limit_goes_to_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sheet, "ROOT", tmp_path)
    text = "x" * (sheet.CELL_LIMIT + 10)
    stored = sheet.cell(text, "Jobs")
    assert len(stored) <= sheet.CELL_LIMIT and stored.endswith(".txt]")
    path = stored.rsplit("full text: ", 1)[1][:-1]
    assert open(path).read() == text and sheet.cell("short", "Jobs") == "short"


def test_quota_errors_are_retried(monkeypatch):
    from googleapiclient.errors import HttpError
    calls = []

    def read():
        calls.append(1)
        if len(calls) == 1:
            raise HttpError(type("Response", (), {"status": 429, "reason": "quota"})(), b"quota")
        return []

    monkeypatch.setattr(sheet.time, "sleep", lambda _: None)
    assert sheet.call(read) == [] and len(calls) == 2
