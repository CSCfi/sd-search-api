"""Bigpicture load service."""

from psycopg import AsyncCursor
from psycopg.types.json import Json

from search_api.bigpicture.models import (
    BigpictureFields,
    BigpictureCodeAttributeValue,
    BigpictureStainingFields,
    BigpictureBlockFields,
)


class BigPictureLoadService:
    @staticmethod
    async def load_fields(cur: AsyncCursor, fields: BigpictureFields) -> None:
        """
        Load Bigpicture fields for one image into the database.

        :param cur: The database cursor.
        :param fields: The Bigpicture fields for one image.
        """

        def _extract_code(value: BigpictureCodeAttributeValue | None) -> str | None:
            return value.code if value is not None else None

        def _extract_codes(
            values: frozenset[BigpictureCodeAttributeValue],
        ) -> list[str] | None:
            return [v.code for v in values] or None

        blocks = fields.blocks
        stains = fields.stains

        # Replace existing image row.
        await cur.execute(
            """
            INSERT INTO bp_image (
                image_id,
                dataset_id,
                dataset_image_cnt,
                dataset_short_name,
                dataset_title,
                dataset_description,
                blocks,
                stains
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (image_id) DO UPDATE
            SET
                dataset_id = EXCLUDED.dataset_id,
                dataset_image_cnt = EXCLUDED.dataset_image_cnt,
                dataset_short_name = EXCLUDED.dataset_short_name,
                dataset_title = EXCLUDED.dataset_title,
                dataset_description = EXCLUDED.dataset_description,
                blocks = EXCLUDED.blocks,
                stains = EXCLUDED.stains
            """,
            (
                fields.image_id,
                fields.dataset_id,
                fields.dataset_image_cnt,
                fields.dataset_short_name,
                fields.dataset_title,
                fields.dataset_description,
                # blocks
                Json(
                    [
                        b
                        for b in [
                            {
                                k: v
                                for k, v in {
                                    "block_preparation": _extract_code(
                                        block.block_preparation
                                    ),
                                    "anatomical_site": _extract_codes(
                                        block.anatomical_site
                                    ),
                                    "fixation_type": _extract_code(block.fixation_type),
                                    "fixation_type_text": block.fixation_type_text,
                                    "specimen_type": _extract_code(block.specimen_type),
                                    "age_at_extraction": (
                                        {
                                            "gte": block.age_at_extraction[0],
                                            "lte": block.age_at_extraction[1],
                                        }
                                        if block.age_at_extraction is not None
                                        else None
                                    ),
                                    "species": _extract_code(block.species),
                                    "sex": block.sex,
                                }.items()
                                if v is not None
                            }
                            for block in blocks
                        ]
                        if b
                    ]
                )
                if blocks
                else None,
                # stains
                Json(
                    [
                        s
                        for s in [
                            {
                                k: v
                                for k, v in {
                                    "staining_target": stain.staining_target,
                                    "staining_procedure": _extract_code(
                                        stain.staining_procedure
                                    ),
                                    "staining_procedure_text": stain.staining_procedure_text,
                                    "staining_substance": _extract_code(
                                        stain.staining_substance
                                    ),
                                    "staining_substance_text": stain.staining_substance_text,
                                }.items()
                                if v is not None
                            }
                            for stain in stains
                        ]
                        if s
                    ]
                )
                if stains
                else None,
            ),
        )

    @staticmethod
    async def get_fields(cur: AsyncCursor, image_id: str) -> BigpictureFields | None:
        """
        Get Bigpicture fields for one image from the database. For ontology fields columns
        uses the code value also for the meaning.

        :param cur: The database cursor.
        :param image_id: Unique identifier of the image.
        :return: The Bigpicture fields for the image.
        """

        await cur.execute(
            """
            SELECT
                image_id,
                dataset_id,
                dataset_image_cnt,
                dataset_short_name,
                dataset_title,
                dataset_description,
                blocks,
                stains
            FROM bp_image
            WHERE image_id = %s
            """,
            (image_id,),
        )

        row = await cur.fetchone()
        if not row:
            return None

        (
            image_id,
            dataset_id,
            dataset_image_cnt,
            dataset_short_name,
            dataset_title,
            dataset_description,
            blocks,
            stains,
        ) = row

        def _convert_code(key: str, _dict: dict) -> dict:
            v = _dict.get(key)
            if v is None:
                return {}
            return {key: BigpictureCodeAttributeValue(code=v, meaning=v)}

        def _convert_codes(key: str, _dict: dict) -> dict:
            v = _dict.get(key)
            if not v:
                return {}
            codes = v if isinstance(v, list) else [v]
            return {
                key: frozenset(
                    BigpictureCodeAttributeValue(code=c, meaning=c) for c in codes
                )
            }

        def _convert_age_at_extraction(_dict: dict) -> dict:
            v = _dict.get("age_at_extraction")
            if v is None:
                return {}
            return {"age_at_extraction": (v["gte"], v["lte"])}

        blocks = {
            BigpictureBlockFields(
                **{
                    **block,
                    **_convert_code("block_preparation", block),
                    **_convert_code("species", block),
                    **_convert_codes("anatomical_site", block),
                    **_convert_code("fixation_type", block),
                    **_convert_code("specimen_type", block),
                    **_convert_age_at_extraction(block),
                }
            )
            for block in (blocks or [])
        }

        stains = {
            BigpictureStainingFields(
                **{
                    **stain,
                    **_convert_code("staining_procedure", stain),
                    **_convert_code("staining_substance", stain),
                }
            )
            for stain in (stains or [])
        }

        return BigpictureFields(
            image_id=image_id,
            dataset_id=dataset_id,
            dataset_image_cnt=dataset_image_cnt,
            dataset_short_name=dataset_short_name,
            dataset_title=dataset_title,
            dataset_description=dataset_description,
            blocks=blocks,
            stains=stains,
        )
