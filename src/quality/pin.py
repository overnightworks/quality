"""Which quality judges here: the running one, where it is the commit the repository's gate pins or quality's own
checkout run from its code; else, once quality's remote showed the pinned commit is the comment's release, that release
prepared through uv, which this process then becomes, once."""

import os
import shlex
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum, StrEnum, auto
from importlib.metadata import distribution
from pathlib import Path
from typing import NoReturn

from quality import DISTRIBUTION, __version__, checkout, installation, output, process
from quality.checkout import GatePin, GateState, NoGatePin, OwnGate, UnknownGatePin
from quality.installation import DirectoryOrigin, VcsOrigin
from quality.output import Glyph, Row
from quality.report import CannotJudge, Command, Instruction, Release, Step

FIRST_RELEASE_WITH_CHECK = "v0.10.0"
RUN_AT_PIN_VARIABLE = "QUALITY_RUN_AT_PIN"
PREPARE_TIMEOUT_SECONDS = 600
TAGS_TIMEOUT_SECONDS = 60
SCRIPTS_QUESTION = ("python", "-I", "-c", 'import sysconfig; print(sysconfig.get_path("scripts"))')
# git's repository-local variables, which steer uv's git toward the judged repository when a commit hook exports them;
# the three configuration variables stay out because they carry the operator's own configuration.
REPOSITORY_VARIABLES = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE", "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE",
        "GIT_INDEX_FILE", "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX", "GIT_SHALLOW_FILE", "GIT_COMMON_DIR",
    }
)
COMMENT_STEP = Instruction("make the pin's comment name its commit's own tag, as docs/gate.md shows")


class Fetch(StrEnum):
    """How the re-executed run's release was prepared, the value of `RUN_AT_PIN_VARIABLE`."""

    CACHED = "cached"
    FETCHED = "fetched"


class ReleaseEvent(StrEnum):
    THIS_CHECKOUT = "this checkout"
    INSTALLED = "installed"


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

    def row(self) -> Row:
        return Row(Glyph.NOTE, "release", self.event, self.fact)


@dataclass(frozen=True)
class ReleaseAtPin:
    """The release the gate pins, to be prepared from its commit."""

    release: Release
    commit: str


@dataclass(frozen=True)
class ThisCheckout:
    """quality's own checkout at `top`, to be prepared from its code."""

    top: Path


Reexecution = ReleaseAtPin | ThisCheckout


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


def _at_the_pin(pin: GatePin, running: RunningQuality, command: str, fetch: Fetch | None, door: str) -> JudgingRelease | ReleaseAtPin:
    commit = running_commit(running)
    if commit == pin.commit and pin.release == f"v{running.version}":
        fetched = "fetched now · " if fetch is Fetch.FETCHED else ""
        return JudgingRelease(Release(pin.release), f"{fetched}pinned in " + ", ".join(pin.workflows))
    if commit == pin.commit:
        fact = f"the gate pins {pin.commit[:7]} as {pin.release}, but that commit is quality {running.version}"
        raise CannotJudge(fact, (COMMENT_STEP,))
    if checkout.release_order(pin.release) < checkout.release_order(FIRST_RELEASE_WITH_CHECK):
        fact = f"the gate pins quality {pin.release}, which has no {door}"
        raise CannotJudge(fact, (Instruction(f"raise the gate pin to {FIRST_RELEASE_WITH_CHECK} or later"),))
    if fetch is None:
        return ReleaseAtPin(Release(pin.release), pin.commit)
    at = "an unknown commit" if commit is None else commit[:7]
    fact = f"the gate pins {pin.commit[:7]} as {pin.release}; uv prepared quality {running.version} at {at}"
    raise CannotJudge(fact, (Command(f"uv cache clean {DISTRIBUTION}"), Instruction(f"then run {command} again")))


def _run_at_pin(environment: Mapping[str, str], command: str) -> Fetch | None:
    """The guard a re-executed run carries; None in the run the operator started."""
    value = environment.get(RUN_AT_PIN_VARIABLE)
    if value is None:
        return None
    try:
        return Fetch(value)
    except ValueError:
        fact = f"{RUN_AT_PIN_VARIABLE} holds {value}, which quality never sets"
        raise CannotJudge(fact, (Instruction(f"unset {RUN_AT_PIN_VARIABLE}, then run {command} again"),)) from None


