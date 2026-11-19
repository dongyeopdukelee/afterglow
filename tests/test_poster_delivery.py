"""HTTP/S3 boundary checks using real SDK requests and disposable movie data."""

import gzip
import io
import threading
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from os import environ
from pathlib import Path
from typing import cast

import pytest
from anyio.abc import BlockingPortal
from anyio.to_thread import current_default_thread_limiter
from boto3 import Session as AWSSession
from botocore.client import BaseClient
from botocore.config import Config
from botocore.response import StreamingBody
from fastapi.testclient import TestClient
from httpx import Client
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from urllib3.exceptions import SSLError

from movie_db_llm import config, main, posters
from movie_db_llm import poster_store as storage
from movie_db_llm.database import configure_sqlite_connection
from movie_db_llm.models import Base, Movie
from movie_db_llm.recommendations import MovieIntent

real_poster_index = posters.poster_index


def bundled_image(filename: str) -> tuple[posters.PosterAsset, bytes]:
    root = Path(__file__).resolve().parents[1]
    data = (root / "frontend/public/posters" / filename).read_bytes()
    return posters.PosterAsset(
        filename, len(data), sha256(data).hexdigest()
    ), data


@pytest.fixture
def image() -> tuple[posters.PosterAsset, bytes]:
    return bundled_image("18601f3133cb00c8af58.jpg")


@pytest.fixture
def second_image() -> tuple[posters.PosterAsset, bytes]:
    return bundled_image("0bc01a747de463d751f4.jpg")


class FakeS3:
    def __init__(self, objects: dict[str, bytes], primary_path: str) -> None:
        self.objects = objects
        self.primary_path = primary_path
        self.statuses: list[int] = []
        self.truncate = False
        self.chunked = False
        self.corrupt = False
        self.calls: list[tuple[str, str, bool]] = []
        self.redirect_region = False
        self.hold_calls = 0
        self.held = threading.Event()
        self.release = threading.Event()
        self.lock = threading.Lock()

    def assert_gets(self, count: int) -> None:
        assert self.calls == [("GET", self.primary_path, True)] * count


type PosterDelivery = tuple[Client, FakeS3, BlockingPortal]


