"""Bounded Bedrock intent extraction; model output never becomes SQL."""

import json
from functools import lru_cache
from typing import TYPE_CHECKING, Annotated, Literal, cast

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel, Field, ValidationError

from movie_db_llm.config import AWS_REGION, BEDROCK_MODEL_ID
from movie_db_llm.intent_contract import ModelIntent, intent_messages
from movie_db_llm.recommendations import (
    InvalidModelResponseError,
    ModelUnavailableError,
    MovieIntent,
)

if TYPE_CHECKING:
    from mypy_boto3_bedrock_runtime import BedrockRuntimeClient
    from mypy_boto3_bedrock_runtime.type_defs import MessageTypeDef

MAX_INPUT_BYTES = 60_000
MAX_OUTPUT_TOKENS = 512


class _ToolUse(BaseModel):
    name: Literal["movie_intent"]
    input: ModelIntent


class _ToolBlock(BaseModel):
    toolUse: _ToolUse


class _Message(BaseModel):
    role: Literal["assistant"]
    content: Annotated[list[_ToolBlock], Field(min_length=1, max_length=1)]


class _Output(BaseModel):
    message: _Message


class _Response(BaseModel):
    stopReason: Literal["tool_use"]
    output: _Output


@lru_cache(maxsize=1)
def bedrock_client() -> BedrockRuntimeClient:
    """Reuse one connection pool and resolve credentials through AWS roles."""
    return cast(
        "BedrockRuntimeClient",
        boto3.client(
            "bedrock-runtime",
            region_name=AWS_REGION,
            config=Config(
                connect_timeout=5,
                read_timeout=30,
                retries={"total_max_attempts": 1, "mode": "adaptive"},
            ),
        ),
    )


class BedrockIntentParser:
    """Use the same validated intent and year semantics as local inference."""

    def __init__(
        self, client: BedrockRuntimeClient, model: str = BEDROCK_MODEL_ID
    ) -> None:
        self.client = client
        self.model = model

    def parse(self, query: str, genres: list[str]) -> MovieIntent:
        """Force one structured result and reject truncated/malformed output."""
        turns = intent_messages(query, genres)
        if len(json.dumps(turns).encode("utf-8")) > MAX_INPUT_BYTES:
            raise ModelUnavailableError("Search context exceeds its limit.")
        messages: list[MessageTypeDef] = [
            {
                "role": "user" if turn["role"] == "user" else "assistant",
                "content": [{"text": turn["content"]}],
            }
            for turn in turns[1:]
        ]
        try:
            response = self.client.converse(
                modelId=self.model,
                system=[{"text": turns[0]["content"]}],
                messages=messages,
                inferenceConfig={
                    "maxTokens": MAX_OUTPUT_TOKENS,
                    "temperature": 0,
                },
                toolConfig={
                    "tools": [
                        {
                            "toolSpec": {
                                "name": "movie_intent",
                                "description": "Extract movie search intent.",
                                "inputSchema": {
                                    "json": ModelIntent.model_json_schema()
                                },
                            }
                        }
                    ],
                    "toolChoice": {"tool": {"name": "movie_intent"}},
                },
            )
        except (BotoCoreError, ClientError) as error:
            raise ModelUnavailableError(
                "Hosted search is temporarily unavailable."
            ) from error
        try:
            envelope = _Response.model_validate(response)
            return envelope.output.message.content[0].toolUse.input.to_intent()
        except ValidationError as error:
            raise InvalidModelResponseError(
                "Model returned invalid or incomplete intent."
            ) from error
