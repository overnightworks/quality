"""What the gate holds at each side of a pull request, read at the head's checkout, at the base commit or from file texts,
and the files git renamed between the two."""

import re
import subprocess
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal, overload

from quality import adapter_imports, architecture, checkout, layout, process, repository
from quality.baseline_file import BASELINE_PATH, Baseline, SizeError, load_baseline, read_baseline
from quality.config import GateSettings, LegacyMode, load_gate_settings, read_gate_settings
from quality.config_allowances import ConfigSide, LayoutAllowance, LayoutEntry, RootAllowance, RootEntry
from quality.config_entries import CONFIG_PATH, ConfigError
from quality.ratchet import NO_RENAMES, Renames, head_step, renames_of
from quality.report import CannotJudge, Step

FETCH_TIMEOUT_SECONDS = 120
NO_BASE_SETTINGS = GateSettings((), LegacyMode.REPORT, sonar=False)
HEAD = "the head"
IMPORT_CONFIG = "import-linter config"


@dataclass(frozen=True)
class Side[RootRecord: RootEntry = RootAllowance, LayoutRecord: LayoutEntry = LayoutAllowance]:
    """What the gate holds at one side of a pull request: its settings, its baseline (None without one) and its import
    contracts; a base is a `Side[RootEntry, LayoutEntry]`, its entries read by identity only."""

    settings: GateSettings[RootRecord, LayoutRecord]
    baseline: Baseline | None
    contracts: architecture.SideContracts = field(default_factory=architecture.SideContracts)


type BaseSide = Side[RootEntry, LayoutEntry]


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


def read_base(repo: Path, base_sha: str) -> BaseSide:
    """The gate's settings, baseline and import-linter configs at the base commit; a file the base lacks reads as absent."""
    where = f"the base commit {base_sha}"
    try:
        texts = {
            path: process.read_blob(repo, base_sha, path, f"cannot read {where}", f"cannot read {path} at {where}")
            for path in (CONFIG_PATH, BASELINE_PATH, *architecture.CONFIG_FILES)
        }
    except process.ProcessError as error:
        raise CannotJudge(str(error)) from error
    return side_of(
        texts[CONFIG_PATH], texts[BASELINE_PATH], where, {name: texts[name] for name in architecture.CONFIG_FILES}, side=ConfigSide.BASE
    )


def _reads_tree(base: BaseSide | None, head: Side) -> bool:
    """Whether the files of the two commits can change the judgement: only a module a contract list lost needs them."""
    return base is not None and bool(architecture.struck_modules(base.contracts, head.contracts))


def pull_files(
    base: BaseSide | None, head: Side, read_tree: Callable[[bool], architecture.PullTree],
    read_renames_listing: Callable[[bool], Renames], renames_can_change_entries: bool,
) -> tuple[architecture.PullTree | None, Renames]:
    """What the pull request's two commits add to the judgement, read by the caller's readers: the tree when a module is
    struck (its renames serve the baselines), else the renames when two baselines are compared or the caller says they can
    change the head's allow entries (`loosenings.renames_can_change`), else nothing. A reader is told whether to read the
    test modules' marks, which only a base that holds `[adapter_imports]` carries."""
    carries_marks = base is not None and base.baseline is not None and base.baseline.adapter_imports is not None
    if _reads_tree(base, head):
        return read_tree(carries_marks), NO_RENAMES
    if compares_baselines(base, head) or renames_can_change_entries:
        return None, read_renames_listing(carries_marks)
    return None, NO_RENAMES


def compares_baselines(base: BaseSide | None, head: Side) -> bool:
    """Whether the renames can change the judgement: only a comparison of two baselines reads them. Any other pair is
    judged without them, so a pull request that renames nothing the baselines name is never held up by the diff."""
    return base is not None and base.baseline is not None and head.baseline is not None


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


def read_renames(
    repo: Path, base: str, head: str, env: dict[str, str] | None = None, bound: float | None = None, marks: bool = False,
    words: TreeWords = CHECKOUT_WORDS,
) -> Renames:
    """The files git renamed between two commits, with the test modules that carry the registered mark at the head when
    `marks`; the verdict and the door read the same pair.

    A `bound` in seconds runs git in its own process group, for the door inside its scratch directory."""
    try:
        listed = process.git(
            ["-C", str(repo.resolve()), "diff", *checkout.RENAME_LISTING, "--end-of-options", base, head, "--"],
            words.unrenamed.format(base=base, head=head),
            env,
            bound,
            encoding=None,
        )
    except process.ProcessError as error:
        raise CannotJudge(str(error)) from error
    renames = renames_of(listed)
    if not marks:
        return renames
    head_paths, _ = _tracked_at(["git", "-C", str(repo.resolve())], head, env, bound, words)
    return _marked_at(renames, repo, head, env, head_paths)


def _marked_at(renames: Renames, repo: Path, commit: str, env: dict[str, str] | None, tracked: Iterable[str]) -> Renames:
    """The renames with the test modules that carry the registered mark in the commit's blobs, never the working tree;
    `tracked` is the commit's paths, which say each module's project. A blob that cannot be read has no mark, so its
    module carries no count and the verdict shows the raise."""

    def read(path: str) -> str | None:
        try:
            return process.read_blob(repo, commit, path, f"cannot list {path} at {commit}", f"cannot read {path} at {commit}", env)
        except process.ProcessError:
            return None

    return adapter_imports.with_marks(renames, read, layout.test_project_files(tracked))


