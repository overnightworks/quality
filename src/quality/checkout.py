"""The checkout as quality reads it: git's listing of the tracked files and their index modes, what the tree holds of
them, which of them are workflow files, which release their gate calls pin, whether the checkout is quality's own, and
the turning of a refused read into `cannot judge`."""

import os
import re
import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path, PurePosixPath

import yaml

from quality import process, repository
from quality.report import CannotJudge, Command, Instruction, Reason, Step, shell_word

UNSHALLOW_STEP = Command("git fetch --unshallow")
RENAME_LISTING = ("-M50%", "-l1000", "--no-color", "--name-status", "-z")
WORKFLOW_DIRECTORY = PurePosixPath(".github/workflows")
YAML_SUFFIXES = frozenset({".yml", ".yaml"})
ACTION_FILES = frozenset({"action.yml", "action.yaml"})
INDEX_MODE = re.compile(r"[0-7]{6}")
# git's sentence when the search up from the path finds no repository, read in the C locale; a git that words it
# differently leaves the refusal general, which stays true.
NO_REPOSITORY_SENTENCE = b"fatal: not a git repository (or any"
GITHUB = "https://github.com/"
GATE_REPOSITORY = "overnightworks/quality"
GATE_URL = f"{GITHUB}{GATE_REPOSITORY}"
GATE_FILE = "gate.yml"
CODEQL_FILE = "codeql.yml"
LOCAL_PREFIXES = ("./", "$/")
RELEASE_TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
RELEASE_COMMENT = re.compile(rf"\s+# ?(?P<release>{RELEASE_TAG.pattern})\s*")
QUALITY_REMOTE = re.compile(r"(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)(?P<name>[^/]+/[^/]+)", re.IGNORECASE)
BLOCK_STYLES = frozenset({"|", ">"})
LINE_BREAKS = re.compile("[\r\n\x85\u2028\u2029]")
NOT_PINNED_STEP = Instruction("pin the gate as docs/gate.md shows: @<commit sha> # v<tag>")


class OutsideWorkTree(CannotJudge):
    """The path lies in no work tree: git says so, or the path is a bare repository or the inside of `.git`."""

    def __init__(self, path: Path, fact: str, next_steps: tuple[Step, ...]) -> None:
        super().__init__(fact, next_steps)
        self.path = path


def _refusal_reason(path: Path, because: str) -> tuple[str, tuple[Step, ...]]:
    return f"{path} is not a readable git repository: {because}", (Command(f"git -C {shell_word(str(path))} status"),)


def _refusal(path: Path, because: str) -> CannotJudge:
    return CannotJudge(*_refusal_reason(path, because))


def _refusal_without_git(path: Path, what: str, reason: object) -> CannotJudge:
    return CannotJudge(f"{path} is not a readable git repository: {what} ({reason})")


def _git_listing(path: Path, command: list[str], answers: frozenset[int] = frozenset({0})) -> subprocess.CompletedProcess:
    """git's answer for the repository at `path`, asked in the C locale so its words read the same everywhere; one that
    refuses with an exit outside `answers`, or does not run, is a repository git cannot read."""
    try:
        done = process.run(["git", "-C", str(path.absolute()), *command], env={**os.environ, "LC_ALL": "C"}, encoding=None)
    except OSError as error:
        raise _refusal_without_git(path, "the working directory cannot be read", repository.os_error_reason(error)) from error
    except process.ProcessError as error:
        reason = repository.os_error_reason(error.__cause__) if isinstance(error.__cause__, OSError) else error
        raise _refusal_without_git(path, "git did not run", reason) from error
    if done.returncode not in answers:
        # git's own words name the checkout it resolved, so only the exit code is printed and git is asked again.
        because = f"git {command[0]} exited {done.returncode}"
        if done.stderr.startswith(NO_REPOSITORY_SENTENCE):
            raise OutsideWorkTree(path, *_refusal_reason(path, because))
        raise _refusal(path, because)
    return done


def work_tree_top(path: Path) -> Path:
    """The top of the work tree `path` lies in, as `path` reaches it; `--show-cdup` never prints where the checkout lies."""
    answer = _git_listing(path, ["rev-parse", "--is-inside-work-tree", "--show-cdup"]).stdout
    inside, _, cdup = os.fsdecode(answer).strip().partition("\n")
    if inside != "true":
        raise OutsideWorkTree(path, *_refusal_reason(path, "it lies outside any work tree"))
    return path / cdup


def tracked_files(root: Path) -> list[str]:
    listing = _git_listing(root, ["ls-files", "-z", "--"])
    return [os.fsdecode(name) for name in listing.stdout.split(b"\0") if name]


