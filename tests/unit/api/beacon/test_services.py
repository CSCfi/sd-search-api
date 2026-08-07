import pytest

from search_api.api.beacon.models import BeaconQueryFilter
from search_api.exceptions import SystemException, UserException
from search_api.api.beacon.services import (
    OpenSearchBeaconService,
    build_opensearch_query,
)
from search_api.api.bigpicture.models import (
    BP_FILTERING_QUALIFIERS,
    BP_FILTERING_TERMS,
)
from search_api.api.bigpicture.opensearch import BigpictureOpenSearchBeaconService
from search_api.api.opensearch.models import ONTOLOGY_OTHER_VALUE_FIELD_SUFFIX
from search_api.api.opensearch.services import fetch_indexed_keywords


def get_term(field_id: str):
    return next(t for t in BP_FILTERING_TERMS if t.id == field_id)


def get_query(*id_value_pairs: tuple[str, str], scope: str | None = None) -> dict:
    """Build and return the OpenSearch query clause for the given filter pairs."""
    filters = [BeaconQueryFilter(id=fid, value=val) for fid, val in id_value_pairs]
    return OpenSearchBeaconService._get_query(filters, BP_FILTERING_TERMS, scope)


# ---------------------------------------------------------------------------
# build_opensearch_query — exact-match types (controlledValue / ontology)
# ---------------------------------------------------------------------------


def test_build_opensearch_query_controlled_value_single():
    """Single value for controlledValue produces a terms query."""
    term = get_term("sex")  # controlledValue
    result = build_opensearch_query(term, "Male")
    assert result == {
        "bool": {
            "should": [{"terms": {"specimen.sex": ["Male"]}}],
            "minimum_should_match": 1,
        }
    }


def test_build_opensearch_query_controlled_value_multi():
    """Multiple values for controlledValue are combined in a single terms query."""
    term = get_term("sex")
    result = build_opensearch_query(term, ["Male", "Female"])
    assert result == {
        "bool": {
            "should": [{"terms": {"specimen.sex": ["Male", "Female"]}}],
            "minimum_should_match": 1,
        }
    }


def test_build_opensearch_query_keyword_single():
    """Single value for keyword produces an exact terms query (not a match query)."""
    term = get_term("staining_target")  # keyword
    result = build_opensearch_query(term, "Ki-67")
    assert result == {
        "bool": {
            "should": [{"terms": {"staining.staining_target": ["Ki-67"]}}],
            "minimum_should_match": 1,
        }
    }


def test_build_opensearch_query_ontology_single():
    """Single concept ID for ontology produces a terms query."""
    term = get_term("animal_species")  # ontology
    result = build_opensearch_query(term, "410607006")
    assert result == {
        "bool": {
            "should": [{"terms": {"specimen.animal_species": ["410607006"]}}],
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
                {"terms": {"specimen.animal_species": ["123456789", "987654321"]}}
            ],
            "minimum_should_match": 1,
        }
    }


def test_build_opensearch_query_ontology_or_value_concept_ids_only():
    """ontologyOrValue with concept IDs only."""
    term = get_term(
        "fixation_type"
    )  # maps to specimen.fixation_type + specimen.fixation_type_other
    result = build_opensearch_query(term, ["123", "456"])
    assert result == {
        "bool": {
            "should": [{"terms": {"specimen.fixation_type": ["123", "456"]}}],
            "minimum_should_match": 1,
        }
    }


def test_build_opensearch_query_ontology_or_value_mixed_values():
    """ontologyOrValue with mixed concept IDs and other values."""
    term = get_term(
        "fixation_type"
    )  # maps to specimen.fixation_type + specimen.fixation_type_other
    result = build_opensearch_query(term, ["123", "Formalin"])
    assert result == {
        "bool": {
            "should": [
                {"terms": {"specimen.fixation_type": ["123"]}},
                {
                    "terms": {
                        f"specimen.fixation_type{ONTOLOGY_OTHER_VALUE_FIELD_SUFFIX}": [
                            "Formalin"
                        ]
                    }
                },
            ],
            "minimum_should_match": 1,
        }
    }


