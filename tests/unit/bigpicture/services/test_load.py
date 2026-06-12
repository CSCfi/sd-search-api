from search_api.bigpicture.models import (
    BigpictureBlockFields,
    BigpictureCodeAttributeValue,
    BigpictureFields,
    BigpictureStainingFields,
)
from search_api.bigpicture.services.load import get_concept_ids

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


def test_get_concept_ids_single_ontology_field():
    fields = _fields(blocks={BigpictureBlockFields(species=_code(_SPECIES))})
    assert _SPECIES in get_concept_ids(fields)


def test_get_concept_ids_list_ontology_field():
    block = BigpictureBlockFields(
        anatomical_site=frozenset([_code(_BREAST), _code(_AXILLA)])
    )
    result = get_concept_ids(_fields(blocks={block}))
    assert {_BREAST, _AXILLA} <= result


def test_get_concept_ids_ontology_or_value_concept():
    """Concept IDs stored in ontologyOrValue fields."""
    fields = _fields(blocks={BigpictureBlockFields(fixation_type=_code(_FFPE))})
    assert _FFPE in get_concept_ids(fields)


def test_get_concept_ids_ontology_or_value_free_text_excluded():
    """Free-text values in ontologyOrValue fields"""
    block = BigpictureBlockFields(
        fixation_type=BigpictureCodeAttributeValue(code="Formalin", meaning="Formalin")
    )
    assert "Formalin" not in get_concept_ids(_fields(blocks={block}))


def test_get_concept_ids_staining_fields():
    stain = BigpictureStainingFields(staining_procedure=_code(_HE))
    assert _HE in get_concept_ids(_fields(stains={stain}))


def test_get_concept_ids_block_fields():
    block1 = BigpictureBlockFields(species=_code(_SPECIES))
    block2 = BigpictureBlockFields(block_preparation=_code(_PARAFFIN))
    result = get_concept_ids(_fields(blocks={block1, block2}))
    assert {_SPECIES, _PARAFFIN} <= result


def test_get_concept_ids_non_concept_fields_ignored():
    """sex and age_at_extraction are not ontology fields — not collected."""
    block = BigpictureBlockFields(sex="Male", age_at_extraction=("P0Y", "P1Y"))
    result = get_concept_ids(_fields(blocks={block}))
    assert "Male" not in result
    assert result == set()
