"""Mantle protocol and hosted API behavior, without credentials or paid AI."""

import json
import runpy
import subprocess
import sys
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import boto3
import httpx
import pytest
from botocore.credentials import Credentials
from botocore.exceptions import (
    ClientError,
    CredentialRetrievalError,
    InvalidRegionError,
    NoCredentialsError,
)
from fastapi.testclient import TestClient
from sqlalchemy import URL, Connection, create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from movie_db_llm import bedrock, config, ollama
from movie_db_llm.bedrock import BedrockAuth, BedrockIntentParser
from movie_db_llm.database import configure_sqlite_connection, get_session
from movie_db_llm.intent_contract import intent_messages
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
    result = BedrockIntentParser(aws_client, "test-model").parse(
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
    assert body["model"] == "test-model"
    assert body["max_completion_tokens"] == 1024
    assert "reasoning_effort" not in body
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
    for index, turn in enumerate(body["messages"]):
        if turn["role"] != "assistant":
            continue
        assert turn["content"] is None
        assert len(turn["tool_calls"]) == 1
        call = turn["tool_calls"][0]
        assert len(call["id"]) == 9
        assert call["id"].isascii() and call["id"].isalnum()
        assert call["type"] == "function"
        assert call["function"]["name"] == "movie_intent"
        following = body["messages"][index + 1]
        assert following["role"] == "tool"
        assert following["tool_call_id"] == call["id"]
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


@pytest.mark.parametrize("invalid_field", ["refusal", "function"])
def test_independent_response_guards(
    aws_client: httpx.Client, replies: Replies, invalid_field: str
) -> None:
    """A refusal or wrong function alone invalidates an otherwise valid call."""
    payload = reply(intent_payload()).json()
    message = payload["choices"][0]["message"]
    if invalid_field == "refusal":
        message["refusal"] = "No"
    else:
        message["tool_calls"][0]["function"]["name"] = "run_sql"
    replies.append(httpx.Response(200, json=payload))
    with pytest.raises(InvalidModelResponseError):
        BedrockIntentParser(aws_client).parse("Comedies", ["Comedy"])


@pytest.mark.parametrize("tool_call", [False, True])
@pytest.mark.parametrize("genres", [[], ["Comedy"], ["Mystery", "Thriller"]])
def test_emitted_examples_only_select_available_genres(
    tool_call: bool, genres: list[str]
) -> None:
    """The generated model interface must obey its own catalog allowlist."""
    examples: list[dict[str, object]] = []
    for turn in intent_messages("Comedies", genres, tool_call=tool_call):
        if turn["role"] != "assistant":
            continue
        if tool_call:
            calls = cast(list[dict[str, object]], turn["tool_calls"])
            function = cast(dict[str, object], calls[0]["function"])
            raw = function["arguments"]
        else:
            raw = turn["content"]
        assert isinstance(raw, str)
        intent = cast(dict[str, object], json.loads(raw))
        selected = cast(list[str], intent["genres"])
        assert set(selected) <= set(genres)
        examples.append(intent)
    assert examples
    assert any(item["unsupported_constraints"] for item in examples)
    assert any(item["title_keywords"] == ["A_B%/"] for item in examples)
    if set(genres) == {"Mystery", "Thriller"}:
        assert any(
            item["genres"] == ["Mystery", "Thriller"] for item in examples
        )


def test_oversized_context_makes_no_paid_request(
    aws_client: httpx.Client, requests: list[httpx.Request]
) -> None:
    with pytest.raises(ModelUnavailableError, match="context"):
        BedrockIntentParser(aws_client).parse("Comedies", ["x" * 60_000])
    assert requests == []


def test_signer_refreshes_temporary_credentials() -> None:
    """Each signed request uses the current token, not a cached frozen key."""
    session = Mock(spec=boto3.Session)
    session.get_credentials.side_effect = [
        Credentials("first-key", "first-secret", "first-token"),
        Credentials("second-key", "second-secret", "second-token"),
    ]
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    with httpx.Client(
        auth=BedrockAuth(cast(boto3.Session, session), "us-west-2"),
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


def test_missing_credentials_never_send_a_request() -> None:
    session = Mock(spec=boto3.Session)
    session.get_credentials.return_value = None
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return reply(intent_payload())

    with httpx.Client(
        base_url="https://bedrock-mantle.us-west-2.api.aws/v1/",
        auth=BedrockAuth(cast(boto3.Session, session), "us-west-2"),
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


def run_live_check(monkeypatch: pytest.MonkeyPatch, *arguments: str) -> int:
    monkeypatch.setattr(
        sys, "argv", ["check_bedrock.py", "--live", *arguments]
    )
    root = Path(__file__).resolve().parents[1]
    main = cast(
        Callable[[], int],
        runpy.run_path(str(root / "scripts/check_bedrock.py"))["main"],
    )
    return main()


@pytest.mark.parametrize("stage", ["lookup", "refresh"])
@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("private credential helper path"),
        json.JSONDecodeError("private helper output", "malformed JSON", 0),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "private helper output"),
        AttributeError("private credential result is not an object"),
        ValueError("private expiration cannot be parsed"),
        TypeError("private expiration has the wrong type"),
        RuntimeError("private refreshed credentials have expired"),
    ],
)
def test_credential_provider_failure_is_sanitized(
    hosted_api: httpx.Client,
    aws_client: httpx.Client,
    requests: list[httpx.Request],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stage: str,
    error: Exception,
) -> None:
    session = Mock(spec=boto3.Session)
    if stage == "lookup":
        session.get_credentials.side_effect = error
    else:
        credentials = Mock(spec=Credentials)
        credentials.get_frozen_credentials.side_effect = error
        session.get_credentials.return_value = credentials
    aws_client.auth = BedrockAuth(cast(boto3.Session, session), "us-west-2")
    monkeypatch.setattr(bedrock, "bedrock_client", lambda: aws_client)

    with pytest.raises(ModelUnavailableError) as caught:
        BedrockIntentParser(aws_client).parse("Comedies", ["Comedy"])
    assert caught.value.__cause__ is error

    message = "Hosted search is temporarily unavailable."
    response = hosted_api.post("/recommendations", json={"query": "Comedies"})
    assert response.status_code == 503
    assert response.json() == {"detail": message}
    assert hosted_api.get("/movies").status_code == 200
    assert run_live_check(monkeypatch) == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {
        "query": "Comedies from the 1990s",
        "status": "blocked",
        "message": message,
    }
    assert output.err == ""
    assert requests == []