def judging_release(
    root: Path, command: str, staged: bool, environment: Mapping[str, str], *, running: RunningQuality
) -> JudgingRelease | Reexecution:
    """The release that judges `command` run at `root` by `running`, the one to re-execute into, or the refusal that
    names why none may; the commit hook's `staged` run judges with the installed release where no gate pins one. A run
    the guard in `environment` marks as re-executed never re-executes again."""
    fetch = _run_at_pin(environment, command)
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
        case OwnGate() if fetch is None:
            return ThisCheckout(top.absolute())
        case OwnGate():
            raise CannotJudge(f"this is quality {running.version}, not this checkout's code", (Command(f"uv run {command}"),))
        case GatePin() as pin:
            return _at_the_pin(pin, running, command, fetch, "quality check")


def judging_of(
    state: GateState, repository: str, command: str, environment: Mapping[str, str], *, door: str, running: RunningQuality,
    reexecute: Callable[[ReleaseAtPin, str], NoReturn],
) -> JudgingRelease:
    """The release that judges `door` for `repository` from its gate's pin `state`, read elsewhere than a checkout, or
    the refusal that names why none may; a pinned release `running` is not, it becomes through `reexecute`. quality's own
    gate pins no release, so the installed quality judges it, never the code of the pull request."""
    fetch = _run_at_pin(environment, command)
    match state:
        case NoGatePin():
            fact = f"no workflow of {repository} at the pull request's merge commit calls quality's gate.yml, so no release is pinned"
            raise CannotJudge(fact, (Instruction("add the gate as quality's docs/gate.md describes"),))
        case UnknownGatePin(reason):
            raise CannotJudge(reason.fact, reason.next_steps)
        case OwnGate() if fetch is None:
            return JudgingRelease(ReleaseEvent.INSTALLED, f"{running.version} · quality's gate pins no release")
        case OwnGate():
            fact = f"the gate of {repository} pins no release, but this is quality {running.version} prepared by uv"
            raise CannotJudge(fact, (Instruction(f"run {command} again"),))
        case GatePin() as pin:
            release = _at_the_pin(pin, running, command, fetch, door)
            if isinstance(release, ReleaseAtPin):
                reexecute(release, command)
            return release


def gate_run_release(version: str) -> JudgingRelease:
    """The release a gate run judges at: the tool the gate checked out, a release, or quality's own checkout."""
    tag = f"v{version}"
    if checkout.RELEASE_TAG.fullmatch(tag):
        return JudgingRelease(Release(tag), "this gate run")
    return JudgingRelease(ReleaseEvent.THIS_CHECKOUT, f"{version} · quality judges itself")


def judge_here(root: Path, command: str, staged: bool, environment: Mapping[str, str]) -> JudgingRelease | Reexecution:
    return judging_release(root, command, staged, environment, running=this_installation())


def release_records(environment: Mapping[str, str]) -> Path | None:
    """Where quality keeps one empty file per release its remote showed at its commit; None keeps none, so every run asks."""
    for variable, below in (("XDG_CACHE_HOME", ()), ("HOME", (".cache",))):
        base = Path(environment.get(variable, ""))
        if base.is_absolute():
            return base.joinpath(*below, DISTRIBUTION, "releases")
    return None


def is_recorded(records: Path | None, commit: str, release: str) -> bool:
    """Both parts come validated from `checkout.gate_pin`, so the name holds no path character."""
    return records is not None and (records / f"{commit}-{release}").exists()


def keep_record(records: Path | None, commit: str, release: str) -> None:
    """Only after quality's remote showed `release` at `commit`; a directory that refuses the record keeps none."""
    if records is None:
        return
    with suppress(OSError):
        records.mkdir(parents=True, exist_ok=True)
        (records / f"{commit}-{release}").touch()


def _release_source(commit: str) -> str:
    return f"{DISTRIBUTION} @ git+{checkout.GATE_URL}@{commit}"


