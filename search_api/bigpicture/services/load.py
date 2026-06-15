"""Bigpicture load service."""

import logging
from collections.abc import Iterator
from datetime import datetime

from pydantic import BaseModel
from psycopg import AsyncCursor
from psycopg.types.json import Json

from search_api.bigpicture.models import (
    BigpictureFields,
    BigpictureCodeAttributeValue,
    BigpictureStainingFields,
    BigpictureBlockFields,
)
from search_api.database.repository import get_cursor
from search_api.services.snomed import SnomedService, is_concept_id
from search_api.services.snomed_term import SnomedTermCacheService

logger = logging.getLogger(__name__)


def _iter_concept_ids(model: BaseModel) -> Iterator[str]:
    """Yield all concept IDs found in any BigpictureCodeAttributeValue field of model."""
    for field_name in type(model).model_fields:
        value = getattr(model, field_name)
        if isinstance(value, BigpictureCodeAttributeValue):
            if is_concept_id(value.code):
                yield value.code
        elif isinstance(value, frozenset):
            for item in value:
                if isinstance(item, BigpictureCodeAttributeValue) and is_concept_id(
                    item.code
                ):
                    yield item.code


def get_concept_ids(fields: BigpictureFields) -> set[str]:
    """Return all SNOMED CT concept IDs referenced in a BigpictureFields object."""
    result: set[str] = set()
    for block in fields.blocks:
        result.update(_iter_concept_ids(block))
    for stain in fields.stains:
        result.update(_iter_concept_ids(stain))
    return result


class BigPictureLoadService:
    def __init__(
        self,
        snomed_term_service: SnomedTermCacheService,
        snomed_service: SnomedService,
    ) -> None:
        self._snomed_term_service = snomed_term_service
        self._snomed_service = snomed_service

    @staticmethod
    async def _load_fields(cur: AsyncCursor, fields: BigpictureFields) -> None:
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
                stains,
                dataset_modified_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (image_id) DO UPDATE
            SET
                dataset_id = EXCLUDED.dataset_id,
                dataset_image_cnt = EXCLUDED.dataset_image_cnt,
                dataset_short_name = EXCLUDED.dataset_short_name,
                dataset_title = EXCLUDED.dataset_title,
                dataset_description = EXCLUDED.dataset_description,
                blocks = EXCLUDED.blocks,
                stains = EXCLUDED.stains,
                dataset_modified_at = EXCLUDED.dataset_modified_at,
                opensearch_synced_at = NULL
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
                fields.dataset_modified_at,
            ),
        )

    @staticmethod
    async def get_dataset_modified_at(
        cur: AsyncCursor, dataset_id: str
    ) -> datetime | None:
        """
        Return the newest dataset_modified_at stored for a dataset, or None if not loaded yet.

        :param cur: The database cursor.
        :param dataset_id: The dataset identifier.
        :return: The newest file date recorded at last load, or None.
        """
        await cur.execute(
            """
            SELECT dataset_modified_at
            FROM bp_image
            WHERE dataset_id = %s
            LIMIT 1
            """,
            (dataset_id,),
        )
        row = await cur.fetchone()
        return row[0] if row else None

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

    async def load_fields(self, fields_iter: Iterator[BigpictureFields]) -> None:
        """
        Write extracted fields to the database, skipping datasets whose files have not
        changed since the last load.

        Preferred terms for all concept IDs in each loaded image are resolved via
        the SNOMED service and stored in the SNOMED term cache.

        :param fields_iter: Iterator of extracted fields, typically from
            ``BigPictureExtractService.extract_fields``.
        """
        await self._snomed_term_service.load()

        loaded = 0
        skipped_datasets: set[str] = set()
        checked_datasets: set[str] = set()

        async with get_cursor() as cur:
            for fields in fields_iter:
                if fields.dataset_id in skipped_datasets:
                    continue

                if fields.dataset_id not in checked_datasets:
                    checked_datasets.add(fields.dataset_id)
                    existing_date = await BigPictureLoadService.get_dataset_modified_at(
                        cur, fields.dataset_id
                    )
                    if (
                        existing_date is not None
                        and fields.dataset_modified_at is not None
                        and existing_date >= fields.dataset_modified_at
                    ):
                        logger.info(
                            "Skipping dataset %s — no newer files.", fields.dataset_id
                        )
                        skipped_datasets.add(fields.dataset_id)
                        continue

                await BigPictureLoadService._load_fields(cur, fields)
                loaded += 1
                logger.info(
                    "Loaded image %s (dataset %s).", fields.image_id, fields.dataset_id
                )

                concept_ids = get_concept_ids(fields)
                await self._snomed_term_service.cache_preferred_terms(
                    concept_ids, self._snomed_service
                )

        logger.info(
            "Done — loaded %d image(s), skipped %d dataset(s).",
            loaded,
            len(skipped_datasets),
        )
