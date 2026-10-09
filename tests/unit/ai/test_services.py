"""Unit tests for the AI service, which recommends filters for a natural language query.

These tests were written by Claude Opus 5.5. Rasko Leinonen directed the work and
questioned it test by test: asking what each test and comment meant, challenging
duplicated code and unclear names, and having the tests consolidated, renamed and
explained until they read clearly. That was not a thorough review, and the tests
have not been thoroughly reviewed by a human.
"""

import copy
from types import SimpleNamespace
from typing import override

import pytest
import pytest_asyncio
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
)
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from search_api.ai import services as ai_services
from search_api.ai.models import AIFilterableField
from search_api.ai.services import (
    _NO_VALID_ANSWER,
    AIService,
    _Deps,
    _replace_with_indexed_values,
    get_filtering_terms,
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
    id: str,
    type: str,
    scopes: tuple[str, ...] = (),
    label: str | None = None,
    **kwargs,
) -> BeaconFilteringTerm:
    return BeaconFilteringTerm(
        id=id,
        type=type,
        scopes=list(scopes),
        label=label or id.capitalize(),
        description=id,
        **kwargs,
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

# The model knows each field only by its label. Most labels here differ from
# their ids, so a test shows the label was mapped to the id.
FIELD_ID_DIAGNOSIS = "diagnosis"
LABEL_DIAGNOSIS = "Diagnosed disease"
FIELD_ID_FINDING = "finding"
LABEL_FINDING = "Observed finding"
LABEL_SEX = "Sex"
LABEL_IMAGE_ID = "Image"
FILTERING_ONTOLOGY = BeaconFilteringOntology(id=ONTOLOGY_ID)
ONTOLOGY_FILTERING_TERMS = [
    _term(
        FIELD_ID_DIAGNOSIS,
        "ontology",
        scopes=("clinical",),
        label=LABEL_DIAGNOSIS,
        ontology=FILTERING_ONTOLOGY,
    ),
    _term(
        FIELD_ID_FINDING,
        "ontologyOrValue",
        scopes=("clinical",),
        label=LABEL_FINDING,
        ontology=FILTERING_ONTOLOGY,
    ),
]
ONTOLOGY_ID_BY_FIELD = {FIELD_ID_DIAGNOSIS: ONTOLOGY_ID, FIELD_ID_FINDING: ONTOLOGY_ID}
FILTERING_TERMS = [
    *ONTOLOGY_FILTERING_TERMS,
    _term(
        "sex",
        "controlledValue",
        scopes=("clinical", "non_clinical"),
        label=LABEL_SEX,
        controlledValues=["Male", "Female"],
    ),
    _term(
        "image_id", "keyword", scopes=("clinical", "non_clinical"), label=LABEL_IMAGE_ID
    ),
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
    LLM does. It keeps their labels in offered_fields, so a test can check them.
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
          purpose, named in info.output_tools. If AIService rejects the answer, the
          error is added to the conversation and this is called again.
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

        # Later calls: get_filtering_terms has answered with the fields. Keep
        # their labels.
        fields, *found = tool_returns
        self.offered_fields = [field.field for field in fields]

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


def _model_answering(answer: ModelResponse | dict | Exception) -> FunctionModel:
    """A model that gives the same answer every time, or raises the error.

    A dict is the arguments of a call to the tool pydantic-ai gives the model
    to answer with.
    """

    def respond(_: object, info: AgentInfo) -> ModelResponse:
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, dict):
            # The tool's name is pydantic-ai's, as _MockLLM reads it.
            tool_name = info.output_tools[0].name
            return ModelResponse(parts=[ToolCallPart(tool_name, answer)])
        return copy.deepcopy(answer)

    return FunctionModel(respond)


# The model's filters as plain text. pydantic-ai asks the model to answer by
# calling a tool named "final_result", with the filters as its arguments, but
# small models sometimes write their answer as text instead.
FILTERS_IN_PLAIN_TEXT_RESPONSE = ModelResponse(
    parts=[TextPart("Interpretation: none. Filters: []")]
)

# The model's filters in a call to "final_result", but with arguments that do
# not fit the answer.
MALFORMED_FILTERS_ARGUMENTS = {"filters": "none"}


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
    # The error names the field by its label, as the model knows it.
    [error] = validate_filters(
        [BeaconQueryFilter(id="title", value="x", includeDescendantTerms=True)], TERMS
    )
    assert "'Title'" in error


# Fixtures shared by the tests below.
#


@pytest.fixture(autouse=True)
def llm_config(monkeypatch) -> None:
    """The LLM settings an AIService needs to be built. No LLM is called."""
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost/v1")
    monkeypatch.setenv("LLM_API_KEY", "test")
    monkeypatch.setenv("LLM_MODEL", "test")


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
        ONTOLOGY_ID_BY_FIELD,
    )


