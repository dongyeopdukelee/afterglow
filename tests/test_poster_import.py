"""Poster acquisition must resolve film versions and preserve source bytes."""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import httpx
import pytest

from movie_db_llm import poster_import
from movie_db_llm.cmu_parser import parse_catalog
from movie_db_llm.poster_import import (
    AuthenticationError,
    Film,
    Importer,
    RateLimit,
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
    assert id_crosswalk(metadata, mapping) == {
        ("Alias", 2001): 123,
        ("Ambiguous", 1980): None,
    }


@pytest.mark.parametrize("year", ["2001", ""])
@pytest.mark.parametrize(
    "pairs",
    [
        [["/m/a", "10"]],
        [["/m/a", "10"], ["/m/b", "10"]],
        [["/m/a", "10"], ["/m/b", "20"]],
        [],
    ],
)
def test_crosswalk_preserves_source_identity_conflicts(
    tmp_path: Path, year: str, pairs: list[list[str]]
) -> None:
    metadata = tmp_path / "metadata.tsv"
    metadata.write_text(f"1\t/m/a\tShared\t{year}\n2\t/m/b\tShared\t{year}\n")
    mapping = tmp_path / "ids.json"
    mapping.write_text(json.dumps(pairs))
    assert id_crosswalk(metadata, mapping) == {
        ("Shared", int(year) if year else None): None
    }
    assert id_crosswalk(metadata, None) == {
        ("Shared", int(year) if year else None): None
    }


def test_crosswalk_allows_repeated_rows_but_not_distinct_source_films(
    tmp_path: Path,
) -> None:
    metadata = tmp_path / "metadata.tsv"
    metadata.write_text(
        "1\t/m/a\tAlias\t2001\n1\t/m/a\tAlias\t2001\n"
        "2\t/m/b\tShared\t2002\n3\t/m/b\tShared\t2002\n"
        "4\t/m/c\tUnmapped\t2003\n"
    )
    mapping = tmp_path / "ids.json"
    mapping.write_text(json.dumps([["/m/a", "10"], ["/m/b", "20"]]))
    assert id_crosswalk(metadata, mapping) == {
        ("Alias", 2001): 10,
        ("Shared", 2002): None,
    }


@pytest.mark.parametrize(
    ("title", "date", "year"),
    [
        ('"Manos": The Hands of Fate', " 1966-11-15 ", 1966),
        ("\"She Shoulda Said 'No'!\"", "1949", 1949),
        ('"Undated"', "unknown", None),
        ('"Short date"', "12", None),
        ('"Leading year"', "20010", 2001),
    ],
)
@pytest.mark.parametrize("conflicting", [False, True])
@pytest.mark.parametrize("padding", ["", " "])
def test_crosswalk_joins_literal_titles_with_catalog_title_and_year_rules(
    tmp_path: Path,
    title: str,
    date: str,
    year: int | None,
    conflicting: bool,
    padding: str,
) -> None:
    metadata = tmp_path / "metadata.tsv"
    metadata.write_text(
        f"1\t/m/a\t{padding}{title}{padding}\t{date}\t\t\t{{}}\t{{}}\t{{}}\n",
        encoding="utf-8",
    )
    summaries = tmp_path / "summaries.txt"
    summaries.write_text("1\tA synopsis.\n", encoding="utf-8")
    catalog = parse_catalog(metadata, summaries)
    assert [(film.title, film.release_year) for film in catalog] == [
        (title, year)
    ]
    mapping = tmp_path / "ids.json"
    mapping.write_text(
        json.dumps(
            [["/m/a", "10"], ["/m/a", "20"]]
            if conflicting
            else [["/m/a", "10"]]
        )
    )
    assert id_crosswalk(metadata, mapping) == {
        (title, year): None if conflicting else 10
    }


@pytest.mark.parametrize("tmdb_id", [None, 10])
@pytest.mark.parametrize("year", [None, 2001])
def test_identity_conflicts_block_both_matching_paths(
    tmp_path: Path, tmdb_id: int | None, year: int | None
) -> None:
    film = Film(1, "Shared", year, tmdb_id, identity_conflict=True)
    candidate = {
        "id": 10,
        "title": "Shared",
        "release_date": "2001-01-01",
        "poster_path": "/Ab12.jpg",
    }
    assert select_match(film, [candidate]) is None
    seen: list[httpx.Request] = []

    def provider(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=candidate)

    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        result = Importer(client, "fake-token", tmp_path).acquire(film)
    assert result == {"status": "needs_review", "reason": "identity_conflict"}
    assert seen == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("retry", [False, True])
@pytest.mark.parametrize("crosswalk_inputs", ["none", "metadata", "mapping"])
@pytest.mark.parametrize("normalized_collisions", [False, True])
def test_cli_reconciles_conflicts_in_every_checkpoint_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retry: bool,
    crosswalk_inputs: str,
    normalized_collisions: bool,
) -> None:
    database = tmp_path / "catalog.sqlite3"
    statuses = [None, "stored", "no_poster", "error", "needs_review"]
    films = [
        (i + 1, "Shared" if i < 5 else "Undated", 2001 if i < 5 else None)
        for i in range(10)
    ] + [(11, "Mapped", 2002), (12, "Fallback", 2003)]
    if normalized_collisions:
        films[:5] = [
            (i + 1, title, 2010)
            for i, title in enumerate(
                ["Frozen", "Frozen", "Fro-zen", "FROZEN", "Ｆｒｏｚｅｎ"]
            )
        ]
    with sqlite3.connect(database) as catalog:
        catalog.execute(
            "CREATE TABLE movies (id INTEGER, title TEXT, release_year INTEGER)"
        )
        catalog.executemany("INSERT INTO movies VALUES (?,?,?)", films)
    directory = tmp_path / "state"
    directory.mkdir()
    with sqlite3.connect(directory / "jobs.sqlite3") as state:
        state.execute(
            "CREATE TABLE jobs (movie_id INTEGER PRIMARY KEY, title TEXT, year INTEGER, result TEXT)"
        )
        state.executemany(
            "INSERT INTO jobs VALUES (?,?,?,?)",
            [
                (
                    *film,
                    json.dumps({"status": statuses[index % 5]})
                    if statuses[index % 5]
                    else None,
                )
                for index, film in enumerate(films[:10])
            ]
            + [(*film, None) for film in films[10:]],
        )
    metadata = tmp_path / "metadata.tsv"
    metadata.write_text(
        "".join(
            f"{i}\t/m/{i}\t{title}\t{year or ''}\n" for i, title, year in films
        )
    )
    mapping = tmp_path / "ids.json"
    mapping.write_text(
        json.dumps([["/m/1", "10"], ["/m/6", "60"], ["/m/11", "110"]])
    )
    monkeypatch.setenv("TMDB_READ_ACCESS_TOKEN", "fake-token")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "poster_import",
            "--database",
            str(database),
            "--state-dir",
            str(directory),
            "--workers",
            "1",
            "--requests-per-second",
            "20",
            "--limit",
            "2",
        ]
        + (
            ["--cmu-metadata", str(metadata)]
            if crosswalk_inputs != "none"
            else []
        )
        + (["--id-map", str(mapping)] if crosswalk_inputs == "mapping" else [])
        + (["--retry-errors", "--retry-review"] if retry else []),
    )
    seen: list[str] = []

    def provider(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/3/configuration":
            return httpx.Response(200, json={})
        if request.url.path == "/3/movie/110":
            return httpx.Response(
                200,
                json={
                    "id": 110,
                    "release_date": "2002-01-01",
                    "poster_path": None,
                },
            )
        assert request.url.path == "/3/search/movie"
        query = request.url.params["query"]
        assert query in {"Mapped", "Fallback"}
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 110 if query == "Mapped" else 120,
                        "title": query,
                        "release_date": "2002-01-01"
                        if query == "Mapped"
                        else "2003-01-01",
                        "poster_path": None,
                    }
                ]
            },
        )

    client_type = httpx.Client

    def client(**kwargs: object) -> httpx.Client:
        return client_type(transport=httpx.MockTransport(provider))

    monkeypatch.setattr(poster_import.httpx, "Client", client)
    assert poster_import.main() == 0
    coverage = json.loads((directory / "coverage.json").read_text())
    assert len(coverage) == 12
    assert all(
        row["status"] == "needs_review"
        and row["reason"] == "identity_conflict"
        and "tmdb_id" not in row
        for row in coverage[:10]
    )
    assert [row["status"] for row in coverage[10:]] == [
        "no_poster",
        "no_poster",
    ]
    assert json.loads((directory / "manifest.json").read_text()) == []
    assert json.loads((directory / "progress.json").read_text()) == {
        "needs_review": 10,
        "no_poster": 2,
    }
    assert seen == [
        "/3/configuration",
        "/3/movie/110" if crosswalk_inputs == "mapping" else "/3/search/movie",
        "/3/search/movie",
    ]
    with sqlite3.connect(directory / "jobs.sqlite3") as state:
        results = [
            json.loads(row[0])
            for row in state.execute(
                "SELECT result FROM jobs WHERE movie_id <= 10"
            )
        ]
    assert (
        results
        == [{"status": "needs_review", "reason": "identity_conflict"}] * 10
    )


