"""HTTP/S3 boundary checks using real SDK requests and disposable movie data."""

import io
import json
import threading
from collections.abc import Generator
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

import boto3
import httpx
import pytest
from botocore.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from movie_db_llm import main, posters
from movie_db_llm import poster_store as storage
from movie_db_llm.models import Base, Movie


@pytest.fixture
def image() -> tuple[posters.PosterAsset, bytes]:
    root = Path(__file__).resolve().parents[1]
    entry = json.loads((root / "src/movie_db_llm/posters.json").read_text())[0]
    data = (root / "frontend/public/posters" / entry["filename"]).read_bytes()
    return posters.PosterAsset(
        entry["filename"], len(data), sha256(data).hexdigest()
    ), data


class FakeS3:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.statuses: list[int] = []
        self.truncate = False
        self.corrupt = False
        self.calls: list[tuple[str, str]] = []


@pytest.fixture
def delivery(
    monkeypatch: pytest.MonkeyPatch,
    image: tuple[posters.PosterAsset, bytes],
) -> Generator[tuple[httpx.Client, FakeS3]]:
    asset, data = image
    source = FakeS3(data)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            source.calls.append((self.command, self.path))
            status = source.statuses.pop(0) if source.statuses else 200
            payload = source.data
            if status != 200:
                code = "SlowDown" if status == 503 else "AccessDenied"
                payload = f"<Error><Code>{code}</Code></Error>".encode()
            elif source.corrupt:
                payload = b"x" * len(payload)
            self.send_response(status)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload[:20] if source.truncate else payload)
            self.close_connection = True

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    real_session = boto3.Session(
        aws_access_key_id="fake-test-key",
        aws_secret_access_key="fake-test-secret",
    )

    class LocalSession:
        def client(
            self, service: str, *, region_name: str, config: Config
        ) -> storage.PosterS3Client:
            assert service == "s3"
            return cast(
                storage.PosterS3Client,
                real_session.client(
                    "s3",
                    region_name=region_name,
                    config=config,
                    endpoint_url=f"http://127.0.0.1:{server.server_port}",
                ),
            )

    monkeypatch.setattr(storage.boto3, "Session", LocalSession)
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "9")
    monkeypatch.setattr(storage, "POSTER_BUCKET", "test-posters")
    monkeypatch.setattr(main, "POSTER_BUCKET", "test-posters")

    def no_sleep(_delay: float) -> None:
        pass

    monkeypatch.setattr(storage.time, "sleep", no_sleep)
    storage.cached_poster_store.cache_clear()
    identity = posters.poster_identity(
        "A Film", 2001, sha256(b"Story.").hexdigest()
    )
    monkeypatch.setattr(posters, "poster_index", lambda: {identity: asset})
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Movie(
                    id=451,
                    title="A Film",
                    release_year=2001,
                    synopsis="Story.",
                ),
                Movie(
                    id=452,
                    title="A Film",
                    release_year=2002,
                    synopsis="Story.",
                ),
                Movie(
                    id=453,
                    title="A Film",
                    release_year=2001,
                    synopsis="Another story.",
                ),
            ]
        )
        session.commit()

        def database() -> Generator[Session]:
            yield session

        previous = main.app.dependency_overrides.copy()
        main.app.dependency_overrides[main.get_session] = database
        try:
            with TestClient(main.app) as client:
                yield cast(httpx.Client, client), source
        finally:
            main.app.dependency_overrides = previous
            storage.cached_poster_store.cache_clear()
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)
            session.close()
            engine.dispose()


def test_poster_uses_one_get_per_request_and_returns_verified_bytes(
    delivery: tuple[httpx.Client, FakeS3],
    image: tuple[posters.PosterAsset, bytes],
) -> None:
    client, s3 = delivery
    asset, data = image
    for _ in range(2):
        response = client.get("/movies/451/poster")
        assert response.status_code == 200
        assert response.content == data
        assert response.headers["content-type"] == asset.media_type
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
    assert s3.calls == [("GET", f"/test-posters/posters/{asset.filename}")] * 2


def test_transient_failure_can_succeed_on_third_and_last_attempt(
    delivery: tuple[httpx.Client, FakeS3],
    image: tuple[posters.PosterAsset, bytes],
) -> None:
    client, s3 = delivery
    s3.statuses = [503, 503, 200]
    response = client.get("/movies/451/poster")
    assert response.status_code == 200
    assert response.content == image[1]
    assert len(s3.calls) == 3


@pytest.mark.parametrize("failure", ["service", "truncated", "checksum"])
def test_three_failed_attempts_omit_poster_and_keep_catalog_available(
    delivery: tuple[httpx.Client, FakeS3], failure: str
) -> None:
    client, s3 = delivery
    s3.statuses = [503] * 12 if failure == "service" else []
    s3.truncate = failure == "truncated"
    s3.corrupt = failure == "checksum"
    response = client.get("/movies/451/poster")
    assert response.status_code == 503
    assert response.json() == {"detail": "Poster temporarily unavailable."}
    assert response.headers["cache-control"] == "no-store"
    assert (
        len(s3.calls) == 3
    )  # Real SDK requests, even with AWS_MAX_ATTEMPTS=9.
    assert client.get("/movies").status_code == 200
    assert client.get("/movies/451").json()["synopsis"] == "Story."
    assert len(s3.calls) == 3


@pytest.mark.parametrize("movie_id", [452, 453, 999])
def test_unmatched_identity_never_uses_s3(
    delivery: tuple[httpx.Client, FakeS3], movie_id: int
) -> None:
    client, s3 = delivery
    assert client.get(f"/movies/{movie_id}/poster").status_code == 404
    assert s3.calls == []


def test_permanent_access_error_is_not_retried(
    delivery: tuple[httpx.Client, FakeS3],
) -> None:
    client, s3 = delivery
    s3.statuses = [403] * 10
    assert client.get("/movies/451/poster").status_code == 503
    assert len(s3.calls) == 1


def test_unconfigured_bucket_omits_images_without_s3(
    delivery: tuple[httpx.Client, FakeS3], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, s3 = delivery
    monkeypatch.setattr(main, "POSTER_BUCKET", "")
    assert client.get("/movies/451/poster").status_code == 404
    assert s3.calls == []


def test_partial_bodies_are_closed_before_retry(
    image: tuple[posters.PosterAsset, bytes],
) -> None:
    asset, data = image
    streams: list[io.BytesIO] = []

    class TruncatedClient:
        def get_object(self, *, Bucket: str, Key: str) -> storage.S3Object:
            from botocore.response import StreamingBody

            stream = io.BytesIO(data[:20])
            streams.append(stream)
            return {
                "Body": StreamingBody(stream, len(data)),
                "ContentLength": len(data),
            }

    with pytest.raises(storage.PosterUnavailableError):
        storage.PosterStore(TruncatedClient(), "test-posters").fetch(asset)
    assert len(streams) == 3
    assert all(stream.closed for stream in streams)
