"""Command-line interface for local project operations."""

import argparse
import json
from pathlib import Path

from botocore.exceptions import InvalidRegionError
from sqlalchemy.exc import SQLAlchemyError

from movie_db_llm.bedrock import inference_configuration
from movie_db_llm.catalog import catalog_genres, open_catalog
from movie_db_llm.catalog_importer import import_catalog
from movie_db_llm.cmu_parser import parse_catalog
from movie_db_llm.config import DATABASE_URL, INTENT_PROVIDER, OLLAMA_MODEL

CMU_SOURCE_DIRECTORY = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "cmu_movie_summary_corpus"
    / "MovieSummaries"
)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(prog="movie-db-llm")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser(
        "import-cmu",
        help="Import the CMU Movie Summary Corpus into an empty catalog.",
    )
    subcommands.add_parser(
        "inference-config",
        help="Show the provider and fingerprint for the active Bedrock route.",
    )
    return parser


def import_cmu() -> None:
    """Parse and import the repository's CMU movie catalog source files."""
    from movie_db_llm.database import SessionLocal

    catalog_movies = parse_catalog(
        CMU_SOURCE_DIRECTORY / "movie.metadata.tsv",
        CMU_SOURCE_DIRECTORY / "plot_summaries.txt",
    )
    with SessionLocal() as session:
        result = import_catalog(session, catalog_movies)

    print(
        "Imported "
        f"{result.movie_count} movies and "
        f"{result.genre_assignment_count} genre assignments."
    )


def main() -> None:
    """Run the requested project command."""
    arguments = build_parser().parse_args()
    if arguments.command == "import-cmu":
        import_cmu()
        return
    if arguments.command == "inference-config":
        if INTENT_PROVIDER == "ollama":
            report: dict[str, object] = {
                "provider": INTENT_PROVIDER,
                "model": OLLAMA_MODEL,
                "note": "This route does not validate Bedrock.",
            }
        elif INTENT_PROVIDER == "bedrock":
            try:
                with open_catalog(DATABASE_URL) as connection:
                    genres = catalog_genres(connection)
                report = inference_configuration(genres)
            except InvalidRegionError:
                raise SystemExit("Invalid AWS region.") from None
            except SQLAlchemyError, ValueError:
                raise SystemExit("Cannot read catalog.") from None
        else:
            raise SystemExit("INTENT_PROVIDER must be bedrock or ollama.")
        print(json.dumps(report, indent=2))
        return

    from movie_db_llm.main import main as run_server

    run_server()
