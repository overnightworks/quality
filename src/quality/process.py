import os
import re
import signal
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import NoReturn, overload

VERSION_TIMEOUT_SECONDS = 30
STDERR_TAIL_LINES = 5
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class ProcessError(Exception):
    """A process or git call that cannot be trusted: it did not run, did not finish or failed."""


def exit_as_interrupted(send_signal: Callable[[int, int], None]) -> NoReturn:
    """Ends the process the way an unhandled Ctrl-C does: it dies by SIGINT, so a shell reads 130 and a calling loop stops.

    Only after the cleanup has unwound; the self-kill skips finalization. Returns to the fallback exit only when SIGINT is blocked.
    """
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    send_signal(os.getpid(), signal.SIGINT)
    raise SystemExit(128 + signal.SIGINT)


@contextmanager
def scratch_directory() -> Iterator[Path]:
    """A 0700 directory removed on every exit path, SIGTERM included (a hook runner or `timeout` sends it).

    The signal raises, so subprocess.run kills its child and the directory goes with the unredacted report in it.
    """
    def unwind(signal_number: int, _frame: object) -> None:
        raise SystemExit(128 + signal_number)

    previous = signal.signal(signal.SIGTERM, unwind)
    try:
        with tempfile.TemporaryDirectory() as scratch:
            yield Path(scratch)
    finally:
        signal.signal(signal.SIGTERM, previous)


def attribute_free_git_environment(empty_home: Path) -> dict[str, str]:
    """The environment git runs in so that no .gitattributes line can turn a diff into "Binary files differ".

    This cuts off the repository and tree attributes, the system file, the user's global file
    (`$XDG_CONFIG_HOME/git/attributes`) and any `core.attributesFile`, the repository's own config included, since
    the forced command-line value wins. It does not cut off `$GIT_DIR/info/attributes`: git reads it from disk whatever
    the environment says. The hygiene scan stays clear of it by scanning a fresh template-less mirror, which copies
    none; a caller that runs git in the real repository must deal with it itself.
    """
    empty_home.mkdir(parents=True)
    inherited = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    return inherited | {
        "GIT_ATTR_SOURCE": EMPTY_TREE,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.attributesFile",
        "GIT_CONFIG_VALUE_0": os.devnull,
        "HOME": str(empty_home),
        "XDG_CONFIG_HOME": str(empty_home),
    }


def parse_git_version(version_output: str) -> str | None:
    """The first `major.minor[.patch]` number in `git --version` output, wherever it sits in a word.

    Digit runs and the text between them are tokenised once, so a long run of digits cannot backtrack.
    """
    tokens = re.findall(r"\d+|\D+", version_output)
    for start in range(len(tokens) - 2):
        major, dot, minor = tokens[start : start + 3]
        if major.isdigit() and dot == "." and minor.isdigit():
            patch = tokens[start + 3 : start + 5]
            if len(patch) == 2 and patch[0] == "." and patch[1].isdigit():
                return f"{major}.{minor}.{patch[1]}"
            return f"{major}.{minor}"
    return None


def unborn_aware_head_commit(repository: Path, git_environment: dict[str, str]) -> str | None:
    """The commit HEAD names; None when the repository has no commit yet."""
    # --ignore-missing: a HEAD without a commit lists nothing and exits 0, where rev-parse would fail.
    listed = git(["-C", str(repository.resolve()), "rev-list", "--no-walk", "--ignore-missing", "HEAD", "--"],
                 f"cannot resolve HEAD of {repository}", git_environment)
    return listed.strip() or None


