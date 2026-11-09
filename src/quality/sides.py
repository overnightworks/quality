"""What the gate holds at each side of a pull request, read at the head's checkout, at the base commit or from file texts,
and the files git renamed between the two."""

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from quality import architecture, checkout, process, repository
from quality.config import CONFIG_PATH, ConfigError, GateSettings, LegacyMode, load_gate_settings, read_gate_settings
from quality.ratchet import (
    BASELINE_PATH,
    Baseline,
    Renames,
    SizeError,
    head_step,
    load_baseline,
    read_baseline,
    renames_of,
)
from quality.report import CannotJudge, Step

CREDENTIAL_HELPER = "!gh auth git-credential"
FETCH_TIMEOUT_SECONDS = 120
NO_BASE_SETTINGS = GateSettings((), LegacyMode.REPORT, sonar=False)
HEAD = "the head"
IMPORT_CONFIG = "import-linter config"


@dataclass(frozen=True)
class Side:
    """What the gate holds at one side of a pull request: its settings, its baseline (None without one) and its import contracts."""

    settings: GateSettings
    baseline: Baseline | None
    contracts: architecture.SideContracts = field(default_factory=architecture.SideContracts)


FORBIDDEN_REF_CHARACTERS = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]")


def is_git_ref_name(ref: str) -> bool:
    """git-check-ref-format's rules, plus no leading '-' so a ref can never read as an option."""
    broken_name = (
        ref.startswith("-") or ref == "@" or ref.endswith(".")
        or ".." in ref or "@{" in ref or FORBIDDEN_REF_CHARACTERS.search(ref)
    )
    broken_component = any(
        part == "" or part.startswith(".") or part.endswith(".lock") for part in ref.split("/")
    )
    return not (broken_name or broken_component)


def _require_safe_ref(ref: str, role: str) -> None:
    if not is_git_ref_name(ref):
        raise CannotJudge(f"the {role} {ref!r} is not a ref name or a SHA")


class UnusableConfig(CannotJudge):
    """A side's config that cannot be read: `where` names the side and `error` keeps the config's fact and example
    apart; the callers print `error` as the refusal pair."""

    def __init__(self, where: str, error: ConfigError) -> None:
        super().__init__(f"{CONFIG_PATH} at {where}: {error}")
        self.where, self.error = where, error


def read_head(repo: Path) -> Side:
    """The gate's settings and baseline in the checked-out tree; an unusable file names the head.

    Only the head's malformed baseline says how to restore it: the pull request cannot rewrite its base.
    """
    try:
        settings = read_gate_settings(repo)
    except ConfigError as error:
        raise UnusableConfig(HEAD, error) from error
    try:
        baseline = read_baseline(repo)
    except SizeError as error:
        raise _unusable(BASELINE_PATH, HEAD, error, (head_step(error),)) from error
    except CannotJudge as error:
        raise _unusable(BASELINE_PATH, HEAD, error.reason.fact, error.reason.next_steps) from error
    try:
        import_configs = {name: repository.read_text(repo, name) for name in architecture.CONFIG_FILES}
    except repository.RepositoryFileError as error:
        raise _unusable(IMPORT_CONFIG, HEAD, error) from error
    return Side(settings, baseline, _contracts_at(import_configs, HEAD))


def read_base(repo: Path, base_sha: str) -> Side:
    """The gate's settings, baseline and import-linter configs at the base commit; a file the base lacks reads as absent."""
    where = f"the base commit {base_sha}"
    try:
        texts = {
            path: process.read_blob(repo, base_sha, path, f"cannot read {where}", f"cannot read {path} at {where}")
            for path in (CONFIG_PATH, BASELINE_PATH, *architecture.CONFIG_FILES)
        }
    except process.ProcessError as error:
        raise CannotJudge(str(error)) from error
    return side_of(texts[CONFIG_PATH], texts[BASELINE_PATH], where, {name: texts[name] for name in architecture.CONFIG_FILES})


def reads_tree(base: Side | None, head: Side) -> bool:
    """Whether the files of the two commits can change the judgement: only a module a contract list lost needs them."""
    return base is not None and bool(architecture.struck_modules(base.contracts, head.contracts))


def compares_baselines(base: Side | None, head: Side) -> bool:
    """Whether the renames can change the judgement: only a comparison of two baselines reads them. Any other pair is
    judged without them, so a pull request that renames nothing the baselines name is never held up by the diff."""
    return base is not None and base.baseline is not None and head.baseline is not None


