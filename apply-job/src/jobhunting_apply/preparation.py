"""Tailor a chosen job's résumé before its form is filled."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from jobhunting_core import sheet, state

# The fixed master CV PDF, used when tailoring fails
MASTER = Path(os.environ['JOBHUNTING_MASTER_CV'])


class ModelUnavailable(RuntimeError):
    """The model service is down or out of quota: a temporary stop, not a failed tailoring."""


def tailor(job: dict[str, str], master: bool = False) -> Path:
    """The résumé PDF from `jobhunting-tailor`, tailored to the JD saved in the Jobs row; with `master`, the master
    CV whose website link carries this job's tracking ID."""
    with tempfile.TemporaryDirectory(prefix='apply-prepare-') as directory:
        source = Path(directory) / 'job.json'
        source.write_text(json.dumps({key: job.get(key, '') for key in ('job_id', 'url', 'company', 'title', 'summary', 'requirements', 'jd')},
                                     ensure_ascii=False))
        result = subprocess.run(
            ['uv', 'run', '--no-sync', 'jobhunting-tailor', str(source), '--output-root', os.environ['JOBHUNTING_TAILOR_OUTPUT'],
             *(['--master'] if master else [])],
            cwd=Path(__file__).resolve().parents[3] / 'tailor-resume',
            capture_output=True, text=True, timeout=3600, check=False,
        )
    if result.returncode:
        lines = (result.stderr or result.stdout).strip().splitlines()
        reason = lines[-1] if lines else f'Tailor exit code {result.returncode}'
        if 'model unavailable' in reason:
            raise ModelUnavailable(reason[-300:])
        raise RuntimeError(reason)
    if not result.stdout.strip():
        raise RuntimeError('Tailor returned no output path')
    output = Path(result.stdout.strip().splitlines()[-1])
    if not (output / 'resume.pdf').is_file() or not (output / 'changes.md').is_file():
        raise RuntimeError(f'missing résumé or review note: {output}')
    return output / 'resume.pdf'


def prepare(service, job: dict[str, str], generate=tailor) -> str:
    """Give a chosen job its résumé and move it to Ready to fill; returns the log note."""
    os.umask(0o077)
    resume = job['resume'].strip()
    # A path the user put there themselves is kept; empty, the master and earlier Tailor output are replaced
    tailor_root = Path(os.environ['JOBHUNTING_TAILOR_OUTPUT']).resolve()
    replaceable = (not resume or resume == str(MASTER) or
                   (Path(resume).name == 'resume.pdf' and Path(resume).resolve().is_relative_to(tailor_root)))
    extra = {}
    if not replaceable:
        note = "kept the user's resume path"
    else:
        try:
            pdf = generate(job)
            note = 'résumé tailored'
            extra['resume'] = str(pdf.resolve())
        except ModelUnavailable:
            raise  # the job keeps its state, so the next prepare tailors it
        except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
            # A failed tailoring still proceeds with the master résumé, tracked if it compiles; the log keeps the reason
            note = f'tailoring failed, using the master: {str(exc)[:300]}'
            try:
                extra['resume'] = str(generate(job, master=True).resolve())
            except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as again:
                note += f'; the tracked master also failed, using the fixed master: {str(again)[:200]}'
                extra['resume'] = str(MASTER)
    sheet.transition(service, job['job_id'], state.TO_FILL, note,
                     expect=(state.current(job),), expect_fields={key: job[key] for key in ('url', 'resume')}, extra=extra)
    return note
