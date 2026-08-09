"""The ontology_cache table: one ontology's whole concept table, as one JSON row."""

from datetime import datetime

from psycopg.types.json import Jsonb

from search_api.database.models import StoredOntology
from search_api.database.repository import get_cursor

ONTOLOGY_CACHE_TABLE = "ontology_cache"


async def read_ontology(ontology_id: str) -> StoredOntology | None:
    """Return the stored ontology, or None if nothing is stored."""
    async with get_cursor() as cur:
        await cur.execute(
            f"SELECT version, sha256, data FROM {ONTOLOGY_CACHE_TABLE} "
            f"WHERE ontology_id = %s",
            (ontology_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return StoredOntology(version=row[0], sha256=row[1], concepts=row[2])


async def read_updated_at(ontology_id: str) -> datetime | None:
    """Return when the stored ontology was last written."""
    async with get_cursor() as cur:
        await cur.execute(
            f"SELECT updated_at FROM {ONTOLOGY_CACHE_TABLE} WHERE ontology_id = %s",
            (ontology_id,),
        )
        row = await cur.fetchone()
    return row[0] if row else None


async def write_ontology(ontology_id: str, ontology: StoredOntology) -> None:
    """Replace stored ontology."""
    async with get_cursor() as cur:
        await cur.execute(
            f"""
            INSERT INTO {ONTOLOGY_CACHE_TABLE}
                (ontology_id, version, sha256, data)
            VALUES (%(ontology_id)s, %(version)s, %(sha256)s, %(data)s)
            ON CONFLICT (ontology_id) DO UPDATE
            SET version = EXCLUDED.version,
                sha256 = EXCLUDED.sha256,
                data = EXCLUDED.data,
                updated_at = now()
            """,
            {
                "ontology_id": ontology_id,
                "version": ontology.version,
                "sha256": ontology.sha256,
                "data": Jsonb(ontology.concepts),
            },
        )
