"""Field value counts against a real OpenSearch index.

These run against OpenSearch rather than a mocked response because what is being
checked is what OpenSearch does with the aggregation — that a bucket inside a
nested aggregation counts group items, and that reverse_nested counts the
documents holding them. A mocked response could only restate the assumption.
"""

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import pytest
import pytest_asyncio

from search_api.api.beacon.models import BeaconQueryFilter
from search_api.api.beacon.services import BeaconQueryService, BeaconService
from search_api.api.bigpicture.models import (
    BP_FILTERING_QUALIFIERS,
    BP_FILTERING_SCOPES,
    BP_FILTERING_TERMS,
    BigpictureBeaconDatasetResult,
    BigpictureBeaconImageResult,
)
from search_api.api.bigpicture.opensearch import (
    BigpictureDatasetBeaconService,
    BigpictureImageBeaconService,
)
from search_api.api.opensearch.models import OpenSearchBeaconFilteringTerm
from search_api.exceptions import SystemException
from search_api.api.opensearch.clauses import build_match_clause
from search_api.api.opensearch.index import create_index, index_documents
from search_api.api.opensearch.keywords import fetch_indexed_keywords
from search_api.api.opensearch.search import iter_paged_buckets, iter_paged_documents
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


def _dataset_service(index_name: str) -> BigpictureDatasetBeaconService:
    return BigpictureDatasetBeaconService(
        client=bp_search,
        index_name=index_name,
        filtering_terms=BP_FILTERING_TERMS,
        filtering_scopes=BP_FILTERING_SCOPES,
        filtering_qualifiers=BP_FILTERING_QUALIFIERS,
    )


@pytest.fixture
def dataset_service(bp_opensearch_index_name: str) -> BigpictureDatasetBeaconService:
    return _dataset_service(bp_opensearch_index_name)


def _image_service(index_name: str) -> BigpictureImageBeaconService:
    return BigpictureImageBeaconService(
        client=bp_search,
        index_name=index_name,
        filtering_terms=BP_FILTERING_TERMS,
        filtering_scopes=BP_FILTERING_SCOPES,
        filtering_qualifiers=BP_FILTERING_QUALIFIERS,
    )


@pytest.fixture
def image_service(bp_opensearch_index_name: str) -> BigpictureImageBeaconService:
    return _image_service(bp_opensearch_index_name)


# Test value counts.
#


async def _value_counts(
    beacon_service: BeaconService[OpenSearchBeaconFilteringTerm],
    field_id: str,
    scope: str | None = None,
    qualifiers: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, int]:
    """Return the value counts for a field."""
    result = await beacon_service.get_value_counts(field_id, scope, qualifiers)
    return result.counts


async def _assert_value_counts(
    beacon_service: BeaconService[OpenSearchBeaconFilteringTerm],
) -> None:
    # Female is on three specimens across two images, so it counts 2, not 3.
    assert await _value_counts(beacon_service, "sex") == {"Female": 2, "Male": 1}

    # 73211009 is on three items across two images, so it counts 2, not 3.
    assert await _value_counts(beacon_service, "diagnosis") == {
        "73211009": 2,
        "38341003": 1,
    }

    # Scope restricts which documents are counted.
    assert await _value_counts(beacon_service, "sex", scope="clinical") == {
        "Female": 1,
        "Male": 1,
    }
    assert await _value_counts(beacon_service, "sex", scope="non_clinical") == {
        "Female": 1
    }

    # Qualifier restricts which group items are counted. 73211009 is confirmed on
    # image_1 and image_2, and candidate on image_1; 38341003 is only ever a
    # candidate, on image_1.
    assert await _value_counts(
        beacon_service, "diagnosis", qualifiers={"observation": ["confirmed"]}
    ) == {"73211009": 2}
    assert await _value_counts(
        beacon_service, "diagnosis", qualifiers={"observation": ["candidate"]}
    ) == {"73211009": 1, "38341003": 1}

    # Scope and qualifier compose: image_3 is non-clinical, so its confirmed
    # 38341003 is excluded.
    assert await _value_counts(
        beacon_service,
        "diagnosis",
        scope="clinical",
        qualifiers={"observation": ["confirmed"]},
    ) == {"73211009": 2}

    # specimen declares no qualifier, so requesting one must not filter it out.
    assert await _value_counts(
        beacon_service, "sex", qualifiers={"observation": ["confirmed"]}
    ) == {"Female": 2, "Male": 1}


