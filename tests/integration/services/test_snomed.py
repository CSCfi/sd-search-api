"""Integration tests for the SNOMED CT service."""

import pytest
from search_api.api.bigpicture.models import BP_SPECIES_FILTERING_TERM
from search_api.services.snomed import SnomedService


@pytest.mark.asyncio
async def test_find_concept():
    service = SnomedService()
    for term in ("human", "Homo sapiens", "337915000"):
        concept = await service.find_concept(
            term, ecl=BP_SPECIES_FILTERING_TERM.snomed_ecl
        )
        assert concept is not None
        assert concept.concept_id == "337915000"
        assert concept.preferred_term == "Homo sapiens"
        assert set(concept.synonyms) == set(["Human", "Homo sapiens"])


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