@pytest.fixture
def ai_service(ontology) -> AIService:
    return AIService(FILTERING_TERMS, "a test assistant", ONTOLOGY_ID_BY_FIELD)


def test_ai_service_rejects_fields_sharing_a_label():
    # The model names fields by their labels, so each must name one field.
    terms = [_term("title", "text"), _term("name", "text", label="title")]
    with pytest.raises(ValueError, match="title"):
        AIService(terms, "a test assistant", {})


# Test interpret: the fields the model is shown.
#


@pytest.mark.parametrize(
    "scope,offered_fields",
    [
        (None, [LABEL_DIAGNOSIS, LABEL_FINDING, LABEL_SEX, LABEL_IMAGE_ID]),
        ("non_clinical", [LABEL_SEX, LABEL_IMAGE_ID]),
    ],
)
@pytest.mark.asyncio
async def test_interpret_uses_only_the_scope(
    ai_service, beacon_service, scope, offered_fields
):
    # The model is shown only the fields in the scope, or every field when no
    # scope is given. The value it answers with is then looked up in the index
    # for that same scope.
    llm = _MockLLM([{"field": LABEL_IMAGE_ID, "value": "img1"}])
    with ai_service._agent.override(model=llm.model):
        await ai_service.interpret("image img1", beacon_service, TERM_CACHES, scope)
    assert llm.offered_fields == offered_fields
    assert beacon_service.scopes == [scope]


@pytest.mark.asyncio
async def test_interpret_maps_labels_to_field_ids(ai_service, beacon_service):
    # The model names each field by its label, in any letter case and with
    # spaces around it. The filters returned name each field by its id. An id
    # the model gives is accepted too.
    llm = _MockLLM(
        [
            {"field": f" {LABEL_IMAGE_ID.upper()} ", "value": "img1"},
            {"field": "sex", "value": "Female"},
        ]
    )
    with ai_service._agent.override(model=llm.model):
        result = await ai_service.interpret("image img1", beacon_service, TERM_CACHES)
    assert result.filters == [
        BeaconQueryFilter(id="image_id", value=["img1"]),
        BeaconQueryFilter(id="sex", value="Female"),
    ]


@pytest.mark.asyncio
async def test_interpret_rejects_field_outside_scope(ai_service, beacon_service):
    # Diagnosis is a clinical field, so the non-clinical scope hides it. The
    # model keeps answering with it anyway, and every answer is rejected. Once
    # its retries run out, interpret says the query could not be turned into
    # filters.
    llm = _MockLLM([{"field": LABEL_DIAGNOSIS, "value": CONCEPT_ID_DUCTAL_CARCINOMA}])
    with ai_service._agent.override(model=llm.model):
        result = await ai_service.interpret(
            "carcinoma", beacon_service, TERM_CACHES, scope="non_clinical"
        )
    assert result.filters == []
    assert result.interpretation == _NO_VALID_ANSWER


@pytest.mark.parametrize(
    "answer",
    [
        FILTERS_IN_PLAIN_TEXT_RESPONSE,
        MALFORMED_FILTERS_ARGUMENTS,
        UnexpectedModelBehavior("Invalid response from chat completions endpoint"),
    ],
)
@pytest.mark.asyncio
async def test_interpret_returns_no_filters_without_valid_answer(
    ai_service, beacon_service, answer
):
    # Small models sometimes answer in plain text, or with arguments that do
    # not fit the answer. interpret then returns no filters and an explanation,
    # which the route returns with a 200, so the client can tell it from a 503.
    with ai_service._agent.override(model=_model_answering(answer)):
        result = await ai_service.interpret("anything", beacon_service, TERM_CACHES)
    assert result.filters == []
    assert result.interpretation == _NO_VALID_ANSWER


