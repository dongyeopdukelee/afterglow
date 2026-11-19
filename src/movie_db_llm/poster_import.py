"""Resumable, offline TMDB poster acquisition; never used by web requests."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx

from movie_db_llm.cmu_parser import RELEASE_YEAR_PATTERN
from movie_db_llm.posters import MAX_POSTER_BYTES, POSTER_MEDIA_TYPES

API = "https://api.themoviedb.org/3"
IMAGES = "https://image.tmdb.org/t/p/w500"


@dataclass(frozen=True)
class Film:
    id: int
    title: str
    year: int | None
    tmdb_id: int | None = None
    identity_conflict: bool = False


def normalized(title: str) -> str:
    """Ignore case, spacing and punctuation, without fuzzy title guessing."""
    return "".join(
        c
        for c in unicodedata.normalize("NFKC", title).casefold()
        if c.isalnum()
    )


def select_match(
    film: Film, results: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Require a unique exact title/year; an external ID also resolves aliases."""
    if film.identity_conflict:
        return None
    matches: dict[int, dict[str, Any]] = {}
    for result in results:
        identifier = result.get("id")
        if type(identifier) is not int:
            continue
        date = result.get("release_date", "")
        if film.tmdb_id is not None:
            if identifier != film.tmdb_id:
                continue
        else:
            if film.year is None:
                continue
            names = (result.get("title"), result.get("original_title"))
            if not any(
                isinstance(name, str)
                and normalized(name) == normalized(film.title)
                for name in names
            ):
                continue
        if film.year is not None and (
            not isinstance(date, str) or date[:4] != str(film.year)
        ):
            continue
        matches[identifier] = result
    return next(iter(matches.values())) if len(matches) == 1 else None


class AuthenticationError(RuntimeError):
    """Stop the campaign instead of marking every film as an auth failure."""


class RateLimit:
    def __init__(self, per_second: float) -> None:
        self.interval = 1 / per_second
        self.next_at = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                if now >= self.next_at:
                    self.next_at = now + self.interval
                    return
                delay = self.next_at - now
            time.sleep(delay)

    def defer(self, seconds: float) -> None:
        with self.lock:
            self.next_at = max(self.next_at, time.monotonic() + seconds)


class Importer:
    def __init__(
        self,
        client: httpx.Client,
        token: str,
        destination: Path,
        per_second: float = 8,
    ) -> None:
        self.client = client
        self.token = token
        self.destination = destination
        self.destination.mkdir(parents=True, exist_ok=True)
        self.rate = RateLimit(per_second)

    def metadata(self, path: str, params: dict[str, str] | None = None) -> Any:
        for attempt in range(4):
            self.rate.wait()
            response = self.client.get(
                API + path,
                params=params,
                headers={"Authorization": "Bearer " + self.token},
                follow_redirects=False,
            )
            if response.status_code in {401, 403}:
                raise AuthenticationError("TMDB authentication was rejected")
            if response.status_code == 429:
                retry = response.headers.get("Retry-After", "30")
                seconds = float(retry) if retry.isdigit() else 30
                self.rate.defer(min(600, max(1, seconds)))
                continue
            if response.status_code >= 500 and attempt < 3:
                self.rate.defer(2**attempt)
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError(
            "TMDB rate limit persisted; resume this batch later"
        )

    def acquire(self, film: Film) -> dict[str, Any]:
        if film.identity_conflict:
            return {"status": "needs_review", "reason": "identity_conflict"}
        try:
            candidates: list[dict[str, Any]]
            if film.tmdb_id is not None:
                data = self.metadata(f"/movie/{film.tmdb_id}")
                candidates = (
                    [cast(dict[str, Any], data)]
                    if isinstance(data, dict)
                    else []
                )
            elif film.year is not None:
                data = self.metadata(
                    "/search/movie",
                    {
                        "query": film.title,
                        "year": str(film.year),
                        "include_adult": "true",
                        "language": "en-US",
                    },
                )
                candidates = data.get("results", [])
                if data.get("total_pages", 1) > 1:
                    return {"status": "needs_review", "reason": "many_results"}
            else:
                return {
                    "status": "needs_review",
                    "reason": "missing_year_and_id",
                }
            match = select_match(film, candidates)
            if match is None:
                return {"status": "needs_review", "reason": "no_unique_match"}
            poster_path = match.get("poster_path")
            if poster_path is None:
                return {"status": "no_poster", "tmdb_id": match["id"]}
            if not isinstance(poster_path, str) or not re.fullmatch(
                r"/[A-Za-z0-9]+\.[a-z]+", poster_path
            ):
                return {"status": "error", "reason": "invalid_image_path"}
            extension = poster_path.rsplit(".", 1)[1]
            if extension not in POSTER_MEDIA_TYPES:
                return {"status": "error", "reason": "invalid_image_path"}
            image_url = IMAGES + poster_path
            with self.client.stream(
                "GET", image_url, follow_redirects=False
            ) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_POSTER_BYTES:
                        return {"status": "error", "reason": "image_too_large"}
            signatures = {
                "jpg": b"\xff\xd8\xff",
                "png": b"\x89PNG\r\n\x1a\n",
                "webp": b"RIFF",
            }
            if not body.startswith(signatures[extension]) or (
                extension == "webp" and bytes(body[8:12]) != b"WEBP"
            ):
                return {"status": "error", "reason": "invalid_image_bytes"}
            digest = hashlib.sha256(body).hexdigest()
            filename = digest[:20] + "." + extension
            target = self.destination / filename
            temporary = target.with_suffix(f".{film.id}.part")
            temporary.write_bytes(body)
            temporary.replace(target)
            return {
                "status": "stored",
                "tmdb_id": match["id"],
                "match_method": "external_id"
                if film.tmdb_id
                else "title_year",
                "filename": filename,
                "bytes": len(body),
                "sha256": digest,
                "source_url": f"https://www.themoviedb.org/movie/{match['id']}",
                "image_url": image_url,
                "provider": "TMDB",
                "license": "TMDB API terms; image rights remain with owners",
            }
        except AuthenticationError:
            raise
        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
            # URLs contain no secrets; credentials only travel in API headers.
            return {"status": "error", "reason": type(exc).__name__}