def preparation_command(reexecution: Reexecution, *flags: str) -> list[str]:
    """uv's command that prepares `reexecution` and asks the prepared environment for its scripts directory."""
    match reexecution:
        case ReleaseAtPin(_, commit):
            return ["uv", "tool", "run", *flags, "--from", _release_source(commit), *SCRIPTS_QUESTION]
        case ThisCheckout(top):
            return ["uv", "run", *flags, "--locked", "--project", str(top), *SCRIPTS_QUESTION]


def prepared_quality(stdout: str) -> Path | None:
    """The `quality` executable the prepared environment's one-line answer names, None where the answer is no such path."""
    lines = stdout.splitlines()
    if len(lines) != 1 or not Path(lines[0]).is_absolute():
        return None
    executable = Path(lines[0], "quality")
    return executable if executable.is_file() and os.access(executable, os.X_OK) else None


class Failure(Enum):
    TAG_ELSEWHERE = auto()
    CANNOT_START = auto()
    UNREACHABLE = "github.com did not answer"
    UV_MISSING = "uv is not installed"
    UV_CANNOT_RUN = "cannot run uv"
    UV_EXITED = "uv exited"
    UV_TIMED_OUT = f"uv did not finish within {PREPARE_TIMEOUT_SECONDS} seconds"
    NO_EXECUTABLE = "uv's environment holds no quality executable"


@dataclass
class Failed(Exception):
    """What went wrong on the way to the prepared release; `detail` is the other commit, the exit or the reason."""

    failure: Failure
    detail: str = ""

    @classmethod
    def of(cls, error: process.ProcessError) -> Failed:
        """A uv that did not start has a cause; one that did not finish within its bound has none."""
        match error.__cause__:
            case FileNotFoundError():
                return cls(Failure.UV_MISSING)
            case None:
                return cls(Failure.UV_TIMED_OUT)
            case cause:
                return cls(Failure.UV_CANNOT_RUN, str(cause))

    def what(self) -> str:
        match self.failure:
            case Failure.UV_CANNOT_RUN | Failure.UV_EXITED:
                return f"{self.failure.value}{':' if self.failure is Failure.UV_CANNOT_RUN else ''} {self.detail}"
            case Failure.CANNOT_START:
                return self.detail
            case _:
                return self.failure.value


def preparation_refusal(failed: Failed, reexecution: Reexecution, command: str) -> CannotJudge:
    """The pair of a preparation that failed: what happened, and the next step that answers it."""
    if isinstance(reexecution, ThisCheckout):
        return CannotJudge(f"cannot prepare this checkout · {failed.what()}", (Command(f"uv run {command}"),))
    release, commit = reexecution.release.tag, reexecution.commit
    uv_command = Command(shlex.join(["uv", "tool", "run", "--from", _release_source(commit), "quality", "--version"]))
    match failed.failure:
        case Failure.TAG_ELSEWHERE:
            other = f"quality's {release} is {failed.detail[:7]}" if failed.detail else f"quality has no release {release}"
            return CannotJudge(f"the gate pins {commit[:7]} as {release}, but {other}", (COMMENT_STEP,))
        case Failure.UNREACHABLE:
            steps: tuple[Step, ...] = (Instruction(f"{command}, once online"),)
        case Failure.UV_MISSING:
            steps = (Instruction(f"install uv, then run {command} again"),)
        case Failure.UV_CANNOT_RUN:
            steps = (uv_command,)
        case Failure.CANNOT_START:
            return CannotJudge(f"cannot start quality {release} · {failed.what()}", (Instruction(f"{command} again"),))
        case _:
            return CannotJudge(f"cannot prepare quality {release}, which the gate pins · {failed.what()}", (uv_command, Instruction(f"{command} again")))
    return CannotJudge(f"cannot fetch quality {release}, which the gate pins · {failed.what()}", steps)


def _preparation_environment(environment: Mapping[str, str]) -> dict[str, str]:
    return {name: value for name, value in environment.items() if name not in REPOSITORY_VARIABLES}