def test_unexpected_transport_error_propagates(
    aws_client: httpx.Client, replies: Replies
) -> None:
    error = RuntimeError("transport programming error")
    replies.append(error)
    with pytest.raises(RuntimeError) as caught:
        BedrockIntentParser(aws_client).parse("Comedies", ["Comedy"])
    assert caught.value is error


@pytest.mark.parametrize("region", ["us-west-2:443", "", "us-west-2\n"])
def test_invalid_region_blocks_hosted_setup(
    hosted_api: httpx.Client,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    region: str,
) -> None:
    monkeypatch.setattr(bedrock, "AWS_REGION", region)
    monkeypatch.setattr(
        boto3, "Session", lambda: pytest.fail("Invalid region reached AWS")
    )
    bedrock.bedrock_client.cache_clear()
    monkeypatch.setattr(ollama, "bedrock_client", bedrock.bedrock_client)
    with pytest.raises(InvalidRegionError):
        bedrock.bedrock_client()

    response = hosted_api.post("/recommendations", json={"query": "Comedies"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Hosted search is unavailable."}
    assert run_live_check(monkeypatch) == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "blocked",
        "message": "Check the AWS region and credentials.",
    }


def test_live_check_initialization_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    credential_error: Exception,
) -> None:
    def unavailable() -> httpx.Client:
        raise credential_error

    monkeypatch.setattr(bedrock, "bedrock_client", unavailable)
    assert run_live_check(monkeypatch) == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "blocked",
        "message": "Check the AWS region and credentials.",
    }


