"""HTTP/S3 boundary checks using real SDK requests and disposable movie data."""

import gzip
import io
import json
import threading
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from anyio.abc import BlockingPortal
from anyio.to_thread import current_default_thread_limiter
from fastapi.testclient import TestClient
from httpx import Client
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from movie_db_llm import config, main, posters
from movie_db_llm import poster_store as storage
from movie_db_llm.database import configure_sqlite_connection
from movie_db_llm.models import Base, Movie
from movie_db_llm.recommendations import MovieIntent

real_poster_index = posters.poster_index


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
        self.redirect_region = False
        self.hold_calls = 0
        self.held = threading.Event()
        self.release = threading.Event()
        self.lock = threading.Lock()


type PosterDelivery = tuple[Client, FakeS3, BlockingPortal]


@pytest.fixture
def delivery(
    monkeypatch: pytest.MonkeyPatch,
    image: tuple[posters.PosterAsset, bytes],
    tmp_path: Path,
) -> Generator[PosterDelivery]:
    asset, data = image
    source = FakeS3(data)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            with source.lock:
                source.calls.append((self.command, self.path))
                attempt = len(source.calls)
                status = source.statuses.pop(0) if source.statuses else 200
            payload = source.data
            if status != 200:
                code = {
                    301: "PermanentRedirect",
                    403: "AccessDenied",
                    503: "SlowDown",
                }[status]
                payload = f"<Error><Code>{code}</Code></Error>".encode()
            elif source.corrupt:
                payload = b"x" * len(payload)
            self.send_response(status)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            if status == 301 and source.redirect_region:
                self.send_header("x-amz-bucket-region", "us-east-1")
            self.end_headers()
            if 0 < attempt <= source.hold_calls:
                if attempt == source.hold_calls:
                    source.held.set()
                source.release.wait(10)
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(payload[:20] if source.truncate else payload)
            self.close_connection = True

        def do_HEAD(self) -> None:
            source.calls.append((self.command, self.path))
            self.send_error(403)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    for variable in ("AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
        monkeypatch.setenv(variable, str(tmp_path / variable))
    for variable in (
        "AWS_PROFILE",
        "AWS_DEFAULT_PROFILE",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fake-test-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake-test-secret")
    monkeypatch.setenv("AWS_IGNORE_CONFIGURED_ENDPOINT_URLS", "false")
    monkeypatch.setenv(
        "AWS_ENDPOINT_URL_S3", f"http://127.0.0.1:{server.server_port}"
    )
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "9")
    monkeypatch.setattr(config, "POSTER_BUCKET", "test-posters")

    def no_sleep(_delay: float) -> None:
        pass

    monkeypatch.setattr(storage, "sleep", no_sleep)
    storage.cached_poster_store.cache_clear()
    identity = (
        "6457d8d5bd7786afa0772964304b45283322e826318539425cd8049ac430d988"
    )
    monkeypatch.setattr(posters, "poster_index", lambda: {identity: asset})
    engine = create_engine(
        f"sqlite:///{tmp_path / 'catalog.sqlite3'}",
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.5,
    )
    event.listen(engine, "connect", configure_sqlite_connection)
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
        with Session(engine) as session:
            yield session

    class Parser:
        def parse(self, query: str, genres: list[str]) -> MovieIntent:
            return MovieIntent(
                genres=[],
                year_min=None,
                year_max=None,
                year_min_inclusive=True,
                year_max_inclusive=True,
                title_keywords=["A Film"],
                unsupported_constraints=[],
            )

    previous = main.app.dependency_overrides.copy()
    main.app.dependency_overrides[main.get_session] = database
    main.app.dependency_overrides[main.get_intent_parser] = Parser
    try:
        with TestClient(main.app) as client:
            portal = client.portal
            assert portal is not None
            yield client, source, portal
    finally:
        source.release.set()
        main.app.dependency_overrides = previous
        storage.cached_poster_store.cache_clear()
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
        engine.dispose()


@pytest.mark.parametrize(
    "payload",
    [
        b"not gzip",
        gzip.compress(b"{}")[:-4],
        gzip.compress(b"{}")[:10] + b"\x07" + b"\x00" * 10,
    ],
)
def test_corrupt_packaged_index_omits_images_without_breaking_catalog(
    delivery: PosterDelivery,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: bytes,
) -> None:
    client, s3, _ = delivery
    (tmp_path / "poster_index.json.gz").write_bytes(payload)

    def resource_files(_package: str) -> Path:
        return tmp_path

    monkeypatch.setattr(posters, "files", resource_files)
    monkeypatch.setattr(posters, "poster_index", real_poster_index)
    posters.cached_poster_index.cache_clear()
    try:
        response = client.get("/movies/451/poster")
        assert response.status_code == 503
        assert response.json() == {"detail": "Poster temporarily unavailable."}
        assert client.get("/movies").status_code == 200
        assert client.get("/movies/451").json()["synopsis"] == "Story."
        assert s3.calls == []
    finally:
        posters.cached_poster_index.cache_clear()


def test_poster_uses_one_get_per_request_and_returns_verified_bytes(
    delivery: PosterDelivery,
    image: tuple[posters.PosterAsset, bytes],
) -> None:
    client, s3, _ = delivery
    asset, data = image
    for _ in range(2):
        response = client.get("/movies/451/poster")
        assert response.status_code == 200
        assert response.content == data
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
    assert s3.calls == [("GET", f"/test-posters/posters/{asset.filename}")] * 2


def test_transient_failure_can_succeed_on_third_and_last_attempt(
    delivery: PosterDelivery,
    image: tuple[posters.PosterAsset, bytes],
) -> None:
    client, s3, _ = delivery
    s3.statuses = [503, 503, 200]
    response = client.get("/movies/451/poster")
    assert response.status_code == 200
    assert response.content == image[1]
    assert len(s3.calls) == 3


def test_a_stalled_body_can_recover_on_the_next_get(
    delivery: PosterDelivery,
    image: tuple[posters.PosterAsset, bytes],
) -> None:
    client, s3, _ = delivery
    s3.hold_calls = 1
    response = client.get("/movies/451/poster")
    assert response.status_code == 200
    assert response.content == image[1]
    assert [method for method, _ in s3.calls] == ["GET", "GET"]


@pytest.mark.parametrize("failure", ["service", "truncated", "checksum"])
def test_three_failed_attempts_omit_poster_and_keep_catalog_available(
    delivery: PosterDelivery, failure: str
) -> None:
    client, s3, _ = delivery
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


def test_in_flight_posters_leave_catalog_workers_and_connections_free(
    delivery: PosterDelivery,
) -> None:
    client, s3, portal = delivery
    s3.hold_calls = 2

    async def limit_catalog_workers() -> None:
        current_default_thread_limiter().total_tokens = 2

    portal.call(limit_catalog_workers)
    with ThreadPoolExecutor(max_workers=3) as workers:
        posters = [
            workers.submit(client.get, "/movies/451/poster") for _ in range(2)
        ]
        try:
            assert s3.held.wait(2)
            browsed = workers.submit(client.get, "/movies").result(1)
            assert browsed.status_code == 200
            detail = workers.submit(client.get, "/movies/451").result(1)
            assert detail.json()["synopsis"] == "Story."
            search = workers.submit(
                client.post, "/recommendations", json={"query": "A Film"}
            ).result(1)
            assert search.status_code == 200
            assert search.json()["items"][0]["title"] == "A Film"
            assert all(not poster.done() for poster in posters)
        finally:
            s3.release.set()
        assert all(poster.result(3).status_code == 200 for poster in posters)


@pytest.mark.parametrize("movie_id", [452, 453, 999])
def test_unmatched_identity_never_uses_s3(
    delivery: PosterDelivery, movie_id: int
) -> None:
    client, s3, _ = delivery
    assert client.get(f"/movies/{movie_id}/poster").status_code == 404
    assert s3.calls == []


def test_permanent_access_error_is_not_retried(
    delivery: PosterDelivery,
) -> None:
    client, s3, _ = delivery
    s3.statuses = [403] * 10
    assert client.get("/movies/451/poster").status_code == 503
    assert len(s3.calls) == 1


@pytest.mark.parametrize("with_region_hint", [False, True])
def test_redirect_neither_replays_get_nor_probes_bucket(
    delivery: PosterDelivery, with_region_hint: bool
) -> None:
    client, s3, _ = delivery
    s3.statuses = [503, 503, 301]
    s3.redirect_region = with_region_hint
    assert client.get("/movies/451/poster").status_code == 503
    assert [method for method, _ in s3.calls] == ["GET"] * 3


def test_unconfigured_bucket_omits_images_without_s3(
    delivery: PosterDelivery, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, s3, _ = delivery
    monkeypatch.setattr(config, "POSTER_BUCKET", "")
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
