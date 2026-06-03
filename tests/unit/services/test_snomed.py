"""Unit tests for SnomedService.expand_ontology_filter."""

import pytest
from unittest.mock import AsyncMock, patch

from search_api.api.beacon.models import BeaconQueryFilter
from search_api.api.bigpicture.models import BP_FILTERING_TERMS
from search_api.services.snomed import SnomedConcept, SnomedService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _filter(
    field_id: str,
    value: str | list[str],
    include_descendants: bool = True,
) -> BeaconQueryFilter:
    return BeaconQueryFilter(
        id=field_id, value=value, includeDescendantTerms=include_descendants
    )


def _concept(concept_id: str) -> SnomedConcept:
    return SnomedConcept(concept_id=concept_id, preferred_term=f"Term {concept_id}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def service() -> SnomedService:
    return SnomedService()


@pytest.mark.asyncio
async def test_expand_ontology_filter_include_descendant_terms_false_returns_unchanged(
    service,
):
    f = _filter("animal_species", "410607006", include_descendants=False)
    result = await service.expand_ontology_filter(f, BP_FILTERING_TERMS)
    assert result is f


@pytest.mark.asyncio
async def test_expand_ontology_filter_non_ontology_field_returns_unchanged(service):
    f = _filter("sex", "Male")
    result = await service.expand_ontology_filter(f, BP_FILTERING_TERMS)
    assert result is f


@pytest.mark.asyncio
async def test_expand_ontology_filter_text_field_returns_unchanged(service):
    f = _filter("dataset_title", "cancer")
    result = await service.expand_ontology_filter(f, BP_FILTERING_TERMS)
    assert result is f


@pytest.mark.asyncio
async def test_expand_ontology_filter_unknown_field_returns_unchanged(service):
    f = _filter("unknown_field", "some_value")
    result = await service.expand_ontology_filter(f, BP_FILTERING_TERMS)
    assert result is f


@pytest.mark.asyncio
async def test_expand_ontology_filter_single_value_resolved_and_expanded(service):
    f = _filter("animal_species", "410607006")
    service.find_concept = AsyncMock(return_value="410607006")
    with patch.object(
        SnomedService,
        "get_descendants",
        new=AsyncMock(return_value=[_concept("111"), _concept("222")]),
    ):
        result = await service.expand_ontology_filter(f, BP_FILTERING_TERMS)

    assert set(result.value) == {"410607006", "111", "222"}


@pytest.mark.asyncio
async def test_expand_ontology_filter_multiple_values_expanded_and_merged(service):
    f = _filter("animal_species", ["410607006", "888"])
    service.find_concept = AsyncMock(side_effect=["410607006", "888"])
    with patch.object(
        SnomedService,
        "get_descendants",
        new=AsyncMock(side_effect=[[_concept("111")], [_concept("222")]]),
    ):
        result = await service.expand_ontology_filter(f, BP_FILTERING_TERMS)

    assert set(result.value) == {"410607006", "888", "111", "222"}


@pytest.mark.asyncio
async def test_expand_ontology_filter_all_unresolvable_returns_unchanged(service):
    f = _filter("animal_species", "no match here")
    service.find_concept = AsyncMock(return_value=None)
    result = await service.expand_ontology_filter(f, BP_FILTERING_TERMS)
    assert result is f


@pytest.mark.asyncio
async def test_expand_ontology_filter_resolvable_and_unresolvable(service):
    f = _filter("fixation_type", ["410607006", "free text value"])
    service.find_concept = AsyncMock(side_effect=["410607006", None])
    with patch.object(
        SnomedService,
        "get_descendants",
        new=AsyncMock(return_value=[_concept("111")]),
    ):
        result = await service.expand_ontology_filter(f, BP_FILTERING_TERMS)

    assert "410607006" in result.value
    assert "111" in result.value
    assert "free text value" in result.value


@pytest.mark.asyncio
async def test_expand_ontology_filter_shared_descendants_deduplicated(service):
    f = _filter("animal_species", ["111", "222"])
    service.find_concept = AsyncMock(side_effect=["111", "222"])
    with patch.object(
        SnomedService,
        "get_descendants",
        # Both concepts share descendant "999".
        new=AsyncMock(side_effect=[[_concept("999")], [_concept("999")]]),
    ):
        result = await service.expand_ontology_filter(f, BP_FILTERING_TERMS)

    assert isinstance(result.value, list)
    assert result.value.count("999") == 1
