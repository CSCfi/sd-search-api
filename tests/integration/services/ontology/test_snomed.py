"""Integration tests for the SNOMED CT service."""

import logging

import httpx
import pytest
from search_api.api.bigpicture.models import BP_FILTERING_TERM_BY_ID
from search_api.services.ontology.snomed import (
    SnomedService,
    _is_selected_by_ecl,
    is_well_formed_concept_id,
)

_CONCEPT_ID_ORGANISM = "410607006"  # the animal_species restriction's root
_CONCEPT_ID_TISSUE_FIXATIVE = "1388477003"  # the fixation_type restriction's root
_CONCEPT_ID_HOMO_SAPIENS = "337915000"
_CONCEPT_ID_LEFT_BREAST_STRUCTURE = "80248007"
_CONCEPT_ID_MYOCARDIAL_INFARCTION = "22298006"
_CONCEPT_ID_RETIRED_TISSUE_FIXATIVE = "430864009"
_CONCEPT_ID_NEUTRAL_BUFFERED_FORMALIN = "1388516000"

# A well-formed concept id that does not exist in SNOMED CT.
_CONCEPT_ID_NONEXISTENT = "999999006"

# A malformed concept id. Partition identifier '02' is a relationship
# and its check digit should be 6.
_CONCEPT_ID_MALFORMED = "12710022"

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
        assert concept.concept_id == _CONCEPT_ID_HOMO_SAPIENS
        assert concept.preferred_term == "Homo sapiens"


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_get_preferred_terms():
    service = SnomedService()
    result = await service.get_preferred_terms(
        {_CONCEPT_ID_HOMO_SAPIENS, _CONCEPT_ID_LEFT_BREAST_STRUCTURE}
    )
    assert result[_CONCEPT_ID_HOMO_SAPIENS] == "Homo sapiens"
    assert result[_CONCEPT_ID_LEFT_BREAST_STRUCTURE] == "Left breast structure"


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

    assert await service.replacement_concept_id(_CONCEPT_ID_HOMO_SAPIENS) is None
    assert await service.replacement_concept_id(_CONCEPT_ID_NONEXISTENT) is None


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_get_preferred_terms_unknown_id_omitted():
    service = SnomedService()
    result = await service.get_preferred_terms({_CONCEPT_ID_HOMO_SAPIENS, "000000000"})
    assert _CONCEPT_ID_HOMO_SAPIENS in result
    assert "000000000" not in result


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_get_preferred_terms_nonexistent_concept_id():
    service = SnomedService()

    assert is_well_formed_concept_id(_CONCEPT_ID_NONEXISTENT)
    assert not is_well_formed_concept_id(_CONCEPT_ID_MALFORMED)
    assert await service.get_preferred_terms({_CONCEPT_ID_NONEXISTENT}) == {}


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
    assert concept.concept_id == _CONCEPT_ID_MYOCARDIAL_INFARCTION
    concepts = await service.find_descendants(concept.concept_id)
    assert len(concepts) > 0
    assert _CONCEPT_ID_MYOCARDIAL_INFARCTION not in [c.concept_id for c in concepts]
    assert all(c.concept_id for c in concepts)
    assert all(c.preferred_term for c in concepts)


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_describes_true_for_preferred_term_or_synonym():
    # "Homo sapiens" is 337915000's preferred term, "Human" a synonym of it.
    assert (
        await SnomedService._describes(_CONCEPT_ID_HOMO_SAPIENS, "Homo sapiens") is True
    )
    assert await SnomedService._describes(_CONCEPT_ID_HOMO_SAPIENS, "Human") is True


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_describes_is_case_and_space_insensitive():
    assert (
        await SnomedService._describes(_CONCEPT_ID_HOMO_SAPIENS, "  HOMO   SAPIENS  ")
        is True
    )


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_describes_false_for_a_partial_term():
    # "Formalin" is only part of 1388516000's description "Neutral buffered
    # formalin 10% solution", so it is not one of its descriptions.
    assert (
        await SnomedService._describes(
            _CONCEPT_ID_NEUTRAL_BUFFERED_FORMALIN, "Formalin"
        )
        is False
    )


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_describes_false_for_an_unrelated_value():
    assert (
        await SnomedService._describes(_CONCEPT_ID_HOMO_SAPIENS, "zzz nonsense")
        is False
    )


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_find_concept_short_term():
    service = SnomedService()

    # A term under three characters resolves to nothing.
    assert await service.find_concept("5", ecl=None) is None
    assert await service.find_concept("ab", ecl=None) is None


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_is_within_restriction():
    """A concept is within a field's restriction only in the hierarchy it belongs to."""
    service = SnomedService()
    animal_species = BP_FILTERING_TERM_BY_ID["animal_species"]  # << Organism
    anatomical_site = BP_FILTERING_TERM_BY_ID["anatomical_site"]  # << Body structure

    # Homo sapiens is an organism, Left breast structure a body structure.
    assert (
        await service.is_within_restriction(_CONCEPT_ID_HOMO_SAPIENS, animal_species)
        is True
    )
    assert (
        await service.is_within_restriction(
            _CONCEPT_ID_LEFT_BREAST_STRUCTURE, anatomical_site
        )
        is True
    )

    # Each is outside the other's restriction, which is what a load rejects.
    assert (
        await service.is_within_restriction(
            _CONCEPT_ID_LEFT_BREAST_STRUCTURE, animal_species
        )
        is False
    )
    assert (
        await service.is_within_restriction(_CONCEPT_ID_HOMO_SAPIENS, anatomical_site)
        is False
    )

    # The restriction's own root concept is within it.
    assert (
        await service.is_within_restriction(_CONCEPT_ID_ORGANISM, animal_species)
        is True
    )

    # A concept SNOMED CT does not have is in no hierarchy at all.
    assert (
        await service.is_within_restriction(_CONCEPT_ID_NONEXISTENT, animal_species)
        is False
    )


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_is_within_restriction_of_an_unrestricted_term():
    """A term without a restriction resolves against the whole of SNOMED CT."""
    service = SnomedService()
    unrestricted = BP_FILTERING_TERM_BY_ID["animal_species"].model_copy(
        update={"ontologyRestriction": None}
    )

    assert (
        await service.is_within_restriction(
            _CONCEPT_ID_LEFT_BREAST_STRUCTURE, unrestricted
        )
        is True
    )


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_is_within_restriction_retired_concept():
    """A retired concept is within a restriction that cannot place it.

    ``430864009`` is a retired tissue fixative replaced by ``1388477003``, the very
    concept ``fixation_type`` restricts its values to. Retiring it stripped its
    relationships, so no expression selects it, and it is taken as within the
    restriction rather than outside it.
    """
    service = SnomedService()
    fixation_type = BP_FILTERING_TERM_BY_ID["fixation_type"]

    assert await service.is_retired(_CONCEPT_ID_RETIRED_TISSUE_FIXATIVE) is True
    assert (
        await service.is_within_restriction(
            _CONCEPT_ID_RETIRED_TISSUE_FIXATIVE, fixation_type
        )
        is True
    )


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_is_selected_by_ecl_invalid_ecl():
    with pytest.raises(httpx.HTTPStatusError):
        await _is_selected_by_ecl(_CONCEPT_ID_HOMO_SAPIENS, "<<< not an ecl", "MAIN")
