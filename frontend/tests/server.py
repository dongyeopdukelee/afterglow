"""Disposable real catalog API for browser tests; never a production seed."""

import asyncio
import subprocess
import sys
import threading
from collections import Counter
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from os import environ
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import patch

import uvicorn
from boto3 import Session as AWSSession
from botocore.awsrequest import AWSRequest
from botocore.client import BaseClient
from botocore.config import Config
from fastapi import HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from starlette.middleware.base import RequestResponseEndpoint

from movie_db_llm import config, posters
from movie_db_llm import poster_store as storage
from movie_db_llm.database import configure_sqlite_connection, get_session
from movie_db_llm.main import app, get_intent_parser
from movie_db_llm.models import Movie, MovieGenre
from movie_db_llm.recommendations import MovieIntent


@contextmanager
def local_posters(directory: Path) -> Generator[posters.PosterAsset]:
    filename = "18601f3133cb00c8af58.jpg"
    data = (
        Path(__file__).resolve().parents[1] / "public" / "posters" / filename
    ).read_bytes()
    asset = posters.PosterAsset(filename, len(data), sha256(data).hexdigest())
    expected_get = ("GET", f"/test-posters/posters/{filename}", True)
    scope = ContextVar("poster_test", default="")
    calls: Counter[str] = Counter()
    unexpected: list[tuple[str, str, bool]] = []
    lock = threading.Lock()
    pending: dict[str, asyncio.Event] = {}

    class S3Handler(BaseHTTPRequestHandler):
        def parse_request(self) -> bool:
            if not super().parse_request():
                return False
            tag = self.headers.get("X-Poster-Test", "")
            signed = self.headers.get("Authorization", "").startswith(
                "AWS4-HMAC-SHA256 Credential=fake-test-key/"
            )
            with lock:
                calls[tag] += 1
                arrival = (self.command, self.path, signed)
                self.valid = bool(tag) and arrival == expected_get
                if not self.valid:
                    unexpected.append(arrival)
            return True

        def do_GET(self) -> None:
            if not self.valid:
                self.send_error(403)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            pass

    async def track_poster_calls(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        tag = request.headers.get("X-Poster-Test") or "browser"
        token = scope.set(tag)
        try:
            response = await call_next(request)
            path = request.url.path
            held_card = (
                tag.startswith("detail-") and path == "/movies/1/poster"
            ) or (
                tag.startswith("stalled-")
                and path.startswith("/movies/")
                and path.endswith("/poster")
            )
            if held_card and not request.url.query:
                await pending.setdefault(tag, asyncio.Event()).wait()
            return response
        finally:
            scope.reset(token)

    async def poster_calls(tag: str) -> int:
        with lock:
            if unexpected:
                raise HTTPException(
                    500, f"Unexpected S3 fixture request: {unexpected[0]!r}"
                )
            return calls[tag]

    async def release_card(tag: str) -> None:
        pending.setdefault(tag, asyncio.Event()).set()

    app.middleware("http")(track_poster_calls)
    app.add_api_route(
        "/movies/_poster-fixture/{tag}", poster_calls, methods=["GET"]
    )
    app.add_api_route(
        "/movies/_poster-fixture/{tag}/release", release_card, methods=["POST"]
    )

    def tag_s3_call(request: AWSRequest, **_: object) -> None:
        request.headers["X-Poster-Test"] = scope.get()

    with ThreadingHTTPServer(("127.0.0.1", 0), S3Handler) as server:
        for variable in tuple(environ):
            if variable.startswith("AWS_") or variable == "BOTO_CONFIG":
                del environ[variable]
        for variable in (
            "AWS_CONFIG_FILE",
            "AWS_SHARED_CREDENTIALS_FILE",
            "BOTO_CONFIG",
        ):
            environ[variable] = str(directory / variable)
        environ["AWS_ACCESS_KEY_ID"] = "fake-test-key"
        environ["AWS_SECRET_ACCESS_KEY"] = "fake-test-secret"
        environ["AWS_EC2_METADATA_DISABLED"] = "true"
        environ["NO_PROXY"] = "127.0.0.1"
        environ["no_proxy"] = "127.0.0.1"
        config.POSTER_BUCKET = "test-posters"
        sdk_session = AWSSession()
        endpoint = f"http://127.0.0.1:{server.server_port}"

        class LoopbackSession:
            def client(
                self, service_name: str, *, region_name: str, config: Config
            ) -> BaseClient:
                return sdk_session.client(
                    service_name,
                    region_name=region_name,
                    config=config,
                    endpoint_url=endpoint,
                )

        with patch.object(storage, "Session", LoopbackSession):
            client = cast(BaseClient, storage.poster_store().client)
        assert client.meta.endpoint_url == endpoint
        client.meta.events.register("before-sign.s3", tag_s3_call)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield asset
        finally:
            server.shutdown()
            worker.join(timeout=3)
            client.close()
            storage.cached_poster_store.cache_clear()


def run() -> None:
    with (
        TemporaryDirectory(prefix="afterglow-browser-") as temp,
        local_posters(Path(temp)) as asset,
    ):
        url = f"sqlite:///{Path(temp) / 'catalog.sqlite3'}"
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            env={**environ, "DATABASE_URL": url},
        )
        engine = create_engine(url, connect_args={"check_same_thread": False})
        event.listen(engine, "connect", configure_sqlite_connection)
        movies = [
            Movie(
                title=f"Film {number:02}",
                synopsis=f"The complete synopsis for film {number:02}.",
                release_year=2000 + number,
                genre_assignments=[MovieGenre(genre="Drama")],
            )
            for number in range(14)
        ]
        index = {
            posters.poster_identity(
                movie.title,
                movie.release_year,
                sha256(movie.synopsis.encode()).hexdigest(),
            ): asset
            for movie in movies
        }
        posters.poster_index = lambda: index
        with Session(engine) as session:
            session.add_all(movies)
            session.commit()

        def test_session() -> Generator[Session]:
            with Session(engine) as session:
                yield session

        class FixtureParser:
            def parse(self, query: str, genres: list[str]) -> MovieIntent:
                return MovieIntent(
                    genres=[],
                    year_min=None,
                    year_max=None,
                    year_min_inclusive=True,
                    year_max_inclusive=True,
                    title_keywords=[query],
                    unsupported_constraints=[],
                )

        app.dependency_overrides[get_session] = test_session
        app.dependency_overrides[get_intent_parser] = FixtureParser
        try:
            uvicorn.run(app, host="127.0.0.1", port=8000)
        finally:
            engine.dispose()


if __name__ == "__main__":
    run()
