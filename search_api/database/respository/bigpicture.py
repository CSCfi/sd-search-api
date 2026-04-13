"""Bigpicture database repository."""

from psycopg2.extras import NumericRange  # type: ignore[import-untyped]

from search_api.bigpicture.models import BigpictureFields, BigpictureCodeAttributeValue


def load_bigpicture_fields(cur, fields: BigpictureFields) -> None:
    """
    Load Bigpicture fields for one image.

    :param cur: The database cursor.
    :param fields: The Bigpicture fields for one image.
    """

    def _get_codes(
        items: list[BigpictureCodeAttributeValue | None] | None,
    ) -> list[str] | None:
        if not items:
            return None
        return [item.code for item in items if item is not None]

    # Get ontology codes.
    species_codes = _get_codes([b.species for b in fields.biological_being_fields])
    anatomical_site_codes = _get_codes(
        [s.anatomical_site for s in fields.specimen_fields if s.anatomical_site]
    )
    fixation_type_codes = _get_codes(
        [s.fixation_type for s in fields.specimen_fields if s.fixation_type]
    )
    specimen_type_codes = _get_codes(
        [s.specimen_type for s in fields.specimen_fields if s.specimen_type]
    )
    block_preparation_codes = _get_codes(
        [b.block_preparation for b in fields.block_fields if b.block_preparation]
    )

    # Get controlled values.
    sex_values = [str(a.sex) for a in fields.biological_being_fields]

    # Get age at extraction values.
    age_at_extraction_values = []
    for s in fields.specimen_fields:
        value = s.age_at_extraction_range
        if not value:
            continue
        start, end = value
        age_at_extraction_values.append((start, end))

    _load_bigpicture_fields(
        cur,
        fields.image_id,
        fields.dataset_id,
        fields.dataset_description,
        species_codes,
        anatomical_site_codes,
        sex_values,
        fixation_type_codes,
        specimen_type_codes,
        block_preparation_codes,
        age_at_extraction_values,
    )


def _load_bigpicture_fields(
    cur,
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
    Load Bigpicture fields for one image.

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
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
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

    if age_at_extraction_ranges:
        for age_at_extraction_range in age_at_extraction_ranges:
            cur.execute(
                """
                INSERT INTO bp_image_extraction (
                    image_id,
                    age_at_extraction
                )
                VALUES (%s, %s);
                """,
                (
                    image_id,
                    NumericRange(age_at_extraction_range[0], age_at_extraction_range[1])
                    if age_at_extraction_range
                    else None,  # int range into GIST indexed int4range field
                ),
            )
