from datetime import datetime
from typing import Sequence

from search_api.database.models import StoredTerm
from search_api.database.repository import get_cursor

TERMS_CACHE_TABLE = "terms_cache"

_BATCH_SIZE = 1000


async def read_terms(ontology_id: str) -> list[StoredTerm]:
    """Return every term stored for an ontology."""
    async with get_cursor() as cur:
        await cur.execute(
            f"SELECT field_id, concept_id, preferred_term FROM {TERMS_CACHE_TABLE} "
            f"WHERE ontology_id = %s",
            (ontology_id,),
        )
        return [
            StoredTerm(field_id=row[0], concept_id=row[1], preferred_term=row[2])
            for row in await cur.fetchall()
        ]


async def read_concept_ids_by_field(ontology_id: str) -> dict[str, set[str]]:
    """Return the concept ids stored for an ontology, by the field they are indexed for."""
    concept_ids_by_field: dict[str, set[str]] = {}
    async with get_cursor() as cur:
        await cur.execute(
            f"SELECT field_id, concept_id FROM {TERMS_CACHE_TABLE} "
            f"WHERE ontology_id = %s",
            (ontology_id,),
        )
        for field_id, concept_id in await cur.fetchall():
            concept_ids_by_field.setdefault(field_id, set()).add(concept_id)
    return concept_ids_by_field


async def delete_terms(ontology_id: str, field_ids: Sequence[str]) -> int:
    """Delete the terms cached for these fields of an ontology, returning how many."""
    async with get_cursor() as cur:
        await cur.execute(
            f"DELETE FROM {TERMS_CACHE_TABLE} "
            f"WHERE ontology_id = %s AND field_id = ANY(%s)",
            (ontology_id, list(field_ids)),
        )
        return cur.rowcount


async def read_updated_at(ontology_id: str) -> datetime | None:
    """Return when a term of this ontology was last written, or None if none was."""
    async with get_cursor() as cur:
        await cur.execute(
            f"SELECT max(updated_at) FROM {TERMS_CACHE_TABLE} WHERE ontology_id = %s",
            (ontology_id,),
        )
        row = await cur.fetchone()
    return row[0] if row else None


def _rows(ontology_id: str, terms: Sequence[StoredTerm]) -> list[dict[str, str]]:
    """Return the terms as named parameters.

    Named rather than positional, because every column is ``TEXT``: two of them
    bound in the wrong order would insert happily and only show up as a term that
    never resolves.
    """
    return [{"ontology_id": ontology_id, **term.model_dump()} for term in terms]


async def insert_terms(ontology_id: str, terms: Sequence[StoredTerm]) -> None:
    """Store terms, keeping any already there."""
    rows = _rows(ontology_id, terms)
    async with get_cursor() as cur:
        for start in range(0, len(rows), _BATCH_SIZE):
            await cur.executemany(
                f"""
                INSERT INTO {TERMS_CACHE_TABLE}
                    (ontology_id, concept_id, field_id, preferred_term)
                VALUES (
                    %(ontology_id)s,
                    %(concept_id)s,
                    %(field_id)s,
                    %(preferred_term)s
                )
                ON CONFLICT (ontology_id, concept_id, field_id) DO NOTHING
                """,
                rows[start : start + _BATCH_SIZE],
            )


async def update_terms(ontology_id: str, terms: Sequence[StoredTerm]) -> None:
    """Replace the preferred term of terms already stored."""
    rows = _rows(ontology_id, terms)
    async with get_cursor() as cur:
        for start in range(0, len(rows), _BATCH_SIZE):
            await cur.executemany(
                f"""
                UPDATE {TERMS_CACHE_TABLE}
                SET preferred_term = %(preferred_term)s, updated_at = now()
                WHERE ontology_id = %(ontology_id)s
                  AND concept_id = %(concept_id)s
                  AND field_id = %(field_id)s
                """,
                rows[start : start + _BATCH_SIZE],
            )
