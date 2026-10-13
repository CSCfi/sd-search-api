from functools import partial

import pytest

from search_api.api.beacon.models import SNOMED_ONTOLOGY_ID
from search_api.api.bigpicture.models import BP_FILTERING_TERM_BY_ID
from search_api.api.models import FieldValue, ValueCounts
from search_api.services import field_values
from search_api.services.field_values import get_field_suggestions, get_field_values

FIELD_ID_SEX = "sex"
FIELD_ID_ANIMAL_SPECIES = "animal_species"
FIELD_ID_FIXATION_TYPE = "fixation_type"

CONCEPT_ID_HOMO_SAPIENS = "337915000"
CONCEPT_ID_SUS_SCROFA = "78678003"
CONCEPT_ID_TISSUE_FIXATIVE = "1388477003"
CONCEPT_ID_WITHOUT_PREFERRED_TERM = "9606"

FILTERING_TERM_SEX = BP_FILTERING_TERM_BY_ID[FIELD_ID_SEX]  # controlledValue
FILTERING_TERM_ANIMAL_SPECIES = BP_FILTERING_TERM_BY_ID[
    FIELD_ID_ANIMAL_SPECIES
]  # ontology
FILTERING_TERM_FIXATION_TYPE = BP_FILTERING_TERM_BY_ID[
    FIELD_ID_FIXATION_TYPE
]  # ontologyOrValue

VALUE_COUNTS = {
    FIELD_ID_SEX: ValueCounts(counts={"Male": 10, "Female": 8}),
    FIELD_ID_ANIMAL_SPECIES: ValueCounts(
        counts={
            CONCEPT_ID_HOMO_SAPIENS: 5,
            CONCEPT_ID_SUS_SCROFA: 3,
            CONCEPT_ID_WITHOUT_PREFERRED_TERM: 1,
        }
    ),
    FIELD_ID_FIXATION_TYPE: ValueCounts(
        counts={CONCEPT_ID_TISSUE_FIXATIVE: 4},
        other_counts={"free_text": 1},
    ),
}

PREFERRED_TERMS = {
    CONCEPT_ID_HOMO_SAPIENS: "Homo sapiens",
    CONCEPT_ID_SUS_SCROFA: "Sus scrofa",
    CONCEPT_ID_TISSUE_FIXATIVE: "Tissue fixative",
}


class MockBeaconService:
    """Returns the value counts defined above, and records the request scopes."""

    def __init__(self) -> None:
        self.scopes: list[str | None] = []

    async def get_value_counts(self, field_id: str, scope=None) -> ValueCounts:
        self.scopes.append(scope)
        return VALUE_COUNTS[field_id]


class MockTermCache:
    """Returns the preferred terms defined above."""

    async def get_terms_by_concept_id(
        self, field_id: str, concept_ids: set[str]
    ) -> dict[str, str]:
        return {c: PREFERRED_TERMS[c] for c in concept_ids if c in PREFERRED_TERMS}


class MockOntologyService:
    """Accepts any text of digits as a concept id."""

    def is_concept_id_prefix(self, value: str) -> bool:
        return value.isdigit()


ONTOLOGY_ID_BY_FIELD = {
    FIELD_ID_ANIMAL_SPECIES: SNOMED_ONTOLOGY_ID,
    FIELD_ID_FIXATION_TYPE: SNOMED_ONTOLOGY_ID,
}
ONTOLOGY_TERM_SERVICES = {SNOMED_ONTOLOGY_ID: MockTermCache()}


@pytest.fixture(autouse=True)
def ontology(monkeypatch) -> None:
    monkeypatch.setattr(
        field_values, "get_ontology_service", lambda _: MockOntologyService()
    )


@pytest.fixture
def beacon_service() -> MockBeaconService:
    return MockBeaconService()


@pytest.fixture
def get_suggestions(beacon_service):
    return partial(
        get_field_suggestions,
        scope=None,
        beacon_service=beacon_service,
        ontology_term_services=ONTOLOGY_TERM_SERVICES,
        ontology_id_by_field=ONTOLOGY_ID_BY_FIELD,
    )


@pytest.fixture
def get_values(beacon_service):
    return partial(
        get_field_values,
        scope=None,
        beacon_service=beacon_service,
        ontology_term_services=ONTOLOGY_TERM_SERVICES,
        ontology_id_by_field=ONTOLOGY_ID_BY_FIELD,
    )


# Suggestions
#


@pytest.mark.asyncio
async def test_get_field_suggestions(get_suggestions):
    assert await get_suggestions(FILTERING_TERM_SEX, "FE") == [
        FieldValue(value="Female", count=8)
    ]
    assert await get_suggestions(FILTERING_TERM_SEX, "ale") == []


@pytest.mark.asyncio
async def test_get_field_suggestions_substring_match(get_suggestions):
    assert await get_suggestions(FILTERING_TERM_SEX, "ale", substring_match=True) == [
        FieldValue(value="Female", count=8),
        FieldValue(value="Male", count=10),
    ]


