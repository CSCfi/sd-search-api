import random
import time
from typing import Sequence

import psycopg2

# uv run tests/performance/generate_data.py

# Data for 100,000 images generated and loaded successfully in 14.66 seconds.
# Data for 1,000,000 images generated and loaded successfully in 136.73 seconds.

# Maximum time to generate data in seconds.
MAX_TIME = 600

# Number of images to generate.
IMAGE_CNT = 1000000

# Number of datasets to generate.
DATASET_CNT = 5000

# Number of distinct values in category and code array columns (GIN indexed).
# Multiple occurrences of the same values should be de‑duplicated during data loading.
SPECIES_DISTINCT_VALUE_CNT = 10
ANATOMICAL_DISTINCT_VALUE_CNT = 100
SEX_DISTINCT_VALUE_CNT = 2
FIXATION_DISTINCT_VALUE_CNT = 25
BLOCK_DISTINCT_VALUE_CNT = 25
SPECIMEN_DISTINCT_VALUE_CNT = 25

# Maximum number of values category and code array columns (GIN indexed).
SPECIES_MAX_CNT = 3
ANATOMICAL_MAX_CNT = 3
SEX_MAX_CNT = 2
FIXATION_MAX_CNT = 3
BLOCK_PREP_MAX_CNT = 3
SPECIMEN_MAX_CNT = 3

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

CODES = {
    "species": [f"species{i}" for i in range(1, SPECIES_DISTINCT_VALUE_CNT + 1)],
    "anatomical_site": [
        f"anatomical_site{i}" for i in range(1, ANATOMICAL_DISTINCT_VALUE_CNT + 1)
    ],
    "sex": [f"sex{i}" for i in range(1, SEX_DISTINCT_VALUE_CNT + 1)],
    "fixation_type": [
        f"fixation_type{i}" for i in range(1, FIXATION_DISTINCT_VALUE_CNT + 1)
    ],
    "block_preparation": [
        f"block_preparation{i}" for i in range(1, BLOCK_DISTINCT_VALUE_CNT + 1)
    ],
    "specimen_type": [
        f"specimen_type{i}" for i in range(1, SPECIMEN_DISTINCT_VALUE_CNT + 1)
    ],
}


def random_codes(values: Sequence[str], max_items: int) -> list[str]:
    """Return a list of random codes with 0..max_items elements."""

    size = random.randint(0, min(max_items, len(values)))
    return random.sample(values, size)


def random_description() -> str:
    """Return a random description."""

    return random.choice(DESCRIPTIONS)


def generate_and_load_data():
    start_time = time.time()
    generated_cnt = 0

    with psycopg2.connect(
        host="localhost", dbname="sd_search", user="postgres", password="test"
    ) as conn:
        conn.autocommit = True

        with conn.cursor() as cur:
            # Truncate tables.
            #

            cur.execute("""
                TRUNCATE TABLE bp_sample;
            """)

            # Insert into bp_sample table.
            #

            for i in range(1, IMAGE_CNT + 1):
                if time.time() - start_time > MAX_TIME:
                    print("Time limited exceeded.")
                    break
                generated_cnt += 1

                image_id = f"image{i}"

                dataset_id = f"dataset{((i - 1) % DATASET_CNT) + 1}"

                description = random_description()

                species = random_codes(CODES["species"], SPECIES_MAX_CNT)
                anatomical_site = random_codes(
                    CODES["anatomical_site"], ANATOMICAL_MAX_CNT
                )
                sex = random_codes(CODES["sex"], SEX_MAX_CNT)
                fixation = random_codes(CODES["fixation_type"], FIXATION_MAX_CNT)
                block_prep = random_codes(
                    CODES["block_preparation"], BLOCK_PREP_MAX_CNT
                )
                specimen = random_codes(CODES["specimen_type"], SPECIMEN_MAX_CNT)

                age_at_extraction_cnt = random.randint(1, 5)
                age_at_extraction = set()
                for _ in range(age_at_extraction_cnt):
                    start_age = random.randint(10, 80)
                    end_age = random.randint(start_age, 100)
                    for age in range(start_age, end_age + 1):
                        age_at_extraction.add(age)

                cur.execute(
                    """
                    INSERT INTO bp_sample (
                        image_id,
                        dataset_id,
                        dataset_description,
                        species,
                        anatomical_site,
                        sex,
                        fixation_type,
                        block_preparation,
                        specimen_type,
                        age_at_extraction

                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        image_id,
                        dataset_id,
                        description,
                        species,
                        anatomical_site,
                        sex,
                        fixation,
                        block_prep,
                        specimen,
                        list(age_at_extraction),
                    ),
                )

        elapsed = time.time() - start_time
        print(
            f"Data for {i} images generated and loaded successfully in {elapsed:.2f} seconds."
        )


def main():
    generate_and_load_data()


if __name__ == "__main__":
    main()
