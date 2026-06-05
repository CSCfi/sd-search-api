import pytest

from search_api.api.bigpicture.services.beacon import build_opensearch_query, get_term


# ---------------------------------------------------------------------------
# build_opensearch_query — exact-match types (controlledValue / ontology)
# ---------------------------------------------------------------------------


def test_build_opensearch_query_controlled_value_single():
    """Single value for controlledValue produces a terms query."""
    term = get_term("sex")  # controlledValue
    result = build_opensearch_query(term, "Male")
    assert result == {
        "bool": {
            "should": [{"terms": {"blocks.sex": ["Male"]}}],
            "minimum_should_match": 1,
        }
    }


def test_build_opensearch_query_controlled_value_multi():
    """Multiple values for controlledValue are combined in a single terms query."""
    term = get_term("sex")
    result = build_opensearch_query(term, ["Male", "Female"])
    assert result == {
        "bool": {
            "should": [{"terms": {"blocks.sex": ["Male", "Female"]}}],
            "minimum_should_match": 1,
        }
    }


def test_build_opensearch_query_ontology_single():
    """Single concept ID for ontology produces a terms query."""
    term = get_term("animal_species")  # ontology
    result = build_opensearch_query(term, "410607006")
    assert result == {
        "bool": {
            "should": [{"terms": {"blocks.species": ["410607006"]}}],
            "minimum_should_match": 1,
        }
    }


def test_build_opensearch_query_ontology_multi():
    """Multiple concept IDs for ontology are combined in a single terms query."""
    term = get_term("animal_species")
    result = build_opensearch_query(term, ["123456789", "987654321"])
    assert result == {
        "bool": {
            "should": [{"terms": {"blocks.species": ["123456789", "987654321"]}}],
            "minimum_should_match": 1,
        }
    }


def test_build_opensearch_query_ontology_or_value_multi_field():
    """ontologyOrValue with multiple OpenSearch fields produces one terms query per field."""
    term = get_term(
        "fixation_type"
    )  # maps to blocks.fixation_type + blocks.fixation_type_text
    result = build_opensearch_query(term, ["123", "456"])
    assert result == {
        "bool": {
            "should": [
                {"terms": {"blocks.fixation_type": ["123", "456"]}},
                {"terms": {"blocks.fixation_type_text": ["123", "456"]}},
            ],
            "minimum_should_match": 1,
        }
    }


# ---------------------------------------------------------------------------
# build_opensearch_query — text type
# ---------------------------------------------------------------------------


def test_build_opensearch_query_text_single():
    """Single value for text produces a match query."""
    term = get_term("dataset_title")  # text
    result = build_opensearch_query(term, "cancer")
    assert result == {
        "bool": {
            "should": [{"match": {"dataset_title": "cancer"}}],
            "minimum_should_match": 1,
        }
    }


def test_build_opensearch_query_text_multi():
    """Multiple values for text produce one match query per value (OR semantics)."""
    term = get_term("dataset_title")
    result = build_opensearch_query(term, ["cancer", "tumor"])
    assert result == {
        "bool": {
            "should": [
                {"match": {"dataset_title": "cancer"}},
                {"match": {"dataset_title": "tumor"}},
            ],
            "minimum_should_match": 1,
        }
    }


# ---------------------------------------------------------------------------
# build_opensearch_query — iso8601Range type
# ---------------------------------------------------------------------------


def test_build_opensearch_query_iso8601range_single():
    """Single iso8601 range value produces a range query in days."""
    term = get_term("age_at_extraction")  # iso8601Range
    result = build_opensearch_query(term, "P10Y-P20Y")
    assert result == {
        "bool": {
            "should": [
                {"range": {"blocks.age_at_extraction": {"gte": 3650, "lte": 7300}}}
            ],
            "minimum_should_match": 1,
        }
    }


def test_build_opensearch_query_iso8601range_multi():
    """Multiple iso8601 range values produce one range query per value (OR semantics)."""
    term = get_term("age_at_extraction")
    result = build_opensearch_query(term, ["P10Y-P20Y", "P30Y-P40Y"])
    assert result == {
        "bool": {
            "should": [
                {"range": {"blocks.age_at_extraction": {"gte": 3650, "lte": 7300}}},
                {"range": {"blocks.age_at_extraction": {"gte": 10950, "lte": 14600}}},
            ],
            "minimum_should_match": 1,
        }
    }


# ---------------------------------------------------------------------------
# build_opensearch_query — unsupported type
# ---------------------------------------------------------------------------


def test_build_opensearch_query_unsupported_type():
    """An unsupported term type raises ValueError."""
    term = get_term("sex")
    term_bad = term.model_copy(update={"type": "unknown"})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Unsupported term type unknown"):
        build_opensearch_query(term_bad, "Male")
