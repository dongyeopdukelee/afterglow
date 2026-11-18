"""Exercise the development commands without contacting AWS."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT_ROOT / "scripts/dev-api.sh"


def launcher_environment(tmp_path: Path, release: str) -> dict[str, str]:
    uname = tmp_path / "uname"
    uname.write_text(f"#!/bin/sh\necho '{release}'\n")
    uname.chmod(0o755)
    return {"HOME": str(tmp_path), "PATH": f"{tmp_path}:{os.defpath}"}


@pytest.mark.parametrize(
    ("arguments", "release", "port"),
    [
        ([], "4.4.0-19041-Microsoft", 8000),
        (["--port", "8765"], "6.6.87-microsoft-standard-WSL2", 8765),
        (["--check"], "6.6.87-microsoft-standard-WSL2", None),
    ],
)
def test_launcher_keeps_project_auth_through_dotenv_loaders(
    tmp_path: Path, arguments: list[str], release: str, port: int | None
) -> None:
    """Observe real uv and Uvicorn after dotenv processing, before binding."""
    real_uv = shutil.which("uv")
    assert real_uv is not None
    probe = tmp_path / "observe_child.py"
    probe.write_text(
        dedent("""\
            import json, os, sys

            def report(target):
                env = {k: v for k, v in os.environ.items()
                       if k.startswith(('AWS_', 'BEDROCK_', 'INTENT_'))}
                print(json.dumps([os.getcwd(), target, env]))
                raise SystemExit(0)

            if sys.argv[1] == 'uvicorn':
                import uvicorn
                def observe_bind(config):
                    report([config.app, config.host, config.port, config.reload,
                            config.fd, config.uds])
                uvicorn.Config.bind_socket = observe_bind
                uvicorn.main(args=sys.argv[2:])
            else:
                report(sys.argv[1:])
        """)
    )
    wrapper = tmp_path / "uv"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "args = sys.argv[1:]\n"
        "i = next(i for i, arg in enumerate(args)\n"
        "         if arg in ('python', 'uvicorn'))\n"
        f"os.execv({real_uv!r}, [{real_uv!r}, *args[:i], "
        f"{sys.executable!r}, {str(probe)!r}, *args[i:]])\n"
    )
    wrapper.chmod(0o755)
    dotenv = tmp_path / "inherited.env"
    dotenv.write_text("AWS_ACCESS_KEY_ID=from-dotenv\n")
    aws_directory = tmp_path / ".config/afterglow/aws"
    provider = "ollama" if port is not None else "bedrock"
    environment = launcher_environment(tmp_path, release) | {
        "UV_NO_SYNC": "1",
        "UV_OFFLINE": "1",
        "UV_ENV_FILE": str(dotenv),
        "UVICORN_ENV_FILE": str(dotenv),
        "UVICORN_HOST": "0.0.0.0",
        "UVICORN_FD": "9",
        "UVICORN_UDS": str(tmp_path / "other.sock"),
        "AFTERGLOW_AWS_DIRECTORY": str(tmp_path / ".aws"),
        "AFTERGLOW_AWS_PROFILE": "afterglow-source",
        "AWS_CONFIG_FILE": str(tmp_path / ".aws/config"),
        "AWS_SHARED_CREDENTIALS_FILE": str(tmp_path / ".aws/credentials"),
        "AWS_LOGIN_CACHE_DIRECTORY": str(tmp_path / ".aws/login/cache"),
        "AWS_PROFILE": "unrelated",
        "AWS_DEFAULT_PROFILE": "unrelated-default",
        "AWS_ACCESS_KEY_ID": "inherited-test-key",
        "AWS_SECRET_ACCESS_KEY": "inherited-test-secret",
        "AWS_SESSION_TOKEN": "inherited-test-token",
        "AWS_SECURITY_TOKEN": "inherited-legacy-token",
        "AWS_WEB_IDENTITY_TOKEN_FILE": "/unrelated/token",
        "AWS_ROLE_ARN": "unrelated-role",
        "AWS_ROLE_SESSION_NAME": "unrelated-session",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://127.0.0.1/credentials",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/other",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN": "other-token",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE": "/other/token",
        "BEDROCK_MODEL_ID": "explicit-model",
        "AWS_REGION": "us-east-2",
        "INTENT_PROVIDER": provider,
    }
    result = subprocess.run(
        ["bash", str(LAUNCHER), *arguments],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    cwd, target, env = json.loads(result.stdout)
    assert cwd == str(PROJECT_ROOT)
    assert target == (
        ["movie_db_llm.main:app", "127.0.0.1", port, True, None, None]
        if port is not None
        else ["python", "scripts/check_bedrock.py", "--live"]
    )
    assert env == {
        "AWS_PROFILE": "afterglow",
        "AWS_CONFIG_FILE": str(aws_directory / "config"),
        "AWS_SHARED_CREDENTIALS_FILE": str(aws_directory / "credentials"),
        "AWS_LOGIN_CACHE_DIRECTORY": str(aws_directory / "login-cache"),
        "AWS_REGION": "us-east-2",
        "BEDROCK_MODEL_ID": "explicit-model",
        "INTENT_PROVIDER": provider,
    }


@pytest.mark.parametrize(
    ("release", "arguments", "returncode", "message"),
    [
        ("6.8.0-generic", [], 1, "requires WSL"),
        ("6.8.0-generic", ["--check"], 1, "requires WSL"),
        ("5.15-microsoft", ["--env-file", "other.env"], 2, "Usage"),
        ("5.15-microsoft", ["--host", "0.0.0.0"], 2, "Usage"),
        ("5.15-microsoft", ["--port", "8001", "--reload"], 2, "Usage"),
        ("5.15-microsoft", ["--check", "extra"], 2, "Usage"),
        ("5.15-microsoft", ["--print-config"], 2, "Usage"),
    ],
)
def test_launcher_rejects_unsupported_invocations_before_dispatch(
    tmp_path: Path,
    release: str,
    arguments: list[str],
    returncode: int,
    message: str,
) -> None:
    result = subprocess.run(
        ["bash", str(LAUNCHER), *arguments],
        env=launcher_environment(tmp_path, release),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == returncode
    assert message in result.stderr


def run_config_report(
    tmp_path: Path, **settings: str
) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AWS_", "BEDROCK_", "INTENT_", "OLLAMA_"))
    }
    environment.update(
        {
            "AWS_PROFILE": "does-not-exist",
            "AWS_CONFIG_FILE": str(tmp_path / "absent-config"),
            "AWS_SHARED_CREDENTIALS_FILE": str(
                tmp_path / "absent-credentials"
            ),
            "AWS_EC2_METADATA_DISABLED": "true",
            **settings,
        }
    )
    return subprocess.run(
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
        check=False,
    )


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
    settings = {"DATABASE_URL": f"sqlite:///{database}"}
    if provider:
        settings.update(INTENT_PROVIDER=provider, OLLAMA_MODEL="offline-test")
    result = run_config_report(tmp_path, **settings)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["provider"] == (provider or "bedrock")
    assert report["model"] == (
        "offline-test" if provider else "mistral.ministral-3-8b-instruct"
    )
    if not provider:
        assert report["catalog_genres"] == 2
        assert len(bytes.fromhex(report["fingerprint"])) == 32
    assert "does-not-exist" not in result.stdout
    assert database.read_bytes() == before


@pytest.mark.parametrize("uri", [False, True])
def test_config_report_leaves_missing_catalog_absent(
    tmp_path: Path, uri: bool
) -> None:
    database = tmp_path / "missing.sqlite3"
    url = (
        f"sqlite:///{database.as_uri()}?mode=rwc&uri=true"
        if uri
        else f"sqlite:///{database}"
    )
    result = run_config_report(tmp_path, DATABASE_URL=url)
    assert result.returncode != 0
    assert "Cannot read catalog" in result.stderr
    assert result.stdout == ""
    assert not database.exists()


def test_config_report_rejects_unknown_provider(tmp_path: Path) -> None:
    result = run_config_report(tmp_path, INTENT_PROVIDER="bedrok")
    assert result.returncode != 0
    assert "INTENT_PROVIDER" in result.stderr
    assert result.stdout == ""


def test_config_report_rejects_invalid_region(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE movie_genres (genre TEXT)")
    result = run_config_report(
        tmp_path,
        DATABASE_URL=f"sqlite:///{database}",
        AWS_REGION="us-west-2:443",
    )
    assert result.returncode != 0
    assert "Invalid AWS region" in result.stderr
    assert result.stdout == ""


def test_compose_publishes_api_only_on_loopback() -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose is needed to resolve the published port")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "--profile",
            "offline",
            "config",
            "--format",
            "json",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    services = json.loads(result.stdout)["services"]
    (port,) = services["api"]["ports"]
    assert (port["host_ip"], int(port["published"]), port["target"]) == (
        "127.0.0.1",
        8000,
        8000,
    )
    assert not services["ollama"].get("ports")