@overload
def git(
    arguments: list[str], failure: str, env: dict[str, str] | None = ..., bound: float | None = ...,
    encoding: str = ...,
) -> str: ...
@overload
def git(
    arguments: list[str], failure: str, env: dict[str, str] | None = ..., bound: float | None = ..., *,
    encoding: None,
) -> bytes: ...
def git(
    arguments: list[str], failure: str, env: dict[str, str] | None = None, bound: float | None = None,
    encoding: str | None = "utf-8",
) -> str | bytes:
    """git's output, bytes for `encoding=None`; a git that fails is a ProcessError of `failure` and git's own words.

    A `bound` in seconds runs git in its own process group (see `run_bounded_in_own_group`).
    """
    command = ["git", *arguments]
    done = run(command, env=env, encoding=encoding) if bound is None else run_bounded_in_own_group(command, bound, env, encoding)
    if done.returncode != 0:
        stderr = done.stderr if isinstance(done.stderr, str) else done.stderr.decode("utf-8", "replace")
        raise ProcessError(f"{failure}{stderr_tail(stderr)}")
    return done.stdout


REGULAR_FILE_MODES = frozenset({"100644", "100755"})


def read_blob(
    repository: Path, revision: str, path: str, unlisted: str, unreadable: str, env: dict[str, str] | None = None
) -> str | None:
    """The UTF-8 text of `path` at `revision`, None when the tree there lacks it.

    Each caller words its own failures: `unlisted` when the tree cannot be listed, `unreadable` when the file cannot.
    """
    in_repository = ["-C", str(repository.resolve())]
    listed = run(["git", *in_repository, "ls-tree", "--end-of-options", revision, "--", path], env=env)
    if listed.returncode != 0:
        raise ProcessError(f"{unlisted}: git exited {listed.returncode}")
    if not listed.stdout.strip():
        return None
    if listed.stdout.split()[0] not in REGULAR_FILE_MODES:
        raise ProcessError(f"{unreadable}: not a regular file")
    shown = run(["git", *in_repository, "show", "--end-of-options", f"{revision}:{path}"], env=env, encoding=None)
    if shown.returncode != 0:
        raise ProcessError(f"{unreadable}: git exited {shown.returncode}")
    try:
        return shown.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProcessError(f"{unreadable}: {error}") from error


@contextmanager
def _failures_as_process_errors(command: list[str]) -> Iterator[None]:
    try:
        yield
    except UnicodeDecodeError as error:
        raise ProcessError(f"{command[0]} printed output that is not UTF-8 ({error})") from error
    except (OSError, ValueError) as error:  # ValueError: an argument the OS refuses, such as an embedded null byte
        raise ProcessError(f"cannot run {command[0]}: {error}") from error


def run(
    command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None, timeout: float | None = None,
    encoding: str | None = "utf-8",
) -> subprocess.CompletedProcess:
    """`command`'s exit and output, decoded as UTF-8 whatever the locale; `encoding=None` keeps the bytes."""
    with _failures_as_process_errors(command):
        try:
            return subprocess.run(  # noqa: S603 - callers pass an argv list and never a shell
                command, capture_output=True, encoding=encoding, check=False, cwd=cwd, env=env, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            raise ProcessError(f"{command[0]} did not finish within {timeout} seconds") from None


def run_bounded_in_own_group(
    command: list[str], timeout: float, env: dict[str, str] | None = None, encoding: str | None = "utf-8"
) -> subprocess.CompletedProcess:
    """`run` for a command that must not outlive its bound or an interruption: it leads its own process group, which
    is killed when the bound passes or an exception (the SIGTERM of `scratch_directory`) unwinds the caller.

    Only inside `scratch_directory`: outside it a SIGTERM ends quality without unwinding, and the group would outlive it.
    """
    with _failures_as_process_errors(command), subprocess.Popen(  # noqa: S603 - callers pass an argv list and never a shell
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding=encoding, env=env, start_new_session=True
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(process)
            process.wait()  # not communicate(): a descendant that left the group would hold the pipes open
            raise ProcessError(f"{command[0]} did not finish within {timeout} seconds") from None
        except BaseException:
            _kill_group(process)
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _kill_group(process: subprocess.Popen) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)


def stderr_tail(stderr: str) -> str:
    tail = "\n".join(stderr.strip().splitlines()[-STDERR_TAIL_LINES:])
    return f": {tail}" if tail else ""
