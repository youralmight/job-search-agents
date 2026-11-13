from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ENTRY = re.compile(r"\\entry\{([^{}]+)\}\{([^{}]+)\}\{([^{}]+)\}")
EDUCATION = re.compile(r"\\section\{Education\}(.*?)\\section\{", re.S)
FORBIDDEN = re.compile(r"\\(?:color\{white\}|textcolor\{white\}|fontsize\s*\{\s*[0-6](?:\.\d+)?\s*pt|tiny\b|scriptsize\b|phantom\b|vphantom\b|hphantom\b|pdfinfo\b)", re.I)


def check_immutable(master: str, tailored: str) -> list[str]:
    errors = []
    if ENTRY.findall(master) != ENTRY.findall(tailored):
        errors.append("A company, title, date or education entry was modified")
    if EDUCATION.search(master).group(1) != EDUCATION.search(tailored).group(1):
        errors.append("The Education block was modified")
    if FORBIDDEN.search(tailored):
        errors.append("Hidden, white or tiny text is present")
    return errors


def strong_terms() -> list[str]:
    """Markers of the applicant's strongest experiences (comma-separated in JOBHUNTING_STRONG_EVIDENCE) that must survive tailoring."""
    return [term.strip() for term in os.environ.get("JOBHUNTING_STRONG_EVIDENCE", "").split(",") if term.strip()]


def check_strong_evidence(tailored: str) -> list[str]:
    experience = tailored.split(r"\section{Experience}", 1)[1].split(r"\section{Education}", 1)[0] if r"\section{Experience}" in tailored else tailored
    return [f"A strong experience was removed: Experience must keep {term}" for term in strong_terms() if term not in experience]


def page_count(pdf: Path) -> int:
    result = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True, timeout=30)
    match = re.search(r"^Pages:\s+(\d+)$", result.stdout, re.M)
    if not match:
        raise RuntimeError("pdfinfo returned no page count")
    return int(match.group(1))
