from dataclasses import dataclass
from datetime import datetime
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from search_api.api.extract_logs import ExtractLog
from search_api.api.opensearch.models import OpenSearchBeaconFilteringTerm
from search_api.exceptions import SystemException


class BigpictureCodeAttributeValue(BaseModel):
    """Bigpicture code attribute value."""

    model_config = ConfigDict(frozen=True)

    code: str
    scheme: str | None = None
    meaning: str
    scheme_version: str | None = None


class BigpictureSampleBiologicalBeingFields(BaseModel):
    """Bigpicture biological being search fields."""

    animal_species: BigpictureCodeAttributeValue | None = None
    sex: Literal["Male", "Female", "Not-known", "Other"] | None = None
    control_terminology: Literal["CONTROL", "TREATED"] | None = None


class BigpictureSampleSpecimenFields(BaseModel):
    """Bigpicture specimen search fields."""

    anatomical_site: frozenset[BigpictureCodeAttributeValue] = Field(
        default_factory=frozenset
    )
    fixation_type: BigpictureCodeAttributeValue | None = None
    fixation_type_other: str | None = None  # Free text alternative
    specimen_type: BigpictureCodeAttributeValue | None = None
    age_at_extraction: tuple[str, str] | None = None

    @field_serializer("anatomical_site")
    def _serialize_anatomical_site(
        self, v: frozenset[BigpictureCodeAttributeValue]
    ) -> list[dict]:
        # Set elements must be hashable; Pydantic serialises frozenset[BaseModel] as
        # set[dict], but dict is unhashable, so serialise as list[dict].
        return [item.model_dump() for item in v]


class BigpictureSampleBlockFields(BaseModel):
    """Bigpicture block search fields."""

    block_preparation: BigpictureCodeAttributeValue | None = None


class BigpictureSpecimenFields(
    BigpictureSampleBiologicalBeingFields,
    BigpictureSampleSpecimenFields,
    BigpictureSampleBlockFields,
    BaseModel,
):
    """Bigpicture specimen search fields (see grouping rationale in fields.yaml)."""

    model_config = ConfigDict(frozen=True)


class BigpictureStainingFields(BaseModel):
    """Bigpicture staining search field."""

    model_config = ConfigDict(frozen=True)

    staining_procedure: BigpictureCodeAttributeValue | None = None
    staining_procedure_other: str | None = None  # Free text alternative
    staining_substance: BigpictureCodeAttributeValue | None = None
    staining_substance_other: str | None = None  # Free text alternative
    staining_target: str | None = None


class BigpictureObservationFields(BaseModel):
    """Bigpicture clinical diagnosis and non-clinical finding search fields.

    One instance per distinct observation.
    """

    model_config = ConfigDict(frozen=True)

    diagnosis: BigpictureCodeAttributeValue | None = None
    finding: BigpictureCodeAttributeValue | None = None
    finding_severity: BigpictureCodeAttributeValue | None = None
    finding_chronicity: BigpictureCodeAttributeValue | None = None
    finding_distribution: BigpictureCodeAttributeValue | None = None
    finding_result_category: BigpictureCodeAttributeValue | None = None
    observation_type: Literal["confirmed", "candidate"]


class BigpictureFields(BaseModel):
    """Bigpicture IDs and search fields."""

    image_id: str
    dataset_id: str
    dataset_image_cnt: int
    scope: Literal["clinical", "non_clinical"]
    dataset_short_name: str | None = None
    dataset_title: str | None = None
    dataset_description: str | None = None
    specimen: set[BigpictureSpecimenFields] = Field(default_factory=set)
    staining: set[BigpictureStainingFields] = Field(default_factory=set)
    observation: set[BigpictureObservationFields] = Field(default_factory=set)
    # Newest file modification date in the dataset.
    dataset_modified_at: datetime | None = None


def _nested_groups() -> tuple[str, ...]:
    """Return the Bigpicture nested group names."""
    return tuple(
        name
        for name, info in BigpictureFields.model_fields.items()
        if (args := get_args(info.annotation))
        and isinstance(args[0], type)
        and issubclass(args[0], BaseModel)
    )


NESTED_GROUPS = _nested_groups()


OBSERVATION_CONFIRMED: Literal["confirmed"] = "confirmed"
OBSERVATION_CANDIDATE: Literal["candidate"] = "candidate"


def validate_observation_types(term: OpenSearchBeaconFilteringTerm) -> None:
    """Validate fields.yaml observation_type term against observation type constants."""
    if set(term.controlledValues or []) != {
        OBSERVATION_CONFIRMED,
        OBSERVATION_CANDIDATE,
    }:
        raise SystemException(
            f"{term.id}'s controlledValues in fields.yaml must be "
            f"{{{OBSERVATION_CONFIRMED!r}, {OBSERVATION_CANDIDATE!r}}}, got "
            f"{term.controlledValues}."
        )
    if term.group != "observation":
        raise SystemException(
            f"{term.id}'s group in fields.yaml must be 'observation', "
            f"got {term.group!r}."
        )


class ObjectKey(BaseModel):
    """Object alias or optional accession."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["accession", "alias"]
    value: str


class ObjectIds(BaseModel):
    """Object alias and, if present, its accession."""

    model_config = ConfigDict(frozen=True)

    alias: str
    accession: str | None = None

    @property
    def id(self) -> str:
        """The accession if present, else the alias."""
        return self.accession or self.alias

    @property
    def keys(self) -> list[ObjectKey]:
        keys = [ObjectKey(kind="alias", value=self.alias)]
        if self.accession is not None:
            keys.append(ObjectKey(kind="accession", value=self.accession))
        return keys


@dataclass(frozen=True)
class BigpictureExtractedObject[FieldsT]:
    """One extracted XML object.

    A dataclass rather than a pydantic model to avoid copying the logs.
    """

    ids: ObjectIds
    fields: FieldsT
    logs: list[ExtractLog]
