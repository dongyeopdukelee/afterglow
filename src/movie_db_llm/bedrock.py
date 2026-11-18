"""Bounded, IAM-authenticated Bedrock Mantle intent extraction."""

import json
from collections.abc import Generator
from functools import lru_cache
from typing import Annotated, Literal

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
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


class BedrockAuth(httpx.Auth):
    """Sign each request with fresh temporary credentials, including on Lambda."""

    requires_request_body = True

    def __init__(self, session: boto3.Session, region: str) -> None:
        self.session = session
        self.region = region

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response]:
        credentials = self.session.get_credentials()
        if credentials is None:
            raise NoCredentialsError()
        signed = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers=dict(request.headers),
        )
        SigV4Auth(
            credentials.get_frozen_credentials(), "bedrock-mantle", self.region
        ).add_auth(signed)
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
        base_url=f"https://bedrock-mantle.{AWS_REGION}.api.aws/v1/",
        auth=BedrockAuth(boto3.Session(), AWS_REGION),
        timeout=httpx.Timeout(20, connect=5),
        transport=httpx.HTTPTransport(retries=0),
        follow_redirects=False,
        trust_env=False,
    )


class BedrockIntentParser:
    """Use the same validated intent and year semantics as local inference."""

    def __init__(
        self, client: httpx.Client, model: str = BEDROCK_MODEL_ID
    ) -> None:
        self.client = client
        self.model = model

    def parse(self, query: str, genres: list[str]) -> MovieIntent:
        """Force one structured result and reject truncated/malformed output."""
        messages = intent_messages(query, genres, tool_call=True)
        if len(json.dumps(messages).encode("utf-8")) > MAX_INPUT_BYTES:
            raise ModelUnavailableError("Search context exceeds its limit.")
        try:
            response = self.client.post(
                "chat/completions",
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_completion_tokens": MAX_OUTPUT_TOKENS,
                    "reasoning_effort": "low",
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
                },
            )
            response.raise_for_status()
        except (httpx.HTTPError, BotoCoreError, ClientError) as error:
            raise ModelUnavailableError(
                "Hosted search is temporarily unavailable."
            ) from error
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
