import copy
import json

import pytest

from jobhunting_discover.results import render_batch_report, search_entry, verify_result
from jobhunting_discover.schema import SEARCH_INPUT_FIELDS, validate_search_input


@pytest.fixture
def metadata():
    return {"search_id": "search-1", "task": {"kind": "board", "source": "Example"}}


@pytest.fixture
def model():
    return {
        "search_id": "search-1", "status": "completed", "summary": "Reviewed new-grad roles up to the end of results.",
        "coverage": [{"query": "graduate 2027", "url": "https://example.com/jobs", "scope": "both pages", "stop_reason": "end of results"}],
        "candidates": [], "remaining": [], "materials": [],
    }


def candidate(url, decision="recommend"):
    return {"url": url, "company": "Example", "title": "Graduate Engineer", "decision": decision,
            "reason": "Eligible for 2027 graduates; Canadian work location.", "evidence": "Accepts 2027 graduates; Python engineering."}


def job(url, search_id="search-1"):
    return {"url": url, "search_id": search_id, "job_id": "job-" + url.rsplit("/", 1)[-1],
            "source": "Example", "company": "Example", "title": "Graduate Engineer", "location": "Canada",
            "work_arrangement": "Remote", "summary": "Python engineering", "requirements": "2027 graduate", "jd": "Full JD text.",
            "log": "Eligible for 2027 graduates; Canadian work location."}


def test_more_than_five_candidates_succeeds_without_mutating_inputs(model, metadata):
    urls = [f"https://example.com/jobs/{index}" for index in range(8)]
    model["candidates"] = [candidate(url) for url in urls]
    jobs = [job(url) for url in urls]
    before = copy.deepcopy((model, metadata, jobs))
    result = verify_result(model, metadata, jobs)
    assert result["counts"] == {"inserted": 8, "excluded": 0, "exact_duplicates": 0, "unread": 0}
    assert result["new_jobs"] == jobs
    assert result["source"] == "Example"
    assert set(result) == set(model) | {"source", "counts", "new_jobs"}
    assert (model, metadata, jobs) == before
    result["new_jobs"][0]["title"] = "changed"
    result["coverage"][0]["scope"] = "changed"
    assert (model, metadata, jobs) == before


@pytest.mark.parametrize("unfinished", ["remaining", "unread"])
def test_completed_cannot_hide_unread_work(model, metadata, unfinished):
    if unfinished == "remaining":
        model["remaining"] = ["second page"]
    else:
        model["candidates"] = [candidate("https://example.com/jobs/1", "unread")]
    with pytest.raises(ValueError, match=unfinished):
        verify_result(model, metadata, [])


def test_recommendation_must_exist_in_actual_jobs(model, metadata):
    model["candidates"] = [candidate("https://example.com/jobs/1")]
    with pytest.raises(ValueError, match="actual Jobs"):
        verify_result(model, metadata, [])


def test_exact_urls_determine_duplicates_and_new_rows(model, metadata):
    old_url = "https://example.com/jobs/1"
    new_url = old_url + "?source=board"
    old = job(old_url, "older-search")
    old.pop("log")
    unrelated = job("https://other.com/jobs/1", "search-10")
    model["candidates"] = [candidate(old_url), candidate(new_url), candidate("https://example.com/jobs/2", "exclude")]
    result = verify_result(model, metadata, [old, job(new_url), unrelated])
    assert result["counts"] == {"inserted": 1, "excluded": 1, "exact_duplicates": 1, "unread": 0}
    assert result["new_jobs"] == [job(new_url)]


def test_source_id_variant_counts_as_existing_job(model, metadata):
    old_url = "https://ca.indeed.com/viewjob?jk=abc&from=old"
    new_url = "https://ca.indeed.com/rc/clk?jk=abc&from=new"
    model["candidates"] = [candidate(new_url)]
    result = verify_result(model, metadata, [job(old_url, "older-search")])
    assert result["counts"]["exact_duplicates"] == 1
    assert result["counts"]["inserted"] == 0


