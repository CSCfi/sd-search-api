"""Generate data for Bigpicture performance testing."""

import asyncio
import random
import time
import logging

from search_api.bigpicture.models import BigpictureFields, BigpictureCodeAttributeValue
from search_api.bigpicture.service import load_fields, sync_fields, sync_count
from search_api.database.repository import get_connection

logging.basicConfig(level=logging.INFO)

# uv run python -m tests.performance.bigpicture.generate_data

# Maximum time to generate data in seconds.
MAX_TIME = 60 * 60 * 2

# Number of images to generate.
IMAGE_CNT = 10000000

# Number of datasets to generate.
DATASET_CNT = 5000

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


def _generate_sex_values() -> set[str]:
    """Generate deduplicated sex values with poor selectivity level (25%)."""

    values = ["Male", "Female", "Not-known", "Other"]
    return set(random.choices(values, k=random.randint(0, SEX_MAX_CNT)))


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


def _generate_age_at_extraction_ranges() -> set[tuple[int, int]]:
    """Generate deduplicated age ranges with different selectivity levels."""

    ranges = [
        (1, 2),  # outstanding
        (3, 4),  # excellent
        (5, 6),  # high
        (7, 8),  # 1%
        (9, 10),  # 5%
        (11, 12),  # 10%
        (13, 100),  # poor
    ]

    return set(
        random.choices(
            ranges,
            weights=SELECTIVITY,
            k=random.randint(0, AGE_AT_EXTRACTION_MAX_CNT),
        )
    )


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


async def generate_and_load_data():
    """Generate and load data into the database."""

    start_time = time.time()
    generated_cnt = 0

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            # Truncate tables.
            #

            logging.info("Truncate tables")

            await cur.execute("""
                TRUNCATE TABLE bp_image, bp_image_extraction;
            """)

            assert await sync_count(cur) == 0

            # Load data.
            #

            logging.info(f"Load {IMAGE_CNT} images")

            for i in range(1, IMAGE_CNT + 1):
                if time.time() - start_time > MAX_TIME:
                    print("Time limited exceeded.")
                    break
                generated_cnt += 1

                image_id = f"image{i}"

                dataset_id = f"dataset{((i - 1) % DATASET_CNT) + 1}"

                dataset_description = _generate_description()

                sex_values = _generate_sex_values()

                species_codes = _generate_code_values()
                anatomical_site_codes = _generate_code_values()
                fixation_type_codes = _generate_code_values()
                block_preparation_codes = _generate_code_values()
                specimen_type_codes = _generate_code_values()
                age_at_extraction_ranges = _generate_age_at_extraction_ranges()

                fields = BigpictureFields(
                    image_id=image_id,
                    dataset_id=dataset_id,
                    dataset_description=dataset_description,
                    sex=sex_values,
                    species=species_codes,
                    anatomical_site=anatomical_site_codes,
                    fixation_type=fixation_type_codes,
                    block_preparation=block_preparation_codes,
                    specimen_type=specimen_type_codes,
                    age_at_extraction=age_at_extraction_ranges,
                )

                # Load fields to the database for each image.

                logging.info(f"Loading image '{image_id}' to the database")
                await load_fields(cur, fields)

            elapsed = time.time() - start_time
            print(
                f"Data for {generated_cnt} images generated and loaded into database in {elapsed:.2f} seconds."
            )

            assert await sync_count(cur) == generated_cnt


async def sync_data():
    """Sync data from the database to OpenSearch."""

    logging.info("Syncing images to to OpenSearch")

    start_time = time.time()

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            logging.info(f"Number of images to sync is {await sync_count(cur)}")
            await sync_fields(cur)

            elapsed = time.time() - start_time
            print(f"Images synced to OpenSearch in {elapsed:.2f} seconds.")

            assert await sync_count(cur) == 0


def main():
    async def run():
        await generate_and_load_data()
        await sync_data()

    asyncio.run(run())


if __name__ == "__main__":
    main()