def test_cli_preserves_external_ids_and_reconciles_unsafe_fallbacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    films = [
        (1, "11/11/11", 2011),
        (2, "11-11-11", 2011),
        (3, "Ａlpha", 2000),
        (4, "alpha", 2000),
        (5, '"Manos": The Hands of Fate', 1966),
        (6, "\"She Shoulda Said 'No'!\"", 1949),
        (7, "Other", 2001),
        (8, "11 11 11", 2012),
        (9, '"Undated"', None),
    ]
    database = tmp_path / "catalog.sqlite3"
    with sqlite3.connect(database) as catalog:
        catalog.execute(
            "CREATE TABLE movies (id INTEGER, title TEXT, release_year INTEGER)"
        )
        catalog.executemany("INSERT INTO movies VALUES (?,?,?)", films)
    metadata = tmp_path / "metadata.tsv"
    metadata.write_text(
        "".join(
            f"{i}\t/m/{i}\t {title} \t {year or ''} \t\t\t{{}}\t{{}}\t{{}}\n"
            for i, title, year in films
        ),
        encoding="utf-8",
    )
    mapping = tmp_path / "ids.json"
    mapping.write_text(
        json.dumps(
            [
                ["/m/1", "100"],
                ["/m/3", "300"],
                ["/m/4", "400"],
                ["/m/5", "500"],
                ["/m/6", "600"],
                ["/m/6", "601"],
                ["/m/9", "900"],
            ]
        )
    )
    directory = tmp_path / "state"
    monkeypatch.setenv("TMDB_READ_ACCESS_TOKEN", "fake-token")
    argv = [
        "poster_import",
        "--database",
        str(database),
        "--state-dir",
        str(directory),
        "--cmu-metadata",
        str(metadata),
        "--id-map",
        str(mapping),
        "--workers",
        "1",
        "--requests-per-second",
        "20",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    seen: list[str] = []
    image = b"\xff\xd8\xffsynthetic-poster"
    external_dates = {
        "/3/movie/100": "2011-01-01",
        "/3/movie/300": "2000-01-01",
        "/3/movie/400": "2000-01-01",
        "/3/movie/500": "1966-01-01",
        "/3/movie/900": "",
    }

    def provider(request: httpx.Request) -> httpx.Response:
        if request.url.host == "image.tmdb.org":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=image)
        seen.append(request.url.path)
        if request.url.path == "/3/configuration":
            return httpx.Response(200, json={})
        if request.url.path in external_dates:
            return httpx.Response(
                200,
                json={
                    "id": int(request.url.path.rsplit("/", 1)[1]),
                    "release_date": external_dates[request.url.path],
                    "poster_path": "/Poster.jpg",
                },
            )
        assert request.url.path == "/3/search/movie"
        query = request.url.params["query"]
        assert query in {"Other", "11 11 11"}
        year = "2001" if query == "Other" else "2012"
        assert request.url.params["year"] == year
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 700 if query == "Other" else 800,
                        "title": query,
                        "release_date": year + "-01-01",
                        "poster_path": "/Poster.jpg",
                    }
                ]
            },
        )

    client_type = httpx.Client

    def client(**kwargs: object) -> httpx.Client:
        return client_type(transport=httpx.MockTransport(provider))

    monkeypatch.setattr(poster_import.httpx, "Client", client)
    assert poster_import.main() == 0
    coverage = json.loads((directory / "coverage.json").read_text())
    assert [
        row["movie_id"] for row in coverage if row["status"] == "needs_review"
    ] == [2, 6]
    manifest = json.loads((directory / "manifest.json").read_text())
    assert [row["movie_id"] for row in manifest] == [1, 3, 4, 5, 7, 8, 9]
    assert [row["match_method"] for row in manifest] == [
        "external_id",
        "external_id",
        "external_id",
        "external_id",
        "title_year",
        "title_year",
        "external_id",
    ]
    assert [(row["movie_id"], row["tmdb_id"]) for row in manifest] == [
        (1, 100),
        (3, 300),
        (4, 400),
        (5, 500),
        (7, 700),
        (8, 800),
        (9, 900),
    ]
    assert all(
        (directory / "images" / row["filename"]).read_bytes() == image
        for row in manifest
    )
    assert seen == [
        "/3/configuration",
        "/3/movie/100",
        "/3/movie/300",
        "/3/movie/400",
        "/3/movie/500",
        "/3/search/movie",
        "/3/search/movie",
        "/3/movie/900",
    ]
    old_image = directory / "images" / "old.jpg"
    old_image.write_bytes(image)
    with sqlite3.connect(directory / "jobs.sqlite3") as state:
        state.executemany(
            "UPDATE jobs SET result=? WHERE movie_id=?",
            [
                (
                    json.dumps(
                        {
                            "status": "stored",
                            "match_method": "title_year",
                            "filename": "old.jpg",
                            "tmdb_id": 999,
                        }
                    ),
                    identifier,
                )
                for identifier in [1, 2, 6]
            ],
        )
    seen.clear()
    assert poster_import.main() == 0
    assert seen == ["/3/configuration"]
    coverage = json.loads((directory / "coverage.json").read_text())
    assert [
        row["movie_id"] for row in coverage if row["status"] == "needs_review"
    ] == [1, 2, 6]
    assert (
        json.loads((directory / "manifest.json").read_text()) == manifest[1:]
    )
    assert old_image.read_bytes() == image
    seen.clear()
    monkeypatch.setattr(sys, "argv", argv + ["--retry-review"])
    assert poster_import.main() == 0
    assert seen == ["/3/configuration", "/3/movie/100"]
    assert json.loads((directory / "manifest.json").read_text()) == manifest
    with sqlite3.connect(database) as catalog:
        assert (
            catalog.execute(
                "SELECT id,title,release_year FROM movies ORDER BY id"
            ).fetchall()
            == films
        )


