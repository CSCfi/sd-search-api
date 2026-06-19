from pydantic import BaseModel


class AIQueryFilter(BaseModel):
    id: str
    value: str | list[str]
    allowed_values: list[str] | None = None
    # TODO(improve): support ontology descendants.
    # includeDescendantTerms: bool = True


class AISearchResult(BaseModel):
    """Generic AI search result.

    A deployment should subclass this to describe its own result shape.
    """

    interpretation: str
    filters: list[AIQueryFilter]
