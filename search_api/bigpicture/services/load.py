"""Bigpicture load service."""

import logging
from collections.abc import Iterator
from datetime import datetime
from typing import TypeVar, get_args, get_origin

from psycopg import AsyncCursor
from psycopg.types.json import Json
from pydantic import BaseModel

from search_api.bigpicture.models import (
    BigpictureFields,
    BigpictureBlockFields,
    BigpictureCodeAttributeValue,
    BigpictureStainingFields,
)
from search_api.database.repository import get_cursor
from search_api.services.snomed import SnomedService, is_concept_id
from search_api.services.snomed_term import SnomedTermCacheService

logger = logging.getLogger(__name__)

_ModelT = TypeVar("_ModelT", bound=BaseModel)


def serialize_fields(obj: BaseModel) -> dict:
    """Serialize a block/stain model to its JSONB representation.

    The JSONB key for each field is the model attribute name, so the stored key
    can never drift from the model.
    """
    out: dict = {}
    for name in type(obj).model_fields:
        value = getattr(obj, name)
        if value is None:
            continue
        if isinstance(value, BigpictureCodeAttributeValue):
            out[name] = value.code
        elif isinstance(value, frozenset):
            if codes := [item.code for item in value]:
                out[name] = codes
        elif isinstance(value, tuple):
            out[name] = {"gte": value[0], "lte": value[1]}
        else:
            out[name] = value
    return out


def deserialize_fields(model_cls: type[_ModelT], data: dict) -> _ModelT:
    """Inverse of serialize_fields: rebuild a block/stain model from JSONB.

    Uses field's declared annotation, so the JSONB key is always
    the model attribute name. For ontology fields the stored code is used for
    both the code and the meaning.
    """
    kwargs: dict = {}
    for name, info in model_cls.model_fields.items():
        if name not in data:
            continue
        raw = data[name]
        annotation = info.annotation
        args = get_args(annotation)
        if get_origin(annotation) is frozenset:
            codes = raw if isinstance(raw, list) else [raw]
            kwargs[name] = frozenset(
                BigpictureCodeAttributeValue(code=code, meaning=code) for code in codes
            )
        elif (
            annotation is BigpictureCodeAttributeValue
            or BigpictureCodeAttributeValue in args
        ):
            kwargs[name] = BigpictureCodeAttributeValue(code=raw, meaning=raw)
        elif get_origin(annotation) is tuple or any(
            get_origin(arg) is tuple for arg in args
        ):
            kwargs[name] = (raw["gte"], raw["lte"])
        else:
            kwargs[name] = raw
    return model_cls(**kwargs)


def _iter_concept_ids(fields: BigpictureFields) -> Iterator[tuple[str, str]]:
    """Yield all concept IDs found in any BigpictureCodeAttributeValue field of model."""

    def _get_concept_id(code: str | None) -> str | None:
        return code if code is not None and is_concept_id(code) else None

    def _iter(
        obj: BigpictureBlockFields | BigpictureStainingFields,
    ) -> Iterator[tuple[str, str]]:
        for field_name in type(obj).model_fields:
            value = getattr(obj, field_name)
            if isinstance(value, BigpictureCodeAttributeValue):
                if concept_id := _get_concept_id(value.code):
                    yield field_name, concept_id
            elif isinstance(value, frozenset):
                for item in value:
                    if isinstance(item, BigpictureCodeAttributeValue):
                        if concept_id := _get_concept_id(item.code):
                            yield field_name, concept_id

    for block in fields.blocks:
        yield from _iter(block)
    for stain in fields.stains:
        yield from _iter(stain)


def get_concept_ids_by_field(fields: BigpictureFields) -> dict[str, set[str]]:
    """Return SNOMED CT concept IDs grouped by filtering term field ID."""
    result: dict[str, set[str]] = {}
    for field_id, concept_id in _iter_concept_ids(fields):
        result.setdefault(field_id, set()).add(concept_id)
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
        block_dicts = [d for block in fields.blocks if (d := serialize_fields(block))]
        stain_dicts = [d for stain in fields.stains if (d := serialize_fields(stain))]

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
                Json(block_dicts) if block_dicts else None,
                Json(stain_dicts) if stain_dicts else None,
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

        blocks = {
            deserialize_fields(BigpictureBlockFields, block) for block in (blocks or [])
        }
        stains = {
            deserialize_fields(BigpictureStainingFields, stain)
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

                for field_id, concept_ids in get_concept_ids_by_field(fields).items():
                    await self._snomed_term_service.cache_preferred_terms(
                        field_id, concept_ids, self._snomed_service
                    )

        logger.info(
            "Done — loaded %d image(s), skipped %d dataset(s).",
            loaded,
            len(skipped_datasets),
        )
