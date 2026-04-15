"""Bigpicture services."""

import logging

from psycopg2.extras import NumericRange  # type: ignore
from psycopg2.extensions import cursor  # type: ignore

from search_api.bigpicture.models import BigpictureFields, BigpictureCodeAttributeValue
from search_api.database.repository import get_cursor
from search_api.services.search import bp_index_document

logging.basicConfig(level=logging.INFO)


def load_fields(cur: cursor, fields: BigpictureFields) -> None:
    """
    Load Bigpicture fields for one image into the database.

    :param cur: The database cursor.
    :param fields: The Bigpicture fields for one image.
    """

    def _get_codes(
        items: set[BigpictureCodeAttributeValue] | None,
    ) -> list[str] | None:
        if not items:
            return None
        return [item.code for item in items if item is not None]

    # Get ontology codes.
    species_codes = _get_codes(fields.species)
    anatomical_site_codes = _get_codes(fields.anatomical_site)
    fixation_type_codes = _get_codes(fields.fixation_type)
    specimen_type_codes = _get_codes(fields.specimen_type)
    block_preparation_codes = _get_codes(fields.block_preparation)

    # Get controlled values.
    sex_values = list(fields.sex)

    # Get age at extraction values.
    age_at_extraction_values = []
    for value in fields.age_at_extraction:
        if not value:
            continue
        start, end = value
        age_at_extraction_values.append((start, end))

    _load_fields(
        cur,
        fields.image_id,
        fields.dataset_id,
        fields.dataset_description,
        species_codes,
        anatomical_site_codes,
        sex_values,  # type: ignore
        fixation_type_codes,
        specimen_type_codes,
        block_preparation_codes,
        age_at_extraction_values,
    )


def _load_fields(
    cur: cursor,
    image_id: str,
    dataset_id: str,
    dataset_description: str | None,
    species_codes: list[str] | None,
    anatomical_site_codes: list[str] | None,
    sex_values: list[str] | None,
    fixation_type_codes: list[str] | None,
    specimen_type_codes: list[str] | None,
    block_preparation_codes: list[str] | None,
    age_at_extraction_ranges: list[tuple[int, int]] | None,
) -> None:
    """
    Load Bigpicture fields for one image into the database.

    :param cur: The database cursor.
    :param image_id: Unique identifier of the image.
    :param dataset_id: Unique Identifier of the dataset the image belongs to.
    :param dataset_description: Description of the dataset.
    :param species_codes: List of species codes.
    :param anatomical_site_codes: List of anatomical site codes.
    :param sex_values: List of sex values (Male, Female, Not-known, Other).
    :param fixation_type_codes: List of fixation type codes.
    :param specimen_type_codes: List of specimen type codes.
    :param block_preparation_codes: List of block preparation codes.
    :param age_at_extraction_ranges: List of ages at extraction ranges.
    """

    # GIN indexed values do not have to be sorted, but
    # this may be convenient when manually looking at
    # the values in the database.

    cur.execute(
        """
        INSERT INTO bp_image (
            image_id,
            dataset_id,
            dataset_description,
            species,
            anatomical_site,
            sex,
            fixation_type,
            specimen_type,
            block_preparation
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            image_id,
            dataset_id,
            dataset_description,  # text into GIN indexes TEXT field
            species_codes,  # codes into GIN indexed TEXT[] field
            anatomical_site_codes,  # codes into GIN indexed TEXT[] field
            sex_values,  # values into GIN indexed TEXT[] field
            fixation_type_codes,  # codes into GIN indexed TEXT[] field
            specimen_type_codes,  # codes into GIN indexed TEXT[] field
            block_preparation_codes,  # codes into GIN indexed TEXT[] field
        ),
    )

    #  ON CONFLICT (image_id) DO UPDATE
    #             SET
    #             dataset_id = EXCLUDED.dataset_id,
    #             dataset_description = EXCLUDED.dataset_description,
    #             species = EXCLUDED.species,
    #             anatomical_site = EXCLUDED.anatomical_site,
    #             sex = EXCLUDED.sex,
    #             fixation_type = EXCLUDED.fixation_type,
    #             specimen_type = EXCLUDED.specimen_type,
    #             block_preparation = EXCLUDED.block_preparation

    if age_at_extraction_ranges:
        for age_at_extraction_range in age_at_extraction_ranges:
            cur.execute(
                """
                INSERT INTO bp_image_extraction (
                    image_id,
                    age_at_extraction
                )
                VALUES (%s, %s)
                """,
                (
                    image_id,
                    NumericRange(age_at_extraction_range[0], age_at_extraction_range[1])
                    if age_at_extraction_range
                    else None,  # int range into GIST indexed int4range field
                ),
            )


#  ON CONFLICT (image_id) DO UPDATE
#                 SET
#                     age_at_extraction = EXCLUDED.age_at_extraction;


def sync_fields(cur: cursor) -> None:
    """
    Sync database fields to OpenSearch.

    :param cur: The database cursor.
    """

    # Find imaged to sync to OpenSearch.

    logging.info("Find images to sync to OpenSearch.")

    with get_cursor() as update_cur:
        cur.execute("""
            SELECT
                bp_image.image_id,
                bp_image.dataset_id,
                bp_image.dataset_description,
                bp_image.species,
                bp_image.anatomical_site,
                bp_image.sex,
                bp_image.fixation_type,
                bp_image.block_preparation,
                bp_image.specimen_type,
                bp_image_extraction.age_at_extraction
            FROM bp_image
            LEFT JOIN bp_image_extraction USING (image_id)
            WHERE bp_image.search_sync = false
        """)

        for row in cur:
            (
                image_id,
                dataset_id,
                dataset_description,
                species,
                anatomical_site,
                sex,
                fixation_type,
                block_preparation,
                specimen_type,
                age_at_extraction,
            ) = row

            # Create OpenSearch document for indexing.

            doc = {
                "image_id": image_id,
                "dataset_id": dataset_id,
                "dataset_description": dataset_description,
                "species": species or [],
                "anatomical_site": anatomical_site or [],
                "sex": sex or [],
                "fixation_type": fixation_type or [],
                "block_preparation": block_preparation or [],
                "specimen_type": specimen_type or [],
            }

            if age_at_extraction is not None:
                doc["age_at_extraction"] = {
                    "gte": age_at_extraction.lower,
                    "lte": age_at_extraction.upper,
                }

            # Index the document in OpenSearch.

            logging.info(f"Index image {doc['image_id']} to OpenSearch.")

            bp_index_document(doc)

            # Update OpenSearch state in database.

            logging.info(
                f"Update image {doc['image_id']} OpenSearch state in database."
            )

            update_cur.execute(
                """
                UPDATE bp_image
                SET
                    search_sync = true,
                    search_sync_date = now()
                WHERE image_id = %s
                """,
                (image_id,),
            )


def sync_count(cur: cursor) -> int:
    """
    Return number of images to sync to OpenSearch.

    :param cur: The database cursor.
    :return: THe number of images to sync to OpenSearch.
    """

    # Find imaged to sync to OpenSearch.

    cur.execute("""
        SELECT COUNT(1)
        FROM bp_image
        WHERE bp_image.search_sync = false
    """)
    return cur.fetchone()[0]
