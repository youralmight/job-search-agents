from pathlib import Path
from unittest.mock import patch
from jobhunting_tailor.checks import check_immutable, check_strong_evidence, page_count
from jobhunting_tailor.letter import required_by_jd
MASTER = r"""\entry{Acme}{Software Engineer}{Jun 2021 -- May 2023}
\section{Education}
\entry{State University}{Master}{Sep 2023 -- Jun 2025}
\section{Skills}"""


def test_immutable_entries_and_education():
    assert check_immutable(MASTER, MASTER) == []
    assert check_immutable(MASTER, MASTER.replace("Jun 2021", "Jun 2020"))
    assert check_immutable(MASTER, MASTER.replace("Software Engineer", "Lead Engineer"))
    assert check_immutable(MASTER, MASTER.replace("State University", "MIT"))
    assert check_immutable(MASTER, MASTER + r"\textcolor{white}{Kafka}")


def test_strong_evidence_must_survive(monkeypatch):
    monkeypatch.setenv("JOBHUNTING_STRONG_EVIDENCE", r"3M+, 40\%, 60\%, RAG")
    assert check_strong_evidence(r"3M+ 40\% 60\% internal RAG tool") == []
    assert check_strong_evidence(r"3M+ 40\% RAG tool") == [r"A strong experience was removed: Experience must keep 60\%"]


def test_page_count(tmp_path: Path):
    pdf = tmp_path / "resume.pdf"
    pdf.touch()
    with patch("subprocess.run") as run:
        run.return_value.stdout = "Pages:          1\n"
        assert page_count(pdf) == 1
        run.return_value.stdout = "Pages:          2\n"
        assert page_count(pdf) == 2


def test_cover_letter_only_when_required():
    assert required_by_jd("A cover letter is required for this position")
    assert required_by_jd("Please attach a cover letter")
    assert not required_by_jd("Cover letter optional")


