"""The checked-in poster snapshot is an explicit, reproducible asset contract."""

import hashlib
import json
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
