"""Generate data for Bigpicture performance testing."""

import random
import time

import psycopg2

from search_api.database.respository.bigpicture import _load_bigpicture_fields

# uv run python -m tests.performance.bigpicture.generate_data

# Data for 100,000 images generated and loaded successfully in 14.66 seconds.
# Data for 1,000,000 images generated and loaded successfully in 136.73 seconds.
# Data for 10,000,000 images generated and loaded successfully in 1364.68 seconds.

# Maximum time to generate data in seconds.
MAX_TIME = 60 * 60 * 2

# Number of images to generate.
IMAGE_CNT = 1000

# Number of datasets to generate.
DATASET_CNT = 5000

# Maximum number of codes and values.
SEX_MAX_CNT = 2
CODE_MAX_CNT = 3
AGE_AT_EXTRACTION_MAX_CNT = 2

# TODO(improve): If there are too many non-selective values then index may not be used

SELECTIVITY = [
    0.00001,  # outstanding
    0.0001,  # excellent
    0.001,  # high
    0.01,  # 1
    0.05,  # 5
    0.10,  # 10
    0.83889,  # poor
]


def _generate_sex_values() -> list[str]:
    """Generate deduplicated sex values sampled uniformly (poor selectivity)."""
    values = ["Male", "Female", "Not-known", "Other"]
    return list(set(random.choices(values, k=random.randint(0, SEX_MAX_CNT))))


def _generate_code_values() -> list[str]:
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

    return list(
        set(
            random.choices(
                values, weights=SELECTIVITY, k=random.randint(0, CODE_MAX_CNT)
            )
        )
    )


def _generate_age_at_extraction_ranges() -> list[tuple[int, int]]:
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

    return list(
        set(
            random.choices(
                ranges,
                weights=SELECTIVITY,
                k=random.randint(0, AGE_AT_EXTRACTION_MAX_CNT),
            )
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
                TRUNCATE TABLE bp_image;
                TRUNCATE TABLE bp_image_extraction;
            """)

            # Load data.
            #

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

                _load_bigpicture_fields(
                    cur,
                    image_id,
                    dataset_id,
                    dataset_description,
                    species_codes,
                    anatomical_site_codes,
                    sex_values,
                    fixation_type_codes,
                    specimen_type_codes,
                    block_preparation_codes,
                    age_at_extraction_ranges,
                )

        elapsed = time.time() - start_time
        print(
            f"Data for {generated_cnt} images generated and loaded successfully in {elapsed:.2f} seconds."
        )


def main():
    generate_and_load_data()


if __name__ == "__main__":
    main()