def read_renames(
    repo: Path, base: str, head: str, env: dict[str, str] | None = None, bound: float | None = None
) -> Renames:
    """The files git renamed between two commits; the verdict and the door read the same pair.

    A `bound` in seconds runs git in its own process group, for the door inside its scratch directory."""
    _require_safe_ref(base, "base")
    _require_safe_ref(head, "head")
    try:
        listed = process.git(
            ["-C", str(repo.resolve()), "diff", *checkout.RENAME_LISTING, "--end-of-options", base, head, "--"],
            f"cannot list the files renamed between {base} and {head}",
            env,
            bound,
            encoding=None,
        )
    except process.ProcessError as error:
        raise CannotJudge(str(error)) from error
    return renames_of(listed)


@dataclass(frozen=True)
class TreeWords:
    """How a refusal to read the pull request's tree is worded; `{commit}`, `{base}` and `{head}` fill in."""

    unlisted: str
    unrenamed: str
    warned: str


CHECKOUT_WORDS = TreeWords(
    "cannot list the files tracked at {commit}",
    "cannot list the files renamed between {base} and {head}",
    "git's list of renamed files between {base} and {head} came with a warning, so it may miss renames",
)
DOOR_WORDS = TreeWords(
    "cannot list the files git holds at the pull request's commits",
    "cannot list the files git renamed between the pull request's commits",
    "git's list of renamed files between the pull request's commits came with a warning, so it may miss renames",
)


def read_pull_tree(
    repo: Path, base: str, head: str, env: dict[str, str] | None = None, bound: float | None = None,
    words: TreeWords = CHECKOUT_WORDS,
) -> architecture.PullTree:
    """The files git tracks at two commits, the head's empty ones, and the renames between them; the verdict and the
    door read the same. A rename list git finished with anything on its standard error may miss renames (past `-l1000`
    git skips its search and only warns), so it cannot be judged. Only names and sizes are read."""
    _require_safe_ref(base, "base")
    _require_safe_ref(head, "head")
    in_repository = ["git", "-C", str(repo.resolve())]
    base_paths, _ = _tracked_at(in_repository, base, env, bound, words)
    head_paths, head_empty = _tracked_at(in_repository, head, env, bound, words)
    words_of = {"base": base, "head": head}
    listed = _run_git([*in_repository, "diff", *checkout.RENAME_LISTING, "--end-of-options", base, head, "--"], env, bound,
                      words.unrenamed.format(**words_of))
    if listed.stderr:
        raise CannotJudge(words.warned.format(**words_of))
    try:
        renames = renames_of(listed.stdout)
    except CannotJudge:
        raise CannotJudge(words.unrenamed.format(**words_of)) from None
    return architecture.PullTree(base_paths, head_paths, head_empty, renames)


def _tracked_at(
    in_repository: list[str], commit: str, env: dict[str, str] | None, bound: float | None, words: TreeWords
) -> tuple[frozenset[str], frozenset[str]]:
    """The blob paths of a commit and those of size 0, decoded as `renames_of` decodes, made total."""
    listed = _run_git([*in_repository, "ls-tree", "-r", "-l", "-z", "--full-tree", "--end-of-options", commit], env, bound,
                      words.unlisted.format(commit=commit))
    paths: set[str] = set()
    empty: set[str] = set()
    for record in listed.stdout.split(b"\0")[:-1]:
        meta, _, raw_path = record.partition(b"\t")
        _, kind, _, size = meta.split()
        if kind != b"blob":
            continue
        path = raw_path.decode("utf-8", errors="surrogateescape")
        paths.add(path)
        if size == b"0":
            empty.add(path)
    return frozenset(paths), frozenset(empty)


def _run_git(command: list[str], env: dict[str, str] | None, bound: float | None, failure: str):
    """A git run that finished with exit 0, its standard error kept; a run that fails is `failure`, git's words never."""
    try:
        done = process.run(command, env=env, encoding=None) if bound is None else process.run_bounded_in_own_group(command, bound, env, None)
    except process.ProcessError:
        raise CannotJudge(failure) from None
    if done.returncode != 0:
        raise CannotJudge(failure)
    return done


