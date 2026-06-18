from search_api.api.beacon.models import BeaconFilteringOntology, SNOMED_ONTOLOGY_ID
from search_api.api.opensearch.index_generator import OpenSearchIndexGeneratorService
from search_api.api.opensearch.models import (
    OpenSearchBeaconFilteringTerm,
    OpenSearchField,
    OpenSearchOntologyOrValue,
)


def _filtering_term(field_id, type_, opensearch_field) -> OpenSearchBeaconFilteringTerm:
    is_ontology = type_ in ("ontology", "ontologyOrValue")
    return OpenSearchBeaconFilteringTerm(
        id=field_id,
        type=type_,
        scopes=["test"],
        label=field_id,
        description=field_id,
        opensearch_field=opensearch_field,
        ontology=BeaconFilteringOntology(id=SNOMED_ONTOLOGY_ID)
        if is_ontology
        else None,
        ontologyConcept="123456789" if is_ontology else None,
        controlledValues=["a", "b"] if type_ == "controlledValue" else None,
    )


def _generate(*fields: OpenSearchField) -> dict:
    body = OpenSearchIndexGeneratorService(list(fields)).generate()
    return body["mappings"]["properties"]


def test_generate_top_level_field_mapped_at_root():
    props = _generate(_filtering_term("dataset_title", "text", "dataset_title"))
    assert props["dataset_title"] == {"type": "text", "analyzer": "english_text"}


def test_generate_nested_field_wrapped_in_nested_container():
    props = _generate(
        _filtering_term("animal_species", "ontology", "blocks.animal_species")
    )
    assert props["blocks"] == {
        "type": "nested",
        "properties": {"animal_species": {"type": "keyword"}},
    }


def test_generate_multiple_nested_fields_share_container():
    props = _generate(
        _filtering_term("animal_species", "ontology", "blocks.animal_species"),
        _filtering_term("sex", "controlledValue", "blocks.sex"),
    )
    assert set(props["blocks"]["properties"]) == {"animal_species", "sex"}


def test_generate_deeply_nested_field_creates_a_container_per_level():
    props = _generate(
        _filtering_term("deep", "keyword", "blocks.specimen.anatomical_site")
    )
    assert props["blocks"] == {
        "type": "nested",
        "properties": {
            "specimen": {
                "type": "nested",
                "properties": {"anatomical_site": {"type": "keyword"}},
            }
        },
    }


def test_generate_nested_fields_at_different_depths_share_containers():
    props = _generate(
        _filtering_term("site", "keyword", "blocks.specimen.anatomical_site"),
        _filtering_term("fixation", "keyword", "blocks.specimen.fixation_type"),
        _filtering_term("species", "ontology", "blocks.animal_species"),
    )
    blocks = props["blocks"]["properties"]
    assert set(blocks) == {"specimen", "animal_species"}
    assert set(blocks["specimen"]["properties"]) == {
        "anatomical_site",
        "fixation_type",
    }


def test_generate_type_to_opensearch_mapping():
    props = _generate(
        _filtering_term("kw", "keyword", "stains.kw"),
        _filtering_term("cv", "controlledValue", "stains.cv"),
        _filtering_term("onto", "ontology", "stains.onto"),
        _filtering_term("age", "iso8601Range", "stains.age"),
        OpenSearchField(id="cnt", type="integer", opensearch_field="cnt"),
    )
    fields = props["stains"]["properties"]
    assert fields["kw"] == {"type": "keyword"}
    assert fields["cv"] == {"type": "keyword"}
    assert fields["onto"] == {"type": "keyword"}
    assert fields["age"] == {"type": "integer_range"}
    assert props["cnt"] == {"type": "long"}


def test_generate_ontology_or_value_expands_to_two_keyword_fields():
    props = _generate(
        _filtering_term(
            "fixation_type",
            "ontologyOrValue",
            OpenSearchOntologyOrValue(
                concept_value_field="blocks.fixation_type",
                other_value_field="blocks.fixation_type_text",
            ),
        )
    )
    assert props["blocks"]["properties"] == {
        "fixation_type": {"type": "keyword"},
        "fixation_type_text": {"type": "keyword"},
    }


def test_generate_non_filtering_field_included():
    props = _generate(
        _filtering_term("dataset_title", "text", "dataset_title"),
        OpenSearchField(id="image_id", type="keyword", opensearch_field="image_id"),
    )
    assert props["image_id"] == {"type": "keyword"}


def test_generate_non_filtering_field_nested():
    props = _generate(
        OpenSearchField(
            id="image_id", type="keyword", opensearch_field="blocks.image_id"
        ),
    )
    assert props["blocks"] == {
        "type": "nested",
        "properties": {"image_id": {"type": "keyword"}},
    }


def test_generate_settings_define_the_text_analyzer():
    body = OpenSearchIndexGeneratorService([]).generate()
    assert "english_text" in body["settings"]["analysis"]["analyzer"]
