"""AI-powered natural language search using pydantic-ai and Ollama."""

from collections.abc import Sequence

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from search_api.api.beacon.models import BeaconFilteringTerm, BeaconQueryFilter
from search_api.api.beacon.services import BeaconService
from search_api.conf import ai_config as _ai_config


class AIQueryFilter(BaseModel):
    id: str
    value: str | list[str]
    allowed_values: list[str] | None = None
    # TODO(improve): support ontology descendants.
    # includeDescendantTerms: bool = True


class AIDatasetResult(BaseModel):
    dataset_id: str
    dataset_title: str | None = None
    matching_image_count: int
    total_image_count: int


class AISearchResult(BaseModel):
    """Structured result returned by the AI search agent."""

    interpretation: str
    filters: list[AIQueryFilter]
    dataset_count: int
    datasets: list[AIDatasetResult]


_SYSTEM_PROMPT = """\
You are a biomedical image search assistant for the Bigpicture digital pathology dataset.
Always respond in the same language as the user's query, or in English if uncertain.

Your job is to translate a natural language query into Beacon V2 filters and search the
OpenSearch image index. Follow these steps for every query:

1. Call get_filtering_terms() to see available field names. Fields with allowed values are
   listed as "field: value1 | value2 | ..."; use one of those values exactly.
2. Call search_images(filters) using the relevant field names from step 1 as filter ids,
   with values derived from the user's query.
3. Return a structured result with:
   - interpretation: a concise explanation of how you understood the query and which
     filters you chose
   - filters: the exact filter list you passed to search_images
   - dataset_count: number of datasets in the results
   - datasets: one entry per dataset in the results
"""


class AIService:
    def __init__(self, filtering_terms: Sequence[BeaconFilteringTerm]) -> None:
        cfg = _ai_config()
        model = OpenAIChatModel(
            # These models are too small to construct filters correctly: "qwen2.5:3b",
            "qwen2.5:14b",
            provider=OpenAIProvider(base_url=cfg.LLM_BASE_URL, api_key=cfg.LLM_API_KEY),
        )

        self._agent: Agent[BeaconService, AISearchResult] = Agent(
            model=model,
            deps_type=BeaconService,  # type: ignore[type-abstract]
            output_type=AISearchResult,
            system_prompt=_SYSTEM_PROMPT,
            output_retries=3,
        )

        # pydantic-ai generates a JSON schema from BeaconQueryFilter and passes it to the
        # model as part of the tool definition so that the model knows the expected filter
        # structure. To inspect:
        # for name, tool in self._agent._function_toolset.tools.items():
        #     print(name, json.dumps(tool.function_schema.json_schema, indent=2))

        @self._agent.tool_plain
        def get_filtering_terms() -> str:
            """
            Return available search filtering terms.
            """
            lines = []
            for term in filtering_terms:
                if term.controlledValues:
                    allowed = " | ".join(term.controlledValues or [])
                    lines.append(f"{term.id}: {allowed}")
                else:
                    lines.append(term.id)
            return "\n".join(lines)

        @self._agent.tool
        async def search_images(
            ctx: RunContext[BeaconService], filters: list[AIQueryFilter]
        ) -> str:
            """
            Execute an image search using Beacon V2 filters and return results.
            """
            results = await ctx.deps.query(
                [BeaconQueryFilter(id=f.id, value=f.value) for f in filters]
            )
            return results.model_dump_json(indent=2)

    async def search(self, query: str, beacon_service: BeaconService) -> AISearchResult:
        """
        Translate a natural language query into Beacon V2 filters and search the
        OpenSearch index.
        """
        result = await self._agent.run(query, deps=beacon_service)
        return result.output
