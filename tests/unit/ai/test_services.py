"""Unit tests for the AI service, which recommends filters for a natural language query.

These tests were written by Claude Opus 5.5. Rasko Leinonen directed the work and
questioned it test by test: asking what each test and comment meant, challenging
duplicated code and unclear names, and having the tests consolidated, renamed and
explained until they read clearly. That was not a thorough review, and the tests
have not been thoroughly reviewed by a human.
"""

from types import SimpleNamespace
from typing import override

import pytest
import pytest_asyncio
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from search_api.ai import services as ai_services
from search_api.ai.services import (
    AIService,
    _Deps,
    _replace_with_indexed_values,
    get_values,
    validate_filters,
)
from search_api.api.beacon.models import (
    BeaconFilteringOntology,
    BeaconFilteringTerm,
    BeaconQueryFilter,
)
from search_api.api.models import FieldValue, ValueCounts
from search_api.exceptions import SystemException
from search_api.services import field_values
from search_api.services.ontology.cache.models import (
    CachedOntology,
    CachedOntologyConcept,
)
from search_api.services.ontology.cache.service import CachedOntologyService
from search_api.services.ontology.cache.store import OntologyCacheStore


# Test data and mocks.
#


def _term(
    id: str, type: str, scopes: tuple[str, ...] = (), **kwargs
) -> BeaconFilteringTerm:
    return BeaconFilteringTerm(
        id=id, type=type, scopes=list(scopes), label=id, description=id, **kwargs
    )


TERMS = [
    _term("sex", "controlledValue", controlledValues=["Male", "Female"]),
    _term("age", "iso8601Range"),
    _term("title", "text"),
    _term("diagnosis", "ontology", ontology=BeaconFilteringOntology(id="SCTID")),
]


# The ontology the AI resolves values in.

ONTOLOGY_ID = "TEST"

CONCEPT_ID_NEOPLASM = "C1"
PREFERRED_TERM_NEOPLASM = "Neoplasm"

CONCEPT_ID_DUCTAL_CARCINOMA = "C2"
PREFERRED_TERM_DUCTAL_CARCINOMA = "Ductal carcinoma"
SYNONYM_DUCTAL_CARCINOMA = "Ductal cancer"

CONCEPT_ID_LOBULAR_CARCINOMA = "C3"
PREFERRED_TERM_LOBULAR_CARCINOMA = "Lobular carcinoma"

ONTOLOGY = CachedOntology(
    version="1",
    sha256="",
    concepts=[
        CachedOntologyConcept(
            concept_id=CONCEPT_ID_NEOPLASM, preferred_term=PREFERRED_TERM_NEOPLASM
        ),
        CachedOntologyConcept(
            concept_id=CONCEPT_ID_DUCTAL_CARCINOMA,
            preferred_term=PREFERRED_TERM_DUCTAL_CARCINOMA,
            synonyms=frozenset({SYNONYM_DUCTAL_CARCINOMA}),
            parent_ids=frozenset({CONCEPT_ID_NEOPLASM}),
        ),
        CachedOntologyConcept(
            concept_id=CONCEPT_ID_LOBULAR_CARCINOMA,
            preferred_term=PREFERRED_TERM_LOBULAR_CARCINOMA,
            parent_ids=frozenset({CONCEPT_ID_NEOPLASM}),
        ),
    ],
)


class MockOntologyCacheStore(OntologyCacheStore):
    """Holds the ontology in memory, in place of the database table."""

    def __init__(self) -> None:
        super().__init__(ONTOLOGY_ID)

    @override
    async def read(self) -> CachedOntology:
        return ONTOLOGY

    @override
    async def updated_at(self) -> None:
        return None


# A concept id the ontology does not have.
CONCEPT_ID_UNKNOWN = "C9"


# The fields.

FIELD_ID_DIAGNOSIS = "diagnosis"
FIELD_ID_FINDING = "finding"
FILTERING_ONTOLOGY = BeaconFilteringOntology(id=ONTOLOGY_ID)
ONTOLOGY_FILTERING_TERMS = [
    _term(
        FIELD_ID_DIAGNOSIS,
        "ontology",
        scopes=("clinical",),
        ontology=FILTERING_ONTOLOGY,
    ),
    _term(
        FIELD_ID_FINDING,
        "ontologyOrValue",
        scopes=("clinical",),
        ontology=FILTERING_ONTOLOGY,
    ),
]
FILTERING_TERMS = [
    *ONTOLOGY_FILTERING_TERMS,
    _term(
        "sex",
        "controlledValue",
        scopes=("clinical", "non_clinical"),
        controlledValues=["Male", "Female"],
    ),
    _term("image_id", "keyword", scopes=("clinical", "non_clinical")),
]