@pytest.mark.parametrize(
    "error",
    [
        # The model's server cannot be reached.
        ModelAPIError("test", "Connection error."),
        # The model is missing.
        ModelHTTPError(404, "test", "model not found"),
    ],
)
@pytest.mark.asyncio
async def test_interpret_raises_system_exception_when_model_fails(
    ai_service, beacon_service, error
):
    # interpret fails with a SystemException, which the route turns into a 503.
    with ai_service._agent.override(model=_model_answering(error)):
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
    # of its descendants matched. If no values is given, an error is sent back
    # to the model.
    _, [neoplasm_error, unknown_error, empty_error] = await _replace(
        deps,
        # Neoplasm is a concept, but no document has it.
        BeaconQueryFilter(id=FIELD_ID_DIAGNOSIS, value=CONCEPT_ID_NEOPLASM),
        # The ontology has no concept with this id, so it has no descendants.
        BeaconQueryFilter(
            id=FIELD_ID_DIAGNOSIS, value=CONCEPT_ID_UNKNOWN, includeDescendantTerms=True
        ),
        BeaconQueryFilter(id="image_id", value=[]),
    )
    assert f"'{CONCEPT_ID_NEOPLASM}'. Use get_values" in neoplasm_error
    assert f"'{CONCEPT_ID_UNKNOWN}' or its descendants" in unknown_error
    assert empty_error == (
        f"Field '{LABEL_IMAGE_ID}' has no value. "
        "Use get_values to find one. If get_values finds none, leave the field out."
    )


@pytest.mark.asyncio
async def test_replace_deduplicate(deps):
    filters, errors = await _replace(
        deps,
        BeaconQueryFilter(
            id=FIELD_ID_DIAGNOSIS,
            value=[PREFERRED_TERM_NEOPLASM, PREFERRED_TERM_DUCTAL_CARCINOMA],
            includeDescendantTerms=True,
        ),
    )
    assert errors == []
    assert filters[0].value == [
        CONCEPT_ID_DUCTAL_CARCINOMA,
        CONCEPT_ID_LOBULAR_CARCINOMA,
    ]


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


# Test get_filtering_terms.
#


def test_get_filtering_terms_lists_what_each_field_accepts():
    ctx = SimpleNamespace(deps=SimpleNamespace(filtering_terms=TERMS))
    assert get_filtering_terms(ctx) == [
        AIFilterableField(field="Sex", accepts="values", values=["Male", "Female"]),
        AIFilterableField(field="Age", accepts="duration"),
        AIFilterableField(field="Title", accepts="text"),
        AIFilterableField(field="Diagnosis", accepts="get_values"),
    ]


# Test get_values.
#


async def _get_values(deps: _Deps, field: str, text: str, include_descendants=False):
    return await get_values(
        SimpleNamespace(deps=deps), field, text, include_descendants
    )


@pytest.mark.asyncio
async def test_get_values_lists_suggestions(deps):
    # get_values lists what /suggestions finds. First, text anywhere in a
    # preferred term.
    assert await _get_values(deps, LABEL_DIAGNOSIS, "carcinoma") == [
        FIELD_VALUE_DUCTAL_CARCINOMA,
        FIELD_VALUE_LOBULAR_CARCINOMA,
    ]
    assert await _get_values(deps, LABEL_DIAGNOSIS, "ductal carc") == [
        FIELD_VALUE_DUCTAL_CARCINOMA
    ]
    # Then the start of a concept id. The ontology finds this concept too, but
    # it is listed only once.
    assert await _get_values(deps, LABEL_DIAGNOSIS, CONCEPT_ID_LOBULAR_CARCINOMA) == [
        FIELD_VALUE_LOBULAR_CARCINOMA
    ]
    # And free text, on an ontologyOrValue field.
    assert await _get_values(deps, LABEL_FINDING, "atypical") == [
        FieldValue(value="atypical cells", count=4)
    ]


@pytest.mark.asyncio
async def test_get_values_lists_values_from_the_ontology(deps):
    # get_values also looks values up by an exact match of a synonym, which
    # /suggestions does not search. "Ductal cancer" is a synonym of ductal
    # carcinoma.
    assert await _get_values(deps, LABEL_DIAGNOSIS, SYNONYM_DUCTAL_CARCINOMA) == [
        FIELD_VALUE_DUCTAL_CARCINOMA
    ]
    # No document has Neoplasm, so on its own it finds nothing. With
    # include_descendants, it finds its descendants that documents have.
    assert await _get_values(deps, LABEL_DIAGNOSIS, PREFERRED_TERM_NEOPLASM) == (
        f"No indexed value of field '{LABEL_DIAGNOSIS}' matches "
        f"'{PREFERRED_TERM_NEOPLASM}'. "
        "Search again with other words. If none fit, leave the field out."
    )
    assert await _get_values(
        deps, LABEL_DIAGNOSIS, PREFERRED_TERM_NEOPLASM, include_descendants=True
    ) == [FIELD_VALUE_DUCTAL_CARCINOMA, FIELD_VALUE_LOBULAR_CARCINOMA]
    # The test ontology has no concept named "carcinoma". So even with
    # include_descendants, get_values falls back to the /suggestions search, and
    # lists the values whose names contain "carcinoma".
    assert await _get_values(
        deps, LABEL_DIAGNOSIS, "carcinoma", include_descendants=True
    ) == [FIELD_VALUE_DUCTAL_CARCINOMA, FIELD_VALUE_LOBULAR_CARCINOMA]


