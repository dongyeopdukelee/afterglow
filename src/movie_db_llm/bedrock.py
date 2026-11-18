"""Bounded, IAM-authenticated Bedrock Mantle intent extraction."""

import json
from collections.abc import Generator
from functools import lru_cache
from hashlib import sha256
from typing import Annotated, Literal

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    InvalidRegionError,
    NoCredentialsError,
)
from botocore.utils import validate_region_name
from pydantic import BaseModel, Field, ValidationError

from movie_db_llm.config import AWS_REGION, BEDROCK_MODEL_ID
from movie_db_llm.intent_contract import ModelIntent, intent_messages
from movie_db_llm.recommendations import (
    InvalidModelResponseError,
    ModelUnavailableError,
    MovieIntent,
)

MAX_INPUT_BYTES = 60_000
MAX_OUTPUT_TOKENS = 1024
_UNAVAILABLE_MESSAGE = "Hosted search is temporarily unavailable."


def _mantle_endpoint(region: str) -> str:
    if not region or region != region.strip():
        raise InvalidRegionError(region_name=region)
    validate_region_name(region)
    return f"https://bedrock-mantle.{region}.api.aws/v1/"


def request_payload(
    query: str, genres: list[str], model: str
) -> dict[str, object]:
    """Build the shared development, evaluation, and hosted request contract."""
    return {
        "model": model,
        "messages": intent_messages(query, genres, tool_call=True),
        "max_completion_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0,
        "parallel_tool_calls": False,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "movie_intent",
                    "description": "Extract movie search intent.",
                    "parameters": ModelIntent.model_json_schema(),
                    "strict": True,
                },
            }
        ],
        "tool_choice": {
            "type": "function",
            "function": {"name": "movie_intent"},
        },
    }


def inference_configuration(
    genres: list[str], *, model: str = BEDROCK_MODEL_ID
) -> dict[str, object]:
    """Identify actual request settings without user text or credentials.

    Replacing only the user query with a fixed placeholder makes this identity
    comparable across requests. Prompt examples, schema, vocabulary, generation
    settings, model, and endpoint all participate in the fingerprint.
    """
    endpoint = _mantle_endpoint(AWS_REGION)
    payload = request_payload("<user query>", genres, model)
    fingerprint = sha256(
        json.dumps(
            {"endpoint": endpoint, "request": payload}, sort_keys=True
        ).encode()
    ).hexdigest()
    return {
        "provider": "bedrock",
        "model": model,
        "region": AWS_REGION,
        "endpoint": endpoint,
        "max_completion_tokens": payload["max_completion_tokens"],
        "temperature": payload["temperature"],
        "catalog_genres": len(genres),
        "fingerprint": fingerprint,
    }


class BedrockAuth(httpx.Auth):
    """Sign each request with fresh temporary credentials, including on Lambda."""

    requires_request_body = True

    def __init__(self, session: boto3.Session, region: str) -> None:
        self.session = session
        self.region = region

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response]:
        try:
            credentials = self.session.get_credentials()
            frozen = (
                credentials.get_frozen_credentials()
                if credentials is not None
                else None
            )
        except Exception as error:
            raise ModelUnavailableError(_UNAVAILABLE_MESSAGE) from error
        if frozen is None:
            raise NoCredentialsError()
        signed = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers=dict(request.headers),
        )
        SigV4Auth(frozen, "bedrock-mantle", self.region).add_auth(signed)
        request.headers.update(dict(signed.headers))
        yield request


class _Function(BaseModel):
    name: Literal["movie_intent"]
    arguments: str


class _ToolCall(BaseModel):
    type: Literal["function"]
    function: _Function


class _Message(BaseModel):
    role: Literal["assistant"]
    refusal: None = None
    tool_calls: Annotated[list[_ToolCall], Field(min_length=1, max_length=1)]


class _Choice(BaseModel):
    finish_reason: Literal["tool_calls"]
    message: _Message


class _Response(BaseModel):
    choices: Annotated[list[_Choice], Field(min_length=1, max_length=1)]


@lru_cache(maxsize=1)
def bedrock_client() -> httpx.Client:
    """Reuse connections, never API keys or a captured expiring credential."""
    return httpx.Client(
        base_url=_mantle_endpoint(AWS_REGION),
        auth=BedrockAuth(boto3.Session(), AWS_REGION),
        timeout=httpx.Timeout(20, connect=5),
        follow_redirects=False,
        trust_env=False,
    )


class BedrockIntentParser:
    """Apply the shared intent and year rules to Bedrock tool responses."""

    def __init__(
        self, client: httpx.Client, model: str = BEDROCK_MODEL_ID
    ) -> None:
        self.client = client
        self.model = model

    def parse(self, query: str, genres: list[str]) -> MovieIntent:
        """Force one structured result and reject truncated/malformed output."""
        payload = request_payload(query, genres, self.model)
        if len(json.dumps(payload["messages"]).encode()) > MAX_INPUT_BYTES:
            raise ModelUnavailableError("Search context exceeds its limit.")
        try:
            response = self.client.post(
                "chat/completions",
                json=payload,
            )
            response.raise_for_status()
        except (httpx.HTTPError, BotoCoreError, ClientError) as error:
            raise ModelUnavailableError(_UNAVAILABLE_MESSAGE) from error
        try:
            envelope = _Response.model_validate_json(response.content)
            arguments = (
                envelope.choices[0].message.tool_calls[0].function.arguments
            )
            return ModelIntent.model_validate_json(arguments).to_intent()
        except ValidationError as error:
            raise InvalidModelResponseError(
                "Model returned invalid or incomplete intent."
            ) from error