# The index. Neoplasm is not in it, but its descendants are. Its name is not
# part of theirs, so only the ontology finds them from it.

# The number of documents with each diagnosis.
COUNT_DUCTAL_CARCINOMA = 5
COUNT_LOBULAR_CARCINOMA = 2

VALUE_COUNTS = {
    FIELD_ID_DIAGNOSIS: ValueCounts(
        counts={
            CONCEPT_ID_DUCTAL_CARCINOMA: COUNT_DUCTAL_CARCINOMA,
            CONCEPT_ID_LOBULAR_CARCINOMA: COUNT_LOBULAR_CARCINOMA,
        }
    ),
    FIELD_ID_FINDING: ValueCounts(
        counts={CONCEPT_ID_DUCTAL_CARCINOMA: 1},
        other_counts={"atypical cells": 4},
    ),
    "sex": ValueCounts(counts={"Female": 3}),
    "image_id": ValueCounts(counts={"img1": 1}),
}

# The values /values lists for diagnosis.
FIELD_VALUE_DUCTAL_CARCINOMA = FieldValue(
    value=PREFERRED_TERM_DUCTAL_CARCINOMA,
    concept_id=CONCEPT_ID_DUCTAL_CARCINOMA,
    count=COUNT_DUCTAL_CARCINOMA,
)
FIELD_VALUE_LOBULAR_CARCINOMA = FieldValue(
    value=PREFERRED_TERM_LOBULAR_CARCINOMA,
    concept_id=CONCEPT_ID_LOBULAR_CARCINOMA,
    count=COUNT_LOBULAR_CARCINOMA,
)


class MockBeaconService:
    def __init__(self) -> None:
        self.scopes: list[str | None] = []

    async def get_value_counts(self, field_id: str, scope=None) -> ValueCounts:
        self.scopes.append(scope)
        return VALUE_COUNTS[field_id]


class MockTermCache:
    async def get_terms_by_concept_id(
        self, field_id: str, concept_ids: set[str]
    ) -> dict[str, str]:
        return {
            c.concept_id: c.preferred_term
            for c in ONTOLOGY.concepts
            if c.concept_id in concept_ids
        }

    async def get_concept_ids_by_term(self, field_id: str, term: str) -> set[str]:
        return {
            c.concept_id
            for c in ONTOLOGY.concepts
            if c.preferred_term.lower() == term.lower()
        }


TERM_CACHES = {ONTOLOGY_ID: MockTermCache()}


class _MockLLM:
    """Stands in for the LLM, so AIService can be tested without one.

    A real LLM reads the user's query and chooses filters. This one ignores the
    query and answers with the filters it was created with. When its answer is
    rejected, it answers with the next filters it was given, if any.

    Before answering, it asks AIService which fields it may filter on, as a real
    LLM does. It keeps their ids in offered_fields, so a test can check them.
    Given find, it then calls get_values with it, and keeps the reply in found.
    """

    def __init__(
        self, filters: list[dict], *retry_filters: list[dict], find: dict | None = None
    ) -> None:
        self.answers = [filters, *retry_filters]
        self.find = find
        self.offered_fields: list[str] = []
        self.found: object = None
        # pydantic-ai calls _respond wherever it would call the real LLM.
        self.model = FunctionModel(self._respond)

    def _respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        """Reply to the conversation so far.

        pydantic-ai calls this again after every reply, each time with the whole
        conversation. A reply is one of two things:

        - A call to a tool. pydantic-ai runs the tool, adds its result to the
          conversation, and calls this again.
        - The final answer. This is a call to a tool pydantic-ai makes for the
          purpose, named in info.output_tools. If AIService's validator rejects the
          answer, the error is added to the conversation and this is called again.
        """
        # The results of the tools called so far.
        tool_returns = [
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]

        # First call: no tool has run yet, so ask which fields there are.
        if not tool_returns:
            return ModelResponse(parts=[ToolCallPart("get_filtering_terms", {})])

        # Later calls: get_filtering_terms has answered. Its reply is one line per
        # field, "field_id" or "field_id: allowed values". Keep the field ids.
        fields, *found = tool_returns
        self.offered_fields = [line.split(":")[0] for line in fields.splitlines()]

        # Then look for values, if asked to.
        if self.find is not None and not found:
            return ModelResponse(parts=[ToolCallPart("get_values", self.find)])
        if found:
            [self.found] = found

        # Then give the final answer, the next one after each rejected answer.
        rejected = sum(
            isinstance(part, RetryPromptPart)
            for message in messages
            for part in message.parts
        )
        filters = self.answers[min(rejected, len(self.answers) - 1)]
        answer = {"interpretation": "i", "filters": filters}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, answer)])


