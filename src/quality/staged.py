import os
import subprocess
from pathlib import Path

from quality import engines
from quality.config import CONFIG_PATH, Allowance, ConfigError, load_secret_allowances
from quality.engines import EngineError, Pin, SecretScan, run, stderr_tail

INDEX_VARIABLE = "GIT_INDEX_FILE"


def scan_staged(path: Path, engine_dir: Path, pins: dict[str, Pin]) -> SecretScan:
    """Scan exactly what the next commit records: the effective index of the repository at `path`, against HEAD.

    gitleaks never sees the live repository. It scans a scratch repository that borrows only the objects, so
    nothing the live repository holds (work-tree ignore files, `info/attributes`, config, the index file the
    caller's environment points at) can change what is found.
    """
    binary = engines.prepare_gitleaks(engine_dir, pins)
    staged_tree = _write_tree(path)
    head = engines.head_commit(path, _caller_git_environment(), allow_unborn=True)
    object_store = _object_store(path)
    with engines.scratch_directory() as scratch:
        git_environment = engines.attribute_free_git_environment(scratch / "home")
        source = _scratch_repository(scratch / "source", object_store, head, staged_tree, git_environment)
        allowances = _allowances_of(source, staged_tree, git_environment)
        return engines.run_gitleaks(binary, scratch, git_environment, source, ["--pre-commit", "--staged"], allowances)


def _caller_git_environment() -> dict[str, str]:
    # The inherited environment on purpose: it is how git finds the index a commit in progress is using.
    # A relative index path means "relative to where the caller stands", not to the repository path.
    environment = dict(os.environ)
    if INDEX_VARIABLE in environment:
        environment[INDEX_VARIABLE] = str(Path(environment[INDEX_VARIABLE]).absolute())
    return environment


def _git_in(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return run(["git", "-C", str(path), *arguments], env=_caller_git_environment())


def _git_output(path: Path, *arguments: str) -> str:
    asked = _git_in(path, *arguments)
    if asked.returncode != 0:
        raise EngineError(f"git {arguments[0]} failed in {path}{stderr_tail(asked.stderr)}")
    return asked.stdout.strip()


def _write_tree(path: Path) -> str:
    return _git_output(path, "write-tree")


def _object_store(path: Path) -> Path:
    return Path(_git_output(path, "rev-parse", "--path-format=absolute", "--git-path", "objects"))


def _scratch_repository(
    destination: Path, object_store: Path, head: str | None, staged_tree: str, git_environment: dict[str, str]
) -> Path:
    def git(*arguments: str) -> str:
        done = run(["git", "-C", str(destination), *arguments], env=git_environment)
        if done.returncode != 0:
            raise EngineError(f"git {arguments[0]} failed in the scratch repository{stderr_tail(done.stderr)}")
        return done.stdout

    init = run(["git", "init", "--quiet", "--template=", str(destination)], env=git_environment)
    if init.returncode != 0:
        raise EngineError(f"cannot create the scratch repository{stderr_tail(init.stderr)}")
    alternates = destination / ".git" / "objects" / "info" / "alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_text(f"{object_store}\n", encoding="utf-8")
    if head is not None:
        git("update-ref", "HEAD", head)
    git("read-tree", staged_tree)
    return destination


def _allowances_of(source: Path, staged_tree: str, git_environment: dict[str, str]) -> dict[Allowance, str]:
    """The allow list of the tree being scanned; a tree without quality/config.toml allows nothing."""
    listed = run(["git", "-C", str(source), "ls-tree", staged_tree, "--", CONFIG_PATH], env=git_environment)
    if listed.returncode != 0:
        raise EngineError(f"cannot look for {CONFIG_PATH} in the staged tree{stderr_tail(listed.stderr)}")
    if not listed.stdout.strip():
        return load_secret_allowances(None)
    read = run(["git", "-C", str(source), "cat-file", "blob", f"{staged_tree}:{CONFIG_PATH}"], env=git_environment)
    if read.returncode != 0:
        raise ConfigError(f"{CONFIG_PATH} cannot be read from the staged tree{stderr_tail(read.stderr)}")
    return load_secret_allowances(read.stdout)
