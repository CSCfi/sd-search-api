from contextlib import contextmanager
from typing import Generator

import psycopg2  # type: ignore
from psycopg2.extensions import connection  # type: ignore
from psycopg2.extensions import cursor  # type: ignore


@contextmanager
def get_connection() -> Generator[connection]:
    """
    Get a new database connection.

    :return: a new database connection.
    """
    # TODO(improve): read connection details from an environmental variable

    conn = psycopg2.connect(
        host="localhost",
        dbname="sd_search",
        user="postgres",
        password="test",
    )
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def get_cursor() -> Generator[cursor]:
    """
    Get a new database cursor.

    :return: a new database cursor.
    """

    with get_connection() as con:
        with con.cursor() as cur:
            yield cur
