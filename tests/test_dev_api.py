"""Execute the WSL launcher against a fake process boundary, never AWS."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (
            ["--port", "8765"],
            [
                "uvicorn",
                "movie_db_llm.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
                "--reload",
                "--port",
                "8765",
            ],
        ),
        (
            ["--check"],
            ["python", "scripts/check_bedrock.py", "--live", "--catalog"],
        ),
        (["--print-config"], ["movie-db-llm", "inference-config"]),
    ],
)
def test_launcher_isolates_aws_auth_without_changing_model_settings(
    tmp_path: Path, arguments: list[str], expected: list[str]
) -> None:
    """Inspect the executed child's environment, not shell source text.

    Known exported keys and container credentials must not override the named
    project profile. Model overrides must reach the shared application config.
    """
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "print(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd(), "
        "'env': {key: value for key, value in os.environ.items() "
        "if key.startswith(('AWS_', 'BEDROCK_', 'INTENT_'))}}))\n"
    )
    fake_uv.chmod(0o755)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AWS_", "AFTERGLOW_", "BEDROCK_", "INTENT_"))
    }
    aws_directory = tmp_path / "private aws"
    environment.update(
        {
            "PATH": f"{tmp_path}:{os.defpath}",
            "AFTERGLOW_AWS_DIRECTORY": str(aws_directory),
            "AWS_PROFILE": "unrelated",
            "AWS_DEFAULT_PROFILE": "unrelated-default",
            "AWS_ACCESS_KEY_ID": "inherited-test-key",
            "AWS_SECRET_ACCESS_KEY": "inherited-test-secret",
            "AWS_SESSION_TOKEN": "inherited-test-token",
            "AWS_WEB_IDENTITY_TOKEN_FILE": "/unrelated/token",
            "AWS_ROLE_ARN": "unrelated-role",
            "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://127.0.0.1/credentials",
            "BEDROCK_MODEL_ID": "explicit-model",
            "AWS_REGION": "us-east-2",
            "INTENT_PROVIDER": "ollama",
        }
    )
    result = subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts/dev-api.sh"), *arguments],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    child = json.loads(result.stdout)
    assert child["cwd"] == str(PROJECT_ROOT)
    assert child["args"] == ["run", "--locked", *expected]
    assert child["env"] == {
        "AWS_PROFILE": "afterglow",
        "AWS_CONFIG_FILE": str(aws_directory / "config"),
        "AWS_SHARED_CREDENTIALS_FILE": str(aws_directory / "credentials"),
        "AWS_LOGIN_CACHE_DIRECTORY": str(aws_directory / "login-cache"),
        "AWS_REGION": "us-east-2",
        "BEDROCK_MODEL_ID": "explicit-model",
        "INTENT_PROVIDER": "ollama",
    }


@pytest.mark.parametrize("provider", [None, "ollama"])
def test_configuration_command_reports_selected_route_without_aws(
    tmp_path: Path, provider: str | None
) -> None:
    """The executable report uses real catalog labels and never needs auth."""
    database = tmp_path / "catalog.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE movie_genres (movie_id INTEGER, genre TEXT)"
        )
        connection.executemany(
            "INSERT INTO movie_genres VALUES (?, ?)",
            [(1, "Thriller"), (2, "Comedy"), (3, "Comedy")],
        )
    before = database.read_bytes()
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AWS_", "BEDROCK_", "INTENT_"))
    }
    environment.update(
        {
            "DATABASE_URL": f"sqlite:///{database}",
            "AWS_PROFILE": "does-not-exist",
            "AWS_CONFIG_FILE": str(tmp_path / "absent-config"),
            "AWS_SHARED_CREDENTIALS_FILE": str(
                tmp_path / "absent-credentials"
            ),
            "AWS_EC2_METADATA_DISABLED": "true",
        }
    )
    if provider:
        environment["INTENT_PROVIDER"] = provider
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from movie_db_llm.cli import main; main()",
            "inference-config",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(result.stdout)
    assert report["provider"] == (provider or "bedrock")
    assert report["model"] == (
        "qwen3.5:2b" if provider else "mistral.ministral-3-8b-instruct"
    )
    if not provider:
        assert report["catalog_genres"] == 2
        assert len(bytes.fromhex(report["fingerprint"])) == 32
    assert "does-not-exist" not in result.stdout
    assert database.read_bytes() == before
