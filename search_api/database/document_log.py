"""The document_log table."""

import logging
import sys

from psycopg import AsyncCursor

from search_api.database.models import StoredDocumentLog
from search_api.severity import LogSeverity

logger = logging.getLogger(__name__)

DOCUMENT_LOG_TABLE = "document_log"

_LOG_LEVELS: dict[LogSeverity, int] = {
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
}


# Whether a log entry's value is printed to the console. Values may be sensitive, so
# this should only be turned on if the console output is not saved.
_show_values = False


def show_values(enabled: bool) -> None:
    """Set whether a log entry's value is printed to stderr. It is never logged or stored."""

    global _show_values
    _show_values = enabled


def log_document_log(log: StoredDocumentLog) -> None:
    """Log a document log entry."""

    source = f"document {log.document_id}"
    if log.field_id is not None:
        source += f", field {log.field_id}"
    if _show_values and log.value is not None:
        # Printed rather than logged, so no logging handler receives the value.
        print(
            f"{log.severity}: {log.message} ({source}, value {log.value!r})",
            file=sys.stderr,
        )
        return
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


async def delete_document_logs(cur: AsyncCursor, document_ids: list[str]) -> int:
    """Delete the log rows of the documents and return how many were deleted."""
    await cur.execute(
        f"DELETE FROM {DOCUMENT_LOG_TABLE} WHERE document_id = ANY(%s)",
        (document_ids,),
    )
    return cur.rowcount


async def delete_all_document_logs(cur: AsyncCursor) -> int:
    await cur.execute(f"DELETE FROM {DOCUMENT_LOG_TABLE}")
    return cur.rowcount
