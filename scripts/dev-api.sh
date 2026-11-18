#!/usr/bin/env bash
# Run the normal backend with the shared inference configuration and WSL auth.
set -euo pipefail

afterglow_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
afterglow_aws_directory="${AFTERGLOW_AWS_DIRECTORY:-$HOME/.config/afterglow/aws}"
cd -- "$afterglow_root"

# A profile selected through AWS_PROFILE alone can lose to inherited keys.
unset AWS_DEFAULT_PROFILE AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
unset AWS_SESSION_TOKEN AWS_SECURITY_TOKEN AWS_WEB_IDENTITY_TOKEN_FILE
unset AWS_ROLE_ARN AWS_ROLE_SESSION_NAME
unset AWS_CONTAINER_CREDENTIALS_RELATIVE_URI AWS_CONTAINER_CREDENTIALS_FULL_URI
unset AWS_CONTAINER_AUTHORIZATION_TOKEN AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE
export AWS_PROFILE="${AFTERGLOW_AWS_PROFILE:-afterglow}"
export AWS_CONFIG_FILE="$afterglow_aws_directory/config"
export AWS_SHARED_CREDENTIALS_FILE="$afterglow_aws_directory/credentials"
export AWS_LOGIN_CACHE_DIRECTORY="$afterglow_aws_directory/login-cache"

case "${1:-}" in
  --check)
    shift
    exec uv run --locked python scripts/check_bedrock.py --live --catalog "$@"
    ;;
  --print-config)
    shift
    exec uv run --locked movie-db-llm inference-config "$@"
    ;;
esac

exec uv run --locked uvicorn movie_db_llm.main:app \
  --host 127.0.0.1 --port 8000 --reload "$@"
