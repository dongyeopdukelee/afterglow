"""Catalog vocabulary and read-only SQLite inspection."""

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlencode

from sqlalchemy import Connection, create_engine, make_url, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from movie_db_llm.models import MovieGenre


def catalog_genres(connection: Session | Connection) -> list[str]:
    statement = select(MovieGenre.genre).distinct().order_by(MovieGenre.genre)
    return list(connection.scalars(statement))


@contextmanager
def open_catalog(database_url: str) -> Generator[Connection]:
    """Open an existing SQLite catalog without permitting writes or creation."""
    url = make_url(database_url)
    database = url.database
    if (
        url.drivername not in ("sqlite", "sqlite+pysqlite")
        or not database
        or database == ":memory:"
    ):
        raise ValueError("Expected a file-backed SQLite catalog.")
    dialect = url.get_dialect()()
    _, driver_options = dialect.create_connect_args(url)
    uri_options: dict[str, str | tuple[str, ...]] = {}
    if database.startswith("file:") and driver_options.get("uri"):
        database = database.partition("#")[0]
        if (
            database in ("file:", "file::memory:")
            or url.query.get("mode") == "memory"
        ):
            raise ValueError("Expected a file-backed SQLite catalog.")
        uri_options = dict(url.difference_update_query(driver_options).query)
    else:
        database = Path(database).absolute().as_uri()
    uri_options["mode"] = "ro"
    query = urlencode(uri_options, doseq=True, quote_via=quote)
    readonly_url = url.set(
        database=f"{database}?{query}", query={"uri": "true"}
    )
    driver_options["uri"] = True
    engine = create_engine(
        readonly_url, connect_args=driver_options, poolclass=NullPool
    )
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()
