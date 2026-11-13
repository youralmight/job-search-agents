import io
import json

import pytest

from jobhunting_core import sheet
from jobhunting_screen import cli
from test_jobs import _job_payload, sheet_service as sheet_service


@pytest.fixture
def cli_service(monkeypatch, sheet_service):
    monkeypatch.setattr(cli, "build_service", lambda: sheet_service)
    monkeypatch.delenv("JOBHUNTING_SCREEN_RUN_DIR", raising=False)
    return sheet_service


def appended(cli_service, result):
    return next(row for row in sheet.read_rows(cli_service, "Jobs")[1] if row["job_id"] == result["job_id"])


@pytest.fixture
def append_context(tmp_path, monkeypatch):
    url = "https://ca.indeed.com/rc/clk?jk=fellow&bb=" + "a%2Fb" * 1000 + "&vjs=3"
    (tmp_path / "run.json").write_text(json.dumps({"search_id": "search-current"}))
    (tmp_path / "result.model.json").write_text(json.dumps({
        "search_id": "search-current", "candidates": [{"url": url, "decision": "unread"}],
    }))
    monkeypatch.setenv("JOBHUNTING_SCREEN_RUN_DIR", str(tmp_path))
    return tmp_path, url


@pytest.mark.parametrize("change", ["truncated", "other-url", "wrong-search", "missing-search"])
def test_screening_append_rejects_unrecorded_url_or_wrong_run_before_sheet_write(
    cli_service, append_context, monkeypatch, capsys, change,
):
    path, url = append_context
    payload = _job_payload(url=url, search_id="search-current")
    if change == "truncated":
        payload["url"] = url[:-25]
    elif change == "other-url":
        payload["url"] = url + "&tracking=other"
    elif change == "wrong-search":
        payload["search_id"] = "search-other"
    else:
        payload.pop("search_id")
    before = json.loads(json.dumps(cli_service.cells))
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(payload)))

    assert cli.main(["append-job"]) == 1

    assert cli_service.cells == before
    output = capsys.readouterr()
    assert output.out == ""
    assert "run.json" in output.err and "result.model.json" in output.err
    assert "programmatically" in output.err


def test_screening_append_accepts_full_literal_inventoried_url_while_decision_is_unread(
    cli_service, append_context, monkeypatch, capsys,
):
    path, url = append_context
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(_job_payload(url=url, search_id="search-current"))))

    assert cli.main(["append-job"]) == 0

    actual = appended(cli_service, json.loads(capsys.readouterr().out))
    assert actual["url"] == url and actual["search_id"] == "search-current"


def test_append_job_command_accepts_discovery_notes(cli_service, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(_job_payload(discovery_notes="June 2027 technical graduate role"))))

    assert cli.main(["append-job"]) == 0

    assert appended(cli_service, json.loads(capsys.readouterr().out))["log"].endswith("June 2027 technical graduate role")


def test_invalid_payload_reports_what_to_fix(cli_service, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps({"url": "https://example/jobs/9"})))
    assert cli.main(["append-job"]) == 1
    assert "missing required job fields" in capsys.readouterr().err
