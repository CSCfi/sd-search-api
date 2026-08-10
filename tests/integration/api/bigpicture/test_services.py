"""Field value counts against a real OpenSearch index.

These run against OpenSearch rather than a mocked response because what is being
checked is what OpenSearch does with the aggregation — that a bucket inside a
nested aggregation counts group items, and that reverse_nested counts the
documents holding them. A mocked response could only restate the assumption.
"""

import uuid
from typing import Any

import pytest
import pytest_asyncio

from search_api.api.beacon.models import BeaconQueryFilter
from search_api.api.bigpicture.models import (
    BP_FILTERING_QUALIFIERS,
    BP_FILTERING_SCOPES,
    BP_FILTERING_TERMS,
)
from search_api.api.bigpicture.opensearch import BigpictureOpenSearchBeaconService
from search_api.exceptions import SystemException
from search_api.api.opensearch.services import (
    build_match_query,
    create_index,
    fetch_indexed_keywords,
    index_documents,
)
from tests.integration.conftest import bp_search

_CONFIRMED = "observation:confirmed"
_CANDIDATE = "observation:candidate"


def _document(image_id: str, scope: str, **groups: Any) -> dict[str, Any]:
    return {
        "image_id": image_id,
        "dataset_id": "ds",
        "dataset_image_cnt": 3,
        "dataset_title": "Test dataset",
        "dataset_description": "Test dataset description",
        "scope": scope,
        **groups,
    }


# image_1 deliberately holds two specimens of the same sex and the same diagnosis
# twice, so an item count and a document count differ.
_DOCS = [
    _document(
        "image_1",
        "clinical",
        specimen=[{"sex": "Female"}, {"sex": "Female"}],
        diagnosis=[
            {"diagnosis": "73211009", "qualifiers": [_CONFIRMED]},
            {"diagnosis": "73211009", "qualifiers": [_CANDIDATE]},
            {"diagnosis": "38341003", "qualifiers": [_CANDIDATE]},
        ],
    ),
    _document(
        "image_2",
        "clinical",
        specimen=[{"sex": "Male"}],
        diagnosis=[{"diagnosis": "73211009", "qualifiers": [_CONFIRMED]}],
    ),
    _document("image_3", "non_clinical", specimen=[{"sex": "Female"}]),
]


@pytest.fixture(scope="module")
def bp_opensearch_docs() -> list[dict[str, Any]]:
    return _DOCS


@pytest.fixture
def service(bp_opensearch_index_name: str) -> BigpictureOpenSearchBeaconService:
    return BigpictureOpenSearchBeaconService(
        client=bp_search,
        index_name=bp_opensearch_index_name,
        filtering_terms=BP_FILTERING_TERMS,
        filtering_scopes=BP_FILTERING_SCOPES,
        filtering_qualifiers=BP_FILTERING_QUALIFIERS,
    )


async def _counts(service, field_id, scope=None, qualifiers=None) -> dict[str, int]:
    """Return the value counts for a field, narrowed by scope and qualifier."""
    result = await service.get_value_counts(field_id, scope, qualifiers)
    return result.counts


@pytest.mark.asyncio
async def test_counts_are_documents_not_group_items(bp_opensearch_index, service):
    """Female is on three specimens across two images, so it counts 2, not 3."""
    assert await _counts(service, "sex") == {"Female": 2, "Male": 1}


@pytest.mark.asyncio
async def test_counts_of_repeated_value_in_one_document(bp_opensearch_index, service):
    """73211009 is on three items across two images, so it counts 2, not 3."""
    assert await _counts(service, "diagnosis") == {"73211009": 2, "38341003": 1}


@pytest.mark.asyncio
async def test_counts_of_top_level_field(bp_opensearch_index, bp_opensearch_index_name):
    # Called directly rather than through the service because Bigpicture declares no
    # top-level keyword filtering term.
    counts = await fetch_indexed_keywords(
        bp_search, bp_opensearch_index_name, "dataset_id"
    )

    assert counts == {"ds": 3}


@pytest.mark.asyncio
async def test_scope_restricts_which_documents_are_counted(
    bp_opensearch_index, service
):
    assert await _counts(service, "sex", scope="clinical") == {"Female": 1, "Male": 1}
    assert await _counts(service, "sex", scope="non_clinical") == {"Female": 1}


@pytest.mark.asyncio
async def test_qualifier_restricts_which_group_items_are_counted(
    bp_opensearch_index, service
):
    confirmed = await _counts(
        service, "diagnosis", qualifiers={"observation": ["confirmed"]}
    )
    candidate = await _counts(
        service, "diagnosis", qualifiers={"observation": ["candidate"]}
    )

    # 73211009 is confirmed on image_1 and image_2, and candidate on image_1.
    # 38341003 is only ever a candidate, on image_1.
    assert confirmed == {"73211009": 2}
    assert candidate == {"73211009": 1, "38341003": 1}


@pytest.mark.asyncio
async def test_scope_and_qualifier_compose(bp_opensearch_index, service):
    counts = await _counts(
        service,
        "diagnosis",
        scope="clinical",
        qualifiers={"observation": ["confirmed"]},
    )

    # image_3 is non-clinical, so its confirmed 38341003 is excluded.
    assert counts == {"73211009": 2}


