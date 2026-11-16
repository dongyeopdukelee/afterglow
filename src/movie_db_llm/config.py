"""Application configuration shared by runtime and migration tooling."""

from os import environ

DATABASE_URL = environ.get("DATABASE_URL", "sqlite:///./movie_db.sqlite3")
OLLAMA_BASE_URL = environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = environ.get("OLLAMA_MODEL", "qwen3.5:2b")
OLLAMA_TIMEOUT_SECONDS = float(environ.get("OLLAMA_TIMEOUT_SECONDS", "120"))
INTENT_PROVIDER = environ.get("INTENT_PROVIDER", "ollama")
AWS_REGION = environ.get("AWS_REGION", "us-west-2")
BEDROCK_MODEL_ID = environ.get("BEDROCK_MODEL_ID", "openai.gpt-oss-20b")