def read_token(path: Path) -> str:
    token = os.environ.get("TMDB_READ_ACCESS_TOKEN", "")
    if not token and path.is_file():
        for line in path.read_text().splitlines():
            key, separator, value = (
                line.strip().removeprefix("export ").partition("=")
            )
            if key == "TMDB_READ_ACCESS_TOKEN" and separator:
                token = value.strip().strip("\"'")
    if not token or any(c.isspace() for c in token):
        raise ValueError("Set TMDB_READ_ACCESS_TOKEN or provide --token-file")
    return token


def id_crosswalk(
    metadata: Path | None, mapping: Path | None
) -> dict[tuple[str, int | None], int | None]:
    if metadata is None:
        return {}
    ids: dict[str, set[int]] = {}
    pairs: list[list[Any]] = json.loads(mapping.read_text()) if mapping else []
    for mid, identifier in pairs:
        if isinstance(mid, str) and str(identifier).isdigit():
            ids.setdefault(mid, set()).add(int(identifier))
    films: dict[tuple[str, int | None], set[tuple[str, str]]] = {}
    with metadata.open(encoding="utf-8") as stream:
        for line in stream:
            row = line.rstrip("\n").split("\t")
            match = RELEASE_YEAR_PATTERN.match(row[3].strip())
            year = int(match.group(1)) if match else None
            films.setdefault((row[2].strip(), year), set()).add(
                (row[0], row[1])
            )
    crosswalk: dict[tuple[str, int | None], int | None] = {}
    for key, identities in films.items():
        if len(identities) != 1:
            crosswalk[key] = None
            continue
        _, mid = next(iter(identities))
        values = ids.get(mid, set())
        if values:
            crosswalk[key] = next(iter(values)) if len(values) == 1 else None
    return crosswalk


