import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from search_api.api.beacon.models import SNOMED_ONTOLOGY_ID
from search_api.api.bigpicture.extract.document import (
    _get_last_modification_time,
    extract_dataset_documents,
)
from search_api.api.extract_logs import invalid_scheme_log
from search_api.api.opensearch.document import build_document
from search_api.exceptions import UserException


_XML_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent.parent
    / "files"
    / "bigpicture"
    / "xml"
)


CLINICAL_DATASET_DIR = _XML_DIR / "dataset_clinical"
NON_CLINICAL_DATASET_DIR = _XML_DIR / "dataset_non_clinical"


# Accessions as the submitter mints them: <center>-<type>-c{6}-c{6}. Only the
# dataset and the images carry one; everything else is referenced by alias.
CLINICAL_DATASET = "bb-dataset-hy4m2v-9tq7cx"
CLINICAL_IMAGE_1 = "bb-image-k3n8pw-6dz2rj"
CLINICAL_IMAGE_2 = "bb-image-q7v5tb-m4hs8n"
NON_CLINICAL_DATASET = "bb-dataset-w2j6fd-3npx7k"
NON_CLINICAL_IMAGE_1 = "bb-image-z9c4gs-7bqm2t"
NON_CLINICAL_IMAGE_2 = "bb-image-v6h3rn-8kwd5p"


def test_extract_fields_clinical():
    docs = {doc.id: doc for doc in extract_dataset_documents(str(CLINICAL_DATASET_DIR))}
    assert set(docs) == {CLINICAL_IMAGE_1, CLINICAL_IMAGE_2}

    payload = build_document(docs[CLINICAL_IMAGE_1])
    assert payload["image_id"] == CLINICAL_IMAGE_1
    assert payload["dataset_id"] == CLINICAL_DATASET
    assert payload["dataset_description"] == "test_description"

    # The block and biological being are flattened to specimen.
    specimen = payload["specimen"][0]
    assert specimen["block_preparation"] == "5"
    assert specimen["anatomical_site"] == ["2"]
    assert specimen["fixation_type"] == "3"
    assert specimen["specimen_type"] == "4"
    assert specimen["age_at_extraction"] == {"gte": 14600, "lte": 14965}
    assert specimen["sex"] == "Male"
    # Animal species and control terminology are only indexed for non-clinical
    # datasets, even though the clinical fixture's sample.xml has them.
    assert "animal_species" not in specimen
    assert "control_terminology" not in specimen

    stain = payload["staining"][0]
    assert stain["staining_procedure"] == "6"
    # The fixture has both a code and free text (the code takes precedence).
    assert "staining_procedure_other" not in stain
    assert "staining_target" not in stain

    payload2 = build_document(docs[CLINICAL_IMAGE_2])
    stain2 = payload2["staining"][0]
    assert stain2["staining_procedure"] == "7"
    # The fixture has both a code and free text (the code takes precedence).
    assert "staining_procedure_other" not in stain2
    assert stain2["staining_target"] == "pan Cytokeratin"


def test_extract_fields_non_clinical():
    docs = {
        doc.id: doc for doc in extract_dataset_documents(str(NON_CLINICAL_DATASET_DIR))
    }
    assert set(docs) == {NON_CLINICAL_IMAGE_1, NON_CLINICAL_IMAGE_2}

    payload = build_document(docs[NON_CLINICAL_IMAGE_1])
    assert payload["scope"] == "non_clinical"
    # The short name is excluded for non-clinical datasets.
    assert "dataset_short_name" not in payload
    # No clinical diagnosis reaches a non-clinical dataset's observation items.
    assert all("diagnosis" not in item for item in payload["observation"])

    assert payload["image_id"] == NON_CLINICAL_IMAGE_1
    assert payload["dataset_id"] == NON_CLINICAL_DATASET
    assert payload["dataset_description"] == "test_description"

    # One observation item per Finding statement, holding all five of its fields.
    assert payload["observation"] == [
        {
            "finding": "C3137",
            "finding_severity": "C147501",
            "finding_chronicity": "C14141",
            "finding_distribution": "C25253",
            "finding_result_category": "C53529",
            "observation_type": "confirmed",
        }
    ]
    specimen = payload["specimen"][0]
    assert specimen["block_preparation"] == "5"
    assert specimen["anatomical_site"] == ["2"]
    assert specimen["fixation_type"] == "3"
    assert specimen["specimen_type"] == "4"
    assert specimen["age_at_extraction"] == {"gte": 14600, "lte": 14965}
    assert specimen["animal_species"] == "1"
    assert specimen["sex"] == "Male"
    assert specimen["control_terminology"] == "CONTROL"

    stain = payload["staining"][0]
    assert stain["staining_procedure"] == "6"
    # The fixture has both a code and free text (the code takes precedence).
    assert "staining_procedure_other" not in stain
    assert "staining_target" not in stain

    payload2 = build_document(docs[NON_CLINICAL_IMAGE_2])
    stain2 = payload2["staining"][0]
    assert stain2["staining_procedure"] == "7"
    # The fixture has both a code and free text (the code takes precedence).
    assert "staining_procedure_other" not in stain2
    assert stain2["staining_target"] == "pan Cytokeratin"

    # Each image gets only the finding of the observation referencing it.
    assert payload2["observation"] == [
        {
            "finding": "C41428",
            "finding_severity": "C147501",
            "finding_result_category": "C53529",
            "observation_type": "confirmed",
        }
    ]
    # This statement declares no MICHRON or MIDISTR, so neither is indexed.
    assert "finding_chronicity" not in payload2["observation"][0]
    assert "finding_distribution" not in payload2["observation"][0]


