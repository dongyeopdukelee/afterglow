"""Disposable real catalog API for browser tests; never a production seed."""

import subprocess
import sys
from collections.abc import Generator
from os import environ
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from movie_db_llm.database import configure_sqlite_connection, get_session
from movie_db_llm.main import app
from movie_db_llm.models import Movie, MovieGenre


def run() -> None:
    with TemporaryDirectory(prefix="afterglow-browser-") as directory:
        url = f"sqlite:///{Path(directory) / 'catalog.sqlite3'}"
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            env={**environ, "DATABASE_URL": url},
        )
        engine = create_engine(url, connect_args={"check_same_thread": False})
        event.listen(engine, "connect", configure_sqlite_connection)
        with Session(engine) as session:
            session.add_all(
                [
                    Movie(
                        title=f"Film {number:02}",
                        synopsis=f"The complete synopsis for film {number:02}.",
                        release_year=2000 + number,
                        genre_assignments=[MovieGenre(genre="Drama")],
                    )
                    for number in range(14)
                ]
            )
            session.commit()

        def test_session() -> Generator[Session]:
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_session] = test_session
        try:
            uvicorn.run(app, host="127.0.0.1", port=8000)
        finally:
            engine.dispose()


if __name__ == "__main__":
    run()
