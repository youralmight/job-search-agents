from __future__ import annotations

import re
import subprocess
from pathlib import Path



def required_by_jd(jd: str) -> bool:
    return bool(re.search(r"(?:cover letter|covering letter).{0,80}(?:required|mandatory|must submit)|(?:required|mandatory|must submit|please (?:attach|submit|include)).{0,80}(?:cover letter|covering letter)", jd, re.I | re.S))


def compile_letter(directory: Path) -> None:
    """Typeset the cover-letter.md the writer wrote as cover-letter.pdf."""
    body = (directory / "cover-letter.md").read_text()
    escaped = body.replace("\\", r"\textbackslash{}").replace("&", r"\&").replace("%", r"\%").replace("#", r"\#").replace("_", r"\_").replace("$", r"\$")
    tex = r"\documentclass[letterpaper,11pt]{article}\usepackage[margin=0.8in]{geometry}\usepackage{fontspec}\setmainfont{TeX Gyre Heros}\pagestyle{empty}\begin{document}" + "\n\n" + escaped.replace("\n\n", "\n\n\\par\n\n") + "\n\\end{document}\n"
    (directory / "cover-letter.tex").write_text(tex)
    result = subprocess.run(["docker", "run", "--rm", "-v", f"{directory}:/work", "-w", "/work", "texlive/texlive:latest", "xelatex", "-interaction=nonstopmode", "-halt-on-error", "cover-letter.tex"], capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise RuntimeError(f"Cover letter compilation failed: {result.stdout[-1500:]}")
