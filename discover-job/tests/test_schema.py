import pytest

from jobhunting_discover.schema import validate_search_input


def test_validate_search_input_requires_visible_fields():
    entry = validate_search_input({
        "source": "RBC Careers",
        "query": "Machine Learning",
        "prompt": "Use discover-jobs: RBC's own careers page.",
        "result_count": "24",
        "outcome": "no problem",
    })
    assert entry["result_count"] == "24"


def test_validate_search_input_rejects_missing_and_unknown_fields():
    with pytest.raises(ValueError, match="prompt"):
        validate_search_input({"source": "RBC", "query": "ML", "result_count": "0"})

    with pytest.raises(ValueError, match="unexpected"):
        validate_search_input({
            "source": "RBC",
            "query": "ML",
            "prompt": "search RBC",
            "result_count": "0",
            "channel": "extra",
        })
