"""The build's check: `release.yml`'s build job runs it at the gated commit, tagged in the runner's own checkout, before
the tag job creates the immutable tag. The gate judges the dispatch; this script judges what only the build can.

    uv sync --locked --reinstall-package overnightworks-quality && uv run --locked python scripts/verify_release.py

The release tag is read from git: the one tag `vX.Y.Z` that points at HEAD. Exit 0 only when exactly one release tag is at
HEAD, the installed version is that tag, the gate's tool checkouts pin it and every rule's release facts name a release;
one line per check says which, and the tag judged is in the first. It reads no remote.
A rule or switch new since the last release names the tag being cut in `src/quality/rules.py`, set in the release pull
request that moves gate.yml's tool refs. Exit 2 for an unexpected command-line argument;
`-h` and `--help` print the usage and exit 0 before any check.
"""

# ruff: noqa: T201 - this script prints its verdict lines as its one output

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

import yaml

from quality.rules import Rule, RuleRelease

TOOL_REPOSITORY = "overnightworks/quality"
GATE = Path(".github") / "workflows" / "gate.yml"
RELEASE_TAG = re.compile(r"v(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)")


@dataclass(frozen=True)
class ReleaseFacts:
    release_tags_at_head: tuple[str, ...]
    installed_version: str
    gate_refs: tuple[str | None, ...]
    rules_without_release: tuple[str, ...]


@dataclass(frozen=True)
class Verdict:
    check: str
    holds: bool

    def line(self) -> str:
        return f"{'ok  ' if self.holds else 'FAIL'} {self.check}"


def gate_tool_refs(gate_text: str) -> list[str | None]:
    """The `ref` of every checkout of the tool repository in gate.yml, in file order; None for one without a ref."""
    jobs = yaml.safe_load(gate_text)["jobs"].values()
    steps = [step.get("with", {}) for job in jobs for step in job["steps"]]
    return [options.get("ref") for options in steps if options.get("repository") == TOOL_REPOSITORY]


def judge_release(facts: ReleaseFacts) -> list[Verdict]:
    """Without exactly one release tag at HEAD there is no tag to hold the version and the gate refs to, so those fail too."""
    found = ", ".join(facts.release_tags_at_head) or "none"
    tag = facts.release_tags_at_head[0] if len(facts.release_tags_at_head) == 1 else None
    return [
        Verdict(f"one release tag at HEAD (found: {found})", tag is not None),
        Verdict("installed version", tag is not None and facts.installed_version == tag.removeprefix("v")),
        Verdict("gate refs", tag is not None and bool(facts.gate_refs) and set(facts.gate_refs) == {tag}),
        Verdict(
            f"rule release facts (missing: {', '.join(facts.rules_without_release) or 'none'})",
            not facts.rules_without_release,
        ),
    ]


def _git(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *arguments], capture_output=True, text=True, check=False)  # noqa: S603, S607 - argv is fixed words and never a shell


def release_tags_at_head() -> tuple[str, ...]:
    """Annotated and lightweight tags both point at HEAD; only the form vX.Y.Z is a release."""
    tags = _git("tag", "--points-at", "HEAD").stdout.split()
    return tuple(tag for tag in tags if RELEASE_TAG.fullmatch(tag))


def rules_without_release(releases: Iterable[RuleRelease]) -> tuple[str, ...]:
    """The rules, or later switches, that only main has."""
    return tuple(
        release.rule
        for release in releases
        if release.first_release is None or (release.switched is not None and release.switched.release is None)
    )


def read_facts() -> ReleaseFacts:
    return ReleaseFacts(
        release_tags_at_head=release_tags_at_head(),
        installed_version=version("overnightworks-quality"),
        gate_refs=tuple(gate_tool_refs(GATE.read_text(encoding="utf-8"))),
        rules_without_release=rules_without_release(rule.release for rule in Rule),
    )


def main(arguments: list[str]) -> int:
    argparse.ArgumentParser(description="Check the tagged commit at HEAD before its tag is pushed.").parse_args(
        arguments
    )
    verdicts = judge_release(read_facts())
    print("\n".join(verdict.line() for verdict in verdicts))
    return 0 if all(verdict.holds for verdict in verdicts) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
