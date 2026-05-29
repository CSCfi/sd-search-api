"""Generate data for Bigpicture performance testing."""

import argparse
import asyncio
import json
import logging
import random
import string
import time
from pathlib import Path

from search_api.api.bigpicture.services.beacon import BP_OPENSEARCH_INDEX
from search_api.bigpicture.models import (
    BigpictureFields,
    BigpictureCodeAttributeValue,
    BigpictureStainingFields,
    BigpictureBlockFields,
)
from search_api.bigpicture.service import load_fields, sync_fields, sync_count
from search_api.database.repository import get_connection
from search_api.services.search import bp_search

_INDEX_MAPPING_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "search_api"
    / "opensearch"
    / "bigpicture"
    / "bp-image-index.json"
)

logging.basicConfig(level=logging.INFO)

# uv run python -m tests.utils.generate_data --images 1000 --datasets 100

# Maximum time to generate data in seconds.
MAX_TIME = 60 * 60 * 2

# Maximum number of codes and values.
SEX_MAX_CNT = 2
CODE_MAX_CNT = 3
AGE_AT_EXTRACTION_MAX_CNT = 2

SELECTIVITY = [
    0.00001,  # outstanding (0.001%)
    0.0001,  # excellent (0.01%)
    0.001,  # high (0.1%)
    0.01,  # 1%
    0.05,  # 5%
    0.10,  # 10%
    0.83889,  # poor (83.9%)
]


def _generate_sex_value() -> str:
    """Generate sex values with poor selectivity level (25%)."""

    values = ["Male", "Female", "Not-known", "Other"]
    return random.choices(values, k=1)[0]


def _generate_code_value() -> BigpictureCodeAttributeValue:
    """Generate code value with different selectivity levels."""

    values = [
        "outstanding",
        "excellent",
        "high",
        "1",
        "5",
        "10",
        "poor",
    ]

    code = random.choices(values, weights=SELECTIVITY, k=1)[0]
    return BigpictureCodeAttributeValue(code=code, meaning=code)


def _generate_code_values() -> set[BigpictureCodeAttributeValue]:
    """Generate deduplicated code values with different selectivity levels."""

    values = [
        "outstanding",
        "excellent",
        "high",
        "1",
        "5",
        "10",
        "poor",
    ]

    generated_values = set()
    for code in random.choices(
        values, weights=SELECTIVITY, k=random.randint(0, CODE_MAX_CNT)
    ):
        generated_values.add(BigpictureCodeAttributeValue(code=code, meaning=code))
    return generated_values


def _generate_age_at_extraction_range() -> tuple[int, int]:
    """Generate age ranges with different selectivity levels."""

    ranges = [
        (1, 2),  # outstanding
        (3, 4),  # excellent
        (5, 6),  # high
        (7, 8),  # 1%
        (9, 10),  # 5%
        (11, 12),  # 10%
        (13, 100),  # poor
    ]

    return random.choices(ranges, weights=SELECTIVITY, k=1)[0]


def _generate_staining_method() -> str:
    """Generate staining method."""

    values = [
        "chemical",
        "immunostaining",
        "in situ hybridization",
    ]

    return random.choice(values)


def _generate_short_name() -> str:
    """Return a random short name."""
    base = ["Atlas", "Helix", "Astra", "Orion", "Nexus", "Vivo", "Index", "Basis"]
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{random.choice(base)}-{suffix}"


def _generate_title() -> str:
    """Return a random title."""
    words = [
        "Histology",
        "Imaging",
        "Pathology",
        "Microscopy",
        "Tissue",
        "Anatomical",
        "Cellular",
        "Atlas",
        "Reference",
        "Collection",
        "Dataset",
    ]
    return " ".join(random.sample(words, k=random.choice([3, 4, 5])))


DESCRIPTIONS = [
    "This dataset contains a variety of biological microscopy images used for testing classification models.",
    "The samples represent diverse anatomical regions and were collected under controlled laboratory conditions.",
    "Image acquisitions followed standardized preparation protocols to ensure reproducibility.",
    "These data support evaluation of segmentation and annotation workflows for biomedical pipelines.",
    "This dataset includes heterogeneous samples prepared across multiple extraction methodologies.",
    "The collection includes high-resolution imaging data across multiple magnification levels.",
    "Samples were curated to reflect variability across tissue types and experimental conditions.",
    "All images were captured using calibrated instruments to maintain consistency.",
    "The dataset enables benchmarking of image preprocessing and normalization techniques.",
    "Data annotations were generated using semi-automated labeling tools with expert validation.",
    "The dataset is intended for training and validating machine learning models in life sciences.",
    "Images were sourced from multiple experimental batches to capture natural variation.",
    "Quality control procedures were applied to remove artifacts and corrupted samples.",
    "The dataset supports comparative analysis across different imaging modalities.",
    "Each sample is accompanied by metadata describing acquisition parameters and context.",
    "The dataset facilitates development of robust feature extraction methods.",
    "Images include both labeled and unlabeled examples for supervised and unsupervised learning.",
    "The collection spans a wide range of biological structures and cellular morphologies.",
    "Standardized file formats were used to ensure compatibility with common analysis tools.",
    "The dataset can be used to evaluate generalization across unseen biological conditions.",
    "Data were aggregated from multiple studies to increase diversity and coverage.",
    "The dataset includes variations in staining techniques and imaging protocols.",
    "Preprocessing steps were applied uniformly across all samples.",
    "The dataset supports reproducible research and model comparison.",
    "Images were anonymized and processed to remove any identifying information.",
    "This dataset is suitable for testing data augmentation and transformation strategies.",
    "The samples exhibit variability in contrast, brightness, and noise levels.",
    "Ground truth labels were curated through expert review processes.",
    "The dataset enables end-to-end evaluation of computer vision pipelines.",
    "Data distribution reflects realistic experimental variability encountered in practice.",
]


