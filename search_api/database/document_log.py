"""The document_log table."""

import logging

from psycopg import AsyncCursor

from search_api.database.models import StoredDocumentLog
from search_api.severity import LogSeverity

logger = logging.getLogger(__name__)

DOCUMENT_LOG_TABLE = "document_log"

_LOG_LEVELS: dict[LogSeverity, int] = {
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
}


def log_document_log(log: StoredDocumentLog) -> None:
    """Log a document log entry."""

    source = f"document {log.document_id}"
    if log.field_id is not None:
        source += f", field {log.field_id}"
    logger.log(_LOG_LEVELS[log.severity], "%s (%s)", log.message, source)


async def write_document_log(cur: AsyncCursor, log: StoredDocumentLog) -> None:
    log_document_log(log)
    await cur.execute(
        f"INSERT INTO {DOCUMENT_LOG_TABLE} (document_id, field_id, severity, message) "
        f"VALUES (%(document_id)s, %(field_id)s, %(severity)s, %(message)s)",
        {
            "document_id": log.document_id,
            "field_id": log.field_id,
            "severity": log.severity,
            "message": log.message,
        },
    )


async def read_document_logs(
    cur: AsyncCursor, document_id: str
) -> list[StoredDocumentLog]:
    await cur.execute(
        f"SELECT document_id, field_id, severity, message, created_at "
        f"FROM {DOCUMENT_LOG_TABLE} WHERE document_id = %s ORDER BY id",
        (document_id,),
    )
    return [
        StoredDocumentLog(
            document_id=row[0],
            field_id=row[1],
            severity=row[2],
            message=row[3],
            created_at=row[4],
        )
        for row in await cur.fetchall()
    ]


async def delete_document_logs(cur: AsyncCursor, document_id: str) -> int:
    await cur.execute(
        f"DELETE FROM {DOCUMENT_LOG_TABLE} WHERE document_id = %s", (document_id,)
    )
    return cur.rowcount


async def delete_all_document_logs(cur: AsyncCursor) -> int:
    await cur.execute(f"DELETE FROM {DOCUMENT_LOG_TABLE}")
    return cur.rowcount