def test_waiting_workers_recheck_backoff_and_keep_request_spacing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = RateLimit(8)
    now = 0.0
    deferred = False

    def sleep(seconds: float) -> None:
        nonlocal now, deferred
        if seconds > 0 and not deferred:
            limiter.defer(30)
            deferred = True
        now += seconds

    monkeypatch.setattr(poster_import.time, "monotonic", lambda: now)
    monkeypatch.setattr(poster_import.time, "sleep", sleep)
    grants: list[float] = []
    for _ in range(4):
        limiter.wait()
        grants.append(now)
    assert grants == [0, 30, 30.125, 30.25]


@pytest.mark.parametrize("status", [429, 503])
def test_metadata_retries_honor_backoff_extended_while_sleeping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    now = 0.0
    requests: list[float] = []
    deferred = False

    def provider(request: httpx.Request) -> httpx.Response:
        requests.append(now)
        return httpx.Response(
            status if len(requests) == 1 else 200,
            headers={"Retry-After": "30"},
            json={"ok": True},
        )

    def sleep(seconds: float) -> None:
        nonlocal now, deferred
        if seconds > 0 and not deferred:
            importer.rate.defer(60)
            importer.rate.defer(1)
            deferred = True
        now += seconds

    monkeypatch.setattr(poster_import.time, "monotonic", lambda: now)
    monkeypatch.setattr(poster_import.time, "sleep", sleep)
    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        importer = Importer(client, "fake-token", tmp_path, per_second=8)
        assert importer.metadata("/configuration") == {"ok": True}
        assert importer.metadata("/configuration") == {"ok": True}
    assert requests == [0, 60, 60.125]


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
