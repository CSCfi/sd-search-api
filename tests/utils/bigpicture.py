"""Bigpicture XML test datasets."""

from pathlib import Path
from typing import Literal, NamedTuple

XML_DIR = Path(__file__).resolve().parent.parent / "files" / "bigpicture" / "xml"

# The datasets of each metadata standard version live under their own directory, beside
# the schemas of that version. A read takes one dataset directory or a parent of several,
# never a parent of those, so a version directory is what it is given rather than the
# root holding them all.
XML_DIR_2_0 = XML_DIR / "2.0"
XML_DIR_3_0 = XML_DIR / "3.0"
VERSION_DIRS = [XML_DIR_2_0, XML_DIR_3_0]

CLINICAL_2_0_DATASET_DIR = XML_DIR_2_0 / "dataset_clinical"
NON_CLINICAL_2_0_DATASET_DIR = XML_DIR_2_0 / "dataset_non_clinical"
CLINICAL_3_0_DATASET_DIR = XML_DIR_3_0 / "dataset_clinical"

# Dataset and image accessions from the example XMLs.
CLINICAL_2_0_DATASET_ID = "bb-dataset-hy4m2v-9tq7cx"
CLINICAL_2_0_IMAGE_1_ID = "bb-image-k3n8pw-6dz2rj"
CLINICAL_2_0_IMAGE_2_ID = "bb-image-q7v5tb-m4hs8n"
CLINICAL_2_0_IMAGE_IDS = [CLINICAL_2_0_IMAGE_1_ID, CLINICAL_2_0_IMAGE_2_ID]

NON_CLINICAL_2_0_DATASET_ID = "bb-dataset-w2j6fd-3npx7k"
NON_CLINICAL_2_0_IMAGE_1_ID = "bb-image-z9c4gs-7bqm2t"
NON_CLINICAL_2_0_IMAGE_2_ID = "bb-image-v6h3rn-8kwd5p"
NON_CLINICAL_2_0_IMAGE_IDS = [
    NON_CLINICAL_2_0_IMAGE_1_ID,
    NON_CLINICAL_2_0_IMAGE_2_ID,
]

CLINICAL_3_0_DATASET_ID = "bb-dataset-p5x8qm-2rv6tw"
CLINICAL_3_0_IMAGE_1_ID = "bb-image-t4m9ks-5xn3hd"
CLINICAL_3_0_IMAGE_2_ID = "bb-image-g7w2cp-9jb4lz"
CLINICAL_3_0_IMAGE_IDS = [CLINICAL_3_0_IMAGE_1_ID, CLINICAL_3_0_IMAGE_2_ID]


class DatasetImage(NamedTuple):
    """The test dataset an image belongs to and its scope."""

    dataset_id: str
    scope: Literal["clinical", "non_clinical"]


# Every image in the test datasets by image id.
DATASET_IMAGES: dict[str, DatasetImage] = {
    **{
        image_id: DatasetImage(CLINICAL_2_0_DATASET_ID, "clinical")
        for image_id in CLINICAL_2_0_IMAGE_IDS
    },
    **{
        image_id: DatasetImage(NON_CLINICAL_2_0_DATASET_ID, "non_clinical")
        for image_id in NON_CLINICAL_2_0_IMAGE_IDS
    },
    **{
        image_id: DatasetImage(CLINICAL_3_0_DATASET_ID, "clinical")
        for image_id in CLINICAL_3_0_IMAGE_IDS
    },
}
