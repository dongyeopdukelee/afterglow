"""FastAPI application entry point."""

from asyncio import to_thread
from typing import Annotated, Literal

import uvicorn
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import case, func, select, tuple_
from sqlalchemy.orm import Session, selectinload

from movie_db_llm import config
from movie_db_llm.database import get_session
from movie_db_llm.models import Movie
from movie_db_llm.ollama import get_intent_parser
from movie_db_llm.poster_store import PosterUnavailableError, poster_store
from movie_db_llm.posters import POSTER_FILMS, PosterAsset, poster_for
from movie_db_llm.recommendations import (
    IntentParser,
    InvalidModelResponseError,
    ModelUnavailableError,
    MovieIntent,
    RecommendationRequest,
    UnsupportedRequestError,
    recommend_movies,
)

app = FastAPI(title="Afterglow")
POSTER_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}


class HealthResponse(BaseModel):
    """Response returned by the health endpoint."""

    status: Literal["ok"]


class MovieListItem(BaseModel):
    """Lightweight movie data returned by the catalog list."""

    id: int
    title: str
    release_year: int | None
    genres: list[str]


class MovieListResponse(BaseModel):
    """Response returned by the movie-list endpoint."""

    items: list[MovieListItem]


class MovieDetail(MovieListItem):
    """Full catalog record for the website's movie detail view."""

    synopsis: str


class RecommendationResponse(MovieListResponse):
    """Grounded movie cards together with the interpreted search filters."""

    intent: MovieIntent


def movie_card(movie: Movie) -> MovieListItem:
    """Serialize a catalog movie without including its synopsis."""
    return MovieListItem(
        id=movie.id,
        title=movie.title,
        release_year=movie.release_year,
        genres=sorted(
            assignment.genre for assignment in movie.genre_assignments
        ),
    )


@app.get("/health")
def health_check() -> HealthResponse:
    """Report that the API is running."""
    return HealthResponse(status="ok")


@app.get("/movies")
def list_movies(
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    seed: Annotated[int | None, Query(ge=0, le=2_147_483_647)] = None,
) -> MovieListResponse:
    """Browse alphabetically, or shuffle with stable pages for a given seed.

    Shuffled discovery puts the reviewed starter films first, then the rest of
    the complete catalog. All pages in one visit use the same seed.
    """
    statement = (
        select(Movie)
        .options(selectinload(Movie.genre_assignments))
        .order_by(Movie.title, Movie.id)
        .limit(limit)
        .offset(offset)
    )
    if seed is not None:
        statement = statement.order_by(None).order_by(
            case(
                (tuple_(Movie.title, Movie.release_year).in_(POSTER_FILMS), 0),
                else_=1,
            ),
            func.catalog_shuffle(Movie.id, seed),
            Movie.id,
        )
    movies = session.scalars(statement).all()

    return MovieListResponse(items=[movie_card(movie) for movie in movies])


@app.get("/movies/{movie_id}")
def get_movie(
    movie_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> MovieDetail:
    """Return a catalog movie and its full synopsis, or a clear 404."""
    movie = session.scalar(
        select(Movie)
        .options(selectinload(Movie.genre_assignments))
        .where(Movie.id == movie_id)
    )
    if movie is None:
        raise HTTPException(status_code=404, detail="Movie not found.")
    return MovieDetail(
        **movie_card(movie).model_dump(), synopsis=movie.synopsis
    )


def find_poster_asset(
    movie_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> PosterAsset:
    """Resolve a poster while borrowing a catalog connection briefly."""
    if not config.POSTER_BUCKET:
        raise HTTPException(404, "Poster unavailable.", headers=POSTER_HEADERS)
    with session:
        movie = session.get(Movie, movie_id)
        if movie is None:
            raise HTTPException(
                404, "Poster unavailable.", headers=POSTER_HEADERS
            )
        identity = movie.title, movie.release_year, movie.synopsis
    try:
        asset = poster_for(*identity)
    except OSError, ValueError:
        raise HTTPException(
            503, "Poster temporarily unavailable.", headers=POSTER_HEADERS
        ) from None
    if asset is None:
        raise HTTPException(404, "Poster unavailable.", headers=POSTER_HEADERS)
    return asset


def fetch_poster(asset: PosterAsset) -> bytes:
    return poster_store().fetch(asset)


@app.get("/movies/{movie_id}/poster", response_class=Response)
async def get_movie_poster(
    asset: Annotated[PosterAsset, Depends(find_poster_asset)],
) -> Response:
    """Fetch one matched private object on demand, with no cache or HEAD call."""
    try:
        data = await to_thread(fetch_poster, asset)
    except (
        PosterUnavailableError,
        BotoCoreError,
        ClientError,
        OSError,
        ValueError,
    ):
        raise HTTPException(
            503, "Poster temporarily unavailable.", headers=POSTER_HEADERS
        ) from None
    return Response(data, media_type=asset.media_type, headers=POSTER_HEADERS)


@app.post("/recommendations")
def get_recommendations(
    request: RecommendationRequest,
    session: Annotated[Session, Depends(get_session)],
    parser: Annotated[IntentParser, Depends(get_intent_parser)],
) -> RecommendationResponse:
    """Interpret a natural-language request and return up to ten matches."""
    try:
        intent, movies = recommend_movies(request.query, session, parser)
    except UnsupportedRequestError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Use genres, release years, and title keywords.",
                "unsupported_constraints": error.constraints,
            },
        ) from error
    except ModelUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except InvalidModelResponseError as error:
        raise HTTPException(
            status_code=502,
            detail="Model returned invalid or incomplete intent.",
        ) from error

    return RecommendationResponse(
        intent=intent, items=[movie_card(movie) for movie in movies]
    )


def main() -> None:
    """Run the application server locally."""
    uvicorn.run(app, host="127.0.0.1", port=8000)
