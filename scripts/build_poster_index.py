"""Build the reproducible, metadata-only website index from a reviewed batch."""

import argparse
import gzip
import json
import os
import re
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

from movie_db_llm.posters import (
    MAX_POSTER_BYTES,
    POSTER_MEDIA_TYPES,
    poster_identity,
)


def build(manifest: Path, output: Path) -> int:
    source = manifest.read_bytes()
    records = json.loads(source)
    films: dict[str, list[str | int]] = {}
    for row in records:
        filename, size, digest = row["filename"], row["bytes"], row["sha256"]
        source_hash = row["source_synopsis_sha256"]
        if (
            row["status"] != "stored"
            or not isinstance(filename, str)
            or not re.fullmatch(r"[0-9a-f]{20}\.[a-z]+", filename)
            or filename.rsplit(".", 1)[1] not in POSTER_MEDIA_TYPES
            or type(size) is not int
            or not 0 < size <= MAX_POSTER_BYTES
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or filename.split(".")[0] != digest[:20]
            or not re.fullmatch(r"[0-9a-f]{64}", source_hash)
            or not isinstance(row["title"], str)
            or not (row["year"] is None or type(row["year"]) is int)
        ):
            raise ValueError("Invalid verified poster record")
        identity = poster_identity(row["title"], row["year"], source_hash)
        if identity in films:
            raise ValueError("Duplicate poster identity")
        films[identity] = [filename, size, digest]
    data = {
        "version": 1,
        "source_manifest_sha256": sha256(source).hexdigest(),
        "films": films,
    }
    payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            dir=output.parent, prefix=f".{output.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(gzip.compress(payload, mtime=0))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o644)
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(films)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(f"Indexed {build(args.manifest, args.output)} verified films.")


if __name__ == "__main__":
    main()