def require_unchanged_tracked_files(root: Path, step: Step) -> None:
    """Refuses a work tree whose tracked files differ from `HEAD` in content or mode, deletions included; untracked and
    ignored files are not asked, and a file rewritten with its own bytes is unchanged."""
    listing = _git_listing(root, ["diff", "--name-only", "--no-renames", "-z", "HEAD", "--"])
    changed = [os.fsdecode(name) for name in listing.stdout.split(b"\0") if name]
    if changed:
        more = f" and {len(changed) - 1} more differ" if len(changed) > 1 else " differs"
        raise CannotJudge(f"{changed[0]}{more} from the checked-out commit", (step,))


def index_modes(root: Path) -> dict[str, str]:
    """Each tracked path's index mode; the index is the only source of a mode, and a link is never followed."""
    listing = _git_listing(root, ["ls-files", "-z", "-s", "--"])
    modes: dict[str, str] = {}
    for record in listing.stdout.split(b"\0"):
        if not record:
            continue
        fields, tab, name = os.fsdecode(record).partition("\t")
        parts = fields.split(" ")
        if not tab or len(parts) != 3 or not INDEX_MODE.fullmatch(parts[0]):
            raise CannotJudge(f"git ls-files -s printed an index record quality cannot read in {root}")
        modes[name] = parts[0]
    return modes


def blobs_of(root: Path, name: str) -> tuple[str, ...]:
    """Each version of `name` any commit HEAD reaches recorded, as blob ids; empty where none did or no commit exists."""
    command = ["log", "--format=", "--raw", "--root", "--no-abbrev", "--full-history", "--diff-merges=separate", "--ignore-missing", "HEAD", "--", name]
    lines = _git_listing(root, command).stdout.splitlines()
    blobs = dict.fromkeys(line.split(b" ")[3].decode() for line in lines if line.startswith(b":"))
    return tuple(blob for blob in blobs if blob.strip("0"))


def blob(root: Path, revision: str) -> bytes:
    return _git_listing(root, ["cat-file", "blob", revision]).stdout


@dataclass(frozen=True)
class Change:
    """A commit that changed a file; a root commit and the edge of a shallow clone have no parent git holds."""

    commit: str
    has_parent: bool


def last_change(root: Path, name: str) -> Change | None:
    """The newest commit HEAD reaches that changed `name`, None where none did or no commit exists; `rev-list` reads no
    `log.*` setting."""
    command = ["rev-list", "-1", "--no-commit-header", "--format=%H %P", "--ignore-missing", "HEAD", "--", name]
    fields = _git_listing(root, command).stdout.split()
    return Change(fields[0].decode(), len(fields) > 1) if fields else None


def renamed_since(root: Path, commit: str) -> bytes:
    """git's renames from `commit` to the index, what the next commit records."""
    return _git_listing(root, ["diff", "--cached", *RENAME_LISTING, commit, "--"]).stdout


def is_shallow(root: Path) -> bool:
    return _git_listing(root, ["rev-parse", "--is-shallow-repository"]).stdout.strip() == b"true"


def judged[T](ask: Callable[[Path, str], T], root: Path, name: str) -> T:
    """Asks the repository reader; a file it cannot use cannot be judged."""
    try:
        return ask(root, name)
    except repository.RepositoryFileError as error:
        raise CannotJudge(str(error)) from error


def on_disk(root: Path, names: Sequence[str]) -> list[str]:
    """The tracked names the tree holds: a file deleted, sparse or linked to nowhere is gone, one the filesystem refuses is not."""
    return [name for name in names if judged(repository.is_on_disk, root, name)]


def read_existing[T](read: Callable[[Path, str], T | None], root: Path, names: Sequence[str]) -> dict[str, T]:
    """The content of each name the tree holds; a name that vanished since it was listed is gone."""
    return {name: content for name in names if (content := judged(read, root, name)) is not None}


def is_workflow(path: PurePosixPath) -> bool:
    return path.parent == WORKFLOW_DIRECTORY and path.suffix in YAML_SUFFIXES


def is_action(path: PurePosixPath) -> bool:
    return path.name in ACTION_FILES


ReleaseTags = dict[str, str]


def release_tags_listed(listing: str) -> ReleaseTags:
    """A peeled line (`refs/tags/v1.0.0^{}`) names the commit and wins over the tag object's own line."""
    tags: ReleaseTags = {}
    peeled: ReleaseTags = {}
    for line in listing.splitlines():
        commit, _, reference = line.partition("\t")
        tag = reference.removeprefix("refs/tags/")
        target = peeled if tag.endswith("^{}") else tags
        target[tag.removesuffix("^{}")] = commit
    return {tag: commit for tag, commit in {**tags, **peeled}.items() if RELEASE_TAG.fullmatch(tag)}


