"""Integration tests for the SNOMED CT service."""

import pytest
from search_api.api.bigpicture.models import BP_SPECIES_FILTERING_TERM
from search_api.services.snomed import SnomedService

_snomed = SnomedService()

skip = pytest.mark.skip(reason="Requires Snowstorm")


@skip
@pytest.mark.asyncio
async def test_find_concept():
    concept_id = await _snomed.find_concept(
        "human", ecl=BP_SPECIES_FILTERING_TERM.snomed_ecl
    )
    assert concept_id is not None
    assert concept_id == "337915000"  # Homo sapiens (organism)
    concept_id = await _snomed.find_concept(
        "337915000", ecl=BP_SPECIES_FILTERING_TERM.snomed_ecl
    )
    assert concept_id is not None
    assert concept_id == "337915000"  # Homo sapiens (organism)


@skip
@pytest.mark.asyncio
async def test_list_descendants():
    concept_id = BP_SPECIES_FILTERING_TERM.ontologyConcept  # Organism (organism)
    concepts = await _snomed.get_descendants(concept_id)
    concept_ids = {c.concept_id for c in concepts}
    assert len(concepts) > 1
    assert concept_id not in concept_ids  # root excluded (strict descendants only)
    assert "337915000" in concept_ids  # Homo sapiens (organism)
    assert all(c.preferred_term for c in concepts)
