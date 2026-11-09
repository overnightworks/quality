import os
from pathlib import Path

import pytest

from tests.support.sonar import (
    CLEAN_PROPERTIES,
    PULL_REQUEST_ENVIRONMENT,
    environment_without_token,
    legacy_config,
    legacy_config_absent,
    run_properties,
    sonar_off_files,
)

ADD_ONLY_NEXT = "next: let the coverage artifact add report files only, never change a file the commit tracks\n"


def rewrite(name: str, content: str):
    return lambda root: (root / name).write_text(content)


def rewrite_with_new_mtime(name: str, content: str):
    def arrange(root: Path) -> None:
        path = root / name
        path.write_text(content)
        later = path.stat().st_mtime + 3600
        os.utime(path, (later, later))

    return arrange


def rewrite_all(*changes):
    def arrange(root: Path) -> None:
        for change in changes:
            change(root)

    return arrange


@pytest.mark.parametrize(
    ("committed", "environment", "arrange", "fact"),
    [
        (
            {"quality/config.toml": legacy_config("fail")},
            PULL_REQUEST_ENVIRONMENT,
            rewrite("quality/config.toml", legacy_config("report")),
            "quality/config.toml differs from the checked-out commit",
        ),
        (
            {"quality/config.toml": legacy_config_absent},
            environment_without_token(),
            rewrite("quality/config.toml", sonar_off_files()["quality/config.toml"]),
            "quality/config.toml differs from the checked-out commit",
        ),
        (
            {"src/app.py": "a = 1\n"},
            PULL_REQUEST_ENVIRONMENT,
            rewrite("src/app.py", "a = 2\n"),
            "src/app.py differs from the checked-out commit",
        ),
        (
            {},
            PULL_REQUEST_ENVIRONMENT,
            rewrite("sonar-project.properties", CLEAN_PROPERTIES + "sonar.exclusions=**\n"),
            "sonar-project.properties differs from the checked-out commit",
        ),
        (
            {"quality/config.toml": legacy_config("fail"), "src/app.py": "a = 1\n"},
            PULL_REQUEST_ENVIRONMENT,
            rewrite_all(rewrite("quality/config.toml", legacy_config("report")), rewrite("src/app.py", "a = 2\n")),
            "quality/config.toml and 1 more differ from the checked-out commit",
        ),
    ],
    ids=["legacy loosened", "sonar switched off", "a source replaced", "properties widened", "two files"],
)
def test_properties_refuses_a_checkout_whose_tracked_files_differ_from_the_commit(
    monkeypatch, capsys, build_repo, tmp_path, committed, environment, arrange, fact
):
    """SONAR-13, SONAR-48, SONAR-49, SONAR-104: An artifact that changes a tracked file is exit 2 before any config read, and nothing is handed to the job."""
    step_output = tmp_path / "github_output"
    code, out, err = run_properties(
        monkeypatch,
        capsys,
        build_repo,
        environment=environment,
        extra_files=committed,
        arrange=arrange,
        step_output=step_output,
    )
    assert (code, out, err, step_output.exists()) == (2, "", f"error: {fact}\n{ADD_ONLY_NEXT}", False)


@pytest.mark.parametrize(
    "arrange",
    [
        rewrite("coverage.xml", "<coverage/>"),
        rewrite_all(
            rewrite("coverage.xml", "<coverage/>"), rewrite_with_new_mtime("quality/config.toml", legacy_config_absent)
        ),
    ],
    ids=["an untracked report", "a tracked file rewritten with its own bytes"],
)
def test_properties_takes_files_the_checkout_only_adds_or_rewrites_unchanged(
    monkeypatch, capsys, build_repo, tmp_path, arrange
):
    """SONAR-48, SONAR-49: An untracked report, or a tracked file rewritten with the bytes the commit holds, is no change."""
    step_output = tmp_path / "github_output"
    properties = CLEAN_PROPERTIES + "sonar.python.coverage.reportPaths=coverage.xml\n"
    committed = {"quality/config.toml": legacy_config_absent}
    code, _, err = run_properties(
        monkeypatch, capsys, build_repo, properties, extra_files=committed, arrange=arrange, step_output=step_output
    )
    assert (code, err, "scanner-arguments=" in step_output.read_text()) == (0, "", True)
