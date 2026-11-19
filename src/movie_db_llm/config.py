"""Application configuration shared by runtime and migration tooling."""

from os import environ

DATABASE_URL = environ.get("DATABASE_URL", "sqlite:///./movie_db.sqlite3")
OLLAMA_BASE_URL = environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = environ.get("OLLAMA_MODEL", "qwen3.5:2b")
OLLAMA_TIMEOUT_SECONDS = float(environ.get("OLLAMA_TIMEOUT_SECONDS", "120"))
INTENT_PROVIDER = environ.get("INTENT_PROVIDER", "bedrock")
AWS_REGION = environ.get("AWS_REGION", "us-west-2")
BEDROCK_MODEL_ID = environ.get(
    "BEDROCK_MODEL_ID", "mistral.ministral-3-8b-instruct"
)
POSTER_BUCKET = environ.get("POSTER_BUCKET", "")
POSTER_REGION = environ.get("POSTER_REGION", "us-west-2")
