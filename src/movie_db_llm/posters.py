"""Reviewed starter films and catalog-wide poster identity metadata."""

import gzip
import json
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files
from threading import Lock
from zlib import error as CompressionError

_index_lock = Lock()
MAX_POSTER_BYTES = 2_000_000
POSTER_MEDIA_TYPES = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}

POSTER_FILMS: tuple[tuple[str, int], ...] = tuple(
    (entry["title"], entry["year"])
    for entry in json.loads(
        files("movie_db_llm").joinpath("posters.json").read_text()
    )
)


@dataclass(frozen=True)
class PosterAsset:
    """A verified object from the offline catalog-wide acquisition."""

    filename: str
    size: int
    sha256: str

    @property
    def media_type(self) -> str:
        return POSTER_MEDIA_TYPES[self.filename.rsplit(".", 1)[1]]


def poster_identity(title: str, year: int | None, synopsis_hash: str) -> str:
    """Identify a film independently of a particular database's local IDs."""
    identity = json.dumps(
        [title, year, synopsis_hash], ensure_ascii=False, separators=(",", ":")
    )
    return sha256(identity.encode()).hexdigest()


def poster_index() -> dict[str, PosterAsset]:
    # Cold page loads request twelve images concurrently. Avoid decompressing
    # twelve copies of the large metadata index before the first cache fill.
    with _index_lock:
        return cached_poster_index()


@lru_cache(maxsize=1)
def cached_poster_index() -> dict[str, PosterAsset]:
    """Load the packaged metadata index; image bytes always come from S3."""
    try:
        data = json.loads(
            gzip.decompress(
                files("movie_db_llm")
                .joinpath("poster_index.json.gz")
                .read_bytes()
            )
        )
    except (EOFError, CompressionError) as error:
        raise ValueError("Invalid compressed poster index") from error
    if data["version"] != 1:
        raise ValueError("Unsupported poster index version")
    return {
        identity: PosterAsset(*asset)
        for identity, asset in data["films"].items()
    }


def poster_for(
    title: str, year: int | None, synopsis: str
) -> PosterAsset | None:
    """Match the catalog version, including the full source synopsis."""
    identity = poster_identity(
        title, year, sha256(synopsis.encode()).hexdigest()
    )
    return poster_index().get(identity)
