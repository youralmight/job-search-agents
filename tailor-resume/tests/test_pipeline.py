import json

import pytest

from jobhunting_tailor import pipeline

MASTER = r"""{\large\bfseries AI / Machine Learning Engineer}\\[3pt]
\cvlink{https://example.com}{Website}
\section{Experience}
\entry{Acme}{Software Engineer}{Jun 2021 -- May 2023}
3M+ 40\% 60\% RAG internal search tool
\section{Education}
\entry{State University}{Master}{Sep 2023 -- Jun 2025}
\section{Skills}
Python"""
TRACKED = MASTER.replace("{https://example.com}", "{https://example.com/?r=job-3f9a1c2b7d4e}")
JOB = {"job_id": "job-3f9a1c2b7d4e", "url": "https://example.com/jobs/1", "company": "Acme Labs, Inc.", "title": "ML Engineer", "jd": "Machine Learning Engineer JD"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Fake JD, compiler, career and Paseo; `edits` are what successive writer tabs do, `verdicts` what checker tabs say."""
    state = type("World", (), {})()
    state.edits, state.verdicts, state.calls, state.career = [], [], [], ""
    master = tmp_path / "main.tex"
    master.write_text(MASTER)
    monkeypatch.setattr(pipeline, "MASTER", master)
    monkeypatch.setattr(pipeline, "compile_resume", lambda directory: (directory / "resume.pdf").write_text("pdf"))
    monkeypatch.setattr(pipeline, "page_count", lambda pdf: 2 if "TOO LONG" in (pdf.parent / "resume.tex").read_text() else 1)
    monkeypatch.setattr(pipeline, "career_state", lambda: state.career)
    monkeypatch.setattr(pipeline, "compile_letter", lambda directory: (directory / "cover-letter.pdf").write_text("pdf"))

    def model(name, prompt, timeout=900, step="", schema=None):
        state.calls.append(step)
        directory = tmp_path / "out" / pipeline.job_key(JOB["url"])
        if step == "check":
            return {"problems": state.verdicts.pop(0)}
        answer = state.edits.pop(0)(directory)
        return answer if isinstance(answer, dict) else {"done": True}
    monkeypatch.setattr(pipeline, "model_json", model)
    state.out = tmp_path / "out"
    return state


def retitle(directory):
    tex = directory / "resume.tex"
    tex.write_text(tex.read_text().replace("AI / Machine Learning Engineer", "ML engineering --- 2 years operating production model services"))
    (directory / "changes.md").write_text("- Headline changed to ML engineering; based on Acme records")


def test_written_and_checked_resume_is_returned_with_a_review(world):
    world.edits, world.verdicts = [retitle], [[]]
    directory = pipeline.run(JOB, world.out)
    assert world.calls == ["rewrite résumé", "check"]
    assert "2 years operating" in (directory / "resume.tex").read_text()
    review = (directory / "changes.md").read_text()
    assert "based on Acme records" in review and "-{\\large\\bfseries AI / Machine Learning Engineer}" in review
    assert (directory / "master.tex").read_text() == MASTER
    assert r"\cvlink{https://example.com/?r=job-3f9a1c2b7d4e}{Website}" in (directory / "resume.tex").read_text()


def test_checker_problems_get_one_rewrite(world):
    world.edits, world.verdicts = [retitle, retitle], [["headline exaggerated"], []]
    pipeline.run(JOB, world.out)
    assert world.calls == ["rewrite résumé", "check", "rework after check", "check"]


def test_still_failing_after_the_rewrite_raises(world):
    world.edits, world.verdicts = [retitle, retitle], [["headline exaggerated"], ["still exaggerated"]]
    with pytest.raises(RuntimeError, match="still exaggerated"):
        pipeline.run(JOB, world.out)


@pytest.mark.parametrize("edit,problem", [
    (lambda d: (d / "resume.tex").write_text(TRACKED.replace("Jun 2021", "Jun 2020")), "dates"),
    (lambda d: (d / "resume.tex").write_text(TRACKED.replace("60\\%", "")), "strong evidence"),
    (lambda d: (d / "resume.tex").write_text(TRACKED + "TOO LONG"), "one page"),
    (lambda d: (d / "resume.tex").write_text(MASTER), "tracked link"),
])
def test_hard_rules_are_sent_back_before_any_check(world, edit, problem):
    def repair(directory):
        (directory / "resume.tex").write_text(TRACKED)
        retitle(directory)
    world.edits, world.verdicts = [edit, repair], [[]]
    pipeline.run(JOB, world.out)
    assert world.calls == ["rewrite résumé", "rework after check", "check"]


def test_changing_career_fails_the_run(world):
    def touch_career(directory):
        world.career = " M experiences/x.md"
        retitle(directory)
    world.edits, world.verdicts = [touch_career], [[]]
    with pytest.raises(RuntimeError, match="career"):
        pipeline.run(JOB, world.out)


def test_required_cover_letter_is_written_and_typeset(world):

    def with_letter(directory):
        retitle(directory)
        (directory / "cover-letter.md").write_text("Dear Hiring Team,\n\nSincerely,\nJane Doe")
    world.edits, world.verdicts = [with_letter], [[]]
    job = {**JOB, "jd": "A cover letter is required for this position"}
    assert (pipeline.run(job, world.out) / "cover-letter.pdf").is_file()


def test_input_needs_company_and_title(tmp_path):
    with pytest.raises(ValueError, match="title"):
        pipeline.run({"url": "https://example.com/1", "company": "Acme"}, tmp_path)


def test_prompts_point_at_the_files_and_the_rules(tmp_path):
    writer = pipeline.writer_prompt(JOB, tmp_path, True, ["headline exaggerated"])
    for text in (str(tmp_path / "jd.md"), str(pipeline.CAREER), "<degree> '27", "3M+", "Jane Doe", "cover-letter.md", "headline exaggerated", "Pages"):
        assert text in writer
    checker = pipeline.checker_prompt(tmp_path, False)
    assert "master.tex" in checker and "Acme" not in checker and json.loads('{"problems": []}') == {"problems": []}


def test_a_writer_that_gives_up_fails_the_run(world):
    world.edits = [lambda directory: {"done": False, "reason": "cannot read career"}]
    with pytest.raises(RuntimeError, match="cannot read career"):
        pipeline.run(JOB, world.out)


def test_missing_change_list_is_sent_back(world):
    def silent(directory):
        tex = directory / "resume.tex"
        tex.write_text(tex.read_text().replace("Python", "Python, Go"))
    world.edits, world.verdicts = [silent, retitle], [[]]
    pipeline.run(JOB, world.out)
    assert world.calls == ["rewrite résumé", "rework after check", "check"]


def test_missing_paseo_is_reported_as_model_unavailable(monkeypatch):
    from jobhunting_tailor import model
    def missing(*args, **kwargs):
        raise FileNotFoundError("paseo")
    monkeypatch.setattr(model.subprocess, "run", missing)
    monkeypatch.setenv(model.WORKSPACE_ENV, "wks_test")
    with pytest.raises(RuntimeError, match=model.UNAVAILABLE):
        model.model_json(model.MODEL, "prompt")


def test_the_saved_jd_is_the_only_jd(world):
    world.edits, world.verdicts = [retitle], [[]]
    directory = pipeline.run(JOB, world.out)
    assert "Machine Learning Engineer JD" in (directory / "jd.md").read_text()
    assert "has no JD text for this job" not in (directory / "changes.md").read_text()


def test_a_row_without_jd_falls_back_to_the_summary_with_a_warning(world):
    world.edits, world.verdicts = [retitle], [[]]
    directory = pipeline.run({**JOB, "jd": "", "summary": "Builds ML services", "requirements": "Python"}, world.out)
    assert "Builds ML services" in (directory / "jd.md").read_text()
    assert "has no JD text for this job" in (directory / "changes.md").read_text()


def test_a_standalone_run_without_job_id_tags_the_company():
    assert pipeline.tracked_website({"company": "Acme Labs, Inc."}) == "https://example.com/?r=acme-labs-inc"


def test_master_only_changes_just_the_link_and_calls_no_model(world):
    directory = pipeline.master_only(JOB, world.out)
    assert world.calls == []
    assert (directory / "resume.tex").read_text() == TRACKED
    assert "job-3f9a1c2b7d4e" in (directory / "changes.md").read_text()
