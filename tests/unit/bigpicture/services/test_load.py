from search_api.bigpicture.models import (
    BigpictureBlockFields,
    BigpictureCodeAttributeValue,
    BigpictureFields,
    BigpictureStainingFields,
)
from search_api.bigpicture.services.load import (
    deserialize_fields,
    get_concept_ids_by_field,
    serialize_fields,
)

_SPECIES = "337915000"
_BREAST = "80248007"
_AXILLA = "368209003"
_FFPE = "1388477003"
_PARAFFIN = "311731000"
_HE = "406768004"


def _fields(**kwargs) -> BigpictureFields:
    return BigpictureFields(
        image_id="img", dataset_id="ds", dataset_image_cnt=1, **kwargs
    )


def _code(code: str) -> BigpictureCodeAttributeValue:
    return BigpictureCodeAttributeValue(code=code, meaning=code)


def test_get_concept_ids_by_field_animal_species():
    result = get_concept_ids_by_field(
        _fields(blocks={BigpictureBlockFields(animal_species=_code(_SPECIES))})
    )
    assert _SPECIES in result.get("animal_species", set())


def test_get_concept_ids_by_field_anatomical_site():
    block = BigpictureBlockFields(
        anatomical_site=frozenset([_code(_BREAST), _code(_AXILLA)])
    )
    result = get_concept_ids_by_field(_fields(blocks={block}))
    assert {_BREAST, _AXILLA} <= result.get("anatomical_site", set())


def test_get_concept_ids_by_field_fixation_type():
    fields = _fields(blocks={BigpictureBlockFields(fixation_type=_code(_FFPE))})
    result = get_concept_ids_by_field(fields)
    assert _FFPE in result.get("fixation_type", set())

    block = BigpictureBlockFields(
        fixation_type=BigpictureCodeAttributeValue(code="Formalin", meaning="Formalin")
    )
    result = get_concept_ids_by_field(_fields(blocks={block}))
    assert "Formalin" not in result.get("fixation_type", set())


def test_get_concept_ids_by_field_staining_procedure():
    stain = BigpictureStainingFields(staining_procedure=_code(_HE))
    result = get_concept_ids_by_field(_fields(stains={stain}))
    assert _HE in result.get("staining_procedure", set())


def test_get_concept_ids_by_field_multiple_blocks():
    block1 = BigpictureBlockFields(animal_species=_code(_SPECIES))
    block2 = BigpictureBlockFields(block_preparation=_code(_PARAFFIN))
    result = get_concept_ids_by_field(_fields(blocks={block1, block2}))
    assert _SPECIES in result.get("animal_species", set())
    assert _PARAFFIN in result.get("block_preparation", set())


def test_serialize_fields():
    block = BigpictureBlockFields(
        animal_species=_code(_SPECIES),
        anatomical_site=frozenset([_code(_BREAST)]),
        fixation_type=_code(_FFPE),
        fixation_type_text="Formalin",
        age_at_extraction=("P40Y", "P41Y"),
        sex="Female",
    )
    assert serialize_fields(block) == {
        "animal_species": _SPECIES,
        "anatomical_site": [_BREAST],
        "fixation_type": _FFPE,
        "fixation_type_text": "Formalin",
        "age_at_extraction": {"gte": "P40Y", "lte": "P41Y"},
        "sex": "Female",
    }


def test_serialize_fields_empty():
    assert serialize_fields(BigpictureBlockFields()) == {}


def test_serialize_and_deserialize_block():
    block = BigpictureBlockFields(
        animal_species=_code(_SPECIES),
        anatomical_site=frozenset([_code(_BREAST), _code(_AXILLA)]),
        fixation_type=_code(_FFPE),
        fixation_type_text="Formalin",
        specimen_type=_code(_PARAFFIN),
        age_at_extraction=("P40Y", "P41Y"),
        block_preparation=_code(_PARAFFIN),
        sex="Female",
    )
    assert deserialize_fields(BigpictureBlockFields, serialize_fields(block)) == block


def test_serialize_and_deserialize_stain():
    stain = BigpictureStainingFields(
        staining_procedure=_code(_HE),
        staining_procedure_text="H&E",
        staining_substance=_code(_BREAST),
        staining_substance_text="Eosin",
        staining_target="Nucleus",
    )
    assert (
        deserialize_fields(BigpictureStainingFields, serialize_fields(stain)) == stain
    )