# Test validate_filters.
#


def test_validate_filters_accepts_valid_filters():
    filters = [
        BeaconQueryFilter(id="sex", value=["Male", "Female"]),
        BeaconQueryFilter(id="age", value="P40Y"),
        BeaconQueryFilter(id="age", value="P40Y-P60Y"),
        BeaconQueryFilter(id="title", value="anything"),
        BeaconQueryFilter(
            id="diagnosis", value="carcinoma", includeDescendantTerms=True
        ),
    ]
    assert validate_filters(filters, TERMS) == []


def test_validate_filters_rejects_unknown_field():
    [error] = validate_filters([BeaconQueryFilter(id="colour", value="red")], TERMS)
    assert "'colour'" in error


def test_validate_filters_rejects_value_not_controlled():
    [error] = validate_filters(
        [BeaconQueryFilter(id="sex", value=["Female", "female"])], TERMS
    )
    assert "'female'" in error
    assert "Male | Female" in error


def test_validate_filters_rejects_malformed_duration():
    assert len(validate_filters([BeaconQueryFilter(id="age", value="40")], TERMS)) == 1
    assert (
        len(validate_filters([BeaconQueryFilter(id="age", value="P40Y-sixty")], TERMS))
        == 1
    )


def test_validate_filters_rejects_descendants_outside_ontology():
    [error] = validate_filters(
        [BeaconQueryFilter(id="title", value="x", includeDescendantTerms=True)], TERMS
    )
    assert "'title'" in error


# Fixtures shared by the tests below.
#


@pytest.fixture(autouse=True)
def llm_config(monkeypatch) -> None:
    """The LLM settings an AIService needs to be built. No LLM is called."""
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost/v1")
    monkeypatch.setenv("LLM_API_KEY", "test")


@pytest_asyncio.fixture
async def ontology(monkeypatch) -> CachedOntologyService:
    """The ontology every field uses: a real one, held in memory.

    It is loaded from the mock store, so its source is never needed.
    """
    service = CachedOntologyService(MockOntologyCacheStore(), None, r"C\d+", r"C\d*")
    await service.init()
    monkeypatch.setattr(ai_services, "get_ontology_service", lambda _: service)
    monkeypatch.setattr(field_values, "get_ontology_service", lambda _: service)
    return service


@pytest.fixture
def beacon_service() -> MockBeaconService:
    return MockBeaconService()


@pytest.fixture
def deps(ontology, beacon_service) -> _Deps:
    return _Deps(
        FILTERING_TERMS,
        "clinical",
        beacon_service,
        TERM_CACHES,
        {FIELD_ID_DIAGNOSIS: ONTOLOGY_ID, FIELD_ID_FINDING: ONTOLOGY_ID},
    )


@pytest.fixture
def ai_service(ontology) -> AIService:
    return AIService(FILTERING_TERMS, "a test assistant")


# Test interpret: the fields the model is shown.
#


@pytest.mark.parametrize(
    "scope,offered_fields",
    [
        (None, [FIELD_ID_DIAGNOSIS, FIELD_ID_FINDING, "sex", "image_id"]),
        ("non_clinical", ["sex", "image_id"]),
    ],
)
@pytest.mark.asyncio
async def test_interpret_uses_only_the_scope(
    ai_service, beacon_service, scope, offered_fields
):
    # The model is shown only the fields in the scope, or every field when no
    # scope is given. The value it answers with is then looked up in the index
    # for that same scope.
    llm = _MockLLM([{"id": "image_id", "value": "img1"}])
    with ai_service._agent.override(model=llm.model):
        await ai_service.interpret("image img1", beacon_service, TERM_CACHES, scope)
    assert llm.offered_fields == offered_fields
    assert beacon_service.scopes == [scope]


@pytest.mark.asyncio
async def test_interpret_rejects_field_outside_scope(ai_service, beacon_service):
    # Diagnosis is a clinical field, so the non-clinical scope hides it. The
    # model keeps answering with it anyway, every answer is rejected, and once
    # its retries run out, interpret fails.
    llm = _MockLLM([{"id": FIELD_ID_DIAGNOSIS, "value": CONCEPT_ID_DUCTAL_CARCINOMA}])
    with ai_service._agent.override(model=llm.model):
        with pytest.raises(SystemException):
            await ai_service.interpret(
                "carcinoma", beacon_service, TERM_CACHES, scope="non_clinical"
            )