@pytest.mark.asyncio
async def test_qualifier_does_not_zero_an_unqualified_group(
    bp_opensearch_index, service
):
    """specimen declares no qualifier, so requesting one must not filter it out."""
    assert await _counts(service, "sex", qualifiers={"observation": ["confirmed"]}) == {
        "Female": 2,
        "Male": 1,
    }


@pytest.mark.asyncio
async def test_group_item_filter_is_rejected_for_a_field_without_a_group(
    bp_opensearch_index, bp_opensearch_index_name
):
    with pytest.raises(SystemException, match="'dataset_id' because the field"):
        await fetch_indexed_keywords(
            bp_search,
            bp_opensearch_index_name,
            "dataset_id",
            group_item_filter={"terms": {"diagnosis.qualifiers": [_CONFIRMED]}},
        )


#
# Queries. A filter only constrains the scopes its field is indexed for.
#


async def _images(
    service, pairs: list[tuple[str, str]], scope=None, qualifiers=None
) -> list[str]:
    """Return the image ids a query matches."""
    result = await service.query(
        filters=[BeaconQueryFilter(id=i, value=v) for i, v in pairs],
        granularity="record",
        scope=scope,
        qualifiers=qualifiers,
    )
    return sorted(
        image_id
        for rs in result.resultSet
        for r in rs.results
        for image_id in r.imageIds
    )


@pytest.mark.asyncio
async def test_query_scope_specific_filter_spares_the_other_scopes(
    bp_opensearch_index, service
):
    """diagnosis is clinical-only, so it must not exclude the non-clinical image.

    image_3 has no diagnosis and cannot have one, so the filter does not apply to
    it; excluding it would hide a document the filter says nothing about.
    """
    assert await _images(service, [("diagnosis", "73211009")]) == [
        "image_1",
        "image_2",
        "image_3",
    ]


@pytest.mark.asyncio
async def test_query_scope_specific_filter_still_constrains_its_own_scope(
    bp_opensearch_index, service
):
    """Restricted to clinical, the filter applies normally."""
    assert await _images(service, [("diagnosis", "38341003")], scope="clinical") == [
        "image_1"
    ]


@pytest.mark.asyncio
async def test_query_qualifier_selects_among_the_items(bp_opensearch_index, service):
    """image_1 states 73211009 both ways; image_2 only confirmed."""
    confirmed = await _images(
        service,
        [("diagnosis", "73211009")],
        scope="clinical",
        qualifiers={"observation": ["confirmed"]},
    )
    candidate = await _images(
        service,
        [("diagnosis", "73211009")],
        scope="clinical",
        qualifiers={"observation": ["candidate"]},
    )

    assert confirmed == ["image_1", "image_2"]
    assert candidate == ["image_1"]


@pytest.mark.asyncio
async def test_query_qualifier_must_hold_for_the_matching_item(
    bp_opensearch_index, service
):
    """The qualifier applies to the item that matched, not merely to some item.

    image_1 holds a confirmed diagnosis (73211009) and holds 38341003 — but only as
    a candidate. A query for 38341003 confirmed must therefore match nothing, which
    it only does if the qualifier clause sits inside the same nested query.
    """
    assert (
        await _images(
            service,
            [("diagnosis", "38341003")],
            scope="clinical",
            qualifiers={"observation": ["confirmed"]},
        )
        == []
    )


#
# Text fields: the english analyzer stems, and a match needs most of its terms.
#


@pytest.fixture(scope="module")
def text_index_name() -> str:
    return f"bp-text-test-{uuid.uuid4().hex}"


@pytest_asyncio.fixture(scope="module")
async def text_index(text_index_name: str):
    """An index of dataset titles only, to exercise the text analyzer directly."""
    docs = {
        "breast": "Human Breast Tissue Collection",
        "lung": "Cancers of the lung",
        "stained": "FFPE breast tissue sections stained with haematoxylin and eosin",
    }
    await create_index(
        bp_search,
        text_index_name,
        {"mappings": {"properties": {"t": {"type": "text", "analyzer": "english"}}}},
    )
    await index_documents(
        bp_search, text_index_name, list(docs), [{"t": v} for v in docs.values()]
    )
    await bp_search.indices.refresh(index=text_index_name)
    yield
    await bp_search.indices.delete(index=text_index_name)


async def _text_hits(index_name: str, query: str) -> list[str]:
    response = await bp_search.search(
        index=index_name,
        body={
            "size": 10,
            "query": {"bool": {"filter": [build_match_query("t", query)]}},
        },
    )
    return sorted(hit["_id"] for hit in response["hits"]["hits"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # The english analyzer stems both the text and the query.
        ("cancer", ["lung"]),  # indexed as "Cancers"
        ("staining", ["stained"]),  # indexed as "stained"
        ("tissues", ["breast", "stained"]),  # indexed as "Tissue"/"tissue"
    ],
)
async def test_text_match_stems_the_query(text_index, text_index_name, query, expected):
    assert await _text_hits(text_index_name, query) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected", "why"),
    [
        ("breast", ["breast", "stained"], "one term, so it must match"),
        ("breast cancer", [], "two terms, so both must match"),
        ("human breast tissue", ["breast", "stained"], "three terms, two suffice"),
        ("human breast tissue elephant", ["breast"], "four terms, one may miss"),
    ],
)
async def test_text_match_requires_most_of_its_terms(
    text_index, text_index_name, query, expected, why
):
    """Up to two terms all must match; beyond that three quarters must."""
    assert await _text_hits(text_index_name, query) == expected, why
