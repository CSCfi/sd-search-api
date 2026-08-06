from search_api.api.opensearch.document import build_document
from search_api.api.bigpicture.extract import (
    BigpictureSpecimenFields,
    BigpictureCodeAttributeValue,
    BigpictureFields,
    BigpictureStainingFields,
    to_opensearch_field_values,
)
from search_api.api.bigpicture.domain import BP_DOMAIN
from search_api.services.load import concept_ids_from_values, ontology_services_by_field

_ONTOLOGY_BY_FIELD = ontology_services_by_field(BP_DOMAIN.filtering_terms)

_SPECIES = "337915000"
_BREAST = "80248007"
_AXILLA = "368209003"
_FFPE = "1388477003"
_PARAFFIN = "311731000"
_HE = "406768004"


def _fields(*, scope: str = "clinical", **kwargs) -> BigpictureFields:
    return BigpictureFields(
        image_id="img", dataset_id="ds", dataset_image_cnt=1, scope=scope, **kwargs
    )


def test_to_opensearch_field_values():
    fields = _fields(
        dataset_title="A title",
        specimen={
            BigpictureSpecimenFields(
                animal_species=_code(_SPECIES),
                anatomical_site=frozenset([_code(_BREAST), _code(_AXILLA)]),
                age_at_extraction=("P40Y", "P41Y"),
                sex="Female",
            )
        },
        staining={BigpictureStainingFields(staining_target="Nucleus")},
    )
    payload = build_document(to_opensearch_field_values(fields))

    assert payload["image_id"] == "img"
    assert payload["dataset_id"] == "ds"
    assert payload["dataset_image_cnt"] == 1
    assert payload["dataset_title"] == "A title"
    specimen = payload["specimen"][0]
    assert specimen["animal_species"] == _SPECIES
    assert sorted(specimen["anatomical_site"]) == sorted([_BREAST, _AXILLA])
    assert specimen["age_at_extraction"] == {"gte": 14600, "lte": 14965}
    assert specimen["sex"] == "Female"
    assert payload["staining"][0]["staining_target"] == "Nucleus"


def _code(code: str) -> BigpictureCodeAttributeValue:
    return BigpictureCodeAttributeValue(code=code, meaning=code)


def _concept_ids(fields: BigpictureFields) -> dict[str, set[str]]:
    return concept_ids_from_values(
        to_opensearch_field_values(fields), _ONTOLOGY_BY_FIELD
    )


def test_concept_ids_from_values_animal_species():
    result = _concept_ids(
        _fields(specimen={BigpictureSpecimenFields(animal_species=_code(_SPECIES))})
    )
    assert _SPECIES in result.get("animal_species", set())


def test_concept_ids_from_values_anatomical_site():
    specimen = BigpictureSpecimenFields(
        anatomical_site=frozenset([_code(_BREAST), _code(_AXILLA)])
    )
    result = _concept_ids(_fields(specimen={specimen}))
    assert {_BREAST, _AXILLA} <= result.get("anatomical_site", set())


def test_concept_ids_from_values_fixation_type():
    result = _concept_ids(
        _fields(specimen={BigpictureSpecimenFields(fixation_type=_code(_FFPE))})
    )
    assert _FFPE in result.get("fixation_type", set())

    specimen = BigpictureSpecimenFields(fixation_type=_code("Formalin"))
    result = _concept_ids(_fields(specimen={specimen}))
    assert "Formalin" not in result.get("fixation_type", set())


def test_concept_ids_from_values_staining_procedure():
    stain = BigpictureStainingFields(staining_procedure=_code(_HE))
    result = _concept_ids(_fields(staining={stain}))
    assert _HE in result.get("staining_procedure", set())


def test_concept_ids_from_values_multiple_specimens():
    specimen1 = BigpictureSpecimenFields(animal_species=_code(_SPECIES))
    specimen2 = BigpictureSpecimenFields(block_preparation=_code(_PARAFFIN))
    result = _concept_ids(_fields(specimen={specimen1, specimen2}))
    assert _SPECIES in result.get("animal_species", set())
    assert _PARAFFIN in result.get("block_preparation", set())
