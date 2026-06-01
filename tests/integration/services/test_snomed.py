"""Integration tests for the SNOMED CT service."""

import pytest
from search_api.api.bigpicture.models import BP_SPECIES_FILTERING_TERM
from search_api.services.snomed import find_concept

skip = pytest.mark.skip(reason="Requires Snowstorm")


@skip
@pytest.mark.asyncio
async def test_find_concept():
    concept_id = await find_concept("human", ecl=BP_SPECIES_FILTERING_TERM.snomed_ecl)
    assert concept_id is not None
    assert concept_id == "337915000"