def test_live_check_service_failure_stops_without_leaking_details(
    aws_client: httpx.Client,
    replies: Replies,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    replies.append(httpx.Response(401, text="private account details"))
    monkeypatch.setattr(bedrock, "bedrock_client", lambda: aws_client)
    assert run_live_check(monkeypatch) == 1
    assert json.loads(capsys.readouterr().out)["message"] == (
        "Hosted search is temporarily unavailable."
    )


def assert_evaluator_request(
    requests: list[httpx.Request], model: str, genres: list[str]
) -> None:
    (request,) = requests
    body = json.loads(request.content)
    assert body["model"] == model
    system = body["messages"][0]
    assert system["role"] == "system"
    assert json.loads(system["content"].splitlines()[-1]) == genres


def test_live_check_reports_wrong_results_and_measured_usage(
    aws_client: httpx.Client,
    replies: Replies,
    requests: list[httpx.Request],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The decade query must not silently acquire an unrelated title filter.
    replies.append(reply(intent_payload()))
    monkeypatch.setattr(config, "BEDROCK_MODEL_ID", "evaluation-test-model")
    monkeypatch.setattr(config, "DATABASE_URL", "invalid-database-url")
    monkeypatch.setattr(bedrock, "bedrock_client", lambda: aws_client)
    assert run_live_check(monkeypatch) == 1
    assert_evaluator_request(
        requests,
        "evaluation-test-model",
        ["Comedy", "Drama", "Horror", "Thriller"],
    )
    record = json.loads(capsys.readouterr().out)
    assert record["status"] == "fail"
    assert record["usage"] == {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
    }
    assert record["model"] == "evaluation-test-model"
    assert record["catalog_genres"] == 4
    assert record["latency_ms"] >= 0
    assert aws_client.event_hooks["response"] == []


@pytest.mark.parametrize("uri", [False, True])
@pytest.mark.parametrize("catalog_state", ["missing", "schema-less", "empty"])
def test_live_check_blocks_invalid_catalog_before_aws(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    catalog_state: str,
    uri: bool,
) -> None:
    """Bad catalog setup must never create a file or start paid inference."""
    database = tmp_path / "catalog.sqlite3"
    if catalog_state != "missing":
        engine = create_engine(f"sqlite:///{database}")
        with engine.begin() as connection:
            if catalog_state == "empty":
                Base.metadata.create_all(connection)
        engine.dispose()
    before = database.read_bytes() if database.exists() else None
    url = (
        f"sqlite:///{database.as_uri()}?mode=rwc&uri=true"
        if uri
        else f"sqlite:///{database}"
    )
    monkeypatch.setattr(config, "DATABASE_URL", url)

    def forbidden() -> httpx.Client:
        pytest.fail("AWS client initialized before catalog validation")

    monkeypatch.setattr(bedrock, "bedrock_client", forbidden)
    assert run_live_check(monkeypatch, "--catalog") == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "blocked",
        "message": "Empty catalog."
        if catalog_state == "empty"
        else "Cannot read catalog.",
    }
    assert (database.read_bytes() if database.exists() else None) == before


@pytest.mark.parametrize("uri", [False, True])
def test_evaluator_catalog_connection_rejects_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, uri: bool
) -> None:
    """SQLite enforces read-only access even if a source URI requests writes."""
    database = tmp_path / (
        "catalog ?#%.sqlite3" if uri else "catalog #%.sqlite3"
    )
    engine = create_engine(URL.create("sqlite", database=str(database)))
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE example (id INTEGER)"))
        connection.execute(text("INSERT INTO example VALUES (1)"))
    engine.dispose()
    before = database.read_bytes()
    root = Path(__file__).resolve().parents[1]
    open_catalog = cast(
        Callable[[str], AbstractContextManager[Connection]],
        runpy.run_path(str(root / "scripts/check_bedrock.py"))["open_catalog"],
    )
    url = (
        f"sqlite:///{database.as_uri()}?mode=rwc&uri=true"
        if uri
        else f"sqlite:///{database}"
    )
    # The callable comes from the executable evaluation script, not a mock.
    with open_catalog(url) as connection:
        assert connection.scalar(text("SELECT id FROM example")) == 1
        with pytest.raises(OperationalError):
            connection.execute(text("INSERT INTO example VALUES (2)"))
    assert database.read_bytes() == before


def create_evaluation_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, genres: list[str]
) -> Path:
    database = tmp_path / "catalog.sqlite3"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            Movie(
                title="Example",
                synopsis="A story",
                genre_assignments=[MovieGenre(genre=genre)],
            )
            for genre in genres
        )
        session.commit()
    engine.dispose()
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{database}")
    return database