def release_order(tag: str) -> tuple[int, ...]:
    match = RELEASE_TAG.fullmatch(tag)
    return tuple(int(number) for number in match.groups()) if match else ()


@dataclass(frozen=True)
class Call:
    """One job-level `uses:` of a reusable workflow: the ref after `@`, None for the repository's own file; the
    workflow file it stands in; and the release the comment after it on its line names, None where none does."""

    ref: str | None
    workflow: str
    release: str | None


@dataclass(frozen=True)
class Workflows:
    """What a clone's workflow files say about the gate and CodeQL, read as data."""

    gate: tuple[Call, ...]
    codeql: tuple[Call, ...]
    unreadable: tuple[str, ...]
    absent: tuple[str, ...]


def commit_of(ref: str) -> str | None:
    """The ref lowercased when it has the shape of a full commit, else None."""
    commit = ref.lower()
    return commit if COMMIT_SHA.fullmatch(commit) else None


class Unread(Enum):
    """Why a listed workflow has no text: gone from the tree since it was listed, or refused by the reader."""

    ABSENT = auto()
    REFUSED = auto()


def workflow_texts(clone: Path, tracked: list[str]) -> dict[str, str | Unread]:
    """Each tracked workflow file's text, in the listing's order."""
    texts: dict[str, str | Unread] = {}
    for name in tracked:
        if not is_workflow(PurePosixPath(name)):
            continue
        try:
            text = repository.read_text(clone, name)
        except repository.RepositoryFileError:
            texts[name] = Unread.REFUSED
            continue
        texts[name] = Unread.ABSENT if text is None else text
    return texts


def workflows_of(texts: Mapping[str, str | Unread]) -> Workflows:
    """Parses each workflow text as data, never running anything, and keeps its job-level reusable calls with the
    release each call's line names."""
    gate: list[Call] = []
    codeql: list[Call] = []
    unreadable: list[str] = []
    absent: list[str] = []
    for name, text in texts.items():
        if text is Unread.ABSENT:
            absent.append(name)
            continue
        if text is Unread.REFUSED:
            unreadable.append(name)
            continue
        try:
            # BaseLoader keeps scalars strings, so no conversion can fail; ValueError is the scanner's chr() on a \U escape past U+10FFFF
            document = _composed(text)
        except (yaml.YAMLError, RecursionError, ValueError):
            unreadable.append(name)
            continue
        for uses in job_calls(document):
            release = _release_after(text, uses)
            gate.extend(calls_of(uses.value, GATE_FILE, name, release))
            codeql.extend(calls_of(uses.value, CODEQL_FILE, name, release))
    return Workflows(tuple(gate), tuple(codeql), tuple(unreadable), tuple(absent))


def read_workflows(clone: Path, tracked: list[str]) -> Workflows:
    return workflows_of(workflow_texts(clone, tracked))


class _AliasMarkingLoader(yaml.BaseLoader):
    """A loader whose alias to a scalar is a node of its own, standing where the alias stands, so a comment is read
    after the alias and never after its anchor."""

    def compose_node(self, parent: yaml.Node | None, index: object) -> yaml.Node | None:
        alias = self.peek_event() if self.check_event(yaml.AliasEvent) else None
        node = super().compose_node(parent, index)
        if alias is not None and isinstance(node, yaml.ScalarNode):
            return yaml.ScalarNode(node.tag, node.value, alias.start_mark, alias.end_mark, style="")
        return node


def _composed(text: str) -> yaml.Node | None:
    """The document's node tree, constructed once so a document the loader refuses is refused here too."""
    loader = _AliasMarkingLoader(text)
    try:
        node = loader.get_single_node()
        if node is not None:
            loader.construct_document(node)
        return node
    finally:
        loader.dispose()


def _mapping(node: object) -> dict[str, yaml.Node]:
    """A mapping node's values by their scalar keys, the last of a repeated key winning, as the loader's dict keeps it."""
    if not isinstance(node, yaml.MappingNode):
        return {}
    return {key.value: value for key, value in node.value if isinstance(key, yaml.ScalarNode)}


def job_calls(document: object) -> Iterator[yaml.ScalarNode]:
    for job in _mapping(_mapping(document).get("jobs")).values():
        uses = _mapping(job).get("uses")
        if isinstance(uses, yaml.ScalarNode):
            yield uses


