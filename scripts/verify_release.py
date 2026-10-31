"""The release step's check: run it on the tagged merge commit before the tag is pushed, because a pushed tag is immutable.

    uv sync --reinstall-package overnightworks-quality && uv run python scripts/verify_release.py

The release tag is read from git: the one tag `vX.Y.Z` that points at HEAD. Exit 0 only when the checkout is clean, exactly
one release tag is at HEAD, HEAD is on origin/main (a patch tag `vX.Y.Z` with Z above 0 may instead have it on
origin/release/vX.Y), the installed version is that tag and the gate's tool checkouts pin it; one line per check says
which, and the tag judged is in the first. Exit 2 for an unexpected command-line argument;
`-h` and `--help` print the usage and exit 0 before any check.
"""

# ruff: noqa: T201 - this script prints its verdict lines as its one output

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

import yaml

TOOL_REPOSITORY = "overnightworks/quality"
GATE = Path(".github") / "workflows" / "gate.yml"
RELEASE_TAG = re.compile(r"v(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)")


@dataclass(frozen=True)
class ReleaseFacts:
    release_tags_at_head: tuple[str, ...]
    work_tree_is_clean: bool
    branches_holding_head: frozenset[str]
    installed_version: str
    gate_refs: tuple[str | None, ...]


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


def branches_for(tag: str | None) -> tuple[str, ...]:
    """The origin branches that may hold the tagged commit: main, and for a patch tag its minor's release branch."""
    match = tag and RELEASE_TAG.fullmatch(tag)
    if not match or match["patch"] == "0":
        return ("main",)
    return ("main", f"release/v{match['major']}.{match['minor']}")


def judge_release(facts: ReleaseFacts) -> list[Verdict]:
    """Without exactly one release tag at HEAD there is no tag to hold the version and the gate refs to, so those fail too."""
    found = ", ".join(facts.release_tags_at_head) or "none"
    tag = facts.release_tags_at_head[0] if len(facts.release_tags_at_head) == 1 else None
    accepted = branches_for(tag)
    holding = next((branch for branch in accepted if branch in facts.branches_holding_head), None)
    on_branch = f"on origin/{holding}" if holding else "on " + " or ".join(f"origin/{branch}" for branch in accepted)
    return [
        Verdict(f"one release tag at HEAD (found: {found})", tag is not None),
        Verdict("clean", facts.work_tree_is_clean),
        Verdict(on_branch, holding is not None),
        Verdict("installed version", tag is not None and facts.installed_version == tag.removeprefix("v")),
        Verdict("gate refs", tag is not None and bool(facts.gate_refs) and set(facts.gate_refs) == {tag}),
    ]


def _git(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *arguments], capture_output=True, text=True, check=False)  # noqa: S603, S607 - argv is fixed words and never a shell


def fetch_origin_branches() -> bool:
    """Prunes, so a cached ref of a branch deleted on origin never holds HEAD; also may import the tags that point into the fetched history, so every read of tags comes after it."""
    refspecs = ("+refs/heads/main:refs/remotes/origin/main", "+refs/heads/release/*:refs/remotes/origin/release/*")
    return _git("fetch", "--quiet", "--prune", "origin", *refspecs).returncode == 0


def branches_holding_head(fetched: bool) -> frozenset[str]:
    """Main and the release branches of origin that contain HEAD; a failed fetch or read holds none."""
    if not fetched:
        return frozenset()
    listed = _git(
        "for-each-ref",
        "--contains",
        "HEAD",
        "--format=%(refname:lstrip=3)",
        "refs/remotes/origin/main",
        "refs/remotes/origin/release/",
    )
    return frozenset(listed.stdout.split()) if listed.returncode == 0 else frozenset()


def release_tags_at_head() -> tuple[str, ...]:
    """Annotated and lightweight tags both point at HEAD; only the form vX.Y.Z is a release."""
    tags = _git("tag", "--points-at", "HEAD").stdout.split()
    return tuple(tag for tag in tags if RELEASE_TAG.fullmatch(tag))


def read_facts() -> ReleaseFacts:
    """The index equals HEAD and the work tree equals the index; the fetch comes first, then every read."""
    fetched = fetch_origin_branches()
    clean = _git("diff", "--cached", "--quiet").returncode == 0 and _git("diff", "--quiet").returncode == 0
    return ReleaseFacts(
        release_tags_at_head=release_tags_at_head(),
        work_tree_is_clean=clean,
        branches_holding_head=branches_holding_head(fetched),
        installed_version=version("overnightworks-quality"),
        gate_refs=tuple(gate_tool_refs(GATE.read_text(encoding="utf-8"))),
    )


def main(arguments: list[str]) -> int:
    argparse.ArgumentParser(description="Check the tagged merge commit at HEAD before its tag is pushed.").parse_args(arguments)
    verdicts = judge_release(read_facts())
    print("\n".join(verdict.line() for verdict in verdicts))
    return 0 if all(verdict.holds for verdict in verdicts) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
