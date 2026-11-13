import json
import time

import pytest

from jobhunting_screen import cli, screening

TASK = {"name": "example", "kind": "board", "source": "Example", "start_url": "https://example.com/jobs"}


@pytest.fixture
def tabs(tmp_path, monkeypatch):
    """Fake Paseo: every started tab runs `tabs.agent(chunk)` at once; `tabs.status` is what waiting returns."""
    world = type("Tabs", (), {})()
    world.runs, world.status = [], "idle"
    world.agent = lambda chunk: decide(tmp_path, chunk, "exclude")

    def paseo(*args):
        assert args[0] == "run"
        world.runs.append(args)
        chunk = json.loads(open(args[-1].split("Read ", 2)[2].split(". It holds")[0]).read())
        world.agent(chunk)
        return json.dumps({"agentId": f"agent-{len(world.runs)}"})

    monkeypatch.setattr(screening, "paseo", paseo)
    monkeypatch.setattr(screening, "wait_for_agent", lambda agent, seconds: world.status)
    return world


def decide(run_dir, chunk, decision):
    path = run_dir / "result.model.json"
    model = json.loads(path.read_text())
    for posting in chunk:
        model["candidates"][posting["index"]].update(decision=decision, reason="read JD", evidence="JD text")
    path.write_text(json.dumps(model))


def collected(run_dir, count, description="Full JD text."):
    postings = [{"url": f"https://example.com/{n}", "company": "Acme", "title": f"Engineer {n}", "location": "Canada",
                 "posted_date": "", "query": "q", "description": description} for n in range(count)]
    (run_dir / "run.json").write_text(json.dumps({"search_id": "search-test", "task": TASK}))
    (run_dir / "listing.json").write_text(json.dumps({"status": "completed", "jobs": postings}))
    screening.save(run_dir / "result.model.json", {"search_id": "search-test", "candidates": [
        {"url": row["url"], "company": "Acme", "title": row["title"], "decision": "unread", "reason": "JD pending",
         "evidence": "collected; not judged yet"} for row in postings]})
    return postings


def test_jds_are_judged_in_batches_of_fifty_one_fresh_tab_each(tabs, tmp_path):
    postings = collected(tmp_path, 120)
    assert screening.judge(tmp_path, postings, "wks", "Example", time.monotonic() + 3600) == (3, False)
    titles = [args[args.index("--title") + 1] for args in tabs.runs]
    assert titles == ["Example — batch 1", "Example — batch 2", "Example — batch 3"]
    model = json.loads((tmp_path / "result.model.json").read_text())
    assert all(candidate["decision"] == "exclude" for candidate in model["candidates"])


def test_tabs_run_in_screen_job_with_their_run_directory(tabs, tmp_path):
    postings = collected(tmp_path, 1)
    screening.judge(tmp_path, postings, "wks", "Example", time.monotonic() + 3600, {"search_id": "search-test", "task": TASK})
    args = tabs.runs[0]
    assert args[args.index("--workspace") + 1] == "wks" and args[args.index("--cwd") + 1] == str(screening.ROOT)
    assert args[args.index("--provider") + 1] == screening.PROVIDER
    assert f"JOBHUNTING_SCREEN_RUN_DIR={tmp_path}" in args and "search=search-test" in args
    assert not any("SECRET" in str(arg) or "TOKEN" in str(arg) for arg in args)


def test_a_posting_without_a_jd_is_never_sent(tabs, tmp_path):
    postings = collected(tmp_path, 2, description="")
    assert screening.judge(tmp_path, postings, "wks", "Example", time.monotonic() + 3600) == (0, False)
    assert tabs.runs == []


def test_a_tab_that_judges_nothing_fails_the_screening(tabs, tmp_path):
    postings = collected(tmp_path, 3)
    tabs.agent = lambda chunk: None
    with pytest.raises(RuntimeError, match="judged none"):
        screening.judge(tmp_path, postings, "wks", "Example", time.monotonic() + 3600)


def test_a_tab_out_of_time_ends_the_screening(tabs, tmp_path):
    postings = collected(tmp_path, 120)
    tabs.status = "timeout"
    assert screening.judge(tmp_path, postings, "wks", "Example", time.monotonic() + 3600) == (1, True)


def test_new_postings_are_appended_and_existing_rows_are_not(tmp_path):
    chunk = tmp_path / "chunk-001.json"
    new = screening.build_prompt(tmp_path, chunk, 90, {"search_id": "search-example", "task": TASK})
    for text in ("screen-jobs/SKILL.md", "screening-rules.md", "append-job", "jd", str(chunk), "search-example",
                 "Do not open any browser", "history.json"):
        assert text in new
    again = screening.build_prompt(tmp_path, chunk, 90, None)
    assert "do not call append-job" in again and "search_id" not in again and "screening-rules.md" in again


def test_screen_command_judges_a_discover_task(tabs, tmp_path, capsys):
    collected(tmp_path, 60)
    assert cli.main(["screen", str(tmp_path), "--workspace", "wks", "--seconds", "3600"]) == 0
    assert json.loads(capsys.readouterr().out) == {"sessions": 2, "out_of_time": False}
    assert "search=search-test" in tabs.runs[0] and "Example — batch 1" in tabs.runs[0]
