"""Command-line interface for local project operations."""

import argparse
import json
from pathlib import Path

from movie_db_llm.bedrock import inference_configuration
from movie_db_llm.catalog_importer import import_catalog
from movie_db_llm.cmu_parser import parse_catalog
from movie_db_llm.config import INTENT_PROVIDER, OLLAMA_MODEL
from movie_db_llm.database import SessionLocal
from movie_db_llm.main import main as run_server
from movie_db_llm.recommendations import catalog_genres_query

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
        help="Show the Bedrock request configuration and catalog fingerprint.",
    )
    return parser


def import_cmu() -> None:
    """Parse and import the repository's CMU movie catalog source files."""
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
        if INTENT_PROVIDER != "bedrock":
            print(
                json.dumps(
                    {
                        "provider": INTENT_PROVIDER,
                        "model": OLLAMA_MODEL
                        if INTENT_PROVIDER == "ollama"
                        else None,
                        "note": "This route does not validate Bedrock.",
                    },
                    indent=2,
                )
            )
            return
        with SessionLocal() as session:
            genres = list(session.scalars(catalog_genres_query()))
        print(json.dumps(inference_configuration(genres), indent=2))
        return

    run_server()