@pytest.mark.asyncio
async def test_interpret_raises_system_exception_when_model_fails(
    ai_service, beacon_service
):
    # The model cannot be reached. interpret fails with a SystemException, which
    # the route turns into a 503.
    def fail(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ConnectionError("LLM is down.")

    with ai_service._agent.override(model=FunctionModel(fail)):
        with pytest.raises(SystemException):
            await ai_service.interpret("anything", beacon_service, TERM_CACHES)


# Test _replace_with_indexed_values.
#


async def _replace(deps: _Deps, *filters: BeaconQueryFilter):
    return await _replace_with_indexed_values(deps, list(filters))


@pytest.mark.asyncio
async def test_replace_names_with_concept_ids(deps):
    # Each kind of name the model may give is replaced by the concept id it
    # names: a synonym and a preferred term in any case on the diagnosis field,
    # and a concept id on the finding field.
    filters, errors = await _replace(
        deps,
        BeaconQueryFilter(
            id=FIELD_ID_DIAGNOSIS, value=[SYNONYM_DUCTAL_CARCINOMA, "lobular CARCINOMA"]
        ),
        BeaconQueryFilter(id=FIELD_ID_FINDING, value=CONCEPT_ID_DUCTAL_CARCINOMA),
    )
    assert errors == []
    assert filters == [
        BeaconQueryFilter(
            id=FIELD_ID_DIAGNOSIS,
            value=[CONCEPT_ID_DUCTAL_CARCINOMA, CONCEPT_ID_LOBULAR_CARCINOMA],
        ),
        BeaconQueryFilter(id=FIELD_ID_FINDING, value=[CONCEPT_ID_DUCTAL_CARCINOMA]),
    ]


@pytest.mark.asyncio
async def test_replace_concept_with_listed_descendants(deps):
    # With includeDescendantTerms, Neoplasm is replaced by its descendants that
    # /values lists, since no document has Neoplasm itself.
    filters, errors = await _replace(
        deps,
        BeaconQueryFilter(
            id=FIELD_ID_DIAGNOSIS,
            value=PREFERRED_TERM_NEOPLASM,
            includeDescendantTerms=True,
        ),
    )
    assert errors == []
    assert filters == [
        BeaconQueryFilter(
            id=FIELD_ID_DIAGNOSIS,
            value=[CONCEPT_ID_DUCTAL_CARCINOMA, CONCEPT_ID_LOBULAR_CARCINOMA],
            includeDescendantTerms=True,
        )
    ]


@pytest.mark.asyncio
async def test_replace_reports_values_matching_nothing(deps):
    # A value that matches no indexed field value gets an error, which is sent
    # back to the model. With includeDescendantTerms, the error says that none
    # of its descendants matched either.
    _, [neoplasm_error, unknown_error] = await _replace(
        deps,
        # Neoplasm is a concept, but no document has it.
        BeaconQueryFilter(id=FIELD_ID_DIAGNOSIS, value=CONCEPT_ID_NEOPLASM),
        # The ontology has no concept with this id, so it has no descendants.
        BeaconQueryFilter(
            id=FIELD_ID_DIAGNOSIS, value=CONCEPT_ID_UNKNOWN, includeDescendantTerms=True
        ),
    )
    assert f"'{CONCEPT_ID_NEOPLASM}'. Use get_values" in neoplasm_error
    assert f"'{CONCEPT_ID_UNKNOWN}' or its descendants" in unknown_error


@pytest.mark.asyncio
async def test_replace_free_text(deps):
    # On an ontologyOrValue field, free text is kept if /values lists it, and
    # reported if it does not.
    filters, [error] = await _replace(
        deps,
        BeaconQueryFilter(
            id=FIELD_ID_FINDING, value=["atypical cells", "abnormal cells"]
        ),
    )
    assert filters == [BeaconQueryFilter(id=FIELD_ID_FINDING, value=["atypical cells"])]
    assert "'abnormal cells'" in error


@pytest.mark.asyncio
async def test_replace_keywords_and_keeps_controlled_values(deps):
    # A keyword is kept only if /values lists it. A controlled value is not
    # looked up: validate_filters has already checked it against the field's
    # declared values, so its filter is kept as it is.
    filters, [error] = await _replace(
        deps,
        BeaconQueryFilter(id="image_id", value=["img1", "img2"]),
        BeaconQueryFilter(id="sex", value="Male"),
    )
    assert filters == [
        BeaconQueryFilter(id="image_id", value=["img1"]),
        BeaconQueryFilter(id="sex", value="Male"),
    ]
    assert "'img2'" in error


# Test get_values.
#


async def _get_values(deps: _Deps, field_id: str, text: str, include_descendants=False):
    return await get_values(
        SimpleNamespace(deps=deps), field_id, text, include_descendants
    )


@pytest.mark.asyncio
async def test_get_values_lists_suggestions(deps):
    # get_values lists what /suggestions finds. First, text anywhere in a
    # preferred term.
    assert await _get_values(deps, FIELD_ID_DIAGNOSIS, "carcinoma") == [
        FIELD_VALUE_DUCTAL_CARCINOMA,
        FIELD_VALUE_LOBULAR_CARCINOMA,
    ]
    assert await _get_values(deps, FIELD_ID_DIAGNOSIS, "ductal carc") == [
        FIELD_VALUE_DUCTAL_CARCINOMA
    ]
    # Then the start of a concept id. The ontology finds this concept too, but
    # it is listed only once.
    assert await _get_values(
        deps, FIELD_ID_DIAGNOSIS, CONCEPT_ID_LOBULAR_CARCINOMA
    ) == [FIELD_VALUE_LOBULAR_CARCINOMA]
    # And free text, on an ontologyOrValue field.
    assert await _get_values(deps, FIELD_ID_FINDING, "atypical") == [
        FieldValue(value="atypical cells", count=4)
    ]


@pytest.mark.asyncio
async def test_get_values_lists_values_from_the_ontology(deps):
    # get_values also looks values up by an exact match of a synonym, which
    # /suggestions does not search. "Ductal cancer" is a synonym of ductal
    # carcinoma.
    assert await _get_values(deps, FIELD_ID_DIAGNOSIS, SYNONYM_DUCTAL_CARCINOMA) == [
        FIELD_VALUE_DUCTAL_CARCINOMA
    ]
    # No document has Neoplasm, so on its own it finds nothing. With
    # include_descendants, it finds its descendants that documents have.
    assert await _get_values(deps, FIELD_ID_DIAGNOSIS, PREFERRED_TERM_NEOPLASM) == []
    assert await _get_values(
        deps, FIELD_ID_DIAGNOSIS, PREFERRED_TERM_NEOPLASM, include_descendants=True
    ) == [FIELD_VALUE_DUCTAL_CARCINOMA, FIELD_VALUE_LOBULAR_CARCINOMA]


@pytest.mark.asyncio
async def test_get_values_refuses_fields_it_cannot_look_up(deps):
    # The model is told why, so it can correct itself: a controlled value
    # field's values are all listed by get_filtering_terms already, and an
    # unknown field does not exist.
    assert await _get_values(deps, "sex", "Female") == (
        "Field 'sex' has no values to find. "
        "Give it a value as get_filtering_terms describes."
    )
    assert await _get_values(deps, "colour", "red") == "Unknown field: 'colour'."


# Test interpret: the get_values tool and the check of the model's answer.
#


@pytest.mark.asyncio
async def test_interpret_offers_get_values(ai_service, beacon_service):
    # The model looks up diagnoses with get_values, then answers with one of
    # them. The answer comes back with the value replaced by its concept id.
    llm = _MockLLM(
        [{"id": FIELD_ID_DIAGNOSIS, "value": "Ductal carcinoma"}],
        find={"field_id": FIELD_ID_DIAGNOSIS, "text": "carcinoma"},
    )
    with ai_service._agent.override(model=llm.model):
        result = await ai_service.interpret("carcinoma", beacon_service, TERM_CACHES)
    assert llm.found == [FIELD_VALUE_DUCTAL_CARCINOMA, FIELD_VALUE_LOBULAR_CARCINOMA]
    assert result.filters == [
        BeaconQueryFilter(id=FIELD_ID_DIAGNOSIS, value=[CONCEPT_ID_DUCTAL_CARCINOMA])
    ]


@pytest.mark.asyncio
async def test_interpret_retries_value_not_indexed(ai_service, beacon_service):
    # The model first answers with a diagnosis no document has. That answer is
    # sent back to it, and its second answer, which documents have, is accepted.
    llm = _MockLLM(
        [{"id": FIELD_ID_DIAGNOSIS, "value": "Carcinoma"}],
        [{"id": FIELD_ID_DIAGNOSIS, "value": "Lobular carcinoma"}],
    )
    with ai_service._agent.override(model=llm.model):
        result = await ai_service.interpret("carcinoma", beacon_service, TERM_CACHES)
    assert result.filters == [
        BeaconQueryFilter(id=FIELD_ID_DIAGNOSIS, value=[CONCEPT_ID_LOBULAR_CARCINOMA])
    ]
