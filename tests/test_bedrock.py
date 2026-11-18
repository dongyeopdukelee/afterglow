"""Mantle protocol and hosted API behavior, without credentials or paid AI."""

import json
import runpy
import subprocess
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import cast

import boto3
import httpx
import pytest
from botocore.credentials import Credentials
from botocore.exceptions import (
    ClientError,
    CredentialRetrievalError,
    NoCredentialsError,
)
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from movie_db_llm import bedrock, ollama
from movie_db_llm.bedrock import BedrockAuth, BedrockIntentParser
from movie_db_llm.database import configure_sqlite_connection, get_session
from movie_db_llm.main import app
from movie_db_llm.models import Base, Movie, MovieGenre
from movie_db_llm.recommendations import (
    InvalidModelResponseError,
    ModelUnavailableError,
)

type Replies = list[httpx.Response | Exception]


@pytest.fixture
def replies() -> Replies:
    return []


@pytest.fixture
def requests() -> list[httpx.Request]:
    return []


@pytest.fixture
def aws_client(
    replies: Replies, requests: list[httpx.Request]
) -> Generator[httpx.Client]:
    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        result = replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    with httpx.Client(
        base_url="https://bedrock-mantle.us-west-2.api.aws/v1/",
        transport=httpx.MockTransport(handle),
    ) as client:
        yield client
    assert replies == []


def intent_payload(**changes: object) -> dict[str, object]:
    return {
        "genres": ["Comedy"],
        "year_filters": [
            {"comparison": "since", "year": 1990},
            {"comparison": "until", "year": 1999},
        ],
        "title_keywords": ["DOG"],
        "unsupported_constraints": [],
    } | changes


def reply(
    payload: dict[str, object], finish: str = "tool_calls"
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "finish_reason": finish,
                    "message": {
                        "role": "assistant",
                        "refusal": None,
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "movie_intent",
                                    "arguments": json.dumps(payload),
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        },
    )


def test_bounded_request_and_literal_years(
    aws_client: httpx.Client,
    replies: Replies,
    requests: list[httpx.Request],
) -> None:
    replies.append(reply(intent_payload()))
    result = BedrockIntentParser(aws_client).parse(
        "90s comedy movies with dog in the title", ["Comedy", "Drama"]
    )
    assert result.genres == ["Comedy"]
    assert (result.year_min, result.year_max) == (1990, 1999)
    assert result.year_min_inclusive is True
    assert result.year_max_inclusive is True
    assert result.title_keywords == ["DOG"]
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == (
        "https://bedrock-mantle.us-west-2.api.aws/v1/chat/completions"
    )
    body = json.loads(request.content)
    assert body["model"] == "mistral.ministral-3-8b-instruct"
    assert body["max_completion_tokens"] == 1024
    assert body["parallel_tool_calls"] is False
    assert body["tool_choice"] == {
        "type": "function",
        "function": {"name": "movie_intent"},
    }
    assert body["tools"][0]["function"]["strict"] is True
    # The emitted model interface must not demonstrate plain-text responses
    # while requiring a function call. Live evaluation verifies interpretation.
    examples = [
        turn for turn in body["messages"] if turn["role"] == "assistant"
    ]
    assert examples
    for turn in examples:
        assert turn["content"] is None
        assert len(turn["tool_calls"]) == 1
        call = turn["tool_calls"][0]
        assert len(call["id"]) == 9
        assert call["id"].isascii() and call["id"].isalnum()
        assert call["type"] == "function"
        assert call["function"]["name"] == "movie_intent"
        assert any(
            following["role"] == "tool"
            and following["tool_call_id"] == call["id"]
            for following in body["messages"]
        )
    assert body["messages"][-1] == {
        "role": "user",
        "content": "90s comedy movies with dog in the title",
    }


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
    aws_client: httpx.Client,
    replies: Replies,
    comparison: str,
    lower: int | None,
    upper: int | None,
    lower_inclusive: bool,
    upper_inclusive: bool,
) -> None:
    replies.append(
        reply(
            intent_payload(
                year_filters=[{"comparison": comparison, "year": 2000}]
            )
        )
    )
    result = BedrockIntentParser(aws_client).parse("Comedies", ["Comedy"])
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
def test_malformed_intent_is_rejected(
    aws_client: httpx.Client, replies: Replies, payload: dict[str, object]
) -> None:
    replies.append(reply(payload))
    with pytest.raises(InvalidModelResponseError):
        BedrockIntentParser(aws_client).parse("Comedies", ["Comedy"])


