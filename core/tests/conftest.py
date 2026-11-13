import pytest

from jobhunting_core import sheet


@pytest.fixture(autouse=True)
def isolated_row_hints(tmp_path, monkeypatch):
    # Full reads record job rows for every process; tests must never touch the real hint file.
    monkeypatch.setattr(sheet, '_row_hints_path', lambda: tmp_path / '.sheet-rows.json')