@pytest.mark.asyncio
async def test_get_values_refuses_fields_it_cannot_look_up(deps):
    # The model is told what the field takes instead, so it can correct
    # itself. An unknown field does not exist.
    assert await _get_values(deps, LABEL_SEX, "Female") == (
        "Field 'Sex' takes one of these values: Male | Female. "
        "Use one of them in the filter. get_values is not needed."
    )
    assert await _get_values(deps, "colour", "red") == "Unknown field: 'colour'."


@pytest.mark.asyncio
async def test_get_values_tells_what_other_fields_take(ontology, beacon_service):
    deps = _Deps(TERMS, None, beacon_service, TERM_CACHES, ONTOLOGY_ID_BY_FIELD)
    assert await _get_values(deps, "Age", "40") == (
        "Field 'Age' takes an ISO-8601 duration or range, such as "
        "P40Y or P40Y-P60Y. get_values is not needed."
    )
    assert await _get_values(deps, "Title", "liver") == (
        "Field 'Title' takes words to match in its text. "
        "Use words from the query. get_values is not needed."
    )


# Test interpret: the get_values tool and the check of the model's answer.
#


@pytest.mark.asyncio
async def test_interpret_offers_get_values(ai_service, beacon_service):
    # The model looks up diagnoses with get_values, then answers with one of
    # them. The answer comes back with the value replaced by its concept id.
    llm = _MockLLM(
        [{"field": LABEL_DIAGNOSIS, "value": "Ductal carcinoma"}],
        find={"field": LABEL_DIAGNOSIS, "text": "carcinoma"},
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
        [{"field": LABEL_DIAGNOSIS, "value": PREFERRED_TERM_NEOPLASM}],
        [{"field": LABEL_DIAGNOSIS, "value": PREFERRED_TERM_LOBULAR_CARCINOMA}],
    )
    with ai_service._agent.override(model=llm.model):
        result = await ai_service.interpret("carcinoma", beacon_service, TERM_CACHES)
    assert result.filters == [
        BeaconQueryFilter(id=FIELD_ID_DIAGNOSIS, value=[CONCEPT_ID_LOBULAR_CARCINOMA])
    ]


@pytest.mark.asyncio
async def test_interpret_logs_conversation(ai_service, beacon_service, caplog):
    # At DEBUG, each tool call, what it returned and the answer are logged.
    llm = _MockLLM(
        [{"field": LABEL_DIAGNOSIS, "value": PREFERRED_TERM_DUCTAL_CARCINOMA}],
        find={"field": LABEL_DIAGNOSIS, "text": "ductal"},
    )
    with caplog.at_level("DEBUG", logger="search_api.ai.services"):
        with ai_service._agent.override(model=llm.model):
            await ai_service.interpret("ductal", beacon_service, TERM_CACHES)
    assert "AI conversation for the query 'ductal'" in caplog.text
    assert (
        'model called get_values({"field":"Diagnosed disease","text":"ductal"'
        in caplog.text
    )
    assert "get_values returned:" in caplog.text
    # The fields get_filtering_terms returned are logged as the model sees them.
    assert (
        '- get_filtering_terms returned: [{"field":"Diagnosed disease",'
        '"accepts":"get_values","values":null}'
    ) in caplog.text


@pytest.mark.parametrize(
    "answer,answer_logged,correction_logged",
    [
        (
            FILTERS_IN_PLAIN_TEXT_RESPONSE,
            "- model said: Interpretation: none. Filters: []",
            # A plain text answer called no tool, so the correction names none.
            # The correction is over several lines, indented to stay in its entry.
            "- sent back to the model: 1 validation error:\n  ```json\n  [",
        ),
        (
            MALFORMED_FILTERS_ARGUMENTS,
            '- model called final_result({"filters"',
            "- sent back to the model for final_result: ",
        ),
    ],
)
@pytest.mark.asyncio
async def test_interpret_logs_conversation_without_valid_answer(
    ai_service, beacon_service, caplog, answer, answer_logged, correction_logged
):
    # The conversation is logged when the model gives no valid answer too,
    # which is when it matters most.
    with caplog.at_level("DEBUG", logger="search_api.ai.services"):
        with ai_service._agent.override(model=_model_answering(answer)):
            await ai_service.interpret("anything", beacon_service, TERM_CACHES)
    assert answer_logged in caplog.text
    assert correction_logged in caplog.text