def test_zero_result_completion_needs_coverage(model, metadata):
    assert verify_result(model, metadata, [job("https://example.com/old", "old")])["counts"]["inserted"] == 0
    model["coverage"] = []
    with pytest.raises(ValueError, match="coverage"):
        verify_result(model, metadata, [])


@pytest.mark.parametrize("status,label", [("blocked", "Blocked"), ("partial", "Partial"), ("failed", "Failed")])
def test_incomplete_status_and_reasons_are_preserved(model, metadata, status, label):
    model.update(status=status, summary="Identity verification blocked further reading", coverage=[], remaining=["Page two and details"])
    model["candidates"] = [candidate("https://example.com/jobs/1", "unread")]
    result = verify_result(model, metadata, [])
    report = render_batch_report([result])
    assert label in report
    assert "Completed" not in report
    assert "Page two and details" in report
    assert "Identity verification blocked further reading" in report
    assert "https://example.com/jobs/1" in report
    assert result["counts"]["unread"] == 1
    outcome = json.loads(search_entry(result, metadata, "actual instruction")["outcome"])
    assert outcome["status"] == status
    assert outcome["remaining"] == model["remaining"]


@pytest.mark.parametrize("materials", [[], [{"clue": "Example hiring program", "status": "unresolved", "reason": "Source page not read yet"}]])
def test_material_with_missing_or_unresolved_clues_stays_incomplete(model, metadata, materials):
    metadata["task"]["kind"] = "material"
    model["materials"] = materials
    with pytest.raises(ValueError, match="materials"):
        verify_result(model, metadata, [])
    model["status"] = "partial"
    result = verify_result(model, metadata, [])
    report = render_batch_report([result])
    assert "Partial" in report
    if materials:
        assert "Source page not read yet" in report
        assert "Example hiring program" in report


def test_resolved_material_can_complete(model, metadata):
    metadata["task"]["kind"] = "material"
    model["materials"] = [{"clue": "Example hiring program", "status": "completed", "reason": "Official postings checked"}]
    assert verify_result(model, metadata, [])["status"] == "completed"


@pytest.mark.parametrize("decision", [None, "exclude", "unread"])
def test_new_rows_must_be_accounted_for_as_recommendations(model, metadata, decision):
    if decision:
        model["candidates"] = [candidate("https://example.com/jobs/1", decision)]
        model["status"] = "partial"
    with pytest.raises(ValueError, match="unaccounted"):
        verify_result(model, metadata, [job("https://example.com/jobs/1")])


def test_duplicate_candidate_urls_are_rejected(model, metadata):
    model["candidates"] = [candidate("https://example.com/jobs/1")] * 2
    with pytest.raises(ValueError, match="duplicate candidate posting"):
        verify_result(model, metadata, [job("https://example.com/jobs/1")])


def test_tracking_variants_of_one_posting_are_one_candidate(model, metadata):
    model["candidates"] = [candidate("https://www.linkedin.com/jobs/view/1234567890/?trackingId=a"),
                           candidate("https://www.linkedin.com/jobs/view/1234567890/?trackingId=b")]
    with pytest.raises(ValueError, match="duplicate candidate posting"):
        verify_result(model, metadata, [])


def test_duplicate_actual_url_is_ambiguous(model, metadata):
    model["candidates"] = [candidate("https://example.com/jobs/1")]
    with pytest.raises(ValueError, match="multiple actual Jobs"):
        verify_result(model, metadata, [job("https://example.com/jobs/1"), job("https://example.com/jobs/1", "old")])


@pytest.mark.parametrize("field,value,error", [
    ("search_id", "wrong", "search_id"), ("status", "done", "status"),
    ("summary", " ", "summary"), ("coverage", {}, "coverage"),
    ("remaining", [3], "remaining"), ("materials", [{"clue": "x", "status": "maybe", "reason": "x"}], "materials"),
])
def test_strict_model_fields(model, metadata, field, value, error):
    model[field] = value
    with pytest.raises(ValueError, match=error):
        verify_result(model, metadata, [])


