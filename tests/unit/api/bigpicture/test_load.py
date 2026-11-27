import pytest

from search_api.api.bigpicture.domain import BP_DOMAIN
from search_api.api.bigpicture.extract.document import to_opensearch_values
from search_api.api.bigpicture.extract.models import (
    BigpictureCodeAttributeValue,
    BigpictureFields,
    BigpictureObservationFields,
    BigpictureSpecimenFields,
    BigpictureStainingFields,
)
from search_api.api.opensearch.document import build_document
from search_api.api.opensearch.models import ExtractedDocument
from search_api.database.models import StoredDocumentLog
from search_api.services.load import (
    LoadService,
    drop_out_of_scope_values,
    drop_values_without_required,
)
from search_api.services.ontology.term_cache import create_term_caches

# A concept id and the preferred term SNOMED CT gives it, since an extracted value
# carries both.
_SPECIES_CODE, _SPECIES_MEANING = "337915000", "Homo sapiens"
_BREAST_CODE, _BREAST_MEANING = "80248007", "Left breast structure"
_UPPER_ARM_CODE, _UPPER_ARM_MEANING = "368209003", "Right upper arm structure"
_FIXATIVE_CODE, _FIXATIVE_MEANING = "1388477003", "Tissue fixative"
_PARAFFIN_CODE, _PARAFFIN_MEANING = "311731000", "Paraffin wax"
_HE_CODE, _HE_MEANING = "104210008", "Hematoxylin and eosin stain method"
_MELANOMA_CODE, _MELANOMA_MEANING = "93655004", "Malignant melanoma of skin"
# Not looked up, so any code will do.
_FINDING_CODE, _FINDING_MEANING = "C1", "A finding"


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


def _drop_values(
    scope: str, observation: BigpictureObservationFields
) -> tuple[ExtractedDocument, list[StoredDocumentLog]]:
    """Drop the values a load drops for scope and requires.

    :param scope: The document's scope.
    :param observation: The document's one observation.
    :return: The document, with the values dropped, and a log entry for each.
    """
    values, groups = to_opensearch_values(
        _fields(scope=scope, observation={observation})
    )
    doc = ExtractedDocument(id="img", scope=scope, values=values, groups=groups)
    service = LoadService(
        create_term_caches(BP_DOMAIN.ontology_ids), BP_DOMAIN.filtering_terms
    )
    logs = drop_out_of_scope_values(doc, service._field_scopes)
    logs += drop_values_without_required(doc, service._required_fields)
    return doc, logs


@pytest.mark.parametrize(
    ("scope", "observation"),
    [
        (
            "clinical",
            BigpictureObservationFields(
                diagnosis=_code(_MELANOMA_CODE, _MELANOMA_MEANING),
                observation_type="distinct",
            ),
        ),
        # Any one of the finding fields is enough.
        (
            "non_clinical",
            BigpictureObservationFields(
                finding_severity=_code(_FINDING_CODE, _FINDING_MEANING),
                observation_type="distinct",
            ),
        ),
    ],
)
def test_observation_in_scope_is_indexed(scope, observation):
    """With the Bigpicture fields, a diagnosis in a clinical document, or a
    finding field in a non-clinical one, has observation_type."""
    doc, logs = _drop_values(scope, observation)

    assert build_document(doc)["observation"][0]["observation_type"] == "distinct"
    assert logs == []


@pytest.mark.parametrize(
    ("scope", "field_id", "observation"),
    [
        (
            "non_clinical",
            "diagnosis",
            BigpictureObservationFields(
                diagnosis=_code(_MELANOMA_CODE, _MELANOMA_MEANING),
                observation_type="distinct",
            ),
        ),
        (
            "clinical",
            "finding",
            BigpictureObservationFields(
                finding=_code(_FINDING_CODE, _FINDING_MEANING),
                observation_type="distinct",
            ),
        ),
    ],
)
def test_observation_outside_scope_is_not_indexed(scope, field_id, observation):
    """With the Bigpicture fields, a diagnosis in a non-clinical document, or a
    finding in a clinical one, leaves no observation. The scope drop removes the
    diagnosis or finding. The requires drop then removes the observation_type."""
    doc, logs = _drop_values(scope, observation)

    assert "observation" not in build_document(doc)
    assert [(log.field_id, log.severity, log.message) for log in logs] == [
        (
            field_id,
            "WARNING",
            f"Value is ignored: the field is not indexed for scope '{scope}'.",
        ),
        (
            "observation_type",
            "WARNING",
            "Value is ignored: none of the fields it requires has a value.",
        ),
    ]
