"""Poster acquisition must resolve film versions and preserve source bytes."""

import hashlib
import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from movie_db_llm.poster_import import (
    AuthenticationError,
    Film,
    Importer,
    export,
    id_crosswalk,
    select_match,
)


def test_matching_rejects_remakes_ambiguity_and_unidentified_years() -> None:
    original = {"id": 10, "title": "The Thing", "release_date": "1982-06-25"}
    remake = {"id": 20, "title": "The Thing", "release_date": "2011-10-12"}
    assert (
        select_match(Film(1, "the thing", 1982), [remake, original])
        == original
    )
    assert select_match(Film(1, "The Thing", None), [original]) is None
    assert (
        select_match(
            Film(1, "The Thing", 1982), [original, {**original, "id": 30}]
        )
        is None
    )
    assert select_match(Film(1, "Other Film", 1982), [original]) is None
    assert (
        select_match(Film(1, "Translated Title", None, 10), [original])
        == original
    )
    assert select_match(Film(1, "The Thing", 2011, 10), [original]) is None


def test_acquisition_downloads_bytes_and_keeps_token_off_image_request(
    tmp_path: Path,
) -> None:
    image = b"\xff\xd8\xff" + b"original-poster-data"
    seen: list[httpx.Request] = []

    def provider(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.themoviedb.org":
            assert (
                request.headers["Authorization"] == "Bearer private-test-token"
            )
            return httpx.Response(
                200,
                json={
                    "id": 10,
                    "release_date": "1982-06-25",
                    "poster_path": "/Ab12.jpg",
                },
            )
        assert "authorization" not in request.headers
        assert str(request.url) == "https://image.tmdb.org/t/p/w500/Ab12.jpg"
        return httpx.Response(200, content=image)

    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        importer = Importer(
            client, "private-test-token", tmp_path, per_second=10000
        )
        result = importer.acquire(Film(1, "The Thing", 1982, 10))
    assert result["status"] == "stored"
    assert (tmp_path / result["filename"]).read_bytes() == image
    assert result["sha256"] == hashlib.sha256(image).hexdigest()
    assert result["bytes"] == len(image)
    assert result["source_url"] == "https://www.themoviedb.org/movie/10"
    assert "private-test-token" not in json.dumps(result)
    assert len(seen) == 2


@pytest.mark.parametrize(
    "poster", ["//evil.example/a.jpg", "/../a.jpg", "/a.svg"]
)
def test_untrusted_image_paths_never_trigger_download(
    tmp_path: Path, poster: str
) -> None:
    seen: list[httpx.Request] = []

    def provider(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": 1,
                "release_date": "2000-01-01",
                "poster_path": poster,
            },
        )

    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        result = Importer(client, "secret", tmp_path).acquire(
            Film(1, "Film", 2000, 1)
        )
    assert result == {"status": "error", "reason": "invalid_image_path"}
    assert len(seen) == 1
    assert list(tmp_path.iterdir()) == []


def test_auth_failure_stops_instead_of_recording_a_missing_poster(
    tmp_path: Path,
) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(401))
    ) as client:
        with pytest.raises(AuthenticationError):
            Importer(client, "bad-token", tmp_path).acquire(
                Film(1, "Film", 2000, 1)
            )


def test_crosswalk_rejects_conflicting_external_ids(tmp_path: Path) -> None:
    metadata = tmp_path / "metadata.tsv"
    metadata.write_text("1\t/m/a\tAlias\t2001\n2\t/m/b\tAmbiguous\t1980\n")
    mapping = tmp_path / "ids.json"
    mapping.write_text(
        json.dumps([["/m/a", "123"], ["/m/b", "4"], ["/m/b", "5"]])
    )
    assert id_crosswalk(metadata, mapping) == {("Alias", 2001): 123}


def test_coverage_reports_every_film_without_claiming_missing_images(
    tmp_path: Path,
) -> None:
    with sqlite3.connect(":memory:") as state:
        state.execute(
            "CREATE TABLE jobs (movie_id INTEGER, title TEXT, year INTEGER, result TEXT)"
        )
        state.executemany(
            "INSERT INTO jobs VALUES (?,?,?,?)",
            [
                (
                    1,
                    "Found",
                    2000,
                    json.dumps({"status": "stored", "filename": "image.jpg"}),
                ),
                (2, "Not found", 2001, json.dumps({"status": "no_poster"})),
                (3, "Waiting", None, None),
            ],
        )
        assert export(state, tmp_path) == {
            "stored": 1,
            "no_poster": 1,
            "pending": 1,
        }
    coverage = json.loads((tmp_path / "coverage.json").read_text())
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert [row["movie_id"] for row in coverage] == [1, 2, 3]
    assert [row["movie_id"] for row in manifest] == [1]
