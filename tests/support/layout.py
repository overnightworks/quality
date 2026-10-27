"""Helpers of the layout rules' tests."""

import subprocess

import pytest

from tests.support.hygiene import hygiene_output

CONFIG = "quality/config.toml"
GUIDANCE = {"AGENTS.md": "# guidance\n"}
EXAMPLE_SHA = "1" * 40


def declaring(path: str, reason: str = "the build reads it by this name", rule: str = "file-suffix") -> str:
    return f'[[layout.allow]]\npath = "{path}"\nrule = "{rule}"\nreason = "{reason}"\n'


def findings(output: str, rule: str = "file-suffix") -> list[str]:
    return [line.split()[1] for line in output.splitlines() if line.startswith(f"{rule} ")]


def in_index(repo, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(repo), *arguments], check=True)


def red(capsys, repo, rule: str) -> list[str]:
    """The paths the hygiene run of `repo` reports under `rule`; a run that cannot judge `rule` fails the test."""
    output = hygiene_output(capsys, repo)[1]
    assert f"hygiene: {rule} cannot judge" not in output
    assert f"hygiene: {rule} unparsable" not in output
    return findings(output, rule)


def empty_files(*paths: str) -> dict[str, str]:
    """The given tracked paths, all empty."""
    return dict.fromkeys(paths, "")


def case(identifier: str, files: dict[str, str], expected: list[str]):
    """One row of a family that maps tracked `files` to the paths a rule reports."""
    return pytest.param(files, expected, id=identifier)
