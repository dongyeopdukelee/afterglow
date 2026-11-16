"""Tests for the movie-list endpoint."""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from movie_db_llm.database import configure_sqlite_connection
from movie_db_llm.main import list_movies
from movie_db_llm.models import Base, Movie, MovieGenre


def test_list_movies_returns_ordered_movie_cards() -> None:
    """The endpoint returns lightweight, ordered movies with their genres."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session_factory = sessionmaker(bind=engine)

    with test_session_factory() as session:
        session.add_all(
            [
                Movie(
                    title="Action Movie",
                    synopsis="An action movie.",
                    release_year=2020,
                    genre_assignments=[MovieGenre(genre="Action")],
                ),
                Movie(
                    title="Drama Movie",
                    synopsis="A drama movie.",
                    release_year=None,
                    genre_assignments=[MovieGenre(genre="Drama")],
                ),
            ]
        )
        session.commit()

    with test_session_factory() as session:
        response = list_movies(session)

    assert response.model_dump(mode="json") == {
        "items": [
            {
                "id": 1,
                "title": "Action Movie",
                "release_year": 2020,
                "genres": ["Action"],
            },
            {
                "id": 2,
                "title": "Drama Movie",
                "release_year": None,
                "genres": ["Drama"],
            },
        ]
    }


def test_list_movies_allows_a_missing_release_year() -> None:
    """Movie cards preserve an unavailable release year as null."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session_factory = sessionmaker(bind=engine)

    with test_session_factory() as session:
        session.add(
            Movie(
                title="Undated Movie",
                synopsis="A movie with an unavailable release year.",
                release_year=None,
                genre_assignments=[MovieGenre(genre="Drama")],
            )
        )
        session.commit()

    with test_session_factory() as session:
        response = list_movies(session)

    assert response.model_dump(mode="json") == {
        "items": [
            {
                "id": 1,
                "title": "Undated Movie",
                "release_year": None,
                "genres": ["Drama"],
            }
        ]
    }


def test_list_movies_applies_limit_and_offset() -> None:
    """Movie pages follow the title and identifier ordering."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session_factory = sessionmaker(bind=engine)

    with test_session_factory() as session:
        session.add_all(
            [
                Movie(title="Alpha", synopsis=".", release_year=2001),
                Movie(title="Bravo", synopsis=".", release_year=2002),
                Movie(title="Charlie", synopsis=".", release_year=2003),
            ]
        )
        session.commit()

    with test_session_factory() as session:
        first_page = list_movies(session, limit=2, offset=0)
        second_page = list_movies(session, limit=2, offset=2)
        empty_page = list_movies(session, limit=2, offset=3)

    assert [movie.title for movie in first_page.items] == ["Alpha", "Bravo"]
    assert [movie.title for movie in second_page.items] == ["Charlie"]
    assert empty_page.items == []


def test_seeded_discovery_has_stable_complete_pages() -> None:
    """One seed never repeats or loses films; another changes their order."""
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", configure_sqlite_connection)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        session.add_all(
            [
                Movie(title="Alpha", synopsis=".", release_year=2001),
                Movie(title="Bravo", synopsis=".", release_year=2002),
                Movie(title="Charlie", synopsis=".", release_year=None),
                Movie(title="Metropolis", synopsis=".", release_year=1927),
            ]
        )
        session.commit()
        first = list_movies(session, limit=2, seed=1)
        second = list_movies(session, limit=2, offset=2, seed=1)
        again = list_movies(session, limit=2, seed=1)
        another = list_movies(session, limit=4, seed=2)
        ids = [movie.id for movie in first.items + second.items]
        assert ids == [4, 2, 3, 1]
        assert len(set(ids)) == 4
        assert again == first
        assert [movie.id for movie in another.items] == [4, 2, 1, 3]
        assert list_movies(session, offset=4, seed=1).items == []
    engine.dispose()


@pytest.mark.parametrize("seed", range(6))
def test_discovery_poster_priority_matches_both_title_and_year(
    seed: int,
) -> None:
    """A same-title remake cannot borrow a verified original's poster slot."""
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", configure_sqlite_connection)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        session.add_all(
            [
                Movie(title="Metropolis", synopsis=".", release_year=2001),
                Movie(title="Metropolis", synopsis=".", release_year=1927),
                Movie(title="Unmatched", synopsis=".", release_year=None),
            ]
        )
        session.commit()
        response = list_movies(session, seed=seed)
        assert response.items[0].release_year == 1927
        assert len(response.items) == 3
    engine.dispose()
