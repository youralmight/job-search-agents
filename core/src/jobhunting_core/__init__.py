"""Shared infrastructure of the job-hunting workflows. Importing it loads the repository's .env (or, without one,
.env.example) into the environment; a variable the caller already set wins."""
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_SETTINGS = _ROOT / ".env" if (_ROOT / ".env").exists() else _ROOT / ".env.example"
for _line in _SETTINGS.read_text().splitlines():
    if _line.strip() and not _line.lstrip().startswith("#"):
        _key, _value = _line.split("=", 1)
        os.environ.setdefault(_key.strip(), _value.strip())

# Relative paths are relative to the repository root, whichever workflow directory runs
for _key in ("JOBHUNTING_PRIVATE_DIR", "JOBHUNTING_TAILOR_OUTPUT", "JOBHUNTING_MASTER_CV", "JOBHUNTING_CAREER_DIR"):
    if os.environ.get(_key):
        os.environ[_key] = str((_ROOT / os.environ[_key]).resolve())
