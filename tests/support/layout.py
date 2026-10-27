"""Helpers of the layout rules' tests."""

import subprocess

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
    """The paths the hygiene run of `repo` reports under `rule`."""
    return findings(hygiene_output(capsys, repo)[1], rule)