def _release_after(text: str, uses: yaml.ScalarNode) -> str | None:
    """The release the comment after the `uses:` value names, on the line the value ends on and with nothing after it;
    a block scalar ends on the line after its text, so none stands after it."""
    if uses.style in BLOCK_STYLES:
        return None
    rest = LINE_BREAKS.split(text[uses.end_mark.index :], maxsplit=1)[0]
    comment = RELEASE_COMMENT.fullmatch(rest)
    return comment["release"] if comment else None


def calls_of(uses: str, workflow_file: str, caller: str, release: str | None) -> list[Call]:
    """The calls `uses`, standing in the workflow file `caller`, makes of quality's reusable `workflow_file`."""
    path = f"{WORKFLOW_DIRECTORY}/{workflow_file}"
    if uses in (f"{prefix}{path}" for prefix in LOCAL_PREFIXES):
        return [Call(None, caller, release)]
    file, _, ref = uses.partition("@")
    owner_and_repository, _, workflow = file.partition(f"/{WORKFLOW_DIRECTORY}/")
    matches = owner_and_repository.lower() == GATE_REPOSITORY and workflow == workflow_file
    return [Call(ref, caller, release)] if matches and ref else []


def _github_repository(url: str) -> str | None:
    """The `owner/name` a remote URL, as configured, names on GitHub in one of git's three forms."""
    named = QUALITY_REMOTE.fullmatch(url.removesuffix("/").removesuffix(".git"))
    return named["name"] if named else None


def github_repositories(root: Path) -> list[str]:
    """The `owner/name` of each configured remote that names a GitHub repository; the URLs read as configured, before
    any `insteadOf`."""
    # exit 1 is git's answer for no matching key: no remote is configured
    listing = _git_listing(root, ["config", "-z", "--get-regexp", r"^remote\..*\.url$"], frozenset({0, 1})).stdout
    urls = (os.fsdecode(record).partition("\n")[2] for record in listing.split(b"\0") if record)
    return [repository for url in urls if (repository := _github_repository(url))]


def is_quality_checkout(root: Path) -> bool:
    """Whether a configured remote names quality's repository."""
    return any(repository.lower() == GATE_REPOSITORY for repository in github_repositories(root))


@dataclass(frozen=True)
class NoGatePin:
    """No tracked workflow calls quality's gate.yml."""


@dataclass(frozen=True)
class OwnGate:
    """quality's own checkout calls its own gate.yml: the checkout judges itself."""


@dataclass(frozen=True)
class GatePin:
    """Every gate call pins this commit, which its line names as this release."""

    commit: str
    release: str
    workflows: tuple[str, ...]


@dataclass(frozen=True)
class UnknownGatePin:
    """The workflows do not say which release the gate runs at; `reason` says why and how to pin."""

    reason: Reason


GateState = NoGatePin | OwnGate | GatePin | UnknownGatePin


def gate_pin(root: Path) -> GateState:
    """What the tracked workflows of the checkout at `root` pin of quality's gate."""
    top = work_tree_top(root)
    return gate_pin_of(workflow_texts(top, tracked_files(top)), is_quality_checkout(top))


def gate_pin_of(texts: Mapping[str, str | Unread], is_quality: bool) -> GateState:
    """What these workflow texts pin of quality's gate; a call of the repository's own gate.yml counts only in quality's
    repository, which `is_quality` says this is."""
    workflows = workflows_of(texts)
    if workflows.unreadable or workflows.absent:
        name = (*workflows.unreadable, *workflows.absent)[0]
        return UnknownGatePin(Reason(f"cannot read {name}, so the gate pin is unknown", (Instruction(f"fix {name}"),)))
    if is_quality and any(call.ref is None for call in workflows.gate):
        return OwnGate()
    pins: dict[tuple[str, str], list[str]] = {}
    for call in workflows.gate:
        if call.ref is None:
            continue
        commit = commit_of(call.ref)
        if commit is None or call.release is None:
            return UnknownGatePin(Reason(f"the gate pin in {call.workflow} is not @<commit sha> # v<tag>", (NOT_PINNED_STEP,)))
        pins.setdefault((commit, call.release), []).append(call.workflow)
    if not pins:
        return NoGatePin()
    ordered = sorted(pins.items(), key=lambda pin: (release_order(pin[0][1]), pin[0][0]))
    (commit, release), workflows_pinning = ordered[0]
    if len(ordered) > 1:
        (_, other), other_workflows = ordered[1]
        fact = f"the workflows pin {release} in {min(workflows_pinning)} and {other} in {min(other_workflows)}"
        return UnknownGatePin(Reason(fact, (Instruction("pin one release in every workflow"),)))
    return GatePin(commit, release, tuple(sorted(set(workflows_pinning))))