@pytest.mark.asyncio
async def test_dataset_value_counts(bp_opensearch_index, dataset_service):
    await _assert_value_counts(dataset_service)


@pytest.mark.asyncio
async def test_image_value_counts(bp_opensearch_index, image_service):
    await _assert_value_counts(image_service)


# Fetch indexed keywords tests.
#


@pytest.mark.asyncio
async def test_fetch_indexed_keywords_group_item_filter_rejected_without_group(
    bp_opensearch_index, bp_opensearch_index_name
):
    with pytest.raises(SystemException, match="'dataset_id' because the field"):
        await fetch_indexed_keywords(
            bp_search,
            bp_opensearch_index_name,
            "dataset_id",
            group_item_filter={"terms": {"diagnosis.qualifiers": [_CONFIRMED]}},
        )


# Count indexed tests.
#


@pytest.mark.asyncio
async def test_dataset_count_indexed(bp_opensearch_index, dataset_service):
    assert await dataset_service.count_indexed() == len(_DOCS)
    assert await dataset_service.count_indexed("clinical") == 2
    assert await dataset_service.count_indexed("non_clinical") == 1


# Test pagination.
#


@pytest.mark.asyncio
async def test_iter_paged_documents_pagination(
    bp_opensearch_index, bp_opensearch_index_name
):
    # 3 documents, page_size 2, two pages (2 + 1).
    image_ids = [
        source["image_id"]
        async for source in iter_paged_documents(
            search=bp_search,
            index_name=bp_opensearch_index_name,
            query_clause={"match_all": {}},
            page_size=2,
            source_fields=["image_id"],
            sort_field="image_id",
        )
    ]
    assert sorted(image_ids) == ["image_1", "image_2", "image_3"]


@pytest.mark.asyncio
async def test_iter_paged_buckets_pagination(
    bp_opensearch_index, bp_opensearch_index_name
):
    # 3 images sharing dataset "ds", page_size 2, two pages (2 + 1).
    image_ids = [
        bucket["key"]["image_id"]
        async for bucket in iter_paged_buckets(
            search=bp_search,
            index_name=bp_opensearch_index_name,
            query_clause={"match_all": {}},
            page_size=2,
            sources=[
                {"dataset_id": {"terms": {"field": "dataset_id"}}},
                {"image_id": {"terms": {"field": "image_id"}}},
            ],
        )
    ]
    assert sorted(image_ids) == ["image_1", "image_2", "image_3"]


# Test queries.
#


async def _dataset_image_ids(
    dataset_service: BeaconQueryService[BigpictureBeaconDatasetResult],
    pairs: list[tuple[str, str]],
    scope: str | None = None,
    qualifiers: Mapping[str, Sequence[str]] | None = None,
) -> list[str]:
    """Return image ids for a dataset query."""
    result = await dataset_service.query(
        filters=[BeaconQueryFilter(id=i, value=v) for i, v in pairs],
        granularity="record",
        scope=scope,
        qualifiers=qualifiers,
    )
    return sorted(
        image_id
        for rs in result.result_sets.resultSet
        for r in rs.results
        for image_id in r.imageIds
    )


@pytest.mark.asyncio
async def test_dataset_query_scope_specific_filter(
    bp_opensearch_index, dataset_service
):
    """diagnosis is clinical-only, so it constrains only the scope it covers."""
    # image_3 has no diagnosis and cannot have one, so the filter does not apply to
    # it; excluding it would hide a document the filter says nothing about.
    assert await _dataset_image_ids(dataset_service, [("diagnosis", "73211009")]) == [
        "image_1",
        "image_2",
        "image_3",
    ]

    # Restricted to clinical, the same filter applies normally.
    assert await _dataset_image_ids(
        dataset_service, [("diagnosis", "38341003")], scope="clinical"
    ) == ["image_1"]


@pytest.mark.asyncio
async def test_dataset_query_qualifier(bp_opensearch_index, dataset_service):
    """image_1 states 73211009 both ways; image_2 only confirmed."""
    confirmed = await _dataset_image_ids(
        dataset_service,
        [("diagnosis", "73211009")],
        scope="clinical",
        qualifiers={"observation": ["confirmed"]},
    )
    candidate = await _dataset_image_ids(
        dataset_service,
        [("diagnosis", "73211009")],
        scope="clinical",
        qualifiers={"observation": ["candidate"]},
    )
    assert confirmed == ["image_1", "image_2"]
    assert candidate == ["image_1"]

    # The qualifier applies to the item that matched, not merely to some item in
    # the group: image_1 holds a confirmed diagnosis (73211009) and holds 38341003
    # — but only as a candidate. A query for 38341003 confirmed must therefore match
    # nothing, which it only does if the qualifier clause sits inside the same
    # nested query as the filter.
    assert (
        await _dataset_image_ids(
            dataset_service,
            [("diagnosis", "38341003")],
            scope="clinical",
            qualifiers={"observation": ["confirmed"]},
        )
        == []
    )


