import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from search_api.ai.services import AIService, validate_filters
from search_api.api.beacon.models import (
    BeaconFilteringOntology,
    BeaconFilteringTerm,
    BeaconQueryFilter,
)
from search_api.exceptions import SystemException


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


SCOPED_TERMS = [
    _term(
        "sex",
        "controlledValue",
        scopes=("clinical", "non_clinical"),
        controlledValues=["Male", "Female"],
    ),
    _term("diagnosis", "text", scopes=("clinical",)),
]


class _MockLLM:
    """Stands in for the LLM, so AIService can be tested without one.

    A real LLM reads the user's query and chooses filters. This one ignores the
    query and always answers with the filters it was created with.

    Before answering, it asks AIService which fields it may filter on, as a real
    LLM does. It keeps their ids in offered_fields, so a test can check them.
    """

    def __init__(self, filters: list[dict]) -> None:
        self.filters = filters
        self.offered_fields: list[str] = []
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
        [fields] = tool_returns
        self.offered_fields = [line.split(":")[0] for line in fields.splitlines()]

        # Then give the final answer, always the same filters.
        answer = {"interpretation": "i", "filters": self.filters}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, answer)])


@pytest.fixture
def ai_service(monkeypatch) -> AIService:
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost/v1")
    monkeypatch.setenv("LLM_API_KEY", "test")
    return AIService(SCOPED_TERMS, "a test assistant")


@pytest.mark.asyncio
async def test_interpret_shows_every_field_without_scope(ai_service):
    llm = _MockLLM([{"id": "diagnosis", "value": "carcinoma"}])
    with ai_service._agent.override(model=llm.model):
        result = await ai_service.interpret("carcinoma")
    assert llm.offered_fields == ["sex", "diagnosis"]
    assert result.filters == [BeaconQueryFilter(id="diagnosis", value="carcinoma")]


@pytest.mark.asyncio
async def test_interpret_shows_only_fields_in_scope(ai_service):
    llm = _MockLLM([{"id": "sex", "value": "Female"}])
    with ai_service._agent.override(model=llm.model):
        await ai_service.interpret("females", scope="non_clinical")
    assert llm.offered_fields == ["sex"]


@pytest.mark.asyncio
async def test_interpret_rejects_field_outside_scope(ai_service):
    # The LLM keeps answering with a field the scope hides, so it runs out of retries.
    llm = _MockLLM([{"id": "diagnosis", "value": "carcinoma"}])
    with ai_service._agent.override(model=llm.model):
        with pytest.raises(SystemException):
            await ai_service.interpret("carcinoma", scope="non_clinical")


@pytest.mark.asyncio
async def test_interpret_raises_system_exception_when_model_fails(ai_service):
    def fail(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ConnectionError("LLM is down.")

    with ai_service._agent.override(model=FunctionModel(fail)):
        with pytest.raises(SystemException):
            await ai_service.interpret("anything")
