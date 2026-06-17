import pytest

from search_api.api.beacon.models import BeaconQueryFilter
from search_api.exceptions import UserException
from search_api.api.beacon.services import (
    OpenSearchBeaconService,
    build_opensearch_query,
)
from search_api.api.bigpicture.models import BP_FILTERING_TERMS


def get_term(field_id: str):
    return next(t for t in BP_FILTERING_TERMS if t.id == field_id)


def get_query(*id_value_pairs: tuple[str, str]) -> dict:
    """Build and return the OpenSearch query clause for the given filter pairs."""
    filters = [BeaconQueryFilter(id=fid, value=val) for fid, val in id_value_pairs]
    return OpenSearchBeaconService._get_query(filters, BP_FILTERING_TERMS)


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
            "should": [{"terms": {"blocks.animal_species": ["410607006"]}}],
            "minimum_should_match": 1,
        }
    }


def test_build_opensearch_query_ontology_multi():
    """Multiple concept IDs for ontology are combined in a single terms query."""
    term = get_term("animal_species")
    result = build_opensearch_query(term, ["123456789", "987654321"])
    assert result == {
        "bool": {
            "should": [
                {"terms": {"blocks.animal_species": ["123456789", "987654321"]}}
            ],
            "minimum_should_match": 1,
        }
    }


def test_build_opensearch_query_ontology_or_value_concept_ids_only():
    """ontologyOrValue with concept IDs only."""
    term = get_term(
        "fixation_type"
    )  # maps to blocks.fixation_type + blocks.fixation_type_text
    result = build_opensearch_query(term, ["123", "456"])
    assert result == {
        "bool": {
            "should": [{"terms": {"blocks.fixation_type": ["123", "456"]}}],
            "minimum_should_match": 1,
        }
    }


def test_build_opensearch_query_ontology_or_value_mixed_values():
    """ontologyOrValue with mixed concept IDs and other values."""
    term = get_term(
        "fixation_type"
    )  # maps to blocks.fixation_type + blocks.fixation_type_text
    result = build_opensearch_query(term, ["123", "Formalin"])
    assert result == {
        "bool": {
            "should": [
                {"terms": {"blocks.fixation_type": ["123"]}},
                {"terms": {"blocks.fixation_type_text": ["Formalin"]}},
            ],
            "minimum_should_match": 1,
        }
    }


def test_build_opensearch_query_ontology_or_value_free_text_only():
    """ontologyOrValue with non-concept IDs only."""
    term = get_term(
        "fixation_type"
    )  # maps to blocks.fixation_type + blocks.fixation_type_text
    result = build_opensearch_query(term, ["Formalin", "Glutaraldehyde"])
    assert result == {
        "bool": {
            "should": [
                {"terms": {"blocks.fixation_type_text": ["Formalin", "Glutaraldehyde"]}}
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
    term = get_term("sex")
    term_bad = term.model_copy(update={"type": "unknown"})  # type: ignore[arg-type]
    with pytest.raises(UserException, match="Unsupported term type unknown"):
        build_opensearch_query(term_bad, "Male")


# ---------------------------------------------------------------------------
# get_query — nested path routing
# ---------------------------------------------------------------------------


def test_get_query_no_filters():
    assert get_query() == {"bool": {"must": [{"match_all": {}}]}}


def test_get_query_top_level_filter():
    assert get_query(("dataset_title", "cancer")) == {
        "bool": {
            "must": [
                {
                    "bool": {
                        "should": [{"match": {"dataset_title": "cancer"}}],
                        "minimum_should_match": 1,
                    }
                },
            ]
        }
    }


def test_get_query_nested_block_filter():
    assert get_query(("sex", "Female")) == {
        "bool": {
            "must": [
                {
                    "nested": {
                        "path": "blocks",
                        "query": {
                            "bool": {
                                "filter": [
                                    {
                                        "bool": {
                                            "should": [
                                                {"terms": {"blocks.sex": ["Female"]}}
                                            ],
                                            "minimum_should_match": 1,
                                        }
                                    }
                                ]
                            }
                        },
                    }
                }
            ]
        }
    }


def test_get_query_nested_stain_filter():
    assert get_query(("staining_target", "Ki-67")) == {
        "bool": {
            "must": [
                {
                    "nested": {
                        "path": "stains",
                        "query": {
                            "bool": {
                                "filter": [
                                    {
                                        "bool": {
                                            "should": [
                                                {
                                                    "match": {
                                                        "stains.staining_target": "Ki-67"
                                                    }
                                                }
                                            ],
                                            "minimum_should_match": 1,
                                        }
                                    }
                                ]
                            }
                        },
                    }
                }
            ]
        }
    }


def test_get_query_multiple_nested_blocks_filter():
    assert get_query(("sex", "Female"), ("animal_species", "337915000")) == {
        "bool": {
            "must": [
                {
                    "nested": {
                        "path": "blocks",
                        "query": {
                            "bool": {
                                "filter": [
                                    {
                                        "bool": {
                                            "should": [
                                                {"terms": {"blocks.sex": ["Female"]}}
                                            ],
                                            "minimum_should_match": 1,
                                        }
                                    },
                                    {
                                        "bool": {
                                            "should": [
                                                {
                                                    "terms": {
                                                        "blocks.animal_species": [
                                                            "337915000"
                                                        ]
                                                    }
                                                }
                                            ],
                                            "minimum_should_match": 1,
                                        }
                                    },
                                ]
                            }
                        },
                    }
                }
            ]
        }
    }


def test_get_query_all_filter_scopes():
    assert get_query(
        ("dataset_title", "cancer"),  # top-level
        ("sex", "Female"),  # blocks
        ("staining_target", "Ki-67"),  # stains
    ) == {
        "bool": {
            "must": [
                {
                    "bool": {
                        "should": [{"match": {"dataset_title": "cancer"}}],
                        "minimum_should_match": 1,
                    }
                },
                {
                    "nested": {
                        "path": "blocks",
                        "query": {
                            "bool": {
                                "filter": [
                                    {
                                        "bool": {
                                            "should": [
                                                {"terms": {"blocks.sex": ["Female"]}}
                                            ],
                                            "minimum_should_match": 1,
                                        }
                                    }
                                ]
                            }
                        },
                    }
                },
                {
                    "nested": {
                        "path": "stains",
                        "query": {
                            "bool": {
                                "filter": [
                                    {
                                        "bool": {
                                            "should": [
                                                {
                                                    "match": {
                                                        "stains.staining_target": "Ki-67"
                                                    }
                                                }
                                            ],
                                            "minimum_should_match": 1,
                                        }
                                    }
                                ]
                            }
                        },
                    }
                },
            ]
        }
    }
