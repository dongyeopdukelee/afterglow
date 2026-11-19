"""The checked-in poster snapshot is an explicit, reproducible asset contract."""

import hashlib
import json
import re
from importlib.resources import files
from pathlib import Path


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
    from movie_db_llm.posters import poster_identity, poster_index

    index = poster_index()
    assert len(index) == 39290
    for identity, asset in index.items():
        assert re.fullmatch(r"[0-9a-f]{64}", identity)
        assert re.fullmatch(r"[0-9a-f]{20}\.(jpg|png|webp)", asset.filename)
        assert asset.filename.split(".")[0] == asset.sha256[:20]
        assert 0 < asset.size <= 2_000_000
    # Independent known records from the verified acquisition manifest:
    original = poster_identity(
        "Titanic",
        1997,
        "292bffb832a09e345066bc1d3d40afd4a91ef69b4a1a8534152b0c12302e4a98",
    )
    earlier = poster_identity(
        "Titanic",
        1943,
        "f3030c3d51cc1ae6da3a90fb60d39918639495f40ea18acf8342d8f6403cda3a",
    )
    assert index[original].filename == "332cf64cdec11a560c16.jpg"
    assert index[earlier].filename == "b50fa4c44892158336de.jpg"
