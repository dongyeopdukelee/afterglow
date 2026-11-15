"""Opt-in, bounded live evaluation; never part of credential-free CI."""

import argparse
import json

from botocore.exceptions import BotoCoreError

from movie_db_llm.bedrock import BedrockIntentParser, bedrock_client
from movie_db_llm.recommendations import (
    InvalidModelResponseError,
    ModelUnavailableError,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        required=True,
        help="Authorize up to four small, paid Bedrock parsing requests.",
    )
    parser.parse_args()
    try:
        model = BedrockIntentParser(bedrock_client())
    except BotoCoreError:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "message": "AWS credentials unavailable.",
                }
            )
        )
        return 1
    cases: list[tuple[str, dict[str, object]]] = [
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
    for query, expected in cases:
        try:
            actual = model.parse(
                query, ["Comedy", "Drama", "Horror", "Thriller"]
            ).model_dump()
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
        passed = actual == expected
        print(
            json.dumps(
                {
                    "query": query,
                    "status": "pass" if passed else "fail",
                    "actual": actual,
                    "expected": expected,
                }
            )
        )
        if not passed:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
