"""The checked-in poster snapshot is an explicit, reproducible asset contract."""

import gzip
import hashlib
import json
import re
from importlib.resources import files
from pathlib import Path

import pytest

from movie_db_llm import posters


def test_shipped_poster_snapshot_matches_its_manifest() -> None:
    """Every claimed image exists, matches its checksum, and has provenance."""
    manifest = json.loads(
        files("movie_db_llm").joinpath("posters.json").read_text()
    )
    assets = Path(__file__).resolve().parents[1] / "frontend/public/posters"
    films: set[tuple[str, int]] = set()
    for poster in manifest:
        key = (poster["title"], poster["year"])
        assert key not in films
        films.add(key)
        path = assets / poster["filename"]
        assert path.parent == assets
        body = path.read_bytes()
        assert len(body) == poster["bytes"]
        assert len(body) <= 2_000_000
        assert hashlib.sha256(body).hexdigest() == poster["sha256"]
        assert poster["source_url"].startswith(
            "https://commons.wikimedia.org/wiki/File:"
        )
        assert poster["license"] == "Public domain"
        assert poster["author"]
        assert body.startswith((b"\xff\xd8\xff", b"\x89PNG", b"RIFF"))
    assert ("Metropolis", 1927) in films
    assert ("Charade", 1963) in films


def test_packaged_bulk_index_matches_the_reviewed_batch_contract() -> None:
    """The owned metadata artifact contains bounded, content-addressed assets."""
    archive = files("movie_db_llm").joinpath("poster_index.json.gz")
    data = json.loads(gzip.decompress(archive.read_bytes()))
    assert data["version"] == 1
    index = data["films"]
    assert len(index) == 39290
    for identity, (filename, size, digest) in index.items():
        assert re.fullmatch(r"[0-9a-f]{64}", identity)
        assert re.fullmatch(r"[0-9a-f]{20}\.(jpg|png|webp)", filename)
        assert filename.split(".")[0] == digest[:20]
        assert 0 < size <= 2_000_000
    # Independent known records from the verified acquisition manifest:
    original = posters.poster_identity(
        "Titanic",
        1997,
        "292bffb832a09e345066bc1d3d40afd4a91ef69b4a1a8534152b0c12302e4a98",
    )
    earlier = posters.poster_identity(
        "Titanic",
        1943,
        "f3030c3d51cc1ae6da3a90fb60d39918639495f40ea18acf8342d8f6403cda3a",
    )
    assert index[original][0] == "332cf64cdec11a560c16.jpg"
    assert index[earlier][0] == "b50fa4c44892158336de.jpg"


def test_packaged_recent_index_matches_the_verified_tmdb_batch() -> None:
    """The recent archive holds the verified TMDB snapshot for the backfill."""
    archive = files("movie_db_llm").joinpath("recent_poster_index.json.gz")
    data = json.loads(gzip.decompress(archive.read_bytes()))
    assert data["version"] == 1
    assert re.fullmatch(r"[0-9a-f]{64}", data["source_manifest_sha256"])
    index = data["films"]
    assert len(index) == 3897
    for identity, (filename, size, digest) in index.items():
        assert re.fullmatch(r"[0-9a-f]{64}", identity)
        assert re.fullmatch(r"[0-9a-f]{20}\.(jpg|png|webp)", filename)
        assert filename.split(".")[0] == digest[:20]
        assert 0 < size <= 2_000_000
    # The TMDB download for Oppenheimer resolves to WebP even on a JPG URL.
    oppenheimer = posters.poster_identity(
        "Oppenheimer",
        2023,
        "4ca3d2f5f05dbc88ef769100af68b6ee836cc3bd38684839e6ffea5dc2a162c1",
    )
    assert index[oppenheimer][0] == "f9f5da82cb41ce3a2563.webp"


def test_poster_lookup_prefers_the_catalog_wide_batch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The documented batch precedence applies only to shared identities."""
    synopsis = "A story."
    synopsis_hash = hashlib.sha256(synopsis.encode()).hexdigest()
    shared = posters.poster_identity("Shared film", 2025, synopsis_hash)
    catalog_only = posters.poster_identity("Catalog film", 2025, synopsis_hash)
    recent_only = posters.poster_identity("Recent film", 2025, synopsis_hash)
    catalog_asset = ("a" * 20 + ".jpg", 100, "a" * 64)
    recent_asset = ("b" * 20 + ".png", 200, "b" * 64)
    catalog_entries = {shared: catalog_asset, catalog_only: catalog_asset}
    recent_entries = {shared: recent_asset, recent_only: recent_asset}
    for filename, entries in (
        ("poster_index.json.gz", catalog_entries),
        ("recent_poster_index.json.gz", recent_entries),
    ):
        payload = json.dumps(
            {
                "version": 1,
                "source_manifest_sha256": "c" * 64,
                "films": entries,
            }
        ).encode()
        (tmp_path / filename).write_bytes(gzip.compress(payload))

    def resource_files(_package: str) -> Path:
        return tmp_path

    monkeypatch.setattr(posters, "files", resource_files)
    posters.cached_poster_index.cache_clear()
    try:
        expected_catalog = posters.PosterAsset(*catalog_asset)
        expected_recent = posters.PosterAsset(*recent_asset)
        assert (
            posters.poster_for("Shared film", 2025, synopsis)
            == expected_catalog
        )
        assert (
            posters.poster_for("Catalog film", 2025, synopsis)
            == expected_catalog
        )
        assert (
            posters.poster_for("Recent film", 2025, synopsis)
            == expected_recent
        )
    finally:
        posters.cached_poster_index.cache_clear()
