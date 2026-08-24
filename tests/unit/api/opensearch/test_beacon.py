import pytest

from search_api.api.beacon.models import BeaconQueryFilter
from search_api.exceptions import UserException
from search_api.api.opensearch.beacon import (
    OpenSearchQueryBeaconService,
    build_filtering_term_clause,
)
from search_api.api.bigpicture.models import (
    BP_FILTERING_SCOPES,
    BP_FILTERING_TERMS,
)
from search_api.api.bigpicture.opensearch import BigpictureDatasetBeaconService
from search_api.api.models import ValueCounts, ValueCountsKey
from search_api.api.opensearch.models import ONTOLOGY_OTHER_VALUE_FIELD_SUFFIX


def get_term(field_id: str):
    return next(t for t in BP_FILTERING_TERMS if t.id == field_id)


def get_query_clause(
    *id_value_pairs: tuple[str, str], scope: str | None = None
) -> dict:
    """Build and return the OpenSearch query clause for the given filter pairs."""
    filters = [BeaconQueryFilter(id=fid, value=val) for fid, val in id_value_pairs]
    return _service()._get_query_clause(filters, scope)


# Test filtering term clause.
#


def test_build_filtering_term_clause_controlled_value_single():
    """Single value for controlledValue produces a terms query."""
    term = get_term("sex")  # controlledValue
    result = build_filtering_term_clause(term, "Male")
    assert result == {
        "bool": {
            "should": [{"terms": {"specimen.sex": ["Male"]}}],
            "minimum_should_match": 1,
        }
    }


def test_build_filtering_term_clause_controlled_value_multi():
    """Multiple values for controlledValue are combined in a single terms query."""
    term = get_term("sex")
    result = build_filtering_term_clause(term, ["Male", "Female"])
    assert result == {
        "bool": {
            "should": [{"terms": {"specimen.sex": ["Male", "Female"]}}],
            "minimum_should_match": 1,
        }
    }


def test_build_filtering_term_clause_keyword_single():
    """Single value for keyword produces an exact terms query (not a match query)."""
    term = get_term("staining_target")  # keyword
    result = build_filtering_term_clause(term, "Ki-67")
    assert result == {
        "bool": {
            "should": [{"terms": {"staining.staining_target": ["Ki-67"]}}],
            "minimum_should_match": 1,
        }
    }


def test_build_filtering_term_clause_ontology_single():
    """Single concept ID for ontology produces a terms query."""
    term = get_term("animal_species")  # ontology
    result = build_filtering_term_clause(term, "410607006")
    assert result == {
        "bool": {
            "should": [{"terms": {"specimen.animal_species": ["410607006"]}}],
            "minimum_should_match": 1,
        }
    }


def test_build_filtering_term_clause_ontology_multi():
    """Multiple concept IDs for ontology are combined in a single terms query."""
    term = get_term("animal_species")
    result = build_filtering_term_clause(term, ["123456789", "987654321"])
    assert result == {
        "bool": {
            "should": [
                {"terms": {"specimen.animal_species": ["123456789", "987654321"]}}
            ],
            "minimum_should_match": 1,
        }
    }


def test_build_filtering_term_clause_ontology_or_value_concept_ids_only():
    """ontologyOrValue with concept IDs only."""
    term = get_term(
        "fixation_type"
    )  # maps to specimen.fixation_type + specimen.fixation_type_other
    result = build_filtering_term_clause(term, ["337915000", "80248007"])
    assert result == {
        "bool": {
            "should": [
                {"terms": {"specimen.fixation_type": ["337915000", "80248007"]}}
            ],
            "minimum_should_match": 1,
        }
    }


def test_build_filtering_term_clause_ontology_or_value_mixed_values():
    """ontologyOrValue with mixed concept IDs and other values."""
    term = get_term(
        "fixation_type"
    )  # maps to specimen.fixation_type + specimen.fixation_type_other
    result = build_filtering_term_clause(term, ["337915000", "Formalin"])
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


def test_build_filtering_term_clause_ontology_or_value_free_text_only():
    """ontologyOrValue with non-concept IDs only."""
    term = get_term(
        "fixation_type"
    )  # maps to specimen.fixation_type + specimen.fixation_type_other
    result = build_filtering_term_clause(term, ["Formalin", "Glutaraldehyde"])
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


def test_build_filtering_term_clause_text_single():
    """Single value for text produces a match query."""
    term = get_term("dataset_title")  # text
    result = build_filtering_term_clause(term, "cancer")
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


def test_build_filtering_term_clause_text_multi():
    """Multiple values for text produce one match query per value (OR semantics)."""
    term = get_term("dataset_title")
    result = build_filtering_term_clause(term, ["cancer", "tumor"])
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


def test_build_filtering_term_clause_iso8601range_single():
    """Single iso8601 range value produces a range query in days."""
    term = get_term("age_at_extraction")  # iso8601Range
    result = build_filtering_term_clause(term, "P10Y-P20Y")
    assert result == {
        "bool": {
            "should": [
                {"range": {"specimen.age_at_extraction": {"gte": 3650, "lte": 7300}}}
            ],
            "minimum_should_match": 1,
        }
    }


