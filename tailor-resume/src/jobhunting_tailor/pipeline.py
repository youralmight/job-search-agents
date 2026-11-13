"""Tailor one résumé from the job's saved JD: a writer tab edits a copy of the master CV against the JD and career, a checker tab
reviews every new claim against career, and code enforces the few hard rules."""
from __future__ import annotations

import difflib
import hashlib
import os
import shutil
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from . import model as model_calls
from .checks import check_immutable, check_strong_evidence, page_count, strong_terms
from .letter import compile_letter, required_by_jd
from .model import MODEL, model_json

CAREER = Path(os.environ["JOBHUNTING_CAREER_DIR"])
MASTER = CAREER / "showcase/cv/main.tex"
# Per-application site link, read back from the nginx log (career showcase/site/visitor-tracking.md).
WEBSITE = os.environ["JOBHUNTING_WEBSITE"]
APPLICANT_NAME = os.environ["JOBHUNTING_APPLICANT_NAME"]
WRITER_ANSWER = {"type": "object", "properties": {"done": {"type": "boolean"}, "reason": {"type": "string"}}, "required": ["done"]}
CHECKER_ANSWER = {"type": "object", "properties": {"problems": {"type": "array", "items": {"type": "string"}}}, "required": ["problems"]}
COMPILE = "docker run --rm -v {directory}:/work -w /work texlive/texlive:latest xelatex -interaction=nonstopmode -halt-on-error resume.tex"


def job_key(url: str) -> str:
    parsed = urlparse(url)
    patterns = [("linkedin", r"/view/(?:[^/?]*-)?(\d+)(?:/|\?|$)"),
                ("greenhouse", r"/(?:jobs|job-boards)/([^/?]+)/jobs/(\d+)"),
                ("lever", r"/([^/?]+)/([a-f\d-]{20,})")]
    for source, pattern in patterns:
        match = re.search(pattern, parsed.path)
        if match:
            return source + "-" + "-".join(match.groups())
    return "url-" + hashlib.sha256(url.encode()).hexdigest()[:16]


def tracked_website(job: dict) -> str:
    # The Sheet job_id; a standalone run without one is tagged with the company.
    if not WEBSITE:
        raise RuntimeError("JOBHUNTING_WEBSITE is not set")
    return f"{WEBSITE}/?r=" + re.sub(r"[^a-z0-9]+", "-", (job.get("job_id") or job["company"]).lower()).strip("-")


