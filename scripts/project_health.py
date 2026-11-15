"""Run deterministic repository checks and publish report-only Git commits."""

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPORT_DIRECTORY = "reports/health"
CHECKS = {
    "Tests": [
        "uv",
        "run",
        "--locked",
        "pytest",
        "--junitxml=/tmp/health-tests.xml",
    ],
    "Lint": ["uv", "run", "--locked", "ruff", "check", "."],
    "Formatting": ["uv", "run", "--locked", "ruff", "format", "--check", "."],
    "Types": ["uv", "run", "--locked", "pyright"],
    "Build": ["uv", "build", "--out-dir", "/tmp/health-dist"],
}


def run_check(command: list[str]) -> str:
    """Stream verbose evidence to Actions; retain only the honest verdict."""
    try:
        result = subprocess.run(command, check=False, timeout=300)
    except OSError, subprocess.TimeoutExpired:
        return "could not run"
    return "passed" if result.returncode == 0 else "failed"


def report_day(now: datetime) -> str:
    return now.astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()


def render(
    day: str,
    source: str,
    results: dict[str, str],
    measurements: dict[str, int],
    run_url: str,
) -> str:
    """Hash stable observations, never the workflow URL or generation time."""
    observation = dict(
        day=day, source=source, checks=results, measurements=measurements
    )
    digest = hashlib.sha256(
        json.dumps(observation, sort_keys=True).encode()
    ).hexdigest()
    lines = [
        f"# Automated project health — {day}",
        "",
        "Generated automatically by GitHub Actions; not a manual review.",
        "",
        f"Source commit checked: `{source}`",
        "",
        "| Check | Result |",
        "| --- | --- |",
    ]
    lines += [f"| {name} | {status} |" for name, status in results.items()]
    lines += ["", "Measurements:"]
    lines += [f"- {name}: {value}" for name, value in measurements.items()]
    lines += [
        "",
        f"[Workflow run]({run_url})",
        "",
        "Live LLM/model quality and Docker integration are outside this daily check.",
        f"<!-- observation: {digest} -->",
        "",
    ]
    return "\n".join(lines)


def same_observation(old: str, new: str) -> bool:
    def marker(text: str) -> str | None:
        return next(
            (
                line
                for line in text.splitlines()
                if line.startswith("<!-- observation: ")
            ),
            None,
        )

    return marker(old) is not None and marker(old) == marker(new)


def collect(output: Path) -> int:
    """Always produce a report, including when dependency setup failed."""
    ready = os.environ.get("DEPENDENCIES_READY") == "true"
    results = {
        name: run_check(command) if ready else "could not run"
        for name, command in CHECKS.items()
    }
    measurements: dict[str, int] = {}
    junit = Path("/tmp/health-tests.xml")
    if ready and junit.exists():
        suites = ET.parse(junit).getroot().iter("testsuite")
        measurements["Tests collected"] = sum(
            int(s.get("tests", "0")) for s in suites
        )
    # Uncompressed wheel bytes are useful and stable across ZIP timestamps.
    if results.get("Build") == "passed":
        from zipfile import ZipFile

        wheel = next(Path("/tmp/health-dist").glob("*.whl"))
        with ZipFile(wheel) as package:
            measurements["Wheel uncompressed bytes"] = sum(
                i.file_size for i in package.infolist()
            )
    # Report-only commits don't change the checked source or cause rerun churn.
    source = subprocess.check_output(
        [
            "git",
            "log",
            "-1",
            "--format=%H",
            "--",
            ".",
            f":(exclude){REPORT_DIRECTORY}",
        ],
        text=True,
    ).strip()
    day = report_day(datetime.now(UTC))
    run_url = (
        f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
        f"/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / f"{day}.md").write_text(
        render(day, source, results, measurements, run_url)
    )
    return int(any(status != "passed" for status in results.values()))


def api(path: str, payload: dict[str, Any] | None = None) -> Any:
    command = ["gh", "api", path]
    if payload is not None:
        command += ["--method", "POST", "--input", "-"]
    result = subprocess.run(
        command,
        input=json.dumps(payload) if payload is not None else None,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


def publish(report: Path) -> None:
    """CAS-style non-force ref updates; build each retry on the latest tree."""
    datetime.strptime(report.stem, "%Y-%m-%d")
    if report.suffix != ".md":
        raise ValueError("Only dated Markdown reports can be published")
    content = report.read_text()
    path = f"{REPORT_DIRECTORY}/{report.name}"
    repo = f"repos/{os.environ['GITHUB_REPOSITORY']}"
    for attempt in range(5):
        metadata = api(repo)
        branch = metadata["default_branch"]
        head = api(f"{repo}/git/ref/heads/{branch}")["object"]["sha"]
        commit = api(f"{repo}/git/commits/{head}")
        tree = api(f"{repo}/git/trees/{commit['tree']['sha']}?recursive=1")
        if tree.get("truncated"):
            raise RuntimeError(
                "Cannot safely inspect a truncated repository tree"
            )
        entry = next((e for e in tree["tree"] if e["path"] == path), None)
        if entry:
            blob = api(f"{repo}/git/blobs/{entry['sha']}")
            old = base64.b64decode(blob["content"]).decode()
            if same_observation(old, content):
                print("Same-day observations unchanged; no commit.")
                return
        new_tree = api(
            f"{repo}/git/trees",
            {
                "base_tree": commit["tree"]["sha"],
                "tree": [
                    {
                        "path": path,
                        "mode": "100644",
                        "type": "blob",
                        "content": content,
                    }
                ],
            },
        )
        new_commit = api(
            f"{repo}/git/commits",
            {
                "message": f"chore(health): automated daily observations {report.stem}",
                "tree": new_tree["sha"],
                "parents": [head],
                "author": {
                    "name": "Duke",
                    "email": "dongyeopdukelee@gmail.com",
                },
            },
        )
        try:
            # gh POST is explicitly overridden: a non-force ref update never
            # replaces a competing sibling commit or bypasses branch rules.
            subprocess.run(
                [
                    "gh",
                    "api",
                    f"{repo}/git/refs/heads/{branch}",
                    "--method",
                    "PATCH",
                    "--input",
                    "-",
                ],
                input=json.dumps({"sha": new_commit["sha"], "force": False}),
                text=True,
                check=True,
            )
            print(f"Published {path}: {new_commit['sha']}")
            return
        except subprocess.CalledProcessError:
            if attempt == 4:
                raise
            time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["collect", "publish"])
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.command == "collect":
        sys.exit(collect(args.path))
    publish(args.path)


if __name__ == "__main__":
    main()
