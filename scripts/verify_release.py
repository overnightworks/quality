"""The release step's check: run it on the tagged merge commit before the tag is pushed, because a pushed tag is immutable.

    uv sync && uv run python scripts/verify_release.py vX.Y.Z

Exit 0 only when the checkout is clean, the tag is at HEAD, the installed version is the tag and the gate's tool
checkouts pin the tag; one line per check says which.
"""

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
    installed_version: str
    gate_refs: tuple[str, ...]


@dataclass(frozen=True)
class Verdict:
    check: str
    holds: bool

    def line(self) -> str:
        return f"{'ok  ' if self.holds else 'FAIL'} {self.check}"


def gate_tool_refs(gate_text: str) -> list[str]:
    """The `ref` of every checkout of the tool repository in gate.yml, in file order."""
    jobs = yaml.safe_load(gate_text)["jobs"].values()
    steps = [step.get("with", {}) for job in jobs for step in job["steps"]]
    return [options.get("ref") for options in steps if options.get("repository") == TOOL_REPOSITORY]


def judge_release(facts: ReleaseFacts) -> list[Verdict]:
    return [
        Verdict("clean", facts.work_tree_is_clean),
        Verdict("tag at HEAD", facts.tag_commit == facts.head_commit),
        Verdict("installed version", facts.installed_version == facts.tag.removeprefix("v")),
        Verdict("gate refs", bool(facts.gate_refs) and set(facts.gate_refs) == {facts.tag}),
    ]


def _git(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *arguments], capture_output=True, text=True, check=False)


def read_facts(tag: str) -> ReleaseFacts:
    """The index equals HEAD and the work tree equals the index; the tag is peeled, so annotated and lightweight both work."""
    clean = _git("diff", "--cached", "--quiet").returncode == 0 and _git("diff", "--quiet").returncode == 0
    tagged = _git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")
    return ReleaseFacts(
        tag=tag,
        work_tree_is_clean=clean,
        tag_commit=tagged.stdout.strip() if tagged.returncode == 0 else None,
        head_commit=_git("rev-parse", "HEAD").stdout.strip(),
        installed_version=version("overnightworks-quality"),
        gate_refs=tuple(gate_tool_refs(GATE.read_text(encoding="utf-8"))),
    )


def main(tag: str) -> int:
    verdicts = judge_release(read_facts(tag))
    print("\n".join(verdict.line() for verdict in verdicts))
    return 0 if all(verdict.holds for verdict in verdicts) else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