def gh_configuration_directory(environment: Mapping[str, str]) -> str:
    """Where gh looks for its login: `GH_CONFIG_DIR`, else `$XDG_CONFIG_HOME/gh`, else `~/.config/gh`."""
    if environment.get("GH_CONFIG_DIR"):
        return environment["GH_CONFIG_DIR"]
    if environment.get("XDG_CONFIG_HOME"):
        return str(Path(environment["XDG_CONFIG_HOME"]) / "gh")
    return str(Path(environment.get("HOME", "~")).expanduser() / ".config" / "gh")


def fetch_environment(environment: Mapping[str, str], empty_home: Path) -> dict[str, str]:
    """The environment of git around the fetch: no inherited git routing or configuration, an empty home so that
    `.netrc` and every other file git reads there is absent, and no way to prompt for a password. gh is the only source
    of credentials, and it keeps its login by being told where the real configuration directory is."""
    isolated = process.attribute_free_git_environment(empty_home)
    kept = {name: value for name, value in isolated.items() if name != "SSH_ASKPASS"}
    return kept | {"GIT_TERMINAL_PROMPT": "0", "GH_CONFIG_DIR": gh_configuration_directory(environment)}


@contextmanager
def _fetched_pull(remote: str, base_sha: str, merge_sha: str, environment: Mapping[str, str]) -> Iterator[tuple[Path, dict[str, str]]]:
    """A bare repository holding the pull request's two commits, and the isolated environment git runs in, removed
    afterwards. Credentials stay in gh's credential protocol; git's own words never reach the operator."""
    with process.scratch_directory() as scratch:
        isolated = fetch_environment(environment, scratch / "home")
        repository = scratch / "repository"
        commands = (
            ["git", "init", "--bare", "--quiet", str(repository)],
            [
                "git", "-c", "credential.helper=", "-c", f"credential.helper={CREDENTIAL_HELPER}", "-c", "core.askPass=",
                "-C", str(repository), "fetch", "--quiet", "--depth=1", "--no-tags", "--", remote, base_sha, merge_sha,
            ],
        )
        for command in commands:
            try:
                done = process.run_bounded_in_own_group(command, FETCH_TIMEOUT_SECONDS, isolated)
            except process.ProcessError as error:
                raise CannotJudge(f"cannot fetch the pull request's commits to compare renamed files ({error})") from None
            if done.returncode != 0:
                raise CannotJudge(
                    f"cannot fetch the pull request's commits to compare renamed files (git exited {done.returncode})"
                )
        yield repository, isolated


def read_renames_of_pull(remote: str, base_sha: str, merge_sha: str, environment: Mapping[str, str]) -> Renames:
    """The renames the verdict reads at the checkout, from the pull request's two commits fetched into a bare repository."""
    with _fetched_pull(remote, base_sha, merge_sha, environment) as (repository, isolated):
        try:
            return read_renames(repository, base_sha, merge_sha, isolated, bound=FETCH_TIMEOUT_SECONDS)
        except CannotJudge:
            raise CannotJudge("cannot list the files git renamed between the pull request's commits") from None


def read_pull_tree_of_pull(remote: str, base_sha: str, merge_sha: str, environment: Mapping[str, str]) -> architecture.PullTree:
    """The tree the verdict reads at the checkout, from the same fetched commits, in the door's words."""
    with _fetched_pull(remote, base_sha, merge_sha, environment) as (repository, isolated):
        return read_pull_tree(repository, base_sha, merge_sha, isolated, FETCH_TIMEOUT_SECONDS, DOOR_WORDS)


def side_of(config: str | None, baseline: str | None, where: str, import_configs: Mapping[str, str | None] = MappingProxyType({})) -> Side:
    """One side from its file texts, None where the side lacks the file; an unreadable file names `where`.

    `import_configs` holds the text of each import-linter config file the side may have."""
    try:
        settings = NO_BASE_SETTINGS if config is None else load_gate_settings(config)
    except ConfigError as error:
        raise UnusableConfig(where, error) from error
    try:
        loaded = None if baseline is None else load_baseline(baseline)
    except SizeError as error:
        raise _unusable(BASELINE_PATH, where, error) from error
    return Side(settings, loaded, _contracts_at(import_configs, where))


def _contracts_at(texts: Mapping[str, str | None], where: str) -> architecture.SideContracts:
    try:
        return architecture.contracts_of({name: text for name, text in texts.items() if text is not None})
    except architecture.ContractError as error:
        raise _unusable(IMPORT_CONFIG, where, error) from error


def _unusable(name: str, where: str, reason: object, next_steps: tuple[Step, ...] = ()) -> CannotJudge:
    return CannotJudge(f"{name} at {where}: {reason}", next_steps)