def test_extract_diagnoses():
    docs = {doc.id: doc for doc in extract_dataset_documents(str(CLINICAL_DATASET_DIR))}

    def observation_type_by_diagnosis(payload) -> dict[str, str]:
        return {
            item["diagnosis"]: item["observation_type"]
            for item in payload["observation"]
        }

    image1 = observation_type_by_diagnosis(build_document(docs[CLINICAL_IMAGE_1]))
    image2 = observation_type_by_diagnosis(build_document(docs[CLINICAL_IMAGE_2]))

    # A diagnosis stated for the image itself, or stated as Distinct, is confirmed
    # for that image; one reaching several images via another ref is a candidate.
    # Image 1: CASE_REF and SPECIMEN_REF (Distinct, both images), IMAGE_REF
    # (image 1), BIOLOGICAL_BEING_REF and BLOCK_REF (Summary, both images).
    assert image1 == {
        "109355002": "confirmed",
        "254837009": "confirmed",
        "73211009": "confirmed",
        "363346000": "candidate",
        "38341003": "candidate",
    }
    # Image 2 differs only in SLIDE_REF (Distinct, image 2) replacing IMAGE_REF.
    assert image2 == {
        "195967001": "confirmed",
        "254837009": "confirmed",
        "73211009": "confirmed",
        "363346000": "candidate",
        "38341003": "candidate",
    }

    # The equality assertions above already exclude the non-SNOMED code 8500/3 and
    # the Finding statement's 404684003, but name them so a regression is obvious.
    for image in (image1, image2):
        assert "8500/3" not in image
        assert "404684003" not in image


def _copy_clinical_xml_dir(tmp_path: Path) -> Path:
    """Copy the dataset_clinical fixture to tmp_path."""
    dst = tmp_path / "dataset_clinical"
    shutil.copytree(CLINICAL_DATASET_DIR, dst)
    return dst


def _replace_in_xml(path: Path, old: str, new: str) -> None:
    path.write_text(path.read_text().replace(old, new))


def test_extract_requires_dataset_accession(tmp_path):
    root = _copy_clinical_xml_dir(tmp_path)
    _replace_in_xml(
        root / "METADATA" / "dataset.xml",
        f'<DATASET alias="1" accession="{CLINICAL_DATASET}">',
        '<DATASET alias="1">',
    )

    with pytest.raises(ValueError, match="Failed to extract dataset accession"):
        list(extract_dataset_documents(str(root)))


def test_extract_image_id_with_accession_and_alias(tmp_path):
    root = _copy_clinical_xml_dir(tmp_path)
    _replace_in_xml(
        root / "METADATA" / "image.xml",
        f'<IMAGE alias="2" accession="{CLINICAL_IMAGE_2}">',
        '<IMAGE alias="2">',
    )

    docs = {doc.id: doc for doc in extract_dataset_documents(str(root))}

    # Only the first image has an accession. The second document id becomes
    # dataset accession followed by image alias.
    assert set(docs) == {CLINICAL_IMAGE_1, f"{CLINICAL_DATASET}-2"}
    opensearch_doc = build_document(docs[f"{CLINICAL_DATASET}-2"])
    assert opensearch_doc["image_id"] == "2"
    assert opensearch_doc["dataset_id"] == CLINICAL_DATASET


@pytest.fixture
def mock_fs():
    def _factory(info_map: dict) -> MagicMock:
        fs = MagicMock()
        fs.info.side_effect = lambda path: info_map[path]
        return fs

    return _factory