@pytest.mark.asyncio
async def test_dataset_query_boolean_and_count_granularity(
    bp_opensearch_index, dataset_service
):
    result = await dataset_service.query(
        filters=[BeaconQueryFilter(id="diagnosis", value="73211009")],
        granularity="boolean",
        scope="clinical",
    )
    assert result.total > 0

    result = await dataset_service.query(
        filters=[BeaconQueryFilter(id="diagnosis", value="73211009")],
        granularity="count",
        scope="clinical",
    )
    assert result.total == 1  # image_1 and image_2 both belong to dataset "ds"
    assert result.result_sets.resultSet == []


async def _image_ids(
    image_service: BeaconQueryService[BigpictureBeaconImageResult],
    pairs: list[tuple[str, str]],
    scope: str | None = None,
    qualifiers: Mapping[str, Sequence[str]] | None = None,
) -> list[str]:
    """Return the image ids for a image query."""
    result = await image_service.query(
        filters=[BeaconQueryFilter(id=i, value=v) for i, v in pairs],
        granularity="record",
        scope=scope,
        qualifiers=qualifiers,
    )
    return sorted(r.imageId for rs in result.result_sets.resultSet for r in rs.results)


@pytest.mark.asyncio
async def test_image_query_returns_one_result_per_image_not_per_dataset(
    bp_opensearch_index, image_service
):
    """All three images share dataset_id 'ds', but are not grouped by it."""
    assert await _image_ids(image_service, []) == ["image_1", "image_2", "image_3"]


@pytest.mark.asyncio
async def test_image_query_scope_specific_filter(bp_opensearch_index, image_service):
    """diagnosis is clinical-only, so it constrains only the scope it covers."""
    # image_3 has no diagnosis and cannot have one, so the filter does not apply to
    # it; excluding it would hide a document the filter says nothing about.
    assert await _image_ids(image_service, [("diagnosis", "73211009")]) == [
        "image_1",
        "image_2",
        "image_3",
    ]

    # Restricted to clinical, the same filter applies normally.
    assert await _image_ids(
        image_service, [("diagnosis", "38341003")], scope="clinical"
    ) == ["image_1"]


@pytest.mark.asyncio
async def test_image_query_qualifier(bp_opensearch_index, image_service):
    confirmed = await _image_ids(
        image_service,
        [("diagnosis", "73211009")],
        scope="clinical",
        qualifiers={"observation": ["confirmed"]},
    )
    candidate = await _image_ids(
        image_service,
        [("diagnosis", "73211009")],
        scope="clinical",
        qualifiers={"observation": ["candidate"]},
    )
    assert confirmed == ["image_1", "image_2"]
    assert candidate == ["image_1"]

    # The qualifier applies to the item that matched, not merely to some item in
    # the group: image_1 holds a confirmed diagnosis (73211009) and holds 38341003
    # — but only as a candidate. A query for 38341003 confirmed must therefore match
    # nothing, which it only does if the qualifier clause sits inside the same
    # nested query as the filter.
    assert (
        await _image_ids(
            image_service,
            [("diagnosis", "38341003")],
            scope="clinical",
            qualifiers={"observation": ["confirmed"]},
        )
        == []
    )


@pytest.mark.asyncio
async def test_image_query_boolean_and_count_granularity(
    bp_opensearch_index, image_service
):
    result = await image_service.query(
        filters=[BeaconQueryFilter(id="diagnosis", value="73211009")],
        granularity="boolean",
        scope="clinical",
    )
    assert result.total > 0

    result = await image_service.query(
        filters=[BeaconQueryFilter(id="diagnosis", value="73211009")],
        granularity="count",
        scope="clinical",
    )
    assert result.total == 2  # image_1 and image_2
    assert result.result_sets.resultSet == []


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
            "query": {"bool": {"filter": [build_match_clause("t", query)]}},
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