def _generate_description() -> str:
    """Return a random description."""

    return random.choice(DESCRIPTIONS)


async def generate_and_load_data(image_cnt: int, dataset_cnt: int) -> None:
    """Generate and load data into the database."""

    start_time = time.time()
    generated_cnt = 0

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            # Truncate tables.
            #

            logging.info("Truncate tables")

            await cur.execute("""
                TRUNCATE TABLE bp_image
            """)

            assert await sync_count(cur) == 0

            # Load data.
            #

            logging.info(f"Load {image_cnt} images")

            # Get dataset image count.
            base = image_cnt // dataset_cnt
            remainder = image_cnt % dataset_cnt
            dataset_image_cnt = {
                f"dataset{k}": base + (1 if k <= remainder else 0)
                for k in range(1, dataset_cnt + 1)
            }

            for i in range(1, image_cnt + 1):
                if time.time() - start_time > MAX_TIME:
                    print("Time limited exceeded.")
                    break
                generated_cnt += 1

                image_id = f"image{i}"
                dataset_id = f"dataset{((i - 1) % dataset_cnt) + 1}"

                fields = BigpictureFields(
                    image_id=image_id,
                    dataset_id=dataset_id,
                    dataset_image_cnt=dataset_image_cnt[dataset_id],
                    dataset_short_name=_generate_short_name(),
                    dataset_title=_generate_title(),
                    dataset_description=_generate_description(),
                    blocks={
                        BigpictureBlockFields(
                            sex=_generate_sex_value(),
                            species=_generate_code_value(),
                            anatomical_site=_generate_code_value(),
                            fixation_type=_generate_code_value(),
                            block_preparation=_generate_code_value(),
                            specimen_type=_generate_code_value(),
                            age_at_extraction=_generate_age_at_extraction_range(),
                        )
                    },
                    stains={
                        BigpictureStainingFields(
                            staining_method=_generate_staining_method(),
                            staining_procedure=_generate_code_value(),
                            staining_procedure_text=f"{_generate_code_value().code}",
                            staining_compound=_generate_code_value(),
                            staining_compound_text=f"{_generate_code_value().code}",
                            staining_target=f"{_generate_code_value().code}",
                        )
                    },
                )

                # Load fields to the database for each image.

                logging.info(f"Loading image '{image_id}' to the database")
                await load_fields(cur, fields)

            elapsed = time.time() - start_time
            print(
                f"Data for {generated_cnt} images generated and loaded into database in {elapsed:.2f} seconds."
            )

            assert await sync_count(cur) == generated_cnt


async def ensure_index() -> None:
    """Create the OpenSearch index from the mapping file if it does not already exist."""

    if await bp_search.indices.exists(index=BP_OPENSEARCH_INDEX):
        logging.info(
            f"Index '{BP_OPENSEARCH_INDEX}' already exists, skipping creation."
        )
        return

    index_conf = json.loads(_INDEX_MAPPING_PATH.read_text())
    logging.info(f"Creating index '{BP_OPENSEARCH_INDEX}'")
    await bp_search.indices.create(index=BP_OPENSEARCH_INDEX, body=index_conf)
    logging.info(f"Index '{BP_OPENSEARCH_INDEX}' created.")


async def sync_data() -> None:
    """Sync data from the database to OpenSearch."""

    logging.info("Syncing images to OpenSearch")

    start_time = time.time()

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            logging.info(f"Number of images to sync is {await sync_count(cur)}")
            await sync_fields(cur)

            elapsed = time.time() - start_time
            print(f"Images synced to OpenSearch in {elapsed:.2f} seconds.")

            assert await sync_count(cur) == 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate and load Bigpicture test data."
    )
    parser.add_argument(
        "--images",
        type=int,
        default=10000,
        help="Number of images to generate (default: 10000).",
    )
    parser.add_argument(
        "--datasets",
        type=int,
        default=50000,
        help="Number of datasets to generate (default: 50000).",
    )
    args = parser.parse_args()

    async def run() -> None:
        await ensure_index()
        await generate_and_load_data(args.images, args.datasets)
        await sync_data()

    asyncio.run(run())


if __name__ == "__main__":
    main()
