from types import SimpleNamespace

import pytest

# Imported for its side effect: registers the ontology services.
import search_api.services.ontology.registrations  # noqa: F401
from search_api.ai.services import (
    _Deps,
    _replace_with_indexed_values,
    get_values,
)
from search_api.api.beacon.models import SNOMED_ONTOLOGY_ID, BeaconQueryFilter
from search_api.api.bigpicture.models import BP_FILTERING_TERMS
from search_api.api.models import FieldValue, ValueCounts

_FIELD_ID_ANIMAL_SPECIES = "animal_species"
_CONCEPT_ID_HOMO_SAPIENS = "337915000"
_PREFERRED_TERM_HOMO_SAPIENS = "Homo sapiens"
_COUNT_HOMO_SAPIENS = 3
_FIELD_VALUE_HOMO_SAPIENS = FieldValue(
    value=_PREFERRED_TERM_HOMO_SAPIENS,
    concept_id=_CONCEPT_ID_HOMO_SAPIENS,
    count=_COUNT_HOMO_SAPIENS,
)


class MockBeaconService:
    async def get_value_counts(self, field_id: str, scope=None) -> ValueCounts:
        return ValueCounts(counts={_CONCEPT_ID_HOMO_SAPIENS: _COUNT_HOMO_SAPIENS})


class MockTermCache:
    async def get_terms_by_concept_id(
        self, field_id: str, concept_ids: set[str]
    ) -> dict[str, str]:
        return {
            c: _PREFERRED_TERM_HOMO_SAPIENS
            for c in concept_ids
            if c == _CONCEPT_ID_HOMO_SAPIENS
        }

    async def get_concept_ids_by_term(self, field_id: str, term: str) -> set[str]:
        if term.lower() == _PREFERRED_TERM_HOMO_SAPIENS.lower():
            return {_CONCEPT_ID_HOMO_SAPIENS}
        return set()


@pytest.fixture
def deps() -> _Deps:
    return _Deps(
        BP_FILTERING_TERMS,
        None,
        MockBeaconService(),
        {SNOMED_ONTOLOGY_ID: MockTermCache()},
        {_FIELD_ID_ANIMAL_SPECIES: SNOMED_ONTOLOGY_ID},
    )


async def _replace(deps: _Deps, value: str, include_descendants: bool):
    return await _replace_with_indexed_values(
        deps,
        [
            BeaconQueryFilter(
                id=_FIELD_ID_ANIMAL_SPECIES,
                value=value,
                includeDescendantTerms=include_descendants,
            )
        ],
    )


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_replace_with_indexed_values_ontology_synonym(deps):
    """Replace an ontology synonym with concept id."""
    filters, errors = await _replace(deps, "human", include_descendants=False)
    assert errors == []
    assert filters[0].value == [_CONCEPT_ID_HOMO_SAPIENS]


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_replace_with_indexed_values_ontology_descendant(deps):
    """Find descendant for an ontology value."""
    filters, errors = await _replace(deps, "Mammal", include_descendants=True)
    assert errors == []
    # Homo sapiens is a descendant of the search value and listed by /values.
    assert filters[0].value == [_CONCEPT_ID_HOMO_SAPIENS]


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_replace_with_indexed_values_ontology_not_indexed(deps):
    _, [error] = await _replace(deps, "Mammal", include_descendants=False)
    assert "'Mammal'" in error


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_get_values_ontology_synonym(deps):
    tool_context = SimpleNamespace(deps=deps)
    assert await get_values(tool_context, _FIELD_ID_ANIMAL_SPECIES, "human") == [
        _FIELD_VALUE_HOMO_SAPIENS
    ]


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_get_values_ontology_descendant(deps):
    tool_context = SimpleNamespace(deps=deps)
    assert await get_values(tool_context, _FIELD_ID_ANIMAL_SPECIES, "Mammal", True) == [
        _FIELD_VALUE_HOMO_SAPIENS
    ]
