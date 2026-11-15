"""Behavioral tests for the automatic daily observation contract."""

import base64
import importlib.util
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "project_health", Path(__file__).parents[1] / "scripts/project_health.py"
)
assert spec is not None and spec.loader is not None
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


def test_date_and_same_day_observations() -> None:
    assert (
        health.report_day(datetime(2026, 11, 14, 1, tzinfo=UTC))
        == "2026-11-13"
    )
    first = health.render(
        "2026-11-14", "abc", {"Tests": "passed"}, {"Tests": 7}, "run/1"
    )
    rerun = health.render(
        "2026-11-14", "abc", {"Tests": "passed"}, {"Tests": 7}, "run/2"
    )
    assert health.same_observation(first, rerun)
    failed = health.render(
        "2026-11-14", "abc", {"Tests": "failed"}, {"Tests": 7}, "run/3"
    )
    assert not health.same_observation(first, failed)
    tomorrow = health.render(
        "2026-11-15", "abc", {"Tests": "passed"}, {"Tests": 7}, "run/4"
    )
    assert not health.same_observation(first, tomorrow)


def test_commands_distinguish_failure_from_unavailable() -> None:
    import sys

    assert (
        health.run_check([sys.executable, "-c", "raise SystemExit(0)"])
        == "passed"
    )
    assert (
        health.run_check([sys.executable, "-c", "raise SystemExit(2)"])
        == "failed"
    )
    assert health.run_check(["/nonexistent-health-command"]) == "could not run"


def test_setup_failure_still_generates_report(tmp_path: Path) -> None:
    with patch.dict(
        "os.environ",
        {
            "DEPENDENCIES_READY": "false",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_RUN_ID": "1",
        },
    ):
        assert health.collect(tmp_path) == 1
    report = next(tmp_path.glob("*.md")).read_text()
    assert report.count("could not run") == 5
    assert "| Tests | failed |" not in report


def test_failed_check_is_reported_and_returns_failure(tmp_path: Path) -> None:
    with (
        patch.dict(
            "os.environ",
            {
                "DEPENDENCIES_READY": "true",
                "GITHUB_SERVER_URL": "https://github.com",
                "GITHUB_REPOSITORY": "owner/repo",
                "GITHUB_RUN_ID": "1",
            },
        ),
        patch.object(health, "CHECKS", {"Broken check": ["false"]}),
    ):
        assert health.collect(tmp_path) == 1
    assert (
        "| Broken check | failed |" in next(tmp_path.glob("*.md")).read_text()
    )


def test_publisher_skips_unchanged_observations(tmp_path: Path) -> None:
    report = tmp_path / "2026-11-14.md"
    old = health.render("2026-11-14", "abc", {"Tests": "passed"}, {}, "run/1")
    report.write_text(
        health.render("2026-11-14", "abc", {"Tests": "passed"}, {}, "run/2")
    )
    with (
        patch.dict("os.environ", {"GITHUB_REPOSITORY": "owner/repo"}),
        patch.object(
            health,
            "api",
            side_effect=[
                {"default_branch": "main"},
                {"object": {"sha": "head"}},
                {"tree": {"sha": "tree"}},
                {
                    "tree": [
                        {"path": "reports/health/2026-11-14.md", "sha": "blob"}
                    ]
                },
                {"content": base64.b64encode(old.encode()).decode()},
            ],
        ) as api,
    ):
        health.publish(report)
    assert api.call_count == 5  # reads only: no tree, commit, or ref mutation


def test_publish_retries_on_latest_tree_without_force(tmp_path: Path) -> None:
    report = tmp_path / "2026-11-14.md"
    report.write_text(
        health.render("2026-11-14", "abc", {"Tests": "passed"}, {}, "run/1")
    )
    responses: list[dict[str, Any]] = []
    for number in [1, 2]:
        responses.extend(
            [
                {"default_branch": "main"},
                {"object": {"sha": f"head{number}"}},
                {"tree": {"sha": f"tree{number}"}},
                {"tree": []},
                {"sha": f"newtree{number}"},
                {"sha": f"newcommit{number}"},
            ]
        )
    with (
        patch.dict("os.environ", {"GITHUB_REPOSITORY": "owner/repo"}),
        patch.object(health, "api", side_effect=responses) as api,
        patch.object(
            health.subprocess,
            "run",
            side_effect=[
                subprocess.CalledProcessError(1, "gh"),
                subprocess.CompletedProcess("gh", 0),
            ],
        ) as update,
        patch.object(health.time, "sleep"),
    ):
        health.publish(report)
    mutations = [
        call.args[1] for call in api.call_args_list if len(call.args) == 2
    ]
    assert mutations[0]["base_tree"] == "tree1"
    assert mutations[2]["base_tree"] == "tree2"
    assert mutations[3]["parents"] == ["head2"]
    for tree in mutations[::2]:
        assert [entry["path"] for entry in tree["tree"]] == [
            "reports/health/2026-11-14.md"
        ]
    assert all(
        '"force": false' in call.kwargs["input"]
        for call in update.call_args_list
    )
