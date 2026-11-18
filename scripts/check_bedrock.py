"""Opt-in, bounded live evaluation; never part of credential-free CI."""

import argparse
import json
from time import perf_counter

import httpx
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from movie_db_llm.bedrock import (
    BedrockIntentParser,
    bedrock_client,
    inference_configuration,
)
from movie_db_llm.catalog import catalog_genres, open_catalog
from movie_db_llm.config import BEDROCK_MODEL_ID, DATABASE_URL, INTENT_PROVIDER
from movie_db_llm.recommendations import (
    InvalidModelResponseError,
    ModelUnavailableError,
)


class Usage(BaseModel):
    """Measured provider counts, including any billed reasoning output."""

    prompt_tokens: int = Field(ge=0, strict=True)
    completion_tokens: int = Field(ge=0, strict=True)
    total_tokens: int = Field(ge=0, strict=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        required=True,
        help="Authorize up to ten bounded, paid Bedrock parsing requests.",
    )
    parser.parse_args()
    if INTENT_PROVIDER != "bedrock":
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "message": "Live checks require INTENT_PROVIDER=bedrock.",
                }
            )
        )
        return 1
    try:
        with open_catalog(DATABASE_URL) as connection:
            genres = catalog_genres(connection)
    except SQLAlchemyError, ValueError:
        print(
            json.dumps({"status": "blocked", "message": "Cannot read catalog."})
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
        configuration = inference_configuration(genres, model=BEDROCK_MODEL_ID)
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
                intent = model.parse(query, genres)
            except (ModelUnavailableError, InvalidModelResponseError) as error:
                elapsed = perf_counter() - started
                result: dict[str, object] = {
                    "status": "blocked",
                    "message": str(error),
                }
            else:
                elapsed = perf_counter() - started
                actual = intent.model_dump()
                passed = all(genre in genres for genre in intent.genres) and (
                    actual == expected
                    if expected is not None
                    else bool(intent.unsupported_constraints)
                )
                result = {
                    "status": "pass" if passed else "fail",
                    "actual": actual,
                    "expected": expected
                    if expected is not None
                    else {
                        "genres": "must be a subset of the catalog genres",
                        "unsupported_constraints": "must be nonempty",
                    },
                }
            measured_usage = usage[-1] if usage else None
            print(
                json.dumps(
                    {
                        **configuration,
                        "query": query,
                        **result,
                        "latency_ms": round(elapsed * 1000),
                        "usage": (
                            measured_usage.model_dump()
                            if measured_usage
                            else None
                        ),
                    }
                ),
                flush=True,
            )
            if result["status"] != "pass":
                return 1
    finally:
        client.event_hooks["response"].remove(observe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
