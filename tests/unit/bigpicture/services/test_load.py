from search_api.bigpicture.models import BigpictureCodeAttributeValue
from search_api.bigpicture.services.load import (
    _convert_blocks_for_opensearch,
    _convert_iso8601_range_for_opensearch,
)


def get_code(code: str) -> BigpictureCodeAttributeValue:
    return BigpictureCodeAttributeValue(code=code, meaning=code)


def test_convert_iso8601_range_for_opensearch_valid():
    assert _convert_iso8601_range_for_opensearch({"gte": "P40Y", "lte": "P50Y"}) == {
        "gte": 14600,
        "lte": 18250,
    }


def test_convert_iso8601_range_for_opensearch_invalid(caplog):
    import logging

    with caplog.at_level(logging.ERROR):
        result = _convert_iso8601_range_for_opensearch(
            {"gte": "NOT_VALID", "lte": "P1Y"}
        )

    assert result is None
    assert "Invalid ISO-8601 duration in age_at_extraction" in caplog.text


def test_convert_iso8601_range_for_opensearch_missing(caplog):
    import logging

    with caplog.at_level(logging.ERROR):
        result = _convert_iso8601_range_for_opensearch({"gte": "P40Y"})

    assert result is None
    assert "is missing" in caplog.text


def test_convert_blocks_for_opensearch(caplog):
    """Invalid duration drops age_at_extraction; valid duration is converted to days."""
    import logging

    blocks = [
        {
            "species": "337915000",
            "age_at_extraction": {"gte": "NOT_VALID", "lte": "P1Y"},
        },
        {"species": "447612001", "age_at_extraction": {"gte": "P40Y", "lte": "P50Y"}},
    ]

    with caplog.at_level(logging.ERROR):
        result = _convert_blocks_for_opensearch(blocks)

    assert result[0] == {"species": "337915000"}
    assert result[1]["age_at_extraction"] == {"gte": 14600, "lte": 18250}
    assert "Invalid ISO-8601 duration in age_at_extraction" in caplog.text
