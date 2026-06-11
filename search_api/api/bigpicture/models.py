import os

from pydantic import BaseModel, Field

from search_api.api.beacon.models import (
    SNOMED_ONTOLOGY_ID,
    BeaconFilteringOntology,
    BeaconFilteringTermsResponse,
    BeaconFilteringTerms,
    BeaconInfoResponse,
    BeaconInfoMeta,
    BeaconResponseMeta,
    BeaconResultCountResponseSummary,
    BeaconResultSetResult,
    BeaconResultSets,
    BeaconSchema,
    BeaconInfo,
)
from search_api.api.opensearch.models import (
    OpenSearchOntologyOrValue,
    OpenSearchBeaconFilteringTerm,
)


class BigpictureBeaconResultSetResult(BeaconResultSetResult):
    """Beacon V2 result set result for the Bigpicture document schema."""

    datasetId: str
    datasetTitle: str
    datasetDescription: str
    totalImageCount: int
    matchingImageCount: int
    imageIds: list[str] = Field(default_factory=list)


class BigpictureBeaconResultSetsResponse(BaseModel):
    """Beacon V2 result sets response for the Bigpicture document schema."""

    meta: BeaconResponseMeta
    responseSummary: BeaconResultCountResponseSummary
    response: BeaconResultSets[BigpictureBeaconResultSetResult]


BP_OPENSEARCH_INDEX = "bp-image-index"

BP_BEACON_ID = "fi.csc.bigpicture.beacon.v2"
BP_BEACON_NAME = "fi.csc.bigpicture.beacon.v2"

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
BP_DATASET_SCOPE = [BP_DATASET_SCHEMA]
BP_BIOLOGICAL_BEING_SCOPE = [BP_BIOLOGICAL_BEING_SCHEMA]
BP_SPECIMEN_SCOPE = [BP_SPECIMEN_SCHEMA]
BP_BLOCK_SCOPE = [BP_BLOCK_SCHEMA]
BP_STAINING_SCOPE = [BP_STAINING_SCHEMA]


