"""Field value counts against a real OpenSearch index.

These run against OpenSearch rather than a mocked response because what is being
checked is what OpenSearch does with the aggregation — that a bucket inside a
nested aggregation counts group items, and that reverse_nested counts the
documents holding them. A mocked response could only restate the assumption.
"""

from typing import Any

import pytest

from search_api.api.bigpicture.models import BP_FILTERING_QUALIFIERS, BP_FILTERING_TERMS
from search_api.api.bigpicture.opensearch import BigpictureOpenSearchBeaconService
from search_api.exceptions import SystemException
from search_api.api.opensearch.services import fetch_indexed_keywords
from tests.integration.conftest import bp_search

_CONFIRMED = "observation:confirmed"
_CANDIDATE = "observation:candidate"


def _document(image_id: str, scope: str, **groups: Any) -> dict[str, Any]:
    return {
        "image_id": image_id,
        "dataset_id": "ds",
        "dataset_image_cnt": 3,
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
    _document(
        "image_3",
        "non_clinical",
        specimen=[{"sex": "Female"}],
        diagnosis=[{"diagnosis": "38341003", "qualifiers": [_CONFIRMED]}],
    ),
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
        filtering_qualifiers=BP_FILTERING_QUALIFIERS,
    )


async def _counts(service, field_id: str, **restrictions) -> dict[str, int]:
    """Return the value counts for a field under the given restrictions.

    fetch_indexed_keywords underneath is cached on its arguments, which include the
    index name and both filters, so tests expecting different counts never collide.
    """
    result = await service.get_indexed_field_value_counts(field_id, **restrictions)
    return result.counts


@pytest.mark.asyncio
async def test_counts_are_documents_not_group_items(bp_opensearch_index, service):
    """Female is on three specimens across two images, so it counts 2, not 3."""
    assert await _counts(service, "sex") == {"Female": 2, "Male": 1}


@pytest.mark.asyncio
async def test_counts_of_repeated_value_in_one_document(bp_opensearch_index, service):
    """73211009 is on three items across two images, so it counts 2, not 3."""
    assert await _counts(service, "diagnosis") == {"73211009": 2, "38341003": 2}


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
    assert confirmed == {"73211009": 2, "38341003": 1}
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