def compile_resume(directory: Path) -> None:
    for attempt in range(2):
        result = subprocess.run(COMPILE.format(directory=directory.resolve()).split(), capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            break
        if attempt == 1:
            raise RuntimeError(f"LaTeX compilation failed: {(result.stdout + result.stderr)[-1500:]}")


def writer_prompt(job: dict, directory: Path, letter: bool, problems: list[str]) -> str:
    fix = ("\n## Problems from the previous round\n\nThis résumé was already edited once; the check found the problems below. Fix each one and leave the rest unchanged:\n"
           + "\n".join(f"- {item}" for item in problems)) if problems else ""
    cover = (f"\n## Cover letter\n\nThe JD asks for a cover letter. Write a 250–350 word English cover letter and save it as {directory}/cover-letter.md, plain text, "
             f"starting with `Dear Hiring Team,` and ending with `Sincerely,\\n{APPLICANT_NAME}`; every fact needs support in career.\n") if letter else ""
    terms = strong_terms()
    strong = (f"\n- These strong experiences must stay in Experience, with their meaning unchanged (same metric definitions and scale): {', '.join(terms)}.") if terms else ""
    return f"""# Tailor a résumé for one job

Job: {job['company']} — {job['title']}
JD: {directory}/jd.md
Résumé to edit: {directory}/resume.tex (a copy of the master {MASTER})
Fact source: personal-info.md and every file under experiences/ in {CAREER}. Read the JD and these records before writing, so you know what the applicant has done.

## Goal

Make the recruiter see, at a glance, the experience and technology most relevant to the JD. You may change the headline, Technical Skills and the wording and order of
experience bullets; delete content only when it does not fit on one page.

Experience bullets matter most to recruiters; changing only the headline and the skills row is not enough. First list the JD's three or four main responsibilities, then find the closest work you did in career, then:
- Rewrite the matching experience bullets to sit closer to the JD: describe the same work in the JD's terms and stress the aspect it cares about most (for example detection, scale, launch, evaluation).
- Within one experience, move the most relevant bullets first.
- If career has something relevant to the JD that the master does not mention, it may replace a less relevant bullet.
Usually 2–4 experience bullets change. If there is truly no relevant experience, say why in the change list.

## Rules

- Every claim needs support in career or the master; do not inflate personal contribution, do not change how a metric is measured, use only the existing numbers.
- Companies, titles, dates and the whole Education block must not change; no summary; no hidden, white or tiny text.{strong}
- Technical Skills may add technologies from the JD, provided the career records show the applicant has used them; do not add what career says the applicant cannot do (such as front-end).
- The headline (the `{{\\large\\bfseries ...}}` line) follows the JD:
  - When the JD targets new graduates or caps experience (new grad, early career, 0–2 years and the like), start with `<degree> '27`; otherwise start with the applicant's years of work experience.
  - State the degree and the employer's work separately; nobody should think the work was done at the university.
  - No hedges such as "approximately" and no labels such as "Candidate"; do not word the target role as if already held.
  - Reference wording (no need to copy): `<degree> '27 --- LLM fine-tuning \\& production ML at <employer>`,
    `ML engineering --- <N> years operating production model services`, `Software engineering (ML systems) --- <N> years shipping production services`.
- Do not change any file under {CAREER}; change only files in {directory}.

## Compile

When done editing, run: `{COMPILE.format(directory=directory)}`, then use `pdfinfo {directory}/resume.pdf` to confirm Pages is 1.
If compilation fails or the résumé exceeds one page, keep editing until it compiles to exactly one page.
{cover}
## Change list

Write every change into {directory}/changes.md: what changed, and which sentence of which career file supports it.
{fix}
## Finish

When everything is done, reply with JSON: {{"done": true}}. If you cannot, reply {{"done": false, "reason": "why"}}."""


def checker_prompt(directory: Path, letter: bool) -> str:
    cover = f"There is also a cover letter {directory}/cover-letter.md; check every fact in it too." if letter else ""
    return f"""# Check a tailored résumé for exaggeration or invention

Master: {directory}/master.tex (the original the applicant approved; every claim in it counts as supported)
Tailored version: {directory}/resume.tex
The writer's change list: {directory}/changes.md
{cover}
Fact source: personal-info.md and every file under experiences/ in {CAREER}.

Look only at claims added or reworded in the tailored version. For each claim, first look for support in career, read the relevant records in full, then judge.
- Support may come from any experience; it need not all come from the same one. For example, fine-tuning or production services in the headline can be supported by the applicant's employer work records.
- The career direction at the start of the headline names the role the applicant is targeting, not a claim to have held that title.
- Only these count as problems: no support in career or the master; inflated personal contribution or scale; numbers that disagree with the source; making a piece of work look as if it happened elsewhere (for example presenting employer work as done at the university).
- The strong experiences must keep their meaning after rewriting: each metric must keep the definition, scale and scope that career gives it, and internal tools must not turn into generative or external products.
- Whether the wording is good or the trade-offs optimal is not a problem.

Do not change any file. Reply with JSON only: {{"problems": ["what is wrong: the tailored sentence; what career actually says"]}}, or {{"problems": []}} if there are none."""


def hard_check(directory: Path, master: str, website: str) -> list[str]:
    tailored = (directory / "resume.tex").read_text()
    try:
        compile_resume(directory)
    except RuntimeError as error:
        return [str(error)]
    problems = check_immutable(master, tailored) + check_strong_evidence(tailored)
    if "{" + website + "}" not in tailored:
        problems.append(f"Website link must be {website}")
    if page_count(directory / "resume.pdf") != 1:
        problems.append("The résumé exceeds one page")
    return problems


def career_state() -> str:
    return subprocess.run(["git", "-C", str(CAREER), "status", "--porcelain"], capture_output=True, text=True, check=True).stdout


def start(job: dict, output_root: Path) -> tuple[Path, str, str]:
    """A fresh job directory holding the master and its copy with the job's tracked website link."""
    if not all(job.get(field) for field in ("url", "company", "title")):
        raise ValueError("The job input needs a non-empty url, company and title")
    directory = output_root / job_key(job["url"])
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    master = MASTER.read_text()
    (directory / "master.tex").write_text(master)
    website = tracked_website(job)
    (directory / "resume.tex").write_text(master.replace("{" + WEBSITE + "}", "{" + website + "}"))
    return directory, master, website


def master_only(job: dict, output_root: Path) -> Path:
    """The master CV with only the tracked website link changed, for a job whose tailoring failed."""
    directory, master, website = start(job, output_root)
    problems = hard_check(directory, master, website)
    if problems:
        raise RuntimeError("The master with the tracked link failed the checks: " + "; ".join(problems))
    (directory / "changes.md").write_text(f"# Review sheet\n\nTailoring failed; this version is the master with only the Website link changed to {website}.\n")
    return directory


def run(job: dict, output_root: Path) -> Path:
    directory, master, website = start(job, output_root)
    model_calls.JOB_LABEL = f"{job['company']} — {job['title']}"
    # The JD Discover saved when it recommended the job; older rows without one fall back to its summary.
    jd = (job.get("jd") or "").strip()
    warning = "" if jd else "⚠️ The Sheet has no JD text for this job; this version was tailored only from Discover's summary and requirements. Check it against the original job page."
    body = jd or f"{job.get('summary', '')}\n\nRequirements: {job.get('requirements', '')}"
    (directory / "jd.md").write_text(f"# {job['company']} — {job['title']}\n\nSource: {job['url']}\n\n{body}\n")
    letter = bool(jd) and required_by_jd(jd)
    career_before = career_state()

    problems: list[str] = []
    for attempt in range(2):
        answer = model_json(MODEL, writer_prompt(job, directory, letter, problems), timeout=1800,
                            step="rewrite résumé" if attempt == 0 else "rework after check", schema=WRITER_ANSWER)
        if answer.get("done") is not True:
            raise RuntimeError(f"The writer tab did not finish: {answer.get('reason') or answer}")
        problems = hard_check(directory, master, website)
        if not (directory / "changes.md").is_file():
            problems.append("changes.md (the change list) was not written")
        if letter and not (directory / "cover-letter.md").is_file():
            problems.append("The JD requires a cover letter but cover-letter.md was not written")
        if not problems:
            problems = model_json(MODEL, checker_prompt(directory, letter), timeout=1800, step="check", schema=CHECKER_ANSWER)["problems"]
        if not problems:
            break
    if career_state() != career_before:
        raise RuntimeError("The career repository changed during tailoring; check it before running again")
    if problems:
        raise RuntimeError("The tailored résumé still failed the checks after two rounds: " + "; ".join(problems))
    if letter:
        compile_letter(directory)

    tailored = (directory / "resume.tex").read_text()
    diff = "\n".join(difflib.unified_diff(master.splitlines(), tailored.splitlines(), fromfile="master", tofile="tailored", lineterm=""))
    notes = (directory / "changes.md").read_text()
    (directory / "changes.md").write_text("# Review sheet\n\n" + (warning + "\n\n" if warning else "")
                                          + "## Changes and their support\n\n" + notes.strip() + "\n\n## Differences from the master\n\n```diff\n" + diff + "\n```\n")
    return directory
