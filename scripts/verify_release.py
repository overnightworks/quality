"""The release step's check: run it on the tagged merge commit before the tag is pushed, because a pushed tag is immutable.

    uv sync --reinstall-package overnightworks-quality && uv run python scripts/verify_release.py vX.Y.Z

Exit 0 only when the checkout is clean, the tag is at HEAD, HEAD is on origin/main, the installed version is the tag and
the gate's tool checkouts pin the tag; one line per check says which. Exit 2 for a wrong command line.
"""

import argparse
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

import yaml

TOOL_REPOSITORY = "overnightworks/quality"
GATE = Path(".github") / "workflows" / "gate.yml"


@dataclass(frozen=True)
class ReleaseFacts:
    tag: str
    work_tree_is_clean: bool
    tag_commit: str | None
    head_commit: str
    head_is_on_origin_main: bool
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


def judge_release(facts: ReleaseFacts) -> list[Verdict]:
    return [
        Verdict("clean", facts.work_tree_is_clean),
        Verdict("tag at HEAD", facts.tag_commit == facts.head_commit),
        Verdict("on origin/main", facts.head_is_on_origin_main),
        Verdict("installed version", facts.installed_version == facts.tag.removeprefix("v")),
        Verdict("gate refs", bool(facts.gate_refs) and set(facts.gate_refs) == {facts.tag}),
    ]


def _git(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *arguments], capture_output=True, text=True, check=False)


def head_is_on_origin_main() -> bool:
    """After a fetch of origin's main; a failed fetch or a missing origin is not on it."""
    fetched = _git("fetch", "--quiet", "origin", "main").returncode == 0
    return fetched and _git("merge-base", "--is-ancestor", "HEAD", "origin/main").returncode == 0


def read_facts(tag: str) -> ReleaseFacts:
    """The index equals HEAD and the work tree equals the index; the tag is peeled, so annotated and lightweight both work."""
    clean = _git("diff", "--cached", "--quiet").returncode == 0 and _git("diff", "--quiet").returncode == 0
    tagged = _git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")
    return ReleaseFacts(
        tag=tag,
        work_tree_is_clean=clean,
        tag_commit=tagged.stdout.strip() if tagged.returncode == 0 else None,
        head_commit=_git("rev-parse", "HEAD").stdout.strip(),
        head_is_on_origin_main=head_is_on_origin_main(),
        installed_version=version("overnightworks-quality"),
        gate_refs=tuple(gate_tool_refs(GATE.read_text(encoding="utf-8"))),
    )


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Check a tagged merge commit before its tag is pushed.")
    parser.add_argument("tag", help="the release tag, vX.Y.Z")
    verdicts = judge_release(read_facts(parser.parse_args(arguments).tag))
    print("\n".join(verdict.line() for verdict in verdicts))
    return 0 if all(verdict.holds for verdict in verdicts) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