def read_pull_tree(
    repo: Path, base: str, head: str, env: dict[str, str] | None = None, bound: float | None = None,
    words: TreeWords = CHECKOUT_WORDS, marks: bool = False,
) -> architecture.PullTree:
    """The files git tracks at two commits, the head's empty ones, and the renames between them; the verdict and the
    door read the same. A rename list git finished with anything on its standard error may miss renames (past `-l1000`
    git skips its search and only warns), so it cannot be judged. Only names and sizes are read."""
    in_repository = ["git", "-C", str(repo.resolve())]
    base_paths, _ = _tracked_at(in_repository, base, env, bound, words)
    head_paths, head_empty = _tracked_at(in_repository, head, env, bound, words)
    words_of = {"base": base, "head": head}
    listed = _run_git([*in_repository, "diff", *checkout.RENAME_LISTING, "--end-of-options", base, head, "--"], env, bound,
                      words.unrenamed.format(**words_of))
    if listed.stderr:
        raise CannotJudge(words.warned.format(**words_of))
    renames = renames_of(listed.stdout)
    return architecture.PullTree(base_paths, head_paths, head_empty, _marked_at(renames, repo, head, env, head_paths) if marks else renames)


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


def _run_git(command: list[str], env: dict[str, str] | None, bound: float | None, failure: str) -> subprocess.CompletedProcess:
    """A git run that finished with exit 0, its standard error kept; a run that fails is `failure`, git's words never."""
    try:
        done = process.run(command, env=env, encoding=None) if bound is None else process.run_bounded_in_own_group(command, bound, env, None)
    except process.ProcessError:
        raise CannotJudge(failure) from None
    if done.returncode != 0:
        raise CannotJudge(failure)
    return done


@contextmanager
def _fetched_pull(remote: str, base_sha: str, merge_sha: str, access: process.ThroughHelper) -> Iterator[tuple[Path, dict[str, str]]]:
    """A bare repository holding the pull request's two commits, and the isolated environment git runs in, removed
    afterwards. Credentials stay in the helper's credential protocol; git's own words never reach the operator."""
    with process.scratch_directory() as scratch:
        network = process.network_git(access, scratch)
        repository = scratch / "repository"
        commands = (
            ["git", "init", "--bare", "--quiet", str(repository)],
            network.command("-C", str(repository), "fetch", "--quiet", "--depth=1", "--no-tags", "--", remote, base_sha, merge_sha),
        )
        for command in commands:
            try:
                done = process.run_bounded_in_own_group(command, FETCH_TIMEOUT_SECONDS, network.environment)
            except process.ProcessError as error:
                raise CannotJudge(f"cannot fetch the pull request's commits to compare renamed files ({error})") from None
            if done.returncode != 0:
                raise CannotJudge(
                    f"cannot fetch the pull request's commits to compare renamed files (git exited {done.returncode})"
                )
        yield repository, network.environment


def read_renames_of_pull(
    remote: str, base_sha: str, merge_sha: str, access: process.ThroughHelper, marks: bool = False
) -> Renames:
    """The renames the verdict reads at the checkout, from the pull request's two commits fetched into a bare repository."""
    with _fetched_pull(remote, base_sha, merge_sha, access) as (repository, isolated):
        try:
            return read_renames(repository, base_sha, merge_sha, isolated, bound=FETCH_TIMEOUT_SECONDS, marks=marks, words=DOOR_WORDS)
        except CannotJudge:
            raise CannotJudge(DOOR_WORDS.unrenamed) from None


def read_pull_tree_of_pull(
    remote: str, base_sha: str, merge_sha: str, access: process.ThroughHelper, marks: bool = False
) -> architecture.PullTree:
    """The tree the verdict reads at the checkout, from the same fetched commits, in the door's words."""
    with _fetched_pull(remote, base_sha, merge_sha, access) as (repository, isolated):
        return read_pull_tree(repository, base_sha, merge_sha, isolated, FETCH_TIMEOUT_SECONDS, DOOR_WORDS, marks)


@overload
def side_of(
    config: str | None, baseline: str | None, where: str, import_configs: Mapping[str, str | None] = ..., *, side: Literal[ConfigSide.HEAD]
) -> Side: ...
@overload
def side_of(
    config: str | None, baseline: str | None, where: str, import_configs: Mapping[str, str | None] = ..., *, side: ConfigSide
) -> BaseSide: ...
def side_of(
    config: str | None, baseline: str | None, where: str, import_configs: Mapping[str, str | None] = MappingProxyType({}), *, side: ConfigSide
) -> BaseSide:
    """One side from its file texts, None where the side lacks the file; an unreadable file names `where`. The config is
    read as `side` says, the head strictly and the base by its entries' identity.

    `import_configs` holds the text of each import-linter config file the side may have."""
    try:
        settings = NO_BASE_SETTINGS if config is None else load_gate_settings(config, side=side)
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