@pytest.mark.parametrize(
    "genres",
    [
        ["Mystery", "Horror", "Thriller"],
        ["Comedy", "Mystery", "Thriller"],
        ["Comedy", "Horror", "Mystery"],
    ],
)
def test_live_check_blocks_catalog_missing_a_benchmark_genre(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    genres: list[str],
) -> None:
    database = create_evaluation_catalog(tmp_path, monkeypatch, genres)
    before = database.read_bytes()
    monkeypatch.setattr(
        bedrock,
        "bedrock_client",
        lambda: pytest.fail("Invalid catalog reached AWS"),
    )
    assert run_live_check(monkeypatch, "--catalog") == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "blocked",
        "message": "Benchmark requires Comedy, Horror and Thriller.",
    }
    assert database.read_bytes() == before


def test_live_check_uses_catalog_vocabulary(
    aws_client: httpx.Client,
    replies: Replies,
    requests: list[httpx.Request],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    database = create_evaluation_catalog(
        tmp_path,
        monkeypatch,
        ["Western", "Horror", "Thriller", "Comedy", "Mystery", "Horror"],
    )
    before = database.read_bytes()
    monkeypatch.setattr(config, "BEDROCK_MODEL_ID", "catalog-test-model")
    monkeypatch.setattr(bedrock, "bedrock_client", lambda: aws_client)
    replies.append(reply(intent_payload()))
    assert run_live_check(monkeypatch, "--catalog") == 1
    assert_evaluator_request(
        requests,
        "catalog-test-model",
        ["Comedy", "Horror", "Mystery", "Thriller", "Western"],
    )
    record = json.loads(capsys.readouterr().out)
    assert record["status"] == "fail"
    assert record["model"] == "catalog-test-model"
    assert record["catalog_genres"] == 5
    assert database.read_bytes() == before


@pytest.mark.parametrize(
    ("plot_genres", "or_filters", "use_catalog", "expected_statuses"),
    [
        (["Dog movies"], {}, False, ["pass"] * 7 + ["fail"]),
        (
            ["Comedy"],
            {"genres": ["Horror", "Drama"]},
            True,
            ["pass"] * 8 + ["fail"],
        ),
        (
            ["Comedy"],
            {"unsupported_constraints": []},
            False,
            ["pass"] * 8 + ["fail"],
        ),
        (["Comedy"], {"genres": []}, False, ["pass"] * 10),
    ],
    ids=[
        "invented-plot-genre",
        "or-genre-absent-from-catalog",
        "missing-reason",
        "valid",
    ],
)
def test_live_check_scores_rejections_against_supplied_genres(
    aws_client: httpx.Client,
    replies: Replies,
    requests: list[httpx.Request],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    plot_genres: list[str],
    or_filters: dict[str, object],
    use_catalog: bool,
    expected_statuses: list[str],
) -> None:
    empty = intent_payload(genres=[], title_keywords=[], year_filters=[])

    def thriller(comparison: str) -> dict[str, object]:
        return empty | {
            "genres": ["Thriller"],
            "year_filters": [{"comparison": comparison, "year": 2000}],
        }

    payloads = [
        intent_payload(title_keywords=[]),
        thriller("after"),
        thriller("since"),
        empty | {"title_keywords": ["A_B%/"]},
        thriller("before"),
        thriller("until"),
        thriller("in"),
        intent_payload(
            genres=plot_genres,
            title_keywords=[],
            unsupported_constraints=["plot about dogs"],
        ),
        empty
        | {
            "genres": ["Horror", "Comedy"],
            "unsupported_constraints": ["either genre was requested"],
        }
        | or_filters,
        empty,
    ]
    arguments: list[str] = []
    if use_catalog:
        create_evaluation_catalog(
            tmp_path, monkeypatch, ["Comedy", "Horror", "Thriller"]
        )
        arguments.append("--catalog")
    replies.extend(
        reply(payload) for payload in payloads[: len(expected_statuses)]
    )
    monkeypatch.setattr(bedrock, "bedrock_client", lambda: aws_client)

    assert run_live_check(monkeypatch, *arguments) == (
        1 if "fail" in expected_statuses else 0
    )
    records = [
        json.loads(line) for line in capsys.readouterr().out.splitlines()
    ]
    assert [record["status"] for record in records] == expected_statuses
    assert len(requests) == len(expected_statuses)
    for record in records[7:9]:
        assert record["expected"] == {
            "genres": "must be a subset of the catalog genres",
            "unsupported_constraints": "must be nonempty",
        }


@pytest.mark.parametrize("arguments", [[], ["--catalog"]])
def test_live_check_requires_explicit_opt_in(arguments: list[str]) -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts/check_bedrock.py"), *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "--live" in result.stderr
