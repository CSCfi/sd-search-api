"""Integration tests for the SNOMED CT service."""

import logging

import pytest
from search_api.api.bigpicture.models import BP_FILTERING_TERM_BY_ID
from search_api.services.ontology.snomed import SnomedService, is_concept_id

# A well-formed concept id that does not exist in SNOMED CT.
_NONEXISTENT_CONCEPT_ID = "999999006"

# A malformed concept id. Partition identifier '02' is a relationship
# and its check digit should be 6.
_MALFORMED_CONCEPT_ID = "12710022"

_RETIRED_CONCEPTS_IDS = {
    "84499006": "Chronic inflammation",
    "35917007": "Adenocarcinoma",
    "68453008": "Carcinoma",
    "430864009": "Tissue fixative",
    "86616005": "Intraductal carcinoma, noninfiltrating",
    "86049000": "Neoplasm, malignant (primary)",
}

# Mapping from replacement concept ids to active ones.
_REPLACEMENT_CONCEPT_IDS = {
    "84499006": "409777003",
    "35917007": "1187332001",
    "68453008": "1187425009",
    "430864009": "1388477003",
    "86616005": "1162814007",
    "86049000": "1240414004",
}


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_find_concept():
    service = SnomedService()
    for term in ("human", "Homo sapiens"):
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
async def test_get_preferred_terms_inactive_concept(caplog):
    service = SnomedService()

    with caplog.at_level(logging.WARNING):
        result = await service.get_preferred_terms(set(_RETIRED_CONCEPTS_IDS))

    assert result == _RETIRED_CONCEPTS_IDS
    assert {
        concept_id
        for concept_id in _RETIRED_CONCEPTS_IDS
        if f"Concept {concept_id} " in caplog.text
    } == set(_RETIRED_CONCEPTS_IDS)
    assert "is inactive in SNOMED CT" in caplog.text


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_replacement_concept_id_retired_concept():
    """Every retired concept these datasets cite names one active replacement."""
    service = SnomedService()

    assert {
        concept_id: await service.replacement_concept_id(concept_id)
        for concept_id in _REPLACEMENT_CONCEPT_IDS
    } == _REPLACEMENT_CONCEPT_IDS


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_replacement_concept_id_active_or_unknown_concept():
    """Nothing to replace, so the value a document carries is kept."""
    service = SnomedService()

    assert await service.replacement_concept_id("337915000") is None
    assert await service.replacement_concept_id(_NONEXISTENT_CONCEPT_ID) is None


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_get_preferred_terms_unknown_id_omitted():
    service = SnomedService()
    result = await service.get_preferred_terms({"337915000", "000000000"})
    assert "337915000" in result
    assert "000000000" not in result


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_get_preferred_terms_nonexistent_concept_id():
    service = SnomedService()

    assert is_concept_id(_NONEXISTENT_CONCEPT_ID)
    assert not is_concept_id(_MALFORMED_CONCEPT_ID)
    assert await service.get_preferred_terms({_NONEXISTENT_CONCEPT_ID}) == {}


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


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_find_concept_short_term():
    service = SnomedService()

    # A term under three characters resolves to nothing.
    assert await service.find_concept("5", ecl=None) is None
    assert await service.find_concept("ab", ecl=None) is None
