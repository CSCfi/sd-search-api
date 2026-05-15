from search_api.api.beacon.models import (
    SNOMED_ONTOLOGY_ID,
    BeaconFilteringMeta,
    BeaconFilteringTerm,
    BeaconFilteringControlledVocabulary,
    BeaconFilteringOntology,
    BeaconFilteringTermsResponse,
    BeaconFilteringTerms,
    BeaconInfoResponse,
    BeaconInfoMeta,
)

BP_BEACON_ID = "fi.csc.bigpicture.beacon.v2"

BP_DATASET_SCOPE = ["dataset"]
BP_BIOLOGICAL_BEING_SCOPE = ["biological_being"]
BP_SPECIMEN_SCOPE = ["specimen"]
BP_BLOCK_SCOPE = ["block"]
BP_STAINING_SCOPE = ["staining"]

BP_FILTERING_META = BeaconFilteringMeta(beaconId=BP_BEACON_ID)

BP_DATASET_TITLE_FILTERING_TERM = BeaconFilteringTerm(
    id="dataset_title", type="text", scopes=BP_DATASET_SCOPE
)
BP_DATASET_DESCRIPTION_FILTERING_TERM = BeaconFilteringTerm(
    id="dataset_description", type="text", scopes=BP_DATASET_SCOPE
)
BP_SPECIES_FILTERING_TERM = BeaconFilteringTerm(
    id="animal_species",
    type="ontology",
    ontology=BeaconFilteringOntology(id=SNOMED_ONTOLOGY_ID),
    scopes=BP_BIOLOGICAL_BEING_SCOPE,
)
BP_SEX_FILTERING_TERM = BeaconFilteringTerm(
    id="sex",
    type="controlledVocabulary",
    controlledVocabulary=BeaconFilteringControlledVocabulary(
        allowedTerms=["Male", "Female", "Not-known", "Other"]
    ),
    scopes=BP_BIOLOGICAL_BEING_SCOPE,
)
BP_ANATOMICAL_SITE_FILTERING_TERM = BeaconFilteringTerm(
    id="anatomical_site",
    type="ontology",
    ontology=BeaconFilteringOntology(id=SNOMED_ONTOLOGY_ID),
    scopes=BP_SPECIMEN_SCOPE,
)
BP_FIXATION_TYPE_FILTERING_TERM = BeaconFilteringTerm(
    id="fixation_type",
    type="ontology",
    ontology=BeaconFilteringOntology(id=SNOMED_ONTOLOGY_ID),
    scopes=BP_SPECIMEN_SCOPE,
)
BP_SPECIMEN_TYPE_FILTERING_TERM = BeaconFilteringTerm(
    id="specimen_type",
    type="ontology",
    ontology=BeaconFilteringOntology(id=SNOMED_ONTOLOGY_ID),
    scopes=BP_SPECIMEN_SCOPE,
)
BP_AGE_AT_EXTRACTION_FILTERING_TERM = BeaconFilteringTerm(
    id="age_at_extraction", type="numberRange", scopes=BP_SPECIMEN_SCOPE
)

BP_BLOCK_PREPARATION_FILTERING_TERM = BeaconFilteringTerm(
    id="block_preparation",
    type="ontology",
    ontology=BeaconFilteringOntology(id=SNOMED_ONTOLOGY_ID),
    scopes=BP_BLOCK_SCOPE,
)
BP_STAINING_METHOD_FILTERING_TERM = BeaconFilteringTerm(
    id="staining_method",
    type="ontology",
    ontology=BeaconFilteringOntology(id=SNOMED_ONTOLOGY_ID),
    scopes=BP_STAINING_SCOPE,
)
BP_STAINING_TARGET_FILTERING_TERM = BeaconFilteringTerm(
    id="staining_target", type="text", scopes=BP_STAINING_SCOPE
)
BP_STAINING_PROCEDURE_FILTERING_TERM = BeaconFilteringTerm(
    id="staining_procedure",
    type="ontologyOrValue",
    ontology=BeaconFilteringOntology(id=SNOMED_ONTOLOGY_ID),
    scopes=BP_STAINING_SCOPE,
)
BP_STAINING_COMPOUND_FILTERING_TERM = BeaconFilteringTerm(
    id="staining_compound",
    type="ontologyOrValue",
    ontology=BeaconFilteringOntology(id=SNOMED_ONTOLOGY_ID),
    scopes=BP_STAINING_SCOPE,
)

BP_FILTERING_TERMS = [
    BP_DATASET_TITLE_FILTERING_TERM,
    BP_DATASET_DESCRIPTION_FILTERING_TERM,
    BP_SPECIES_FILTERING_TERM,
    BP_SEX_FILTERING_TERM,
    BP_ANATOMICAL_SITE_FILTERING_TERM,
    BP_FIXATION_TYPE_FILTERING_TERM,
    BP_SPECIMEN_TYPE_FILTERING_TERM,
    BP_AGE_AT_EXTRACTION_FILTERING_TERM,
    BP_BLOCK_PREPARATION_FILTERING_TERM,
    BP_STAINING_METHOD_FILTERING_TERM,
    BP_STAINING_TARGET_FILTERING_TERM,
    BP_STAINING_PROCEDURE_FILTERING_TERM,
    BP_STAINING_COMPOUND_FILTERING_TERM,
]

BP_FILTERING_TERMS_RESPONSE = BeaconFilteringTermsResponse(
    meta=BeaconFilteringMeta(beaconId=BP_BEACON_ID),
    response=BeaconFilteringTerms(filteringTerms=BP_FILTERING_TERMS),
)

BP_INFO_RESPONSE = BeaconInfoResponse(meta=BeaconInfoMeta(beaconId=BP_BEACON_ID))
