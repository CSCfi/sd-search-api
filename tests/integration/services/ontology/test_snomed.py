"""Integration tests for the SNOMED CT service."""

import pytest
from search_api.api.bigpicture.models import BP_FILTERING_TERM_BY_ID
from search_api.services.ontology.snomed import SnomedService


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_find_concept():
    service = SnomedService()
    for term in ("human", "Homo sapiens", "337915000"):
        concept = await service.find_concept(
            term, ecl=BP_FILTERING_TERM_BY_ID["animal_species"].snomed_ecl
        )
        assert concept is not None
        assert concept.concept_id == "337915000"
        assert concept.preferred_term == "Homo sapiens"


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_get_preferred_terms():
    service = SnomedService()
    result = await service.get_preferred_terms({"337915000", "80248007"})
    assert result["337915000"] == "Homo sapiens"
    assert result["80248007"] == "Left breast structure"


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_get_preferred_terms_unknown_id_omitted():
    service = SnomedService()
    result = await service.get_preferred_terms({"337915000", "000000000"})
    assert "337915000" in result
    assert "000000000" not in result


@pytest.mark.asyncio
async def test_get_preferred_terms_empty():
    service = SnomedService()
    assert await service.get_preferred_terms(set()) == {}


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_find_descendants():
    service = SnomedService()
    concept = await service.find_concept("Myocardial infarction")
    assert concept is not None
    assert concept.concept_id == "22298006"
    concepts = await service.find_descendants(concept.concept_id)
    assert len(concepts) > 0
    assert "22298006" not in [c.concept_id for c in concepts]
    assert all(c.concept_id for c in concepts)
    assert all(c.preferred_term for c in concepts)


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_describes_true_for_preferred_term_or_synonym():
    # "Homo sapiens" is 337915000's preferred term, "Human" a synonym of it.
    assert await SnomedService._describes("337915000", "Homo sapiens") is True
    assert await SnomedService._describes("337915000", "Human") is True


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_describes_is_case_and_space_insensitive():
    assert await SnomedService._describes("337915000", "  HOMO   SAPIENS  ") is True


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_describes_false_for_a_partial_term():
    # "Formalin" is only part of 1388516000's description "Neutral buffered
    # formalin 10% solution", so it is not one of its descriptions.
    assert await SnomedService._describes("1388516000", "Formalin") is False


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_describes_false_for_an_unrelated_value():
    assert await SnomedService._describes("337915000", "zzz nonsense") is False
