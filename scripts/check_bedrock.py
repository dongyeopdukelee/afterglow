"""Opt-in, bounded live evaluation; never part of credential-free CI."""

import argparse
import json
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter
from urllib.parse import quote, urlencode

import httpx
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import Connection, create_engine, make_url, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from movie_db_llm.bedrock import BedrockIntentParser, bedrock_client
from movie_db_llm.config import AWS_REGION, BEDROCK_MODEL_ID, DATABASE_URL
from movie_db_llm.models import MovieGenre
from movie_db_llm.recommendations import (
    InvalidModelResponseError,
    ModelUnavailableError,
)


class Usage(BaseModel):
    """Measured provider counts, including any billed reasoning output."""

    prompt_tokens: int = Field(ge=0, strict=True)
    completion_tokens: int = Field(ge=0, strict=True)
    total_tokens: int = Field(ge=0, strict=True)


@contextmanager
def open_catalog(database_url: str) -> Generator[Connection]:
    """Open an existing SQLite catalog without permitting writes or creation."""
    url = make_url(database_url)
    database = url.database
    if (
        url.drivername not in ("sqlite", "sqlite+pysqlite")
        or not database
        or database == ":memory:"
    ):
        raise ValueError("Expected a file-backed SQLite catalog.")
    dialect = url.get_dialect()()
    _, driver_options = dialect.create_connect_args(url)
    uri_options: dict[str, str | tuple[str, ...]] = {}
    if database.startswith("file:") and driver_options.get("uri"):
        database = database.partition("#")[0]
        if (
            database in ("file:", "file::memory:")
            or url.query.get("mode") == "memory"
        ):
            raise ValueError("Expected a file-backed SQLite catalog.")
        uri_options = dict(url.difference_update_query(driver_options).query)
    else:
        database = Path(database).absolute().as_uri()
    uri_options["mode"] = "ro"
    query = urlencode(uri_options, doseq=True, quote_via=quote)
    readonly_url = url.set(
        database=f"{database}?{query}", query={"uri": "true"}
    )
    driver_options["uri"] = True
    engine = create_engine(
        readonly_url, connect_args=driver_options, poolclass=NullPool
    )
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        required=True,
        help="Authorize up to ten bounded, paid Bedrock parsing requests.",
    )
    parser.add_argument(
        "--catalog",
        action="store_true",
        help="Use all genres in the configured catalog (read-only).",
    )
    arguments = parser.parse_args()
    genres = ["Comedy", "Drama", "Horror", "Thriller"]
    if arguments.catalog:
        try:
            with open_catalog(DATABASE_URL) as connection:
                genres = list(
                    connection.scalars(
                        select(MovieGenre.genre)
                        .distinct()
                        .order_by(MovieGenre.genre)
                    )
                )
        except SQLAlchemyError, ValueError:
            print(
                json.dumps(
                    {"status": "blocked", "message": "Cannot read catalog."}
                )
            )
            return 1
        if not {"Comedy", "Horror", "Thriller"}.issubset(genres):
            message = (
                "Benchmark requires Comedy, Horror and Thriller."
                if genres
                else "Empty catalog."
            )
            print(json.dumps({"status": "blocked", "message": message}))
            return 1
    try:
        client = bedrock_client()
        model = BedrockIntentParser(client, BEDROCK_MODEL_ID)
    except BotoCoreError, ClientError:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "message": "Check the AWS region and credentials.",
                }
            )
        )
        return 1
    cases: list[tuple[str, dict[str, object] | None]] = [
        (
            "Comedies from the 1990s",
            {
                "genres": ["Comedy"],
                "year_min": 1990,
                "year_max": 1999,
                "year_min_inclusive": True,
                "year_max_inclusive": True,
                "title_keywords": [],
                "unsupported_constraints": [],
            },
        ),
        (
            "Thriller movies after 2000",
            {
                "genres": ["Thriller"],
                "year_min": 2000,
                "year_max": None,
                "year_min_inclusive": False,
                "year_max_inclusive": True,
                "title_keywords": [],
                "unsupported_constraints": [],
            },
        ),
        (
            "Thriller movies since 2000",
            {
                "genres": ["Thriller"],
                "year_min": 2000,
                "year_max": None,
                "year_min_inclusive": True,
                "year_max_inclusive": True,
                "title_keywords": [],
                "unsupported_constraints": [],
            },
        ),
        (
            "Movies whose titles contain A_B%/",
            {
                "genres": [],
                "year_min": None,
                "year_max": None,
                "year_min_inclusive": True,
                "year_max_inclusive": True,
                "title_keywords": ["A_B%/"],
                "unsupported_constraints": [],
            },
        ),
    ]
    for word, year_min, year_max, lower_inclusive, upper_inclusive in [
        ("before", None, 2000, True, False),
        ("until", None, 2000, True, True),
        ("in", 2000, 2000, True, True),
    ]:
        cases.append(
            (
                f"Thriller movies {word} 2000",
                {
                    "genres": ["Thriller"],
                    "year_min": year_min,
                    "year_max": year_max,
                    "year_min_inclusive": lower_inclusive,
                    "year_max_inclusive": upper_inclusive,
                    "title_keywords": [],
                    "unsupported_constraints": [],
                },
            )
        )
    cases.extend(
        [
            ("90s comedy movies about dogs", None),
            ("Horror or Comedy movies", None),
            (
                "hello",
                {
                    "genres": [],
                    "year_min": None,
                    "year_max": None,
                    "year_min_inclusive": True,
                    "year_max_inclusive": True,
                    "title_keywords": [],
                    "unsupported_constraints": [],
                },
            ),
        ]
    )
    usage: list[Usage | None] = []

    def observe(response: httpx.Response) -> None:
        response.read()
        try:
            usage.append(
                Usage.model_validate_json(
                    json.dumps(response.json().get("usage"))
                )
            )
        except ValueError, ValidationError, AttributeError:
            usage.append(None)

    client.event_hooks["response"].append(observe)
    try:
        for query, expected in cases:
            usage.clear()
            started = perf_counter()
            try:
                actual = model.parse(query, genres).model_dump()
            except (ModelUnavailableError, InvalidModelResponseError) as error:
                print(
                    json.dumps(
                        {
                            "query": query,
                            "status": "blocked",
                            "message": str(error),
                        }
                    )
                )
                return 1
            passed = (
                actual == expected
                if expected is not None
                else bool(actual["unsupported_constraints"])
            )
            measured_usage = usage[-1] if usage else None
            print(
                json.dumps(
                    {
                        "query": query,
                        "status": "pass" if passed else "fail",
                        "actual": actual,
                        "expected": expected
                        if expected is not None
                        else {"unsupported_constraints": "must be nonempty"},
                        "model": BEDROCK_MODEL_ID,
                        "region": AWS_REGION,
                        "catalog_genres": len(genres),
                        "latency_ms": round((perf_counter() - started) * 1000),
                        "usage": (
                            measured_usage.model_dump()
                            if measured_usage
                            else None
                        ),
                    }
                ),
                flush=True,
            )
            if not passed:
                return 1
    finally:
        client.event_hooks["response"].remove(observe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