def test_build_filtering_term_clause_iso8601range_multi():
    """Multiple iso8601 range values produce one range query per value (OR semantics)."""
    term = get_term("age_at_extraction")
    result = build_filtering_term_clause(term, ["P10Y-P20Y", "P30Y-P40Y"])
    assert result == {
        "bool": {
            "should": [
                {"range": {"specimen.age_at_extraction": {"gte": 3650, "lte": 7300}}},
                {"range": {"specimen.age_at_extraction": {"gte": 10950, "lte": 14600}}},
            ],
            "minimum_should_match": 1,
        }
    }


def test_build_filtering_term_clause_unsupported_type():
    term = get_term("sex")
    term_bad = term.model_copy(update={"type": "unknown"})  # type: ignore[arg-type]
    with pytest.raises(UserException, match="Unsupported term type unknown"):
        build_filtering_term_clause(term_bad, "Male")


# Test get_query.
#


def _clauses_for_scope(query: dict, scope: str) -> list[dict]:
    """Return the filter clauses for one scope."""
    for alternative in query["bool"]["should"]:
        clauses = alternative["bool"]["filter"]
        if clauses[0] == {"term": {"scope": scope}}:
            return clauses[1:]
    raise AssertionError(f"no alternative for scope {scope!r}")


def test_get_query_clause_no_filters():
    assert get_query_clause() == {"bool": {"filter": [{"match_all": {}}]}}


def test_get_query_clause_top_level_filter():
    assert get_query_clause(("dataset_title", "cancer")) == {
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


def test_get_query_clause_nested_block_filter():
    assert get_query_clause(("sex", "Female")) == {
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


def test_get_query_clause_nested_stain_filter():
    assert get_query_clause(("staining_target", "Ki-67")) == {
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


def test_get_query_clause_multiple_nested_specimen_filter():
    assert get_query_clause(("sex", "Female"), ("specimen_type", "119376003")) == {
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


def test_get_query_clause_scope_specific_filter_excludes_other_scopes():
    """animal_species is indexed for non-clinical only and must not exclude
    clinical documents; sex applies to both."""
    query = get_query_clause(("sex", "Female"), ("animal_species", "337915000"))

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


def test_get_query_clause_top_level_and_nested_filters():
    assert get_query_clause(
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


def test_get_query_clause_scope_none():
    """scope=None (the default) means no scope restriction."""
    assert (
        get_query_clause(scope=None)
        == get_query_clause()
        == {"bool": {"filter": [{"match_all": {}}]}}
    )


def test_get_query_clause_scope_clinical():
    assert get_query_clause(scope="clinical") == {
        "bool": {"filter": [{"term": {"scope": "clinical"}}]}
    }


def test_get_query_clause_scope_non_clinical():
    assert get_query_clause(scope="non_clinical") == {
        "bool": {"filter": [{"term": {"scope": "non_clinical"}}]}
    }


def test_get_query_clause_scope_clinical_with_filter():
    assert get_query_clause(("dataset_title", "cancer"), scope="clinical") == {
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


def _service() -> OpenSearchQueryBeaconService:
    return BigpictureDatasetBeaconService(
        client=None,  # type: ignore[arg-type]
        index_name="test",
        filtering_terms=BP_FILTERING_TERMS,
        filtering_scopes=BP_FILTERING_SCOPES,
    )


def test_get_query_clause_diagnosis_and_finding():
    query = get_query_clause(("diagnosis", "73211009"), ("finding", "abc"))

    clinical = _clauses_for_scope(query, "clinical")
    assert clinical == [
        {
            "nested": {
                "path": "observation",
                "query": {
                    "bool": {
                        "filter": [
                            {
                                "bool": {
                                    "should": [
                                        {
                                            "terms": {
                                                "observation.diagnosis": ["73211009"]
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

    non_clinical = _clauses_for_scope(query, "non_clinical")
    assert non_clinical == [
        {
            "nested": {
                "path": "observation",
                "query": {
                    "bool": {
                        "filter": [
                            {
                                "bool": {
                                    "should": [
                                        {"terms": {"observation.finding": ["abc"]}}
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


# Test value counts.
#


def _mock_count_values(monkeypatch, service) -> list[ValueCountsKey]:
    calls: list[ValueCountsKey] = []

    async def mock_count_values(key: ValueCountsKey) -> ValueCounts:
        calls.append(key)
        return ValueCounts(counts={})

    monkeypatch.setattr(service, "_count_values", mock_count_values)
    return calls


@pytest.mark.asyncio
async def test_get_value_counts(monkeypatch):
    service = _service()
    calls = _mock_count_values(monkeypatch, service)

    await service.get_value_counts("sex")
    await service.get_value_counts("sex")
    assert len(calls) == 1

    # A different key of the same field counts something else.
    await service.get_value_counts("sex", scope="clinical")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_refresh_value_counts(monkeypatch):
    service = _service()
    calls = _mock_count_values(monkeypatch, service)

    await service.get_value_counts("sex")
    await service.refresh_value_counts(ValueCountsKey.of("sex"))
    assert len(calls) == 2

    service.clear_value_counts()
    await service.get_value_counts("sex")
    assert len(calls) == 3