@pytest.mark.asyncio
async def test_get_field_suggestions_include_controlled_values(get_suggestions):
    assert await get_suggestions(FILTERING_TERM_SEX, "o") == []
    # Other is a controlled value of sex, but no document has it.
    assert await get_suggestions(
        FILTERING_TERM_SEX, "o", include_all_controlled_values=True
    ) == [FieldValue(value="Other", count=0)]


@pytest.mark.asyncio
async def test_get_field_suggestions_match_preferred_term(get_suggestions):
    homo_sapiens = FieldValue(
        value="Homo sapiens", concept_id=CONCEPT_ID_HOMO_SAPIENS, count=5
    )
    assert await get_suggestions(FILTERING_TERM_ANIMAL_SPECIES, "hom") == [homo_sapiens]


@pytest.mark.asyncio
async def test_get_field_suggestions_match_concept_id(get_suggestions):
    homo_sapiens = FieldValue(
        value="Homo sapiens", concept_id=CONCEPT_ID_HOMO_SAPIENS, count=5
    )
    assert await get_suggestions(
        FILTERING_TERM_ANIMAL_SPECIES, CONCEPT_ID_HOMO_SAPIENS[:4]
    ) == [homo_sapiens]
    # An ontologyOrValue field matches the start of its concept ids.
    assert await get_suggestions(
        FILTERING_TERM_FIXATION_TYPE, CONCEPT_ID_TISSUE_FIXATIVE[:4]
    ) == [
        FieldValue(
            value="Tissue fixative", concept_id=CONCEPT_ID_TISSUE_FIXATIVE, count=4
        )
    ]


@pytest.mark.asyncio
async def test_get_field_suggestions_exclude_concept_without_preferred_term(
    get_suggestions,
):
    assert (
        await get_suggestions(
            FILTERING_TERM_ANIMAL_SPECIES,
            CONCEPT_ID_WITHOUT_PREFERRED_TERM,
        )
        == []
    )


@pytest.mark.asyncio
async def test_get_field_suggestions_include_free_text_values(get_suggestions):
    free_text = FieldValue(value="free_text", count=1)
    # fixation_type is an ontologyOrValue field.
    assert await get_suggestions(FILTERING_TERM_FIXATION_TYPE, "free") == [free_text]
    assert (
        await get_suggestions(
            FILTERING_TERM_FIXATION_TYPE,
            "free",
            include_other_ontology_values=False,
        )
        == []
    )


@pytest.mark.asyncio
async def test_get_field_suggestions_scope(get_suggestions, beacon_service):
    await get_suggestions(FILTERING_TERM_SEX, "fe", scope="clinical")
    await get_suggestions(FILTERING_TERM_ANIMAL_SPECIES, "hom", scope="clinical")
    assert beacon_service.scopes == ["clinical", "clinical"]


# Values
#


@pytest.mark.asyncio
async def test_get_field_values_ordered_by_count(get_values):
    assert await get_values(FILTERING_TERM_SEX) == [
        FieldValue(value="Male", count=10),
        FieldValue(value="Female", count=8),
    ]


@pytest.mark.asyncio
async def test_get_field_values_include_all_controlled_values(get_values):
    result = await get_values(FILTERING_TERM_SEX, include_all_controlled_values=True)
    assert {v.value: v.count for v in result} == {
        "Male": 10,
        "Female": 8,
        "Not-known": 0,
        "Other": 0,
    }


@pytest.mark.asyncio
async def test_get_field_values_exclude_concept_without_preferred_term(get_values):
    assert await get_values(FILTERING_TERM_ANIMAL_SPECIES) == [
        FieldValue(value="Homo sapiens", concept_id=CONCEPT_ID_HOMO_SAPIENS, count=5),
        FieldValue(value="Sus scrofa", concept_id=CONCEPT_ID_SUS_SCROFA, count=3),
    ]


@pytest.mark.asyncio
async def test_get_field_values_include_free_text_values(get_values):
    tissue_fixative = FieldValue(
        value="Tissue fixative", concept_id=CONCEPT_ID_TISSUE_FIXATIVE, count=4
    )
    free_text = FieldValue(value="free_text", count=1)
    # fixation_type is an ontologyOrValue field.
    assert await get_values(FILTERING_TERM_FIXATION_TYPE) == [
        tissue_fixative,
        free_text,
    ]
    assert await get_values(
        FILTERING_TERM_FIXATION_TYPE, include_other_ontology_values=False
    ) == [tissue_fixative]


@pytest.mark.asyncio
async def test_get_field_values_scope(get_values, beacon_service):
    await get_values(FILTERING_TERM_SEX, scope="clinical")
    await get_values(FILTERING_TERM_ANIMAL_SPECIES, scope="clinical")
    assert beacon_service.scopes == ["clinical", "clinical"]