@pytest.mark.parametrize("finish", ["length", "stop", "content_filter"])
def test_incomplete_result_is_rejected(
    aws_client: httpx.Client, replies: Replies, finish: str
) -> None:
    replies.append(reply(intent_payload(), finish))
    with pytest.raises(InvalidModelResponseError):
        BedrockIntentParser(aws_client).parse("Comedies", ["Comedy"])


@pytest.mark.parametrize(
    "message",
    [
        {"role": "assistant", "tool_calls": []},
        {"role": "assistant", "content": "not JSON"},
        {"role": "assistant", "refusal": "No", "tool_calls": []},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "type": "function",
                    "function": {"name": "run_sql", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "name": "movie_intent",
                        "arguments": "not JSON",
                    },
                }
            ],
        },
    ],
)
def test_invalid_envelope_is_rejected(
    aws_client: httpx.Client, replies: Replies, message: dict[str, object]
) -> None:
    replies.append(
        httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "tool_calls", "message": message}
                ]
            },
        )
    )
    with pytest.raises(InvalidModelResponseError):
        BedrockIntentParser(aws_client).parse("Comedies", ["Comedy"])


def test_oversized_context_makes_no_paid_request(
    aws_client: httpx.Client, requests: list[httpx.Request]
) -> None:
    with pytest.raises(ModelUnavailableError, match="context"):
        BedrockIntentParser(aws_client).parse("Comedies", ["x" * 60_000])
    assert requests == []


