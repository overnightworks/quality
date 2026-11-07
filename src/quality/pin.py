"""Which quality judges here: the running one, where it is the commit the repository's gate pins or quality's own
checkout run from its code, never another."""

import os
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import distribution
from pathlib import Path

from quality import DISTRIBUTION, __version__, checkout, installation, process
from quality.checkout import GatePin, NoGatePin, OwnGate, UnknownGatePin
from quality.installation import DirectoryOrigin, VcsOrigin
from quality.report import CannotJudge, Command, Instruction, Release

FIRST_RELEASE_WITH_CHECK = "v0.10.0"


class ReleaseEvent(StrEnum):
    THIS_CHECKOUT = "this checkout"


@dataclass(frozen=True)
class RunningQuality:
    """The version this process runs, the directory its package was imported from, and where its installer got it."""

    version: str
    package: Path
    origin: VcsOrigin | DirectoryOrigin | None


@dataclass(frozen=True)
class JudgingRelease:
    """What the release row says: the release (or this checkout) that judges, and why it may."""

    event: Release | ReleaseEvent
    fact: str


def this_installation() -> RunningQuality:
    return RunningQuality(__version__, installation.package_directory(), installation.origin(distribution(DISTRIBUTION)))


def _source_git(directory: Path, *arguments: str) -> str | None:
    """git's answer about the installation's source directory, every `GIT_` variable removed: a commit hook's
    `GIT_DIR` and `GIT_INDEX_FILE` name the judged repository, not this one. None where git does not answer."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        done = process.run(["git", "--no-optional-locks", "-C", str(directory), *arguments], env=environment)
    except process.ProcessError:
        return None
    return done.stdout if done.returncode == 0 else None


def _clean_head(directory: Path) -> str | None:
    """HEAD of `directory` while no tracked file is changed, staged or deleted and no untracked file stands beside."""
    status = _source_git(directory, "status", "--porcelain=v2", "--branch", "--untracked-files=all")
    if status is None:
        return None
    lines = status.splitlines()
    if any(not line.startswith("#") for line in lines):
        return None
    return next((line.removeprefix("# branch.oid ") for line in lines if line.startswith("# branch.oid ")), None)


def _tagged_commit(directory: Path, tag: str) -> str | None:
    """The commit the tag `tag` names in `directory`; the peeled name of an annotated tag wins."""
    names = _source_git(directory, "for-each-ref", "--format=%(objectname) %(*objectname)", f"refs/tags/{tag}")
    words = (names or "").split()
    return words[-1] if words else None


def _runs_from(running: RunningQuality, directory: Path) -> bool:
    return running.package.resolve() == (directory / "src" / "quality").resolve()


def _directory_commit(running: RunningQuality, directory: Path) -> str | None:
    """The commit a directory installation runs: its HEAD, only while its package runs from the directory's own
    `src/quality`, its work tree is clean and the version's release tag names that HEAD."""
    tag = f"v{running.version}"
    if not checkout.RELEASE_TAG.fullmatch(tag) or not _runs_from(running, directory):
        return None
    head = _clean_head(directory)
    return head if head is not None and _tagged_commit(directory, tag) == head else None


def running_commit(running: RunningQuality) -> str | None:
    """The commit the running code was built from, None where it cannot be known."""
    match running.origin:
        case VcsOrigin(commit):
            return checkout.commit_of(commit)
        case DirectoryOrigin(directory):
            return _directory_commit(running, directory)
        case _:
            return None


def _installed_release(running: RunningQuality) -> JudgingRelease:
    tag = f"v{running.version}"
    shown = tag if checkout.RELEASE_TAG.fullmatch(tag) else running.version
    return JudgingRelease(Release(shown), "installed · no gate pins a release here")


def _at_the_pin(pin: GatePin, running: RunningQuality) -> JudgingRelease:
    commit = running_commit(running)
    if commit == pin.commit and pin.release == f"v{running.version}":
        return JudgingRelease(Release(pin.release), "pinned in " + ", ".join(pin.workflows))
    if commit == pin.commit:
        fact = f"the gate pins {pin.commit[:7]} as {pin.release}, but that commit is quality {running.version}"
        raise CannotJudge(fact, (Instruction("make the pin's comment name its commit's own tag, as docs/gate.md shows"),))
    if checkout.release_order(pin.release) < checkout.release_order(FIRST_RELEASE_WITH_CHECK):
        fact = f"the gate pins quality {pin.release}, which has no quality check"
        raise CannotJudge(fact, (Instruction(f"raise the gate pin to {FIRST_RELEASE_WITH_CHECK} or later"),))
    at = "whose commit is unknown" if commit is None else f"at {commit[:7]}"
    fact = f"the gate pins {pin.commit[:7]} as {pin.release}; this is quality {running.version} {at}"
    raise CannotJudge(fact, (Instruction(f"run quality {pin.release}, the release the gate pins"),))


def judging_release(root: Path, command: str, staged: bool, *, running: RunningQuality) -> JudgingRelease:
    """The release that judges `command` run at `root` by `running`, or the refusal that names why none may; the commit
    hook's `staged` run judges with the installed release where no gate pins one."""
    top = checkout.work_tree_top(root)
    match checkout.gate_pin(top):
        case NoGatePin() if staged:
            return _installed_release(running)
        case NoGatePin():
            fact = "no workflow here calls quality's gate.yml, so no release is pinned"
            raise CannotJudge(fact, (Instruction("add the gate as quality's docs/gate.md describes"),))
        case UnknownGatePin(reason):
            raise CannotJudge(reason.fact, reason.next_steps)
        case OwnGate() if _runs_from(running, top):
            return JudgingRelease(ReleaseEvent.THIS_CHECKOUT, f"{running.version} · quality judges itself")
        case OwnGate():
            raise CannotJudge(f"this is quality {running.version}, not this checkout's code", (Command(f"uv run {command}"),))
        case GatePin() as pin:
            return _at_the_pin(pin, running)


def judge_here(root: Path, command: str, staged: bool) -> JudgingRelease:
    return judging_release(root, command, staged, running=this_installation())