def export(state: sqlite3.Connection, directory: Path) -> dict[str, int]:
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for identifier, title, year, payload in state.execute(
        "SELECT movie_id,title,year,result FROM jobs ORDER BY movie_id"
    ):
        result = json.loads(payload) if payload else {"status": "pending"}
        counts[result["status"]] = counts.get(result["status"], 0) + 1
        rows.append(
            {"movie_id": identifier, "title": title, "year": year, **result}
        )
    for filename, content in [
        ("coverage.json", rows),
        ("manifest.json", [row for row in rows if row["status"] == "stored"]),
        ("progress.json", counts),
    ]:
        temporary = directory / (filename + ".part")
        temporary.write_text(
            json.dumps(content, ensure_ascii=False, indent=2) + "\n"
        )
        temporary.replace(directory / filename)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--state-dir", type=Path, default=Path(".tools/tmdb-posters")
    )
    parser.add_argument(
        "--token-file", type=Path, default=Path(".tools/tmdb.env")
    )
    parser.add_argument("--cmu-metadata", type=Path)
    parser.add_argument("--id-map", type=Path)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--requests-per-second", type=float, default=8)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--retry-review", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.workers <= 16 or not 0 < args.requests_per_second <= 20:
        parser.error(
            "Use 1–16 workers and a request rate greater than 0 and at most 20"
        )
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    token = read_token(args.token_file)
    crosswalk = id_crosswalk(args.cmu_metadata, args.id_map)
    directory: Path = args.state_dir
    directory.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(
        args.database.resolve().as_uri() + "?mode=ro", uri=True
    ) as catalog:
        catalog_films = list(
            catalog.execute(
                "SELECT id,title,release_year FROM movies ORDER BY id"
            )
        )
    match_counts = Counter(
        (normalized(title), year) for _, title, year in catalog_films
    )
    films: list[Film] = []
    for identifier, title, year in catalog_films:
        tmdb_id = crosswalk.get((title, year))
        identity_conflict = tmdb_id is None and (
            (title, year) in crosswalk
            or match_counts[(normalized(title), year)] > 1
        )
        films.append(Film(identifier, title, year, tmdb_id, identity_conflict))
    with sqlite3.connect(directory / "jobs.sqlite3") as state:
        state.execute("PRAGMA journal_mode=WAL")
        state.execute(
            "CREATE TABLE IF NOT EXISTS jobs (movie_id INTEGER PRIMARY KEY, title TEXT, year INTEGER, result TEXT)"
        )
        # A checkpoint belongs to this exact catalog, including title/year.
        existing = dict(
            (i, (t, y))
            for i, t, y in state.execute(
                "SELECT movie_id,title,year FROM jobs"
            )
        )
        if existing and existing != {f.id: (f.title, f.year) for f in films}:
            raise ValueError(
                "Checkpoint catalog differs; use a separate --state-dir"
            )
        state.executemany(
            "INSERT OR IGNORE INTO jobs VALUES (?,?,?,NULL)",
            [(f.id, f.title, f.year) for f in films],
        )
        if args.retry_errors:
            state.execute(
                "UPDATE jobs SET result=NULL WHERE json_extract(result,'$.status')='error'"
            )
        if args.retry_review:
            state.execute(
                "UPDATE jobs SET result=NULL "
                "WHERE json_extract(result,'$.status')='needs_review'"
            )
        state.executemany(
            "UPDATE jobs SET result=? WHERE movie_id=? "
            "AND (? OR json_extract(result,'$.match_method')='title_year' "
            "OR (? AND (json_extract(result,'$.status') IS NOT 'stored' "
            "OR json_extract(result,'$.match_method') IS NOT 'external_id')))",
            [
                (
                    json.dumps(
                        {
                            "status": "needs_review",
                            "reason": "identity_conflict",
                        }
                    ),
                    film.id,
                    (film.title, film.year) in crosswalk
                    and crosswalk[(film.title, film.year)] is None,
                    film.identity_conflict,
                )
                for film in films
                if film.identity_conflict
                or match_counts[(normalized(film.title), film.year)] > 1
            ],
        )
        state.commit()
        pending = {
            row[0]
            for row in state.execute(
                "SELECT movie_id FROM jobs WHERE result IS NULL"
            )
        }
        todo = [film for film in films if film.id in pending][: args.limit]
        print(
            json.dumps(
                {
                    "catalog": len(films),
                    "queued": len(todo),
                    "mapped_ids": sum(
                        film.tmdb_id is not None for film in films
                    ),
                    **export(state, directory),
                }
            ),
            flush=True,
        )
        with httpx.Client(
            timeout=30, limits=httpx.Limits(max_connections=args.workers * 2)
        ) as client:
            importer = Importer(
                client, token, directory / "images", args.requests_per_second
            )
            importer.metadata(
                "/configuration"
            )  # Fail before queuing on invalid credentials.

            def work(film: Film) -> tuple[Film, dict[str, Any]]:
                return film, importer.acquire(film)

            try:
                with ThreadPoolExecutor(max_workers=args.workers) as pool:
                    for index, (film, result) in enumerate(
                        pool.map(
                            work,
                            todo,
                            buffersize=args.workers * 2,
                        ),
                        1,
                    ):
                        state.execute(
                            "UPDATE jobs SET result=? WHERE movie_id=?",
                            (json.dumps(result), film.id),
                        )
                        state.commit()
                        if index % 100 == 0:
                            print(
                                json.dumps(export(state, directory)),
                                flush=True,
                            )
            finally:
                print(json.dumps(export(state, directory)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