def test_get_last_modification_time_mtime(mock_fs):
    """mtime as a UNIX timestamp (float) is converted to a UTC datetime."""
    ts = 1_700_000_000.0
    expected = datetime.fromtimestamp(ts, tz=timezone.utc)
    fs = mock_fs({"/a": {"mtime": ts}})

    result = _get_last_modification_time(fs, ["/a"])

    assert result == expected
    assert result.tzinfo == timezone.utc


def test_get_last_modification_time_last_modified(mock_fs):
    """last_modified as a tz-aware datetime is returned unchanged."""
    dt = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    fs = mock_fs({"/a": {"last_modified": dt}})

    result = _get_last_modification_time(fs, ["/a"])

    assert result == dt


def test_get_last_modification_time_LastModified(mock_fs):
    """LastModified (S3 key) as a naive datetime gets UTC attached."""
    naive = datetime(2024, 6, 1, 8, 30, 0)
    fs = mock_fs({"/a": {"LastModified": naive}})

    result = _get_last_modification_time(fs, ["/a"])

    assert result == naive.replace(tzinfo=timezone.utc)
    assert result.tzinfo == timezone.utc


def test_get_last_modification_time_returns_max(mock_fs):
    """The newest mtime across multiple paths is returned."""
    older = datetime(2023, 1, 1, tzinfo=timezone.utc)
    newer = datetime(2024, 6, 1, tzinfo=timezone.utc)
    fs = mock_fs({"/a": {"mtime": older}, "/b": {"mtime": newer}})

    result = _get_last_modification_time(fs, ["/a", "/b"])

    assert result == newer


def test_get_last_modification_time_no_times(mock_fs):
    fs = mock_fs({"/a": {"size": 1234}})

    result = _get_last_modification_time(fs, ["/a"])

    assert result is None


def test_get_last_modification_time_no_files():
    fs = MagicMock()

    result = _get_last_modification_time(fs, [])

    assert result is None
    fs.info.assert_not_called()


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Clinical/Pseudonymized", "clinical"),
        ("Non-Clinical/Cryptonymized", "non_clinical"),
        ("Non-Clinical/Cryptonymised", "non_clinical"),
        ("clinical/anonymized", "clinical"),
        ("NON-CLINICAL/OBSCURED", "non_clinical"),
        (" Clinical / Anonymized ", "clinical"),
    ],
)
def test_extract_scope_supported_dataset_types(tmp_path, value, expected):
    root = _copy_clinical_xml_dir(tmp_path)
    _replace_in_xml(root / "METADATA" / "policy.xml", "Clinical/Anonymized", value)

    docs = {doc.id: doc for doc in extract_dataset_documents(str(root))}

    assert build_document(docs[CLINICAL_IMAGE_1])["scope"] == expected


@pytest.mark.parametrize(
    "value",
    [
        "Obscured",
        "Clinical-Anonymized",
        "Preclinical/Obscured",
    ],
)
def test_extract_scope_unsupported_dataset_types(tmp_path, value):
    root = _copy_clinical_xml_dir(tmp_path)
    _replace_in_xml(root / "METADATA" / "policy.xml", "Clinical/Anonymized", value)

    with pytest.raises(UserException, match="Unsupported 'type_of_dataset' value"):
        list(extract_dataset_documents(str(root)))


@pytest.mark.parametrize(
    "old,new",
    [
        ("type_of_dataset", "other_tag"),  # Missing scope attribute
        ("Clinical/Anonymized", ""),  # Empty scope attribute
    ],
)
def test_extract_scope_requires_attribute_and_value(tmp_path, old, new):
    root = _copy_clinical_xml_dir(tmp_path)
    _replace_in_xml(root / "METADATA" / "policy.xml", old, new)

    with pytest.raises(UserException, match="Missing 'type_of_dataset' attribute"):
        list(extract_dataset_documents(str(root)))


def test_extract_scope_requires_policy_file(tmp_path):
    root = _copy_clinical_xml_dir(tmp_path)
    (root / "METADATA" / "policy.xml").unlink()

    with pytest.raises(ValueError, match="Missing file: .*policy.xml"):
        list(extract_dataset_documents(str(root)))


def test_extract_invalid_scheme_error():
    dropped = invalid_scheme_log(
        "diagnosis",
        "8500/3",
        "Infiltrating duct carcinoma, NOS",
        "ICDO",
        SNOMED_ONTOLOGY_ID,
    )

    clinical = {
        doc.id: doc.logs for doc in extract_dataset_documents(str(CLINICAL_DATASET_DIR))
    }
    non_clinical = list(extract_dataset_documents(str(NON_CLINICAL_DATASET_DIR)))

    assert clinical == {CLINICAL_IMAGE_1: [dropped], CLINICAL_IMAGE_2: [dropped]}
    assert all(not doc.logs for doc in non_clinical)