def _prepare(command: list[str], environment: Mapping[str, str], every_second: Callable[[int], None] | None = None) -> Path:
    """The prepared `quality`, its uv's output captured and dropped; a uv that fails is `Failed`."""
    with process.scratch_directory() as scratch:
        try:
            done = process.run_bounded_in_own_group(
                command, PREPARE_TIMEOUT_SECONDS, _preparation_environment(environment), cwd=scratch, every_second=every_second
            )
        except process.ProcessError as error:
            raise Failed.of(error) from error
    if done.returncode != 0:
        raise Failed(Failure.UV_EXITED, str(done.returncode))
    executable = prepared_quality(done.stdout)
    if executable is None:
        raise Failed(Failure.NO_EXECUTABLE)
    return executable


def _check_release(pin: ReleaseAtPin, environment: Mapping[str, str], records: Path | None, every_second: Callable[[int], None]) -> None:
    """quality's remote must show the comment's tag at the pinned commit before uv builds or runs any of it."""
    query = ["git", "ls-remote", "--tags", "--", checkout.GATE_URL]
    with process.scratch_directory() as scratch:
        try:
            listed = process.run_bounded_in_own_group(
                query, TAGS_TIMEOUT_SECONDS, {**_preparation_environment(environment), "GIT_TERMINAL_PROMPT": "0"}, cwd=scratch,
                every_second=every_second,
            )
        except process.ProcessError:
            raise Failed(Failure.UNREACHABLE) from None
    if listed.returncode != 0:
        raise Failed(Failure.UNREACHABLE)
    tagged = checkout.release_tags_listed(listed.stdout).get(pin.release.tag)
    if tagged != pin.commit:
        raise Failed(Failure.TAG_ELSEWHERE, tagged or "")
    keep_record(records, pin.commit, pin.release.tag)


def _prepared_offline(reexecution: Reexecution, environment: Mapping[str, str], records: Path | None) -> Path | None:
    """A recorded release or the checkout from uv's cache; uv missing or not able to start is its pair at once."""
    if isinstance(reexecution, ReleaseAtPin) and not is_recorded(records, reexecution.commit, reexecution.release.tag):
        return None
    try:
        return _prepare(preparation_command(reexecution, "--offline", "--quiet"), environment)
    except Failed as failed:
        if failed.failure in {Failure.UV_MISSING, Failure.UV_CANNOT_RUN}:
            raise
        return None


def _prepared_online(reexecution: Reexecution, environment: Mapping[str, str], records: Path | None, live: output.LiveLine) -> Path:
    shown = reexecution.release.tag if isinstance(reexecution, ReleaseAtPin) else "this checkout"
    with live("release", "syncing" if isinstance(reexecution, ThisCheckout) else "fetching") as show:
        queried = 0
        show(f"{shown} · 0s")

        def show_query(seconds: int) -> None:
            nonlocal queried
            queried = seconds
            show(f"{shown} · {seconds}s")

        def show_preparation(seconds: int) -> None:
            show(f"{shown} · {queried + seconds}s")

        if isinstance(reexecution, ReleaseAtPin):
            _check_release(reexecution, environment, records, show_query)
        return _prepare(preparation_command(reexecution, "--quiet"), environment, show_preparation)


def reexecute(reexecution: Reexecution, command: str, words: Sequence[str], environment: Mapping[str, str], live: output.LiveLine) -> NoReturn:
    """Prepares `reexecution` and becomes its `quality` with the same `words` and `environment` plus the guard; every
    failure on the way is a refusal raised in this process."""
    records = release_records(environment)
    try:
        executable = _prepared_offline(reexecution, environment, records)
        fetch = Fetch.CACHED
        if executable is None:
            executable, fetch = _prepared_online(reexecution, environment, records, live), Fetch.FETCHED
        output.flush_streams()
        try:
            process.replace_process(executable, words, {**environment, RUN_AT_PIN_VARIABLE: fetch.value})
        except process.ProcessError as error:
            raise Failed(Failure.CANNOT_START, str(error)) from error
    except Failed as failed:
        raise preparation_refusal(failed, reexecution, command) from failed