@pytest.mark.parametrize("field", ["summary", "coverage", "candidates", "remaining", "materials"])
def test_missing_model_fields_fail_loudly(model, metadata, field):
    model.pop(field)
    with pytest.raises(ValueError, match=field):
        verify_result(model, metadata, [])


def test_model_cannot_supply_invented_counts(model, metadata):
    model["inserted"] = 100
    with pytest.raises(ValueError, match="unexpected.*inserted"):
        verify_result(model, metadata, [])


@pytest.mark.parametrize("field", ["title", "reason", "evidence"])
def test_candidate_needs_reading_evidence_and_reason(model, metadata, field):
    item = candidate("https://example.com/jobs/1")
    item[field] = ""
    model["candidates"] = [item]
    with pytest.raises(ValueError, match=field):
        verify_result(model, metadata, [job(item["url"])])


def test_candidate_must_match_recorded_company_and_title(model, metadata):
    model["candidates"] = [candidate("https://example.com/jobs/1")]
    row = job("https://example.com/jobs/1")
    row["title"] = "Different opening"
    with pytest.raises(ValueError, match="title"):
        verify_result(model, metadata, [row])


def test_coverage_needs_scope_and_stop_reason(model, metadata):
    model["coverage"][0]["stop_reason"] = ""
    with pytest.raises(ValueError, match="stop_reason"):
        verify_result(model, metadata, [])


def test_search_history_and_report_use_same_verified_data(model, metadata):
    old_url, new_url = "https://example.com/jobs/old", "https://example.com/jobs/new"
    model["candidates"] = [candidate(old_url), candidate(new_url)]
    model["coverage"].append({"query": "Python Canada", "url": "https://example.com/jobs", "scope": "first page", "stop_reason": "Matched the earlier result boundary"})
    result = verify_result(model, metadata, [job(old_url, "old"), job(new_url)])
    entry = search_entry(result, metadata, "original instruction")
    assert set(entry) == SEARCH_INPUT_FIELDS
    assert validate_search_input(entry) == entry
    assert entry["query"] == "graduate 2027\nPython Canada"
    assert entry["result_count"] == "1"
    assert entry["prompt"] == "original instruction"
    outcome = json.loads(entry["outcome"])
    assert outcome == {key: result[key] for key in ("status", "summary", "counts", "coverage", "remaining", "materials")}
    report = render_batch_report([result])
    for text in ("Graduate Engineer", old_url, new_url, "Eligible for 2027 graduates", "Accepts 2027 graduates", "graduate 2027", "both pages", "end of results", "Example", "Completed", "New", "exact duplicate"):
        assert text in report
    assert "job-new" in report


def test_a_retried_search_record_is_written_once(monkeypatch):
    from contextlib import nullcontext
    from jobhunting_discover import results
    keys = ["search_id", "source", "query", "prompt", "time", "result_count", "outcome", "note"]
    stored = []
    monkeypatch.setattr(results.sheet, "workbook_lock", nullcontext)
    monkeypatch.setattr(results.sheet, "read_rows", lambda service, tab: (keys, [dict(row) for row in stored]))
    monkeypatch.setattr(results.sheet, "append_row",
                        lambda service, tab, key_row, number, row: stored.append({key: row.get(key, "") for key in key_row}))
    entry = {"search_id": "search-retry", "source": "RBC Careers", "query": "ML", "prompt": "p", "result_count": "2",
             "outcome": "{}"}
    first = results.log_search(None, entry)
    stored[0]["note"] = "human search note"
    # A dropped connection hid the first write; the retry finds it
    assert results.log_search(None, entry) == first == {"search_id": "search-retry", "time": stored[0]["time"]}
    assert len(stored) == 1 and stored[0]["time"]