def test_signer_refreshes_temporary_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each signed request uses the current token, not a cached frozen key."""
    session = boto3.Session(
        aws_access_key_id="unused", aws_secret_access_key="unused"
    )
    credentials = iter(
        [
            Credentials("first-key", "first-secret", "first-token"),
            Credentials("second-key", "second-secret", "second-token"),
        ]
    )
    monkeypatch.setattr(session, "get_credentials", lambda: next(credentials))
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    with httpx.Client(
        auth=BedrockAuth(session, "us-west-2"),
        transport=httpx.MockTransport(handle),
    ) as client:
        client.post(
            "https://bedrock-mantle.us-west-2.api.aws/v1/test", json={}
        )
        client.post(
            "https://bedrock-mantle.us-west-2.api.aws/v1/test", json={}
        )
    for request, key, token in zip(
        sent,
        ["first-key", "second-key"],
        ["first-token", "second-token"],
        strict=True,
    ):
        assert f"Credential={key}/" in request.headers["authorization"]
        assert (
            "/us-west-2/bedrock-mantle/aws4_request"
            in (request.headers["authorization"])
        )
        assert request.headers["x-amz-security-token"] == token


def test_missing_credentials_never_send_a_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = boto3.Session(
        aws_access_key_id="testing", aws_secret_access_key="testing"
    )
    monkeypatch.setattr(session, "get_credentials", lambda: None)
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return reply(intent_payload())

    with httpx.Client(
        base_url="https://bedrock-mantle.us-west-2.api.aws/v1/",
        auth=BedrockAuth(session, "us-west-2"),
        transport=httpx.MockTransport(handle),
    ) as client:
        with pytest.raises(ModelUnavailableError) as caught:
            BedrockIntentParser(client).parse("Comedies", ["Comedy"])
    assert isinstance(caught.value.__cause__, NoCredentialsError)
    assert sent == []


@pytest.fixture
def hosted_api(
    aws_client: httpx.Client, monkeypatch: pytest.MonkeyPatch
) -> Generator[httpx.Client]:
    """Provider selection, HTTP handling, and real SQLite retrieval."""
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
            with TestClient(app) as client:
                yield client
    finally:
        app.dependency_overrides = previous
        engine.dispose()


def test_hosted_intent_drives_real_catalog_filters(
    hosted_api: httpx.Client,
    replies: Replies,
) -> None:
    replies.append(reply(intent_payload()))
    response = hosted_api.post(
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


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_hosted_failure_keeps_browsing_available(
    hosted_api: httpx.Client,
    replies: Replies,
    requests: list[httpx.Request],
    status: int,
) -> None:
    replies.append(httpx.Response(status, text="Private AWS account details"))
    response = hosted_api.post("/recommendations", json={"query": "Comedies"})
    assert response.status_code == 503
    assert response.json() == {
        "detail": "Hosted search is temporarily unavailable."
    }
    assert len(requests) == 1  # No paid retries or provider fallbacks.
    assert hosted_api.get("/movies").status_code == 200
    assert hosted_api.get("/movies/1").status_code == 200
    assert hosted_api.get("/health").json() == {"status": "ok"}


def test_hosted_timeout_is_sanitized(
    hosted_api: httpx.Client,
    replies: Replies,
) -> None:
    replies.append(httpx.ReadTimeout("private endpoint"))
    response = hosted_api.post("/recommendations", json={"query": "Comedies"})
    assert response.status_code == 503
    assert response.json() == {
        "detail": "Hosted search is temporarily unavailable."
    }


def test_invalid_hosted_result_returns_502_without_cards(
    hosted_api: httpx.Client,
    replies: Replies,
) -> None:
    replies.append(reply({}))
    response = hosted_api.post("/recommendations", json={"query": "Comedies"})
    assert response.status_code == 502
    assert "items" not in response.json()


def test_unsupported_constraints_are_not_silently_discarded(
    hosted_api: httpx.Client,
    replies: Replies,
) -> None:
    replies.append(reply(intent_payload(unsupported_constraints=["dogs"])))
    response = hosted_api.post(
        "/recommendations", json={"query": "90s comedy movies about dogs"}
    )
    assert response.status_code == 422
    assert "items" not in response.json()


def test_unknown_provider_does_not_fall_back(
    hosted_api: httpx.Client,
    monkeypatch: pytest.MonkeyPatch,
    requests: list[httpx.Request],
) -> None:
    monkeypatch.setattr(ollama, "INTENT_PROVIDER", "typo")
    assert (
        hosted_api.post(
            "/recommendations", json={"query": "Comedies"}
        ).status_code
        == 503
    )
    assert requests == []


@pytest.fixture(
    params=[
        CredentialRetrievalError(
            provider="private", error_msg="private details"
        ),
        ClientError(
            {
                "Error": {
                    "Code": "ExpiredTokenException",
                    "Message": "private details",
                }
            },
            "AssumeRole",
        ),
    ]
)
def credential_error(request: pytest.FixtureRequest) -> Exception:
    return cast(Exception, request.param)


def test_credentials_failure_during_signing_is_sanitized(
    hosted_api: httpx.Client,
    replies: Replies,
    credential_error: Exception,
) -> None:
    replies.append(credential_error)
    response = hosted_api.post("/recommendations", json={"query": "Comedies"})
    assert response.status_code == 503
    assert response.json() == {
        "detail": "Hosted search is temporarily unavailable."
    }
    assert hosted_api.get("/movies").status_code == 200


def test_credentials_initialization_failure_is_sanitized(
    hosted_api: httpx.Client,
    monkeypatch: pytest.MonkeyPatch,
    credential_error: Exception,
) -> None:
    def unavailable() -> httpx.Client:
        raise credential_error

    monkeypatch.setattr(ollama, "bedrock_client", unavailable)
    response = hosted_api.post("/recommendations", json={"query": "Comedies"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Hosted search is unavailable."}
    assert hosted_api.get("/movies").status_code == 200


def live_check_main(monkeypatch: pytest.MonkeyPatch) -> Callable[[], int]:
    monkeypatch.setattr(sys, "argv", ["check_bedrock.py", "--live"])
    root = Path(__file__).resolve().parents[1]
    return cast(
        Callable[[], int],
        runpy.run_path(str(root / "scripts/check_bedrock.py"))["main"],
    )


def test_live_check_initialization_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    credential_error: Exception,
) -> None:
    def unavailable() -> httpx.Client:
        raise credential_error

    monkeypatch.setattr(bedrock, "bedrock_client", unavailable)
    assert live_check_main(monkeypatch)() == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "blocked",
        "message": "AWS credentials unavailable.",
    }


def test_live_check_service_failure_stops_without_leaking_details(
    aws_client: httpx.Client,
    replies: Replies,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    replies.append(httpx.Response(401, text="private account details"))
    monkeypatch.setattr(bedrock, "bedrock_client", lambda: aws_client)
    assert live_check_main(monkeypatch)() == 1
    assert json.loads(capsys.readouterr().out)["message"] == (
        "Hosted search is temporarily unavailable."
    )


def test_live_check_reports_wrong_results_and_measured_usage(
    aws_client: httpx.Client,
    replies: Replies,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The decade query must not silently acquire an unrelated title filter.
    replies.append(reply(intent_payload()))
    monkeypatch.setattr(bedrock, "bedrock_client", lambda: aws_client)
    assert live_check_main(monkeypatch)() == 1
    record = json.loads(capsys.readouterr().out)
    assert record["status"] == "fail"
    assert record["usage"] == {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
    }
    assert record["model"] == "mistral.ministral-3-8b-instruct"
    assert record["latency_ms"] >= 0
    assert aws_client.event_hooks["response"] == []


def test_live_check_requires_explicit_opt_in() -> None:
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
