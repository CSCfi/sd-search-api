"""Unit tests for SNOMED OntologyService hooks called by ``prepare_ontology_filter`` template method."""

import pytest
from unittest.mock import AsyncMock, patch

from search_api.api.bigpicture.models import BP_FILTERING_TERM_BY_ID
from search_api.services.snomed import SnomedConcept, SnomedService

ANIMAL_SPECIES_TERM = BP_FILTERING_TERM_BY_ID["animal_species"]


def _concept(concept_id: str) -> SnomedConcept:
    return SnomedConcept(concept_id=concept_id, preferred_term=f"Term {concept_id}")


@pytest.fixture
def service() -> SnomedService:
    return SnomedService()


@pytest.mark.parametrize(
    "value,expected",
    [
        ("410607006", True),
        ("Homo sapiens", False),
        ("", False),
    ],
)
def test_is_concept_id(service, value, expected):
    assert service.is_concept_id(value) is expected


@pytest.mark.asyncio
async def test_find_concept_ids_passes_the_terms_ecl_to_snowstorm(service):
    service.find_concept = AsyncMock(return_value=_concept("337915000"))

    with patch.object(SnomedService, "_describes", new=AsyncMock(return_value=True)):
        result = await service._find_concept_ids("Homo sapiens", ANIMAL_SPECIES_TERM)

    assert result == {"337915000"}
    service.find_concept.assert_awaited_once_with(
        "Homo sapiens", ecl=ANIMAL_SPECIES_TERM.snomed_ecl
    )


@pytest.mark.asyncio
async def test_find_concept_ids_resolves_to_nothing_when_no_concept_matches(service):
    service.find_concept = AsyncMock(return_value=None)

    result = await service._find_concept_ids("no match here", ANIMAL_SPECIES_TERM)

    assert result == set()


@pytest.mark.asyncio
async def test_find_concept_ids_rejects_a_match_the_value_does_not_describe(service):
    service.find_concept = AsyncMock(return_value=_concept("261014004"))

    with patch.object(SnomedService, "_describes", new=AsyncMock(return_value=False)):
        result = await service._find_concept_ids("Frozen", ANIMAL_SPECIES_TERM)

    assert result == set()


@pytest.mark.asyncio
async def test_find_descendant_ids_unions_the_descendants_of_every_concept_id(
    service,
):
    with patch.object(
        SnomedService,
        "find_descendants",
        # "222" descends from both concepts.
        new=AsyncMock(
            side_effect=[[_concept("111"), _concept("222")], [_concept("222")]]
        ),
    ) as find_descendants:
        result = await service._find_descendant_ids({"410607006", "888"})

    assert result == {"111", "222"}
    # Every concept id is looked up, and only those.
    assert sorted(call.args[0] for call in find_descendants.await_args_list) == [
        "410607006",
        "888",
    ]


@pytest.mark.asyncio
async def test_find_descendant_ids_resolves_to_nothing_for_a_leaf_concept(service):
    with patch.object(
        SnomedService, "find_descendants", new=AsyncMock(return_value=[])
    ):
        result = await service._find_descendant_ids({"410607006"})

    assert result == set()
