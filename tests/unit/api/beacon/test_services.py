from unittest.mock import AsyncMock

import pytest
from opensearchpy import AsyncOpenSearch

from search_api.api.beacon.models import BeaconQueryFilter
from search_api.exceptions import SystemException, UserException
from search_api.api.beacon.services import (
    OpenSearchBeaconService,
    build_filtering_term_query,
)
from search_api.api.bigpicture.models import (
    BP_FILTERING_QUALIFIERS,
    BP_FILTERING_SCOPES,
    BP_FILTERING_TERMS,
)
from search_api.api.bigpicture.opensearch import BigpictureOpenSearchBeaconService
from search_api.api.models import ValueCountsKey
from search_api.api.opensearch.models import ONTOLOGY_OTHER_VALUE_FIELD_SUFFIX
from search_api.api.opensearch.services import fetch_indexed_keywords


def get_term(field_id: str):
    return next(t for t in BP_FILTERING_TERMS if t.id == field_id)


def get_query(*id_value_pairs: tuple[str, str], scope: str | None = None) -> dict:
    """Build and return the OpenSearch query clause for the given filter pairs."""
    filters = [BeaconQueryFilter(id=fid, value=val) for fid, val in id_value_pairs]
    return _service()._get_query(filters, scope)


# ---------------------------------------------------------------------------
# build_filtering_term_query — exact-match types (controlledValue / ontology)
# ---------------------------------------------------------------------------


def test_build_filtering_term_query_controlled_value_single():
    """Single value for controlledValue produces a terms query."""
    term = get_term("sex")  # controlledValue
    result = build_filtering_term_query(term, "Male")
    assert result == {
        "bool": {
            "should": [{"terms": {"specimen.sex": ["Male"]}}],
            "minimum_should_match": 1,
        }
    }


def test_build_filtering_term_query_controlled_value_multi():
    """Multiple values for controlledValue are combined in a single terms query."""
    term = get_term("sex")
    result = build_filtering_term_query(term, ["Male", "Female"])
    assert result == {
        "bool": {
            "should": [{"terms": {"specimen.sex": ["Male", "Female"]}}],
            "minimum_should_match": 1,
        }
    }


def test_build_filtering_term_query_keyword_single():
    """Single value for keyword produces an exact terms query (not a match query)."""
    term = get_term("staining_target")  # keyword
    result = build_filtering_term_query(term, "Ki-67")
    assert result == {
        "bool": {
            "should": [{"terms": {"staining.staining_target": ["Ki-67"]}}],
            "minimum_should_match": 1,
        }
    }


def test_build_filtering_term_query_ontology_single():
    """Single concept ID for ontology produces a terms query."""
    term = get_term("animal_species")  # ontology
    result = build_filtering_term_query(term, "410607006")
    assert result == {
        "bool": {
            "should": [{"terms": {"specimen.animal_species": ["410607006"]}}],
            "minimum_should_match": 1,
        }
    }


def test_build_filtering_term_query_ontology_multi():
    """Multiple concept IDs for ontology are combined in a single terms query."""
    term = get_term("animal_species")
    result = build_filtering_term_query(term, ["123456789", "987654321"])
    assert result == {
        "bool": {
            "should": [
                {"terms": {"specimen.animal_species": ["123456789", "987654321"]}}
            ],
            "minimum_should_match": 1,
        }
    }


def test_build_filtering_term_query_ontology_or_value_concept_ids_only():
    """ontologyOrValue with concept IDs only."""
    term = get_term(
        "fixation_type"
    )  # maps to specimen.fixation_type + specimen.fixation_type_other
    result = build_filtering_term_query(term, ["337915000", "80248007"])
    assert result == {
        "bool": {
            "should": [
                {"terms": {"specimen.fixation_type": ["337915000", "80248007"]}}
            ],
            "minimum_should_match": 1,
        }
    }


