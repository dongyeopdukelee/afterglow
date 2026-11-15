"""Executable checks for application defaults and explicit overrides."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("region", "expected_region"),
    [(None, "us-west-2"), ("us-east-2", "us-east-2")],
)
def test_aws_region_selects_client_endpoint(
    region: str | None, expected_region: str, tmp_path: Path
) -> None:
    """New clients use Oregon by default and honor AWS_REGION overrides."""
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("AWS_")
    }
    environment.update(
        {
            "AWS_ACCESS_KEY_ID": "testing",
            "AWS_SECRET_ACCESS_KEY": "testing",
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_CONFIG_FILE": str(tmp_path / "absent-config"),
            "AWS_SHARED_CREDENTIALS_FILE": str(
                tmp_path / "absent-credentials"
            ),
        }
    )
    if region is not None:
        environment["AWS_REGION"] = region

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; "
            "from movie_db_llm.bedrock import bedrock_client; "
            "client = bedrock_client(); "
            "print(json.dumps({'region': client.meta.region_name, "
            "'endpoint': client.meta.endpoint_url})); client.close()",
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "region": expected_region,
        "endpoint": f"https://bedrock-runtime.{expected_region}.amazonaws.com",
    }
