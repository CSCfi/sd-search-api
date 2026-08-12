import os
from pathlib import Path

from pydantic import Field

from search_api.api.beacon.models import (
    BeaconFilteringGroup,
    BeaconFilteringQualifier,
    BeaconFilteringScope,
    BeaconFilteringTermsResponse,
    BeaconFilteringTerms,
    BeaconInfoResponse,
    BeaconInfoMeta,
    BeaconResultSetResult,
    BeaconResultSetsResponse,
    BeaconSchema,
    BeaconInfo,
)
from search_api.api.fields import load_fields_config
from search_api.api.groups import load_groups_config, validate_filtering_groups
from search_api.api.qualifiers import (
    load_qualifiers_config,
    validate_filtering_qualifiers,
)
from search_api.api.scopes import load_scopes_config, validate_filtering_scopes
from search_api.exceptions import SystemException
from search_api.api.opensearch.models import (
    OpenSearchField,
    OpenSearchOntologyOrValue,
)


class BigpictureBeaconResultSetResult(BeaconResultSetResult):
    """Beacon V2 result set result for the Bigpicture document schema."""

    datasetId: str
    datasetTitle: str
    datasetDescription: str
    datasetUrl: str | None
    totalImageCount: int
    matchingImageCount: int
    imageIds: list[str] = Field(default_factory=list)


class BigpictureBeaconResultSetsResponse(
    BeaconResultSetsResponse[BigpictureBeaconResultSetResult]
):
    """Beacon V2 result sets response for the Bigpicture document schema."""


BP_DOMAIN_NAME = "Bigpicture"
BP_OPENSEARCH_INDEX = "bp-image-index"

# The Beacon's identity, reported by /info. The id differentiates responses
# within a Beacon network, so it is a reversed domain string.
BP_BEACON_ID = "fi.csc.bigpicture.beacon.v2"
BP_BEACON_NAME = "CSC Bigpicture Beacon"

BP_DATASET_SCHEMA = "dataset"
BP_BIOLOGICAL_BEING_SCHEMA = "biological_being"
BP_SPECIMEN_SCHEMA = "specimen"
BP_BLOCK_SCHEMA = "block"
BP_STAINING_SCHEMA = "staining"
BP_SCHEMAS = [
    BP_DATASET_SCHEMA,
    BP_BIOLOGICAL_BEING_SCHEMA,
    BP_SPECIMEN_SCHEMA,
    BP_BLOCK_SCHEMA,
    BP_STAINING_SCHEMA,
]

# Filtering terms, groups, scopes, and index-only fields are declared in YAML
# files and validated on load.
_FIELDS_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "fields.yaml"
_fields_config = load_fields_config(_FIELDS_CONFIG_PATH)

BP_FILTERING_TERMS = _fields_config.filtering_terms
BP_NON_FILTERING_FIELDS = _fields_config.non_filtering_fields

_GROUPS_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "groups.yaml"
BP_FILTERING_GROUPS: list[BeaconFilteringGroup] = load_groups_config(
    _GROUPS_CONFIG_PATH
).filtering_groups

_SCOPES_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "scopes.yaml"
BP_FILTERING_SCOPES: list[BeaconFilteringScope] = load_scopes_config(
    _SCOPES_CONFIG_PATH
).filtering_scopes

_QUALIFIERS_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "qualifiers.yaml"
BP_FILTERING_QUALIFIERS: list[BeaconFilteringQualifier] = load_qualifiers_config(
    _QUALIFIERS_CONFIG_PATH
).filtering_qualifiers


validate_filtering_groups(BP_FILTERING_TERMS, BP_FILTERING_GROUPS, _FIELDS_CONFIG_PATH)
validate_filtering_scopes(BP_FILTERING_TERMS, BP_FILTERING_SCOPES, _FIELDS_CONFIG_PATH)
validate_filtering_qualifiers(
    BP_FILTERING_TERMS,
    BP_NON_FILTERING_FIELDS,
    BP_FILTERING_QUALIFIERS,
    _QUALIFIERS_CONFIG_PATH,
)

# Filtering term lookup by id.
BP_FILTERING_TERM_BY_ID = {term.id: term for term in BP_FILTERING_TERMS}


def _document_fields() -> dict[str, OpenSearchField]:
    """Returns a dict of OpenSearch document fields keyed by field name (the id)."""

    def leaf(path: str) -> str:
        return path.rsplit(".", 1)[-1]

    def group(path: str) -> str | None:
        prefix, _, _ = path.rpartition(".")
        return prefix or None

    fields: dict[str, OpenSearchField] = {}

    def add_field(field: OpenSearchField) -> None:
        if field.id in fields:
            raise SystemException(
                f"Document field {field.id!r} is defined by more than one OpenSearch field."
            )
        fields[field.id] = field

    for field in BP_NON_FILTERING_FIELDS:
        add_field(field)

    for term in BP_FILTERING_TERMS:
        osf = term.opensearch_field
        if isinstance(osf, OpenSearchOntologyOrValue):
            # Concept ID and free text are stored in separate fields.
            add_field(
                OpenSearchField(
                    id=leaf(osf.concept_value_field),
                    type="ontology",
                    group=group(osf.concept_value_field),
                )
            )
            add_field(
                OpenSearchField(
                    id=leaf(osf.other_value_field),
                    type="keyword",
                    group=group(osf.other_value_field),
                )
            )
        else:
            add_field(term)
    return fields


BP_DOCUMENT_FIELDS: dict[str, OpenSearchField] = _document_fields()

BP_META_RESPONSE = BeaconInfoMeta(
    beaconId=BP_BEACON_ID,
    returnedSchemas=[BeaconSchema(entityType=schema) for schema in BP_SCHEMAS],
)

BP_FILTERING_TERMS_RESPONSE = BeaconFilteringTermsResponse(
    meta=BP_META_RESPONSE,
    response=BeaconFilteringTerms(filteringTerms=BP_FILTERING_TERMS),
)

BP_INFO_RESPONSE = BeaconInfoResponse(
    meta=BP_META_RESPONSE,
    response=BeaconInfo(
        id=BP_BEACON_ID,
        name=BP_BEACON_NAME,
        environment=os.getenv("DEPLOYMENT_ENV", "dev"),
    ),
)