def test_build_filtering_term_query_ontology_or_value_mixed_values():
    """ontologyOrValue with mixed concept IDs and other values."""
    term = get_term(
        "fixation_type"
    )  # maps to specimen.fixation_type + specimen.fixation_type_other
    result = build_filtering_term_query(term, ["337915000", "Formalin"])
    assert result == {
        "bool": {
            "should": [
                {"terms": {"specimen.fixation_type": ["337915000"]}},
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


def test_build_filtering_term_query_ontology_or_value_free_text_only():
    """ontologyOrValue with non-concept IDs only."""
    term = get_term(
        "fixation_type"
    )  # maps to specimen.fixation_type + specimen.fixation_type_other
    result = build_filtering_term_query(term, ["Formalin", "Glutaraldehyde"])
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
# build_filtering_term_query — text type
# ---------------------------------------------------------------------------


def test_build_filtering_term_query_text_single():
    """Single value for text produces a match query."""
    term = get_term("dataset_title")  # text
    result = build_filtering_term_query(term, "cancer")
    assert result == {
        "bool": {
            "should": [
                {
                    "match": {
                        "dataset_title": {
                            "query": "cancer",
                            "minimum_should_match": "2<75%",
                        }
                    }
                }
            ],
            "minimum_should_match": 1,
        }
    }


def test_build_filtering_term_query_text_multi():
    """Multiple values for text produce one match query per value (OR semantics)."""
    term = get_term("dataset_title")
    result = build_filtering_term_query(term, ["cancer", "tumor"])
    assert result == {
        "bool": {
            "should": [
                {
                    "match": {
                        "dataset_title": {
                            "query": "cancer",
                            "minimum_should_match": "2<75%",
                        }
                    }
                },
                {
                    "match": {
                        "dataset_title": {
                            "query": "tumor",
                            "minimum_should_match": "2<75%",
                        }
                    }
                },
            ],
            "minimum_should_match": 1,
        }
    }


# ---------------------------------------------------------------------------
# build_filtering_term_query — iso8601Range type
# ---------------------------------------------------------------------------


def test_build_filtering_term_query_iso8601range_single():
    """Single iso8601 range value produces a range query in days."""
    term = get_term("age_at_extraction")  # iso8601Range
    result = build_filtering_term_query(term, "P10Y-P20Y")
    assert result == {
        "bool": {
            "should": [
                {"range": {"specimen.age_at_extraction": {"gte": 3650, "lte": 7300}}}
            ],
            "minimum_should_match": 1,
        }
    }


def test_build_filtering_term_query_iso8601range_multi():
    """Multiple iso8601 range values produce one range query per value (OR semantics)."""
    term = get_term("age_at_extraction")
    result = build_filtering_term_query(term, ["P10Y-P20Y", "P30Y-P40Y"])
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
# build_filtering_term_query — unsupported type
# ---------------------------------------------------------------------------


def test_build_filtering_term_query_unsupported_type():
    term = get_term("sex")
    term_bad = term.model_copy(update={"type": "unknown"})  # type: ignore[arg-type]
    with pytest.raises(UserException, match="Unsupported term type unknown"):
        build_filtering_term_query(term_bad, "Male")


# ---------------------------------------------------------------------------
# get_query — nested path routing
# ---------------------------------------------------------------------------


def _clauses_for_scope(query: dict, scope: str) -> list[dict]:
    """Return the filter clauses for one scope."""
    for alternative in query["bool"]["should"]:
        clauses = alternative["bool"]["filter"]
        if clauses[0] == {"term": {"scope": scope}}:
            return clauses[1:]
    raise AssertionError(f"no alternative for scope {scope!r}")


def test_get_query_no_filters():
    assert get_query() == {"bool": {"filter": [{"match_all": {}}]}}


def test_get_query_top_level_filter():
    assert get_query(("dataset_title", "cancer")) == {
        "bool": {
            "filter": [
                {
                    "bool": {
                        "should": [
                            {
                                "match": {
                                    "dataset_title": {
                                        "query": "cancer",
                                        "minimum_should_match": "2<75%",
                                    }
                                }
                            }
                        ],
                        "minimum_should_match": 1,
                    }
                },
            ]
        }
    }


def test_get_query_nested_block_filter():
    assert get_query(("sex", "Female")) == {
        "bool": {
            "filter": [
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
            "filter": [
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
    assert get_query(("sex", "Female"), ("specimen_type", "119376003")) == {
        "bool": {
            "filter": [
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
                                                        "specimen.specimen_type": [
                                                            "119376003"
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


def test_get_query_scope_specific_filter_excludes_other_scopes():
    """animal_species is indexed for non-clinical only and must not exclude
    clinical documents; sex applies to both."""
    query = get_query(("sex", "Female"), ("animal_species", "337915000"))

    sex = {
        "bool": {
            "should": [{"terms": {"specimen.sex": ["Female"]}}],
            "minimum_should_match": 1,
        }
    }
    species = {
        "bool": {
            "should": [{"terms": {"specimen.animal_species": ["337915000"]}}],
            "minimum_should_match": 1,
        }
    }

    # Clinical documents are constrained by sex alone.
    assert _clauses_for_scope(query, "clinical") == [
        {"nested": {"path": "specimen", "query": {"bool": {"filter": [sex]}}}}
    ]
    # Non-clinical documents by both, and on the same specimen item.
    assert _clauses_for_scope(query, "non_clinical") == [
        {"nested": {"path": "specimen", "query": {"bool": {"filter": [sex, species]}}}}
    ]


def test_get_query_top_level_and_nested_filters():
    assert get_query(
        ("dataset_title", "cancer"),  # top-level
        ("sex", "Female"),  # specimen
        ("staining_target", "Ki-67"),  # staining
    ) == {
        "bool": {
            "filter": [
                {
                    "bool": {
                        "should": [
                            {
                                "match": {
                                    "dataset_title": {
                                        "query": "cancer",
                                        "minimum_should_match": "2<75%",
                                    }
                                }
                            }
                        ],
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
        get_query(scope=None)
        == get_query()
        == {"bool": {"filter": [{"match_all": {}}]}}
    )


def test_get_query_scope_clinical():
    assert get_query(scope="clinical") == {
        "bool": {"filter": [{"term": {"scope": "clinical"}}]}
    }


def test_get_query_scope_non_clinical():
    assert get_query(scope="non_clinical") == {
        "bool": {"filter": [{"term": {"scope": "non_clinical"}}]}
    }


def test_get_query_scope_clinical_with_filter():
    assert get_query(("dataset_title", "cancer"), scope="clinical") == {
        "bool": {
            "filter": [
                {"term": {"scope": "clinical"}},
                {
                    "bool": {
                        "should": [
                            {
                                "match": {
                                    "dataset_title": {
                                        "query": "cancer",
                                        "minimum_should_match": "2<75%",
                                    }
                                }
                            }
                        ],
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
        filtering_scopes=BP_FILTERING_SCOPES,
        filtering_qualifiers=BP_FILTERING_QUALIFIERS,
    )


def test_qualifier_clauses_absent_qualifier_is_not_filtered():
    """An absent or empty qualifier contributes no clause, so it filters nothing."""
    service = _service()
    assert service._qualifier_clauses_by_group(None) == {}
    assert service._qualifier_clauses_by_group({}) == {}
    assert service._qualifier_clauses_by_group({"observation": []}) == {}


def test_qualifier_clauses_cover_every_group_the_qualifier_names():
    """One requested qualifier yields one clause per group it qualifies."""
    clauses = _service()._qualifier_clauses_by_group({"observation": ["confirmed"]})
    assert clauses == {
        "diagnosis": [{"terms": {"diagnosis.qualifiers": ["observation:confirmed"]}}],
        "finding": [{"terms": {"finding.qualifiers": ["observation:confirmed"]}}],
    }


def test_get_query_qualifier_joins_the_filter_inside_the_nested_query():
    service = _service()
    query = service._get_query(
        [BeaconQueryFilter(id="diagnosis", value="73211009")],
        qualifiers={"observation": ["confirmed"]},
    )
    # diagnosis is clinical-only
    nested = _clauses_for_scope(query, "clinical")[0]["nested"]
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
    query = service._get_query(
        [BeaconQueryFilter(id="dataset_title", value="cancer")],
        qualifiers={"observation": ["candidate"]},
    )
    assert not any("nested" in clause for clause in query["bool"]["filter"])


# ---------------------------------------------------------------------------
# Field value counts — restricted by scope and by qualifier
# ---------------------------------------------------------------------------

# These check the request that gets built.


def _service_over_mock_search() -> tuple[BigpictureOpenSearchBeaconService, AsyncMock]:
    """A real service over a mocked OpenSearch client."""

    def no_values(index: str, body: dict) -> dict:
        """Answer with the aggregations the request asked for, each finding none.

        A terms aggregation answers with one bucket per distinct value, so an empty
        bucket list is a field with no values. The response has to mirror the
        request's aggregation names, because that is how the counts are read out.
        """
        aggregations: dict = {"field_values": {"buckets": []}}
        group_items = body["aggs"].get("group_items")
        if group_items is None:
            # A top-level field: the buckets sit directly under the aggregations.
            return {"aggregations": aggregations}
        if "qualified_items" in group_items["aggs"]:
            aggregations = {"qualified_items": aggregations}
        return {"aggregations": {"group_items": aggregations}}

    client = AsyncMock(spec=AsyncOpenSearch)
    client.search.side_effect = no_values
    service = BigpictureOpenSearchBeaconService(
        client=client,
        index_name="idx",
        filtering_terms=BP_FILTERING_TERMS,
        filtering_scopes=BP_FILTERING_SCOPES,
        filtering_qualifiers=BP_FILTERING_QUALIFIERS,
    )
    return service, client


async def _counts_body(field_id: str, scope=None, qualifiers=None) -> dict:
    """Return the request body that get_value_counts sends."""
    service, client = _service_over_mock_search()
    await service.get_value_counts(field_id, scope, qualifiers)
    return client.search.await_args.kwargs["body"]


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
async def test_field_value_counts_without_scope_or_qualifier_count_everything():
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
async def test_get_value_counts():
    service, client = _service_over_mock_search()

    await service.get_value_counts("sex")
    await service.get_value_counts("sex")
    assert client.search.await_count == 1

    # A different key of the same field counts something else.
    await service.get_value_counts("sex", scope="clinical")
    assert client.search.await_count == 2


@pytest.mark.asyncio
async def test_refresh_value_counts():
    service, client = _service_over_mock_search()

    await service.get_value_counts("sex")
    await service.refresh_value_counts(ValueCountsKey.of("sex"))
    assert client.search.await_count == 2

    service.clear_value_counts()
    await service.get_value_counts("sex")
    assert client.search.await_count == 3


@pytest.mark.asyncio
async def test_group_item_filter_is_rejected_for_a_field_without_a_group():
    """Dropping it silently would return counts wider than the caller asked for."""
    with pytest.raises(
        SystemException, match="Cannot filter the group items of 'dataset_title'"
    ):
        await fetch_indexed_keywords(
            AsyncMock(spec=AsyncOpenSearch),
            "idx",
            "dataset_title",
            group_item_filter={"bool": {"filter": []}},
        )
