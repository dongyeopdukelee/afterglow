"""Local Ollama adapter and configured inference-provider dependency."""

from collections.abc import Generator
from typing import Literal

import httpx
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from movie_db_llm.bedrock import BedrockIntentParser, bedrock_client
from movie_db_llm.config import (
    INTENT_PROVIDER,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    OLLAMA_TIMEOUT_SECONDS,
)
from movie_db_llm.intent_contract import ModelIntent, intent_messages
from movie_db_llm.recommendations import (
    IntentParser,
    InvalidModelResponseError,
    ModelUnavailableError,
    MovieIntent,
)


class _OllamaMessage(BaseModel):
    content: str


class _OllamaResponse(BaseModel):
    message: _OllamaMessage
    done: Literal[True]
    done_reason: Literal["stop"]


class OllamaIntentParser:
    """Parse intent locally without tools or SQL generation."""

    def __init__(
        self, client: httpx.Client, model: str = OLLAMA_MODEL
    ) -> None:
        self.client = client
        self.model = model

    def parse(self, query: str, genres: list[str]) -> MovieIntent:
        """Request schema JSON and independently validate its contents."""
        try:
            response = self.client.post(
                "/api/chat",
                json={
                    "model": self.model,
                    "messages": intent_messages(query, genres),
                    "format": ModelIntent.model_json_schema(),
                    "stream": False,
                    "think": False,
                    "options": {
                        "temperature": 0,
                        "presence_penalty": 0,
                        "num_predict": 512,
                        "num_ctx": 8192,
                    },
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ModelUnavailableError(
                "Local model unavailable. Start Ollama and pull "
                "the configured model."
            ) from error
        try:
            envelope = _OllamaResponse.model_validate_json(response.content)
            return ModelIntent.model_validate_json(
                envelope.message.content
            ).to_intent()
        except ValidationError as error:
            raise InvalidModelResponseError(
                "Model returned invalid or incomplete intent."
            ) from error


def get_intent_parser() -> Generator[IntentParser]:
    """Use the selected provider; never silently fall back between providers."""
    if INTENT_PROVIDER == "bedrock":
        try:
            parser = BedrockIntentParser(bedrock_client())
        except (BotoCoreError, ClientError) as error:
            raise HTTPException(
                status_code=503, detail="Hosted search is unavailable."
            ) from error
        yield parser
        return
    if INTENT_PROVIDER != "ollama":
        raise HTTPException(
            status_code=503, detail="Search provider is not configured."
        )
    with httpx.Client(
        base_url=OLLAMA_BASE_URL,
        timeout=httpx.Timeout(OLLAMA_TIMEOUT_SECONDS, connect=5),
        trust_env=False,
    ) as client:
        yield OllamaIntentParser(client)
