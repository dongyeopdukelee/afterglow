"""Reviewed title/year matches shared with the website poster manifest."""

import json
from importlib.resources import files

POSTER_FILMS: tuple[tuple[str, int], ...] = tuple(
    (entry["title"], entry["year"])
    for entry in json.loads(
        files("movie_db_llm").joinpath("posters.json").read_text()
    )
)