@pytest.fixture
def isolated_aws(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Generator[None]:
    for variable in tuple(environ):
        if variable.startswith("AWS_") or variable == "BOTO_CONFIG":
            monkeypatch.delenv(variable)
    for variable in (
        "AWS_CONFIG_FILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "BOTO_CONFIG",
    ):
        monkeypatch.setenv(variable, str(tmp_path / variable))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fake-test-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake-test-secret")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    storage.cached_poster_store.cache_clear()
    yield
    storage.cached_poster_store.cache_clear()


@pytest.fixture
def delivery(
    isolated_aws: None,
    monkeypatch: pytest.MonkeyPatch,
    image: tuple[posters.PosterAsset, bytes],
    second_image: tuple[posters.PosterAsset, bytes],
    tmp_path: Path,
) -> Generator[PosterDelivery]:
    asset, data = image
    second_asset, second_data = second_image
    primary_path = f"/test-posters/posters/{asset.filename}"
    source = FakeS3(
        {
            primary_path: data,
            f"/test-posters/posters/{second_asset.filename}": second_data,
        },
        primary_path,
    )

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def parse_request(self) -> bool:
            if not super().parse_request():
                return False
            self.signed = self.headers.get("Authorization", "").startswith(
                "AWS4-HMAC-SHA256 Credential=fake-test-key/"
            )
            with source.lock:
                source.calls.append((self.command, self.path, self.signed))
                self.attempt = len(source.calls)
            return True

        def do_GET(self) -> None:
            if not self.signed:
                self.send_error(403)
                return
            payload = source.objects.get(self.path)
            if payload is None:
                self.send_error(404)
                return
            with source.lock:
                status = source.statuses.pop(0) if source.statuses else 200
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
            if source.chunked:
                self.send_header("Transfer-Encoding", "chunked")
            else:
                self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            if status == 301 and source.redirect_region:
                self.send_header("x-amz-bucket-region", "us-east-1")
            self.end_headers()
            if self.attempt <= source.hold_calls:
                if self.attempt == source.hold_calls:
                    source.held.set()
                source.release.wait(10)
            with suppress(BrokenPipeError, ConnectionResetError):
                if source.chunked:
                    self.wfile.write(f"{len(payload):x}\r\n".encode())
                self.wfile.write(payload[:20] if source.truncate else payload)
                if source.chunked and not source.truncate:
                    self.wfile.write(b"\r\n0\r\n\r\n")
            self.close_connection = True

        def do_HEAD(self) -> None:
            self.send_error(403)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "9")
    monkeypatch.setattr(config, "POSTER_BUCKET", "test-posters")
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

    monkeypatch.setattr(storage, "Session", LoopbackSession)
    s3_client = cast(BaseClient, storage.poster_store().client)
    assert s3_client.meta.endpoint_url == endpoint

    def no_sleep(_delay: float) -> None:
        pass

    monkeypatch.setattr(storage, "sleep", no_sleep)
    first_identity = (
        "6457d8d5bd7786afa0772964304b45283322e826318539425cd8049ac430d988"
    )
    second_identity = (
        "0b61eb74aa3e71622a4deb27dca6dbef1c29532743e2101d37518988d73ff842"
    )
    index = {first_identity: asset, second_identity: second_asset}
    monkeypatch.setattr(posters, "poster_index", lambda: index)
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
                Movie(
                    id=454,
                    title="Second Film",
                    release_year=2001,
                    synopsis="Story.",
                ),
                Movie(
                    id=455,
                    title="A Different Film",
                    release_year=2001,
                    synopsis="Story.",
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
        s3_client.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
        engine.dispose()


@pytest.mark.parametrize(
    ("variable", "profile"),
    [
        ("AWS_ENDPOINT_URL_S3", ""),
        ("AWS_ENDPOINT_URL", ""),
        (None, "[default]\nendpoint_url = {url}\n"),
        (
            None,
            "[default]\nservices = fixture\n"
            "[services fixture]\ns3 =\n  endpoint_url = {url}\n",
        ),
    ],
    ids=["service-env", "global-env", "global-profile", "service-profile"],
)
def test_poster_client_ignores_inherited_endpoint_urls(
    isolated_aws: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    variable: str | None,
    profile: str,
) -> None:
    url = "http://127.0.0.1:9"
    if variable is None:
        (tmp_path / "AWS_CONFIG_FILE").write_text(profile.format(url=url))
    else:
        monkeypatch.setenv(variable, url)
    client = cast(BaseClient, storage.poster_store().client)
    try:
        assert client.meta.endpoint_url == "https://s3.us-west-2.amazonaws.com"
    finally:
        client.close()


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


def test_each_film_returns_its_own_poster_in_one_get_per_request(
    delivery: PosterDelivery,
    image: tuple[posters.PosterAsset, bytes],
    second_image: tuple[posters.PosterAsset, bytes],
) -> None:
    client, s3, _ = delivery
    for _ in range(2):
        for movie_id, data in ((451, image[1]), (454, second_image[1])):
            response = client.get(f"/movies/{movie_id}/poster")
            assert response.status_code == 200
            assert response.content == data
            assert response.headers["content-type"] == "image/jpeg"
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-content-type-options"] == "nosniff"
    assert (
        s3.calls
        == [
            ("GET", "/test-posters/posters/18601f3133cb00c8af58.jpg", True),
            ("GET", "/test-posters/posters/0bc01a747de463d751f4.jpg", True),
        ]
        * 2
    )


def test_transient_failure_can_succeed_on_third_and_last_attempt(
    delivery: PosterDelivery,
    image: tuple[posters.PosterAsset, bytes],
) -> None:
    client, s3, _ = delivery
    s3.statuses = [503, 503, 200]
    response = client.get("/movies/451/poster")
    assert response.status_code == 200
    assert response.content == image[1]
    s3.assert_gets(3)


def test_a_stalled_body_can_recover_on_the_next_get(
    delivery: PosterDelivery,
    image: tuple[posters.PosterAsset, bytes],
) -> None:
    client, s3, _ = delivery
    s3.hold_calls = 1
    response = client.get("/movies/451/poster")
    assert response.status_code == 200
    assert response.content == image[1]
    s3.assert_gets(2)


@pytest.mark.parametrize("truncated", [False, True])
def test_chunked_body_without_content_length_is_verified_and_closed(
    delivery: PosterDelivery,
    image: tuple[posters.PosterAsset, bytes],
    monkeypatch: pytest.MonkeyPatch,
    truncated: bool,
) -> None:
    client, s3, _ = delivery
    s3.chunked = True
    s3.truncate = truncated
    closed: list[StreamingBody] = []
    original_close = StreamingBody.close

    def observe_close(body: StreamingBody) -> None:
        original_close(body)
        closed.append(body)

    monkeypatch.setattr(StreamingBody, "close", observe_close)
    response = client.get("/movies/451/poster")
    if truncated:
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/movies").status_code == 200
        assert client.get("/movies/451").json()["synopsis"] == "Story."
    else:
        assert response.status_code == 200
        assert response.content == image[1]
    attempts = 3 if truncated else 1
    s3.assert_gets(attempts)
    assert len(closed) == len({id(body) for body in closed}) == attempts


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
    assert client.get("/movies").status_code == 200
    assert client.get("/movies/451").json()["synopsis"] == "Story."
    s3.assert_gets(3)


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
    s3.assert_gets(2)


@pytest.mark.parametrize("movie_id", [452, 453, 455, 999])
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
    s3.assert_gets(1)


@pytest.mark.parametrize("with_region_hint", [False, True])
def test_redirect_neither_replays_get_nor_probes_bucket(
    delivery: PosterDelivery, with_region_hint: bool
) -> None:
    client, s3, _ = delivery
    s3.statuses = [503, 503, 301]
    s3.redirect_region = with_region_hint
    assert client.get("/movies/451/poster").status_code == 503
    s3.assert_gets(3)


def test_unconfigured_bucket_omits_images_without_s3(
    delivery: PosterDelivery, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, s3, _ = delivery
    monkeypatch.setattr(config, "POSTER_BUCKET", "")
    assert client.get("/movies/451/poster").status_code == 404
    assert s3.calls == []


@pytest.mark.parametrize(
    "tls_failure", [False, True], ids=["truncated", "tls"]
)
def test_failed_bodies_are_closed_before_retry(
    delivery: PosterDelivery,
    image: tuple[posters.PosterAsset, bytes],
    monkeypatch: pytest.MonkeyPatch,
    tls_failure: bool,
) -> None:
    client, _, _ = delivery
    _, data = image
    streams: list[io.BytesIO] = []

    class BrokenTLSStream(io.BytesIO):
        def read(self, size: int | None = -1, /) -> bytes:
            raise SSLError("Invalid TLS record")

    class BrokenBodyClient:
        def get_object(self, *, Bucket: str, Key: str) -> storage.S3Object:
            assert all(stream.closed for stream in streams)
            stream = (
                BrokenTLSStream() if tls_failure else io.BytesIO(data[:20])
            )
            streams.append(stream)
            return {
                "Body": StreamingBody(stream, len(data)),
                "ContentLength": len(data),
            }

    store = storage.PosterStore(BrokenBodyClient(), "test-posters")
    monkeypatch.setattr(main, "poster_store", lambda: store)
    response = client.get("/movies/451/poster")
    assert response.status_code == 503
    assert response.json() == {"detail": "Poster temporarily unavailable."}
    assert response.headers["cache-control"] == "no-store"
    assert len(streams) == 3
    assert all(stream.closed for stream in streams)