def test_build_opensearch_query_ontology_or_value_free_text_only():
    """ontologyOrValue with non-concept IDs only."""
    term = get_term(
        "fixation_type"
    )  # maps to specimen.fixation_type + specimen.fixation_type_other
    result = build_opensearch_query(term, ["Formalin", "Glutaraldehyde"])
    assert result == {
        "bool": {
            "should": [
                {
                    "terms": {
                        f"specimen.fixation_type{ONTOLOGY_OTHER_VALUE_FIELD_SUFFIX}": [
                            "Formalin",
                            "Glutaraldehyde",
                        ]
                    }
                }
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
                {"range": {"specimen.age_at_extraction": {"gte": 3650, "lte": 7300}}}
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
                {"range": {"specimen.age_at_extraction": {"gte": 3650, "lte": 7300}}},
                {"range": {"specimen.age_at_extraction": {"gte": 10950, "lte": 14600}}},
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
                        "path": "specimen",
                        "query": {
                            "bool": {
                                "filter": [
                                    {
                                        "bool": {
                                            "should": [
                                                {"terms": {"specimen.sex": ["Female"]}}
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
                        "path": "staining",
                        "query": {
                            "bool": {
                                "filter": [
                                    {
                                        "bool": {
                                            "should": [
                                                {
                                                    "terms": {
                                                        "staining.staining_target": [
                                                            "Ki-67"
                                                        ]
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


def test_get_query_multiple_nested_specimen_filter():
    assert get_query(("sex", "Female"), ("animal_species", "337915000")) == {
        "bool": {
            "must": [
                {
                    "nested": {
                        "path": "specimen",
                        "query": {
                            "bool": {
                                "filter": [
                                    {
                                        "bool": {
                                            "should": [
                                                {"terms": {"specimen.sex": ["Female"]}}
                                            ],
                                            "minimum_should_match": 1,
                                        }
                                    },
                                    {
                                        "bool": {
                                            "should": [
                                                {
                                                    "terms": {
                                                        "specimen.animal_species": [
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


def test_get_query_top_level_and_nested_filters():
    assert get_query(
        ("dataset_title", "cancer"),  # top-level
        ("sex", "Female"),  # specimen
        ("staining_target", "Ki-67"),  # staining
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
                        "path": "specimen",
                        "query": {
                            "bool": {
                                "filter": [
                                    {
                                        "bool": {
                                            "should": [
                                                {"terms": {"specimen.sex": ["Female"]}}
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
                        "path": "staining",
                        "query": {
                            "bool": {
                                "filter": [
                                    {
                                        "bool": {
                                            "should": [
                                                {
                                                    "terms": {
                                                        "staining.staining_target": [
                                                            "Ki-67"
                                                        ]
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


def test_get_query_scope_none():
    """scope=None (the default) means no scope restriction."""
    assert (
        get_query(scope=None) == get_query() == {"bool": {"must": [{"match_all": {}}]}}
    )


def test_get_query_scope_clinical():
    assert get_query(scope="clinical") == {
        "bool": {"must": [{"term": {"scope": "clinical"}}]}
    }


def test_get_query_scope_non_clinical():
    assert get_query(scope="non_clinical") == {
        "bool": {"must": [{"term": {"scope": "non_clinical"}}]}
    }


def test_get_query_scope_clinical_with_filter():
    assert get_query(("dataset_title", "cancer"), scope="clinical") == {
        "bool": {
            "must": [
                {"term": {"scope": "clinical"}},
                {
                    "bool": {
                        "should": [{"match": {"dataset_title": "cancer"}}],
                        "minimum_should_match": 1,
                    }
                },
            ]
        }
    }


def _service() -> OpenSearchBeaconService:
    return BigpictureOpenSearchBeaconService(
        client=None,  # type: ignore[arg-type]
        index_name="test",
        filtering_terms=BP_FILTERING_TERMS,
        filtering_qualifiers=BP_FILTERING_QUALIFIERS,
    )


def test_qualifier_clauses_absent_qualifier_is_not_filtered():
    """An absent or empty qualifier contributes no clause, so it filters nothing."""
    service = _service()
    assert service._qualifier_clauses(None) == {}
    assert service._qualifier_clauses({}) == {}
    assert service._qualifier_clauses({"observation": []}) == {}


def test_qualifier_clauses_cover_every_group_the_qualifier_names():
    """One requested qualifier yields one clause per group it qualifies."""
    clauses = _service()._qualifier_clauses({"observation": ["confirmed"]})
    assert clauses == {
        "diagnosis": [{"terms": {"diagnosis.qualifiers": ["observation:confirmed"]}}],
        "finding": [{"terms": {"finding.qualifiers": ["observation:confirmed"]}}],
    }


def test_get_query_qualifier_joins_the_filter_inside_the_nested_query():
    service = _service()
    query = OpenSearchBeaconService._get_query(
        [BeaconQueryFilter(id="diagnosis", value="73211009")],
        BP_FILTERING_TERMS,
        None,
        service._qualifier_clauses({"observation": ["confirmed"]}),
    )
    nested = query["bool"]["must"][0]["nested"]
    assert nested["path"] == "diagnosis"
    assert nested["query"]["bool"]["filter"] == [
        {
            "bool": {
                "should": [{"terms": {"diagnosis.diagnosis": ["73211009"]}}],
                "minimum_should_match": 1,
            }
        },
        {"terms": {"diagnosis.qualifiers": ["observation:confirmed"]}},
    ]


def test_get_query_qualifier_alone_does_not_constrain_an_unfiltered_group():
    service = _service()
    query = OpenSearchBeaconService._get_query(
        [BeaconQueryFilter(id="dataset_title", value="cancer")],
        BP_FILTERING_TERMS,
        None,
        service._qualifier_clauses({"observation": ["candidate"]}),
    )
    assert not any("nested" in clause for clause in query["bool"]["must"])


# ---------------------------------------------------------------------------
# Field value counts — restricted by scope and by qualifier
# ---------------------------------------------------------------------------

# These check the request that gets built.


class _BodyCaptured(Exception):
    """Raised once the request body has been captured, to end the call."""

    def __init__(self, body: dict) -> None:
        self.body = body


class _CapturingSearchClient:
    """Captures the request body instead of answering it."""

    async def search(self, index: str, body: dict) -> dict:
        raise _BodyCaptured(body)


async def _counts_body(field_id: str, **restrictions) -> dict:
    """Return the request body that get_indexed_field_value_counts would send."""
    service = BigpictureOpenSearchBeaconService(
        client=_CapturingSearchClient(),  # type: ignore[arg-type]
        index_name="idx",
        filtering_terms=BP_FILTERING_TERMS,
        filtering_qualifiers=BP_FILTERING_QUALIFIERS,
    )
    try:
        await service.get_indexed_field_value_counts(field_id, **restrictions)
    except _BodyCaptured as captured:
        return captured.body
    raise AssertionError("no search request was made")


@pytest.mark.asyncio
async def test_field_value_counts_restrict_by_scope_and_qualifier():
    """Scope restricts the documents counted, a qualifier the group items in them."""
    body = await _counts_body(
        "diagnosis", scope="clinical", qualifiers={"observation": ["confirmed"]}
    )

    assert body["query"] == {"term": {"scope": "clinical"}}
    group_items = body["aggs"]["group_items"]
    assert group_items["nested"] == {"path": "diagnosis"}
    assert group_items["aggs"]["qualified_items"]["filter"] == {
        "bool": {
            "filter": [{"terms": {"diagnosis.qualifiers": ["observation:confirmed"]}}]
        }
    }


@pytest.mark.asyncio
async def test_field_value_counts_without_restrictions_count_everything():
    """Neither axis has a default, so omitting both counts every value."""
    body = await _counts_body("diagnosis")

    assert "query" not in body
    assert "qualified_items" not in body["aggs"]["group_items"]["aggs"]


@pytest.mark.asyncio
async def test_field_value_counts_scope_applies_to_a_top_level_field():
    body = await _counts_body("dataset_title", scope="non_clinical")

    assert body["query"] == {"term": {"scope": "non_clinical"}}


@pytest.mark.asyncio
async def test_field_value_counts_qualifier_ignored_for_an_unqualified_group():
    """specimen carries no qualifier values, so a qualifier must not zero its counts."""
    body = await _counts_body("sex", qualifiers={"observation": ["confirmed"]})

    assert "qualified_items" not in body["aggs"]["group_items"]["aggs"]


@pytest.mark.asyncio
async def test_field_value_counts_of_a_grouped_field_ask_for_document_counts():
    """A grouped field's buckets count items, so reverse_nested is added to climb back."""
    body = await _counts_body("diagnosis")

    field_values = body["aggs"]["group_items"]["aggs"]["field_values"]
    assert field_values["aggs"] == {"documents": {"reverse_nested": {}}}


@pytest.mark.asyncio
async def test_group_item_filter_is_rejected_for_a_field_without_a_group():
    """Dropping it silently would return counts wider than the caller asked for."""
    with pytest.raises(
        SystemException, match="Cannot filter the group items of 'dataset_title'"
    ):
        await fetch_indexed_keywords(
            _CapturingSearchClient(),  # type: ignore[arg-type]
            "idx",
            "dataset_title",
            group_item_filter={"bool": {"filter": []}},
        )
