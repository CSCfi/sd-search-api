from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from search_api.api.qualifiers import decode_qualifier_value, encode_qualifier_value


class FieldValue(BaseModel):
    value: str
    count: int = 0
    concept_id: str | None = None


class ValueCounts(BaseModel):
    counts: dict[str, int]
    other_counts: dict[str, int] = Field(default_factory=dict)


class ValueCountsKey(BaseModel):
    model_config = ConfigDict(frozen=True)

    field_id: str
    scope: str | None = None
    # The requested qualifiers, <id>:<value> (e.g. "observation:confirmed").
    qualifiers: frozenset[str] = frozenset()

    @classmethod
    def of(
        cls,
        field_id: str,
        scope: str | None = None,
        qualifiers: Mapping[str, Sequence[str]] | None = None,
    ) -> "ValueCountsKey":
        return cls(
            field_id=field_id,
            scope=scope,
            qualifiers=frozenset(
                encode_qualifier_value(qualifier_id, value)
                for qualifier_id, values in (qualifiers or {}).items()
                for value in values
            ),
        )

    @property
    def qualifier_values_by_id(self) -> dict[str, list[str]]:
        """Return sorted qualifier values by key."""
        by_id: dict[str, list[str]] = {}
        for encoded in sorted(self.qualifiers):
            qualifier_id, value = decode_qualifier_value(encoded)
            by_id.setdefault(qualifier_id, []).append(value)
        return by_id


class AIQueryRequest(BaseModel):
    query: str
