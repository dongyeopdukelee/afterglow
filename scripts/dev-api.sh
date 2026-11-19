#!/usr/bin/env bash
# Run the normal backend with the shared inference configuration and WSL auth.
set -euo pipefail

case "$(uname -r)" in
  *[Mm]icrosoft*) ;;
  *)
    echo "The development launcher requires WSL." >&2
    exit 1
    ;;
esac

CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
afterglow_aws_directory="$HOME/.config/afterglow/aws"

# A profile selected through AWS_PROFILE alone can lose to inherited keys.
unset AWS_DEFAULT_PROFILE AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
unset AWS_SESSION_TOKEN AWS_SECURITY_TOKEN AWS_WEB_IDENTITY_TOKEN_FILE
unset AWS_ROLE_ARN AWS_ROLE_SESSION_NAME
unset AWS_CONTAINER_CREDENTIALS_RELATIVE_URI AWS_CONTAINER_CREDENTIALS_FULL_URI
unset AWS_CONTAINER_AUTHORIZATION_TOKEN AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE
unset UVICORN_ENV_FILE UVICORN_FD UVICORN_UDS
export UV_NO_ENV_FILE=1
export AWS_PROFILE=afterglow-dev
export AWS_CONFIG_FILE="$afterglow_aws_directory/config"
export AWS_SHARED_CREDENTIALS_FILE="$afterglow_aws_directory/credentials"
export AWS_LOGIN_CACHE_DIRECTORY="$afterglow_aws_directory/login-cache"

port=8000
if [[ $# -eq 1 && $1 == --check ]]; then
  exec uv run --locked python scripts/check_bedrock.py --live
elif [[ $# -eq 2 && $1 == --port ]]; then
  port=$2
elif [[ $# -ne 0 ]]; then
  echo "Usage: scripts/dev-api.sh [--port PORT | --check]" >&2
  exit 2
fi

exec uv run --locked uvicorn movie_db_llm.main:app \
  --host 127.0.0.1 --port "$port" --reload
