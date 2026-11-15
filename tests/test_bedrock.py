"""Bedrock API-contract tests with real application retrieval, not live AI."""

import json
import runpy
import subprocess
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import TYPE_CHECKING, cast

import boto3
import httpx
import pytest
from botocore.exceptions import ClientError, CredentialRetrievalError
from botocore.stub import ANY, Stubber
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from movie_db_llm import bedrock, ollama
from movie_db_llm.bedrock import BedrockIntentParser
from movie_db_llm.database import configure_sqlite_connection, get_session
from movie_db_llm.main import app
from movie_db_llm.models import Base, Movie, MovieGenre
from movie_db_llm.recommendations import (
    InvalidModelResponseError,
    ModelUnavailableError,
)

if TYPE_CHECKING:
    from mypy_boto3_bedrock_runtime import BedrockRuntimeClient


@pytest.fixture
def aws_client() -> Generator[BedrockRuntimeClient]:
    """Never consult a developer's real AWS credentials in deterministic CI."""
    client = cast(
        "BedrockRuntimeClient",
        boto3.client(
            "bedrock-runtime",
            region_name="us-west-2",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        ),
    )
    yield client
    client.close()


def intent_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "genres": ["Comedy"],
        "year_filters": [
            {"comparison": "since", "year": 1990},
            {"comparison": "until", "year": 1999},
        ],
        "title_keywords": ["DOG"],
        "unsupported_constraints": [],
    }
    return payload | changes


def reply(payload: dict[str, object]) -> dict[str, object]:
    return {
        "stopReason": "tool_use",
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "toolUseId": "intent-result",
                            "name": "movie_intent",
                            "input": payload,
                        }
                    }
                ],
            }
        },
        "usage": {"inputTokens": 100, "outputTokens": 50, "totalTokens": 150},
        "metrics": {"latencyMs": 1},
    }


def test_request_has_bounded_generation_and_literal_years(
    aws_client: BedrockRuntimeClient,
) -> None:
    """AWS receives a bounded extraction request, never instructions for SQL."""
    with Stubber(aws_client) as stub:
        stub.add_response(
            "converse",
            reply(intent_payload()),
            expected_params={
                "modelId": "us.amazon.nova-micro-v1:0",
                "system": ANY,
                "messages": ANY,
                "inferenceConfig": {"maxTokens": 512, "temperature": 0},
                "toolConfig": ANY,
            },
        )
        result = BedrockIntentParser(aws_client).parse(
            "90s comedy movies with dog in the title", ["Comedy", "Drama"]
        )
        assert result.genres == ["Comedy"]
        assert result.year_min == 1990
        assert result.year_max == 1999
        assert result.year_min_inclusive is True
        assert result.year_max_inclusive is True
        assert result.title_keywords == ["DOG"]
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    ("comparison", "lower", "upper", "lower_inclusive", "upper_inclusive"),
    [
        ("after", 2000, None, False, True),
        ("since", 2000, None, True, True),
        ("before", None, 2000, True, False),
        ("until", None, 2000, True, True),
        ("in", 2000, 2000, True, True),
    ],
)
def test_year_comparisons_are_owned_by_application(
    aws_client: BedrockRuntimeClient,
    comparison: str,
    lower: int | None,
    upper: int | None,
    lower_inclusive: bool,
    upper_inclusive: bool,
) -> None:
    with Stubber(aws_client) as stub:
        stub.add_response(
            "converse",
            reply(
                intent_payload(
                    year_filters=[{"comparison": comparison, "year": 2000}]
                )
            ),
        )
        result = BedrockIntentParser(aws_client).parse(
            f"Comedy movies {comparison} 2000", ["Comedy"]
        )
    assert (
        result.year_min,
        result.year_max,
        result.year_min_inclusive,
        result.year_max_inclusive,
    ) == (lower, upper, lower_inclusive, upper_inclusive)


@pytest.mark.parametrize(
    "payload",
    [
        intent_payload(genres="Comedy"),
        intent_payload(year_filters=[{"comparison": "after", "year": "2000"}]),
        intent_payload(unexpected="ignore the schema"),
        {},
    ],
)
def test_malformed_model_output_never_becomes_a_query(
    aws_client: BedrockRuntimeClient, payload: dict[str, object]
) -> None:
    with Stubber(aws_client) as stub:
        stub.add_response("converse", reply(payload))
        with pytest.raises(InvalidModelResponseError):
            BedrockIntentParser(aws_client).parse("Comedies", ["Comedy"])


def test_truncated_response_is_not_used(
    aws_client: BedrockRuntimeClient,
) -> None:
    with Stubber(aws_client) as stub:
        response = reply(intent_payload()) | {"stopReason": "max_tokens"}
        stub.add_response("converse", response)
        with pytest.raises(InvalidModelResponseError):
            BedrockIntentParser(aws_client).parse("Comedies", ["Comedy"])


def test_oversized_context_does_not_invoke_aws(
    aws_client: BedrockRuntimeClient,
) -> None:
    with Stubber(aws_client):
        with pytest.raises(ModelUnavailableError, match="context"):
            BedrockIntentParser(aws_client).parse("Comedies", ["x" * 60_000])


