---
name: tailor-resume
description: Generate a one-page résumé for a chosen job from its JD, and check that every new claim has support in career.
---

# Résumé tailoring

Run from `tailor-resume/`. Save the job's `url`, `company`, `title`, `jd`, `summary` and `requirements` as JSON, then run:

```bash
uv run jobhunting-tailor job.json --output-root <directory>
```

The user receives `resume.pdf` and `changes.md`, the review note that lists each change with its support and the diff against the master. `career/` is read-only.
