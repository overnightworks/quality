import os
from pathlib import Path

from quality import process, scanners
from quality.config import CONFIG_PATH, Allowance, ConfigError, load_staged_secret_allowances
from quality.deadline import RunDeadline
from quality.report import Instruction, RuleResult, StagedExtent, config_refusal

INDEX_VARIABLE = "GIT_INDEX_FILE"
COMMAND = "quality check secrets --staged"
STAGE_THE_FIX = Instruction(f"stage the fix, then run {COMMAND} again")


def scan_change(path: Path, deadline: RunDeadline, log: scanners.ScannerLog = scanners.no_log) -> RuleResult:
    """Scan exactly what the next commit records: the effective index of the repository at `path`, against HEAD.

    gitleaks never sees the live repository. It scans a scratch repository that borrows only the objects, so
    nothing the live repository holds (work-tree ignore files, `info/attributes`, config, the index file the
    caller's environment points at) can change what is found. It reports the allowed values and no unused entry: one
    change cannot tell a stale entry from one the history still needs.
    """
    deadline.ensure_time_left()
    binary = scanners.prepare_gitleaks()
    path = path.resolve()  # absolute, so a path that starts with `-` can never read as a git option
    staged_tree = _git_output(path, "write-tree")
    head = process.unborn_aware_head_commit(path, _caller_git_environment())
    object_store = Path(_git_output(path, "rev-parse", "--path-format=absolute", "--git-path", "objects"))
    with process.scratch_directory() as scratch:
        git_environment = process.attribute_free_git_environment(scratch / "home")
        source = _scratch_repository(scratch / "source", object_store, head, staged_tree, git_environment)
        # A tree without quality/config.toml allows nothing.
        staged_config = process.read_blob(
            source, staged_tree, CONFIG_PATH, f"cannot look for {CONFIG_PATH} in the staged tree",
            f"{CONFIG_PATH} cannot be read from the staged tree", git_environment,
        )
        allowances = _staged_allowances(staged_config)
        scan = scanners.run_gitleaks(
            binary, scratch, git_environment, source, ["--pre-commit", "--staged"], allowances, deadline, log
        )
        return RuleResult(scan.findings, StagedExtent(_changed_files(source, git_environment)), False, scan.notes)


def _staged_allowances(text: str | None) -> dict[Allowance, str]:
    try:
        return load_staged_secret_allowances(text)
    except ConfigError as error:
        raise config_refusal(error, STAGE_THE_FIX) from error


def _changed_files(source: Path, git_environment: dict[str, str]) -> int:
    # -z and bytes: a file name that is no UTF-8 is still one file of the change, never a refusal of the commit.
    # `process.git` would append git's stderr to the fact, so a failure says its exit code alone.
    command = ["git", "-C", str(source), "diff", "--staged", "--name-only", "-z"]
    done = process.run(command, env=git_environment, encoding=None)
    if done.returncode != 0:
        raise process.ProcessError(f"git diff exited {done.returncode}")
    return done.stdout.count(b"\0")


def _caller_git_environment() -> dict[str, str]:
    # The inherited environment on purpose: it is how git finds the index a commit in progress is using.
    # A relative index path means "relative to where the caller stands", not to the repository path.
    environment = dict(os.environ)
    if INDEX_VARIABLE in environment:
        environment[INDEX_VARIABLE] = str(Path(environment[INDEX_VARIABLE]).absolute())
    return environment


def _git_output(path: Path, *arguments: str) -> str:
    return process.git(["-C", str(path), *arguments], f"git {arguments[0]} failed", _caller_git_environment()).strip()


def _scratch_repository(
    destination: Path, object_store: Path, head: str | None, staged_tree: str, git_environment: dict[str, str]
) -> Path:
    def git(*arguments: str) -> None:
        process.git(["-C", str(destination), *arguments], f"git {arguments[0]} failed in the scratch repository", git_environment)

    process.git(["init", "--quiet", "--template=", str(destination)], "cannot create the scratch repository", git_environment)
    alternates = destination / ".git" / "objects" / "info" / "alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_text(f"{object_store}\n", encoding="utf-8")
    if head is not None:
        git("update-ref", "HEAD", head)
    git("read-tree", staged_tree)
    return destination
