from search_api.api.opensearch.document import build_document
from search_api.api.opensearch.models import ExtractedDocument
from search_api.api.bigpicture.extract.document import to_opensearch_values
from search_api.api.bigpicture.extract.models import (
    BigpictureCodeAttributeValue,
    BigpictureFields,
    BigpictureSpecimenFields,
    BigpictureStainingFields,
)

# A concept id and the preferred term SNOMED CT gives it, since an extracted value
# carries both.
_SPECIES_CODE, _SPECIES_MEANING = "337915000", "Homo sapiens"
_BREAST_CODE, _BREAST_MEANING = "80248007", "Left breast structure"
_UPPER_ARM_CODE, _UPPER_ARM_MEANING = "368209003", "Right upper arm structure"
_FIXATIVE_CODE, _FIXATIVE_MEANING = "1388477003", "Tissue fixative"
_PARAFFIN_CODE, _PARAFFIN_MEANING = "311731000", "Paraffin wax"
_HE_CODE, _HE_MEANING = "104210008", "Hematoxylin and eosin stain method"


def _fields(*, scope: str = "clinical", **kwargs) -> BigpictureFields:
    return BigpictureFields(
        image_id="img", dataset_id="ds", dataset_image_cnt=1, scope=scope, **kwargs
    )


def test_to_opensearch_values():
    fields = _fields(
        dataset_title="A title",
        specimen={
            BigpictureSpecimenFields(
                animal_species=_code(_SPECIES_CODE, _SPECIES_MEANING),
                anatomical_site=frozenset(
                    [
                        _code(_BREAST_CODE, _BREAST_MEANING),
                        _code(_UPPER_ARM_CODE, _UPPER_ARM_MEANING),
                    ]
                ),
                age_at_extraction=("P40Y", "P41Y"),
                sex="Female",
            )
        },
        staining={BigpictureStainingFields(staining_target="Nucleus")},
    )
    values, groups = to_opensearch_values(fields)
    payload = build_document(ExtractedDocument(id="img", values=values, groups=groups))

    assert payload["image_id"] == "img"
    assert payload["dataset_id"] == "ds"
    assert payload["dataset_image_cnt"] == 1
    assert payload["dataset_title"] == "A title"
    specimen = payload["specimen"][0]
    assert specimen["animal_species"] == _SPECIES_CODE
    assert sorted(specimen["anatomical_site"]) == sorted(
        [_BREAST_CODE, _UPPER_ARM_CODE]
    )
    assert specimen["age_at_extraction"] == {"gte": 14600, "lte": 14965}
    assert specimen["sex"] == "Female"
    assert payload["staining"][0]["staining_target"] == "Nucleus"


def _code(code: str, meaning: str) -> BigpictureCodeAttributeValue:
    return BigpictureCodeAttributeValue(code=code, meaning=meaning)


def _ontology_values(
    fields: BigpictureFields,
) -> dict[str, set[tuple[str | None, str | None]]]:
    """Return the provided (concept id, meaning) pairs, by field id."""
    values, groups = to_opensearch_values(fields)
    pairs: dict[str, set[tuple[str | None, str | None]]] = {}
    for value in ExtractedDocument(id="img", values=values, groups=groups).all_values:
        if isinstance(value.value, tuple):
            pairs.setdefault(value.field.id, set()).add(value.value)
    return pairs


def test_ontology_value_animal_species():
    specimen = BigpictureSpecimenFields(
        animal_species=_code(_SPECIES_CODE, _SPECIES_MEANING)
    )

    assert _ontology_values(_fields(specimen={specimen}))["animal_species"] == {
        (_SPECIES_CODE, _SPECIES_MEANING)
    }


def test_ontology_value_anatomical_site():
    """A multivalued field carries the pair of every value it holds."""
    specimen = BigpictureSpecimenFields(
        anatomical_site=frozenset(
            [
                _code(_BREAST_CODE, _BREAST_MEANING),
                _code(_UPPER_ARM_CODE, _UPPER_ARM_MEANING),
            ]
        )
    )

    assert _ontology_values(_fields(specimen={specimen}))["anatomical_site"] == {
        (_BREAST_CODE, _BREAST_MEANING),
        (_UPPER_ARM_CODE, _UPPER_ARM_MEANING),
    }


def test_ontology_value_fixation_type():
    specimen = BigpictureSpecimenFields(
        fixation_type=_code(_FIXATIVE_CODE, _FIXATIVE_MEANING)
    )
    assert _ontology_values(_fields(specimen={specimen}))["fixation_type"] == {
        (_FIXATIVE_CODE, _FIXATIVE_MEANING)
    }


def test_ontology_value_staining_procedure():
    stain = BigpictureStainingFields(staining_procedure=_code(_HE_CODE, _HE_MEANING))

    assert _ontology_values(_fields(staining={stain}))["staining_procedure"] == {
        (_HE_CODE, _HE_MEANING)
    }


def test_ontology_values_of_multiple_specimens():
    specimen1 = BigpictureSpecimenFields(
        animal_species=_code(_SPECIES_CODE, _SPECIES_MEANING)
    )
    specimen2 = BigpictureSpecimenFields(
        block_preparation=_code(_PARAFFIN_CODE, _PARAFFIN_MEANING)
    )

    pairs = _ontology_values(_fields(specimen={specimen1, specimen2}))

    assert pairs["animal_species"] == {(_SPECIES_CODE, _SPECIES_MEANING)}
    assert pairs["block_preparation"] == {(_PARAFFIN_CODE, _PARAFFIN_MEANING)}