BP_DATASET_TITLE_FILTERING_TERM = OpenSearchBeaconFilteringTerm(
    id="dataset_title",
    type="text",
    scopes=BP_DATASET_SCOPE,
    label="Dataset title",
    description="The title of the dataset",
    opensearch_field="dataset_title",
)
BP_DATASET_DESCRIPTION_FILTERING_TERM = OpenSearchBeaconFilteringTerm(
    id="dataset_description",
    type="text",
    scopes=BP_DATASET_SCOPE,
    label="Dataset description",
    description="The description of the dataset",
    opensearch_field="dataset_description",
)
BP_SPECIES_FILTERING_TERM = OpenSearchBeaconFilteringTerm(
    id="animal_species",
    type="ontology",
    ontology=BeaconFilteringOntology(id=SNOMED_ONTOLOGY_ID),
    ontologyConcept="410607006",  # Organism (organism)
    scopes=BP_BIOLOGICAL_BEING_SCOPE,
    label="Biological species",
    description="Species of the biological being",
    opensearch_field="blocks.species",
)
BP_SEX_FILTERING_TERM = OpenSearchBeaconFilteringTerm(
    id="sex",
    type="controlledValue",
    controlledValues=["Male", "Female", "Not-known", "Other"],
    scopes=BP_BIOLOGICAL_BEING_SCOPE,
    label="Sex",
    description="The sex of the biological being",
    opensearch_field="blocks.sex",
)
BP_ANATOMICAL_SITE_FILTERING_TERM = OpenSearchBeaconFilteringTerm(
    id="anatomical_site",
    type="ontology",
    ontology=BeaconFilteringOntology(id=SNOMED_ONTOLOGY_ID),
    ontologyConcept="123037004",  # Body structure (body structure)
    scopes=BP_SPECIMEN_SCOPE,
    label="Anatomical site",
    description="The anatomical site from which the specimen originated, typically at the organ level. "
    "If no organ can be identified, use an equivalent anatomical region.",
    opensearch_field="blocks.anatomical_site",
)
BP_FIXATION_TYPE_FILTERING_TERM = OpenSearchBeaconFilteringTerm(
    id="fixation_type",
    type="ontologyOrValue",
    ontology=BeaconFilteringOntology(id=SNOMED_ONTOLOGY_ID),
    ontologyConcept="1388477003",  # Tissue fixative (product)
    scopes=BP_SPECIMEN_SCOPE,
    label="Fixation type",
    description="The type of fixation used in the process of the creation of the specimen.",
    opensearch_field=OpenSearchOntologyOrValue(
        concept_value_field="blocks.fixation_type",
        other_value_field="blocks.fixation_type_text",
    ),
)
BP_SPECIMEN_TYPE_FILTERING_TERM = OpenSearchBeaconFilteringTerm(
    id="specimen_type",
    type="ontology",
    ontology=BeaconFilteringOntology(id=SNOMED_ONTOLOGY_ID),
    ontologyConcept="91720002",  # Body substance (substance)
    scopes=BP_SPECIMEN_SCOPE,
    label="Specimen type",
    description="The type of the specimen.",
    opensearch_field="blocks.specimen_type",
)
BP_AGE_AT_EXTRACTION_FILTERING_TERM = OpenSearchBeaconFilteringTerm(
    id="age_at_extraction",
    type="iso8601Range",
    scopes=BP_SPECIMEN_SCOPE,
    label="Age at extraction",
    description="The age of the biological being at the time point of extraction of the specimen.",
    opensearch_field="blocks.age_at_extraction",
)
BP_BLOCK_PREPARATION_FILTERING_TERM = OpenSearchBeaconFilteringTerm(
    id="block_preparation",
    type="ontology",
    ontology=BeaconFilteringOntology(id=SNOMED_ONTOLOGY_ID),
    ontologyConcept=[
        "311731000",  # Paraffin wax (substance)
        "433469005",  # Frozen section embedding medium (substance)
        "61088005",  # Plastic (substance)
        "10249006",  # Agar (substance)
        "65345002",  # Epoxy resin (substance)
        "261712009",  # Acrylic polymer (substance)
    ],
    scopes=BP_BLOCK_SCOPE,
    label="Block preparation",
    description="The preservation technique used.",
    opensearch_field="blocks.block_preparation",
)
BP_STAINING_TARGET_FILTERING_TERM = OpenSearchBeaconFilteringTerm(
    id="staining_target",
    type="text",
    scopes=BP_STAINING_SCOPE,
    label="Staining target",
    description="The specific target of the stain",
    opensearch_field="stains.staining_target",
)
BP_STAINING_PROCEDURE_FILTERING_TERM = OpenSearchBeaconFilteringTerm(
    id="staining_procedure",
    type="ontologyOrValue",
    ontology=BeaconFilteringOntology(id=SNOMED_ONTOLOGY_ID),
    ontologyConcept="127790008",  # Staining method (procedure)
    scopes=BP_STAINING_SCOPE,
    label="Staining procedure",
    description="The name of the staining procedure that was performed to stain the slide",
    opensearch_field=OpenSearchOntologyOrValue(
        concept_value_field="stains.staining_procedure",
        other_value_field="stains.staining_procedure_text",
    ),
)
BP_STAINING_SUBSTANCE_FILTERING_TERM = OpenSearchBeaconFilteringTerm(
    id="staining_substance",
    type="ontologyOrValue",
    ontology=BeaconFilteringOntology(id=SNOMED_ONTOLOGY_ID),
    ontologyConcept="397165007",  # Stain
    scopes=BP_STAINING_SCOPE,
    label="Staining substance",
    description="The staining substance that binds to parts of the tissues of the slide",
    opensearch_field=OpenSearchOntologyOrValue(
        concept_value_field="stains.staining_substance",
        other_value_field="stains.staining_substance_text",
    ),
)

BP_ONTOLOGY_FILTERING_TERMS = [
    BP_SPECIES_FILTERING_TERM,
    BP_ANATOMICAL_SITE_FILTERING_TERM,
    BP_FIXATION_TYPE_FILTERING_TERM,
    BP_SPECIMEN_TYPE_FILTERING_TERM,
    BP_BLOCK_PREPARATION_FILTERING_TERM,
    BP_STAINING_PROCEDURE_FILTERING_TERM,
    BP_STAINING_SUBSTANCE_FILTERING_TERM,
]

BP_FILTERING_TERMS = [
    BP_DATASET_TITLE_FILTERING_TERM,
    BP_DATASET_DESCRIPTION_FILTERING_TERM,
    *BP_ONTOLOGY_FILTERING_TERMS,
    BP_SEX_FILTERING_TERM,
    BP_AGE_AT_EXTRACTION_FILTERING_TERM,
    BP_STAINING_TARGET_FILTERING_TERM,
]

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