@pytest.fixture
def hosted_api(
    aws_client: BedrockRuntimeClient, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[httpx.Client, Stubber]]:
    """Exercise provider selection, HTTP handling, and real SQLite queries."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(engine, "connect", configure_sqlite_connection)
    Base.metadata.create_all(engine)
    previous = app.dependency_overrides.copy()
    monkeypatch.setattr(ollama, "INTENT_PROVIDER", "bedrock")
    monkeypatch.setattr(ollama, "bedrock_client", lambda: aws_client)
    try:
        with Session(engine) as session:
            for title, year, genre in [
                ("Dog Days", 1995, "Comedy"),
                ("Dog Days Redux", 2001, "Comedy"),
                ("Dog Drama", 1995, "Drama"),
                ("Other Comedy", 1995, "Comedy"),
            ]:
                session.add(
                    Movie(
                        title=title,
                        release_year=year,
                        synopsis="Full synopsis.",
                        genre_assignments=[MovieGenre(genre=genre)],
                    )
                )
            session.commit()
            app.dependency_overrides[get_session] = lambda: session
            with Stubber(aws_client) as stub, TestClient(app) as client:
                yield client, stub
                stub.assert_no_pending_responses()
    finally:
        app.dependency_overrides = previous
        engine.dispose()


def test_hosted_contract_drives_real_catalog_filters(
    hosted_api: tuple[httpx.Client, Stubber],
) -> None:
    """Only the film satisfying genre AND year AND literal title is returned."""
    client, stub = hosted_api
    stub.add_response("converse", reply(intent_payload()))
    response = client.post(
        "/recommendations",
        json={"query": "90s comedy movies with dog in the title"},
    )
    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "id": 1,
            "title": "Dog Days",
            "release_year": 1995,
            "genres": ["Comedy"],
        }
    ]
    assert response.json()["intent"]["year_max"] == 1999


@pytest.mark.parametrize(
    "code",
    [
        "AccessDeniedException",
        "ThrottlingException",
        "ValidationException",
        "ServiceUnavailableException",
        "ModelTimeoutException",
        "ExpiredTokenException",
        "InvalidClientTokenId",
    ],
)
def test_hosted_failure_keeps_browsing_available(
    hosted_api: tuple[httpx.Client, Stubber],
    code: str,
) -> None:
    client, stub = hosted_api
    stub.add_client_error(
        "converse",
        service_error_code=code,
        service_message="Private AWS account details must not leak.",
    )
    response = client.post("/recommendations", json={"query": "Comedies"})
    assert response.status_code == 503
    assert response.json() == {
        "detail": "Hosted search is temporarily unavailable."
    }
    assert client.get("/movies").status_code == 200
    assert client.get("/movies/1").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}


def test_invalid_hosted_result_returns_502_without_movie_cards(
    hosted_api: tuple[httpx.Client, Stubber],
) -> None:
    client, stub = hosted_api
    stub.add_response("converse", reply({}))
    response = client.post("/recommendations", json={"query": "Comedies"})
    assert response.status_code == 502
    assert "items" not in response.json()


def test_unknown_provider_does_not_fall_back_to_paid_calls(
    hosted_api: tuple[httpx.Client, Stubber],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = hosted_api
    monkeypatch.setattr(ollama, "INTENT_PROVIDER", "typo")
    assert (
        client.post("/recommendations", json={"query": "Comedies"}).status_code
        == 503
    )
    assert client.get("/movies").status_code == 200


@pytest.fixture(
    params=[
        CredentialRetrievalError(
            provider="private-profile", error_msg="private account details"
        ),
        ClientError(
            {
                "Error": {
                    "Code": "ExpiredTokenException",
                    "Message": "private account details",
                }
            },
            "AssumeRole",
        ),
    ],
    ids=["credential-provider", "service-returned-credentials"],
)
def credential_error(request: pytest.FixtureRequest) -> Exception:
    return cast(Exception, request.param)


def test_expired_credentials_do_not_leak_details_or_break_browsing(
    hosted_api: tuple[httpx.Client, Stubber],
    monkeypatch: pytest.MonkeyPatch,
    credential_error: Exception,
) -> None:
    def unavailable() -> BedrockRuntimeClient:
        raise credential_error

    client, _ = hosted_api
    monkeypatch.setattr(ollama, "bedrock_client", unavailable)
    response = client.post("/recommendations", json={"query": "Comedies"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Hosted search is unavailable."}
    assert client.get("/movies").status_code == 200
    assert client.get("/movies/1").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}


def test_live_check_initialization_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    credential_error: Exception,
) -> None:
    def unavailable() -> BedrockRuntimeClient:
        raise credential_error

    monkeypatch.setattr(bedrock, "bedrock_client", unavailable)
    monkeypatch.setattr(sys, "argv", ["check_bedrock.py", "--live"])
    root = Path(__file__).resolve().parents[1]
    main = cast(
        Callable[[], int],
        runpy.run_path(str(root / "scripts/check_bedrock.py"))["main"],
    )
    assert main() == 1
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "status": "blocked",
        "message": "AWS credentials unavailable.",
    }


@pytest.mark.parametrize(
    "code", ["ExpiredTokenException", "InvalidClientTokenId"]
)
def test_live_check_service_failure_stops_with_sanitized_output(
    aws_client: BedrockRuntimeClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    code: str,
) -> None:
    monkeypatch.setattr(bedrock, "bedrock_client", lambda: aws_client)
    monkeypatch.setattr(sys, "argv", ["check_bedrock.py", "--live"])
    root = Path(__file__).resolve().parents[1]
    main = cast(
        Callable[[], int],
        runpy.run_path(str(root / "scripts/check_bedrock.py"))["main"],
    )
    with Stubber(aws_client) as stub:
        stub.add_client_error(
            "converse",
            service_error_code=code,
            service_message="private account details",
        )
        assert main() == 1
        stub.assert_no_pending_responses()
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "query": "Comedies from the 1990s",
        "status": "blocked",
        "message": "Hosted search is temporarily unavailable.",
    }


def test_live_check_requires_explicit_opt_in() -> None:
    """Running the evaluation accidentally must not make paid model calls."""
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts/check_bedrock.py")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "--live" in result.stderr
