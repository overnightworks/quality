"""Helpers of the layout rules' tests."""

import subprocess

import pytest

from quality.rules import Rule
from tests.support.check import hygiene_run

CONFIG = "quality/config.toml"
GUIDANCE = {"AGENTS.md": "# guidance\n"}
EXAMPLE_SHA = "1" * 40


def declaring(path: str, reason: str = "the build reads it by this name", rule: str = Rule.FILE_SUFFIX) -> str:
    return f'[[layout.allow]]\npath = "{path}"\nrule = "{rule}"\nreason = "{reason}"\n'


def in_index(repo, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(repo), *arguments], check=True)


def red(repo, rule: Rule = Rule.FILE_SUFFIX) -> list[str]:
    """The locations of the findings the rule runner returns for `rule` over `repo`; a run that cannot judge `rule` fails the test."""
    (verdict,) = (verdict for verdict in hygiene_run(repo).verdicts if verdict.rule is rule)
    assert verdict.refusal is None
    return [finding.location for finding in verdict.findings]


def empty_files(*paths: str) -> dict[str, str]:
    """The given tracked paths, all empty."""
    return dict.fromkeys(paths, "")


def case(identifier: str, files: dict[str, str], expected: list[str]):
    """One row of a family that maps tracked `files` to the paths a rule reports."""
    return pytest.param(files, expected, id=identifier)
