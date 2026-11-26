import datetime
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from quality import config_entries, repository
from quality.config_allowances import (
    COMPOSE_ALLOWANCE_EXAMPLE,
    LAYOUT_ALLOWANCE_EXAMPLE,
    PYTHON_ALLOWANCE_EXAMPLE,
    ROOT_ALLOWANCE_EXAMPLE,
    WORKFLOW_ALLOWANCE_EXAMPLE,
    ComposeAllowance,
    ConfigSide,
    LayoutEntry,
    PythonAllowance,
    RootEntry,
    WorkflowAllowance,
    compose_allowances,
    layout_allowances,
    python_allowances,
    root_allowances,
    workflow_allowances,
)

SECRETS_KEYS = {"allow"}
SECRETS_ALLOW = "[secrets] allow"
ALLOWANCE_KEYS = {"rule", "file", "secret_sha256", "reason"}
DECLARATION_KEYS = {"what", "item", "date", "reason"}
GENERATED_FILE_KEYS = {"path", "job", "item", "date", "reason"}
FOREIGN_PREFIX_KEYS = {"prefix", "owner"}
IMPLEMENTED_DOCUMENT_KEYS = {"path", "item", "date", "reason"}
ADVISORY_ALLOWANCE_KEYS = {"id", "lock", "until", "date", "item", "reason"}
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
# The part of a spec ID before its last number; `quality.spec_citations` builds the ID from it.
ID_PREFIX = r"[A-Z][A-Z0-9]*(?:-[A-Z][A-Z0-9]*)*"
ID_PREFIX_PATTERN = re.compile(ID_PREFIX)
MARKDOWN_SUFFIX = ".md"
GATE_HEADING = "[gate]"
LOOSENED_HEADING = "[[gate.loosened]]"
SECRETS_ALLOWANCE_HEADING = "[[secrets.allow]]"
ADVISORY_ALLOWANCE_HEADING = "[[advisories.allow]]"
GENERATED_FILE_HEADING = "[[size.generated]]"
FOREIGN_PREFIX_HEADING = "[[spec_citations.foreign]]"
IMPLEMENTED_DOCUMENT_HEADING = "[[spec_citations.implemented]]"
LANGUAGES_HEADING = "[languages]"
EXAMPLE: config_entries.ExampleLines = (GATE_HEADING, 'mandatory_jobs = ["checks"]')
LEGACY_EXAMPLE: config_entries.ExampleLines = (GATE_HEADING, 'legacy = "fail"')
LANGUAGES_EXAMPLE: config_entries.ExampleLines = (LANGUAGES_HEADING, 'render_pass = "render"')
SONAR_EXAMPLE: config_entries.ExampleLines = (GATE_HEADING, "sonar = false")
SONAR_OFF_EXAMPLE: config_entries.ExampleLines = (GATE_HEADING, 'legacy = "report"', "sonar = false")
ALLOWANCE_EXAMPLE: config_entries.ExampleLines = (
    SECRETS_ALLOWANCE_HEADING,
    'rule = "aws-access-token"',
    'file = "tests/fixture.py"',
    'secret_sha256 = "<64 lowercase hex>"',
    'reason = "fixture value, not a key"',
)
LOOSENED_EXAMPLE: config_entries.ExampleLines = (
    LOOSENED_HEADING,
    "what = 'mandatory job \"Acceptance evidence (integration)\" removed'",
    'item = "overnightworks/songmaker#1345"',
    "date = 2026-10-20",
    'reason = "the operator ruled the requirement shelf machinery out"',
)
ADVISORY_ALLOWANCE_EXAMPLE: config_entries.ExampleLines = (
    ADVISORY_ALLOWANCE_HEADING,
    'id = "GHSA-462x-pgg6-55fp"',
    'lock = "speech/providers/qwen/uv.lock"',
    "until = 2027-01-31",
    "date = 2026-10-21",
    'item = "overnightworks/agent-presentator#371"',
    'reason = "gradio caps pillow<13; no image input"',
)
GENERATED_FILE_EXAMPLE: config_entries.ExampleLines = (
    GENERATED_FILE_HEADING,
    'path = "frontend/src/lib/api/types.ts"',
    'job = "Backend (Python 3.14)"',
    'item = "overnightworks/songmaker#1784"',
    "date = 2026-10-30",
    'reason = "written by scripts/generate_types.py, proven by its --check"',
)
FOREIGN_PREFIX_EXAMPLE: config_entries.ExampleLines = (
    FOREIGN_PREFIX_HEADING,
    'prefix = "LANG"',
    'owner = "overnightworks/design"',
)
IMPLEMENTED_DOCUMENT_EXAMPLE: config_entries.ExampleLines = (
    IMPLEMENTED_DOCUMENT_HEADING,
    'path = "docs/calling.md"',
    'item = "overnightworks/agents#312"',
    "date = 2026-11-23",
    'reason = "the implementers cite its rules, so no test of this repository does"',
)


class TableShape(NamedTuple):
    """One table of the config: its keys as `[table] key` scalars or `[[table.key]]` arrays of tables, and the example of
    a refusal in it; `own_reader` is set where a reader of its own refuses an unknown key."""

    scalars: tuple[str, ...]
    arrays: tuple[str, ...]
    example: config_entries.ExampleLines
    own_reader: bool = False

    @property
    def keys(self) -> set[str]:
        return {*self.scalars, *self.arrays}

    def headings(self, table: str) -> list[str]:
        return [*(f"[{table}] {key}" for key in self.scalars), *(f"[[{table}.{key}]]" for key in self.arrays)]


# Every table of the config, the one place that holds its keys.
TABLE_SHAPES: dict[str, TableShape] = {
    "gate": TableShape(("mandatory_jobs", "legacy", "sonar"), ("loosened",), EXAMPLE),
    "secrets": TableShape(("allow",), (), ALLOWANCE_EXAMPLE, own_reader=True),
    "advisories": TableShape((), ("allow",), ADVISORY_ALLOWANCE_EXAMPLE),
    "root": TableShape((), ("allow",), ROOT_ALLOWANCE_EXAMPLE),
    "layout": TableShape((), ("allow",), LAYOUT_ALLOWANCE_EXAMPLE),
    "python": TableShape((), ("allow",), PYTHON_ALLOWANCE_EXAMPLE),
    "compose": TableShape((), ("allow",), COMPOSE_ALLOWANCE_EXAMPLE),
    "workflow": TableShape((), ("allow",), WORKFLOW_ALLOWANCE_EXAMPLE),
    "size": TableShape((), ("generated",), GENERATED_FILE_EXAMPLE),
    "spec_citations": TableShape((), ("foreign", "implemented"), FOREIGN_PREFIX_EXAMPLE),
    "languages": TableShape(("render_pass",), (), LANGUAGES_EXAMPLE),
}
UNKNOWN_KEY_WITHOUT_EXAMPLE = {"gate", "advisories"}
_HEADINGS = [heading for name, shape in TABLE_SHAPES.items() for heading in shape.headings(name)]
KNOWN_KEYS = f"only {', '.join(_HEADINGS[:-1])} and {_HEADINGS[-1]} exist"


class LegacyMode(Enum):
    """What the repository's open findings on its default branch do to a pull request."""

    REPORT = "report"
    FAIL = "fail"


@dataclass(frozen=True)
class Declaration:
    """A loosening a pull request declares, with the ruling behind it; `what` is the text the verdict prints for the
    loosening under its `loosenings` row, unescaped."""

    what: str
    item: str
    date: datetime.date
    reason: str

    def ruling(self) -> str:
        """The ruling as every line naming the declaration shows it: `<item> of <date>`."""
        return f"{self.item} of {self.date.isoformat()}"


@dataclass(frozen=True)
class AdvisoryAllowance:
    """An advisory the repository allows in one lock file until `until` (inclusive, in UTC), ruled on `date` in `item`;
    `id` is any id or alias of the advisory's group."""

    id: str
    lock: str
    until: datetime.date
    date: datetime.date
    item: str
    reason: str

    @property
    def key(self) -> tuple[str, str]:
        """One allowance per advisory id and lock file."""
        return self.id, self.lock


@dataclass(frozen=True)
class GeneratedFile:
    """A tracked file the repository declares generated by its exact path: the size rule leaves it out because `job`,
    one of the gate's mandatory jobs, proves its generator, ruled on `date` in `item`."""

    path: str
    job: str
    item: str
    date: datetime.date
    reason: str


@dataclass(frozen=True)
class ForeignPrefix:
    """A spec-ID prefix whose IDs the repository's tests cite and the document of `owner`, another repository, defines."""

    prefix: str
    owner: str


@dataclass(frozen=True)
class ImplementedDocument:
    """A tracked Markdown file, by its exact path, that the repository declares a contract others implement: its IDs need
    no citation here, ruled on `date` in `item`."""

    path: str
    item: str
    date: datetime.date
    reason: str


@dataclass(frozen=True)
class GateSettings:
    """What the gate reads from quality/config.toml: the settings a pull request must not loosen, the loosenings
    it declares, and the advisories and secret values it allows; `sonar` is whether the gate's `sonar` job scans."""

    mandatory_jobs: tuple[str, ...]
    legacy: LegacyMode
    loosened: tuple[Declaration, ...] = ()
    advisory_allowances: tuple[AdvisoryAllowance, ...] = ()
    generated_files: tuple[GeneratedFile, ...] = ()
    implemented_documents: tuple[ImplementedDocument, ...] = ()
    sonar: bool = True
    secret_allowances: tuple[Allowance, ...] = ()
    root_allowances: tuple[RootEntry, ...] = ()
    layout_allowances: tuple[LayoutEntry, ...] = ()


class Allowance(NamedTuple):
    """A secret value in a file, as one rule sees it; the value itself is never stored, only its hash."""

    rule: str
    file: str
    secret_sha256: str


@dataclass(frozen=True)
class AllowLists:
    """What the repository allows or declares, one list per table; the default allows nothing, as a missing config does."""

    secrets: dict[Allowance, str] = field(default_factory=dict)
    advisories: tuple[AdvisoryAllowance, ...] = ()
    root: tuple[RootEntry, ...] = ()
    layout: tuple[LayoutEntry, ...] = ()
    python: tuple[PythonAllowance, ...] = ()
    compose: tuple[ComposeAllowance, ...] = ()
    workflow: tuple[WorkflowAllowance, ...] = ()
    generated: tuple[GeneratedFile, ...] = ()
    foreign_prefixes: tuple[ForeignPrefix, ...] = ()
    implemented_documents: tuple[ImplementedDocument, ...] = ()


@dataclass(frozen=True)
class RenderPass:
    """The job `[languages] render_pass` names (None where the key is absent) and the gate's mandatory jobs, read
    leniently: a malformed `mandatory_jobs` lists none."""

    job: str | None = None
    mandatory_jobs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Declarations:
    """What the check reads from one validated config: the allow lists and the render-pass declaration."""

    allow_lists: AllowLists = field(default_factory=AllowLists)
    render_pass: RenderPass = field(default_factory=RenderPass)


@dataclass(frozen=True)
class _Document:
    """The whole config, validated once: the [gate] table, its declarations, and every allow list."""

    gate: dict
    declarations: tuple[Declaration, ...]
    allow_lists: AllowLists
    render_pass: RenderPass


def secret_allowance_entry(allowance: Allowance) -> config_entries.ExampleLines:
    """The entry that allows `allowance`, as the block the reader takes; the reason is the person's to write."""
    return (
        SECRETS_ALLOWANCE_HEADING,
        f"rule = {config_entries.toml_string(allowance.rule)}",
        f"file = {config_entries.toml_string(allowance.file)}",
        f'secret_sha256 = "{allowance.secret_sha256}"',
    )


def _refuse_unknown_secrets_keys(secrets: dict) -> None:
    unknown = set(secrets) - SECRETS_KEYS
    if unknown:
        raise config_entries.ConfigError(f"{config_entries.CONFIG_PATH} has {config_entries.unknown_keys(unknown)} in [secrets] - only allow exists")


def _refuse_unknown_keys(document: dict, tables: dict[str, dict]) -> None:
    unknown = set(document) - TABLE_SHAPES.keys()
    example = None
    for name, shape in TABLE_SHAPES.items():
        if shape.own_reader:
            continue
        extra = set(tables[name]) - shape.keys
        unknown |= extra
        if extra and name not in UNKNOWN_KEY_WITHOUT_EXAMPLE:
            example = shape.example
    if unknown:
        raise config_entries.ConfigError(f"{config_entries.CONFIG_PATH} has {config_entries.unknown_keys(unknown)} - {KNOWN_KEYS}", example)


def _validated_document(text: str, side: ConfigSide) -> _Document:
    """The one validation of the whole config: one parse, one unknown-key check, and every entry of every table; the base
    side reads a root and a layout entry by its identity only."""
    document = config_entries.parse(text, EXAMPLE)
    tables = {name: config_entries.table_named(document, name, shape.example) for name, shape in TABLE_SHAPES.items()}
    _refuse_unknown_keys(document, tables)
    gate = tables["gate"]
    return _Document(
        gate,
        _declarations(gate),
        AllowLists(
            _parse_allowances(tables["secrets"]),
            _advisory_allowances(tables["advisories"]),
            root_allowances(tables["root"], side),
            layout_allowances(tables["layout"], side),
            python_allowances(tables["python"]),
            compose_allowances(tables["compose"]),
            workflow_allowances(tables["workflow"]),
            _generated_files(tables["size"], gate),
            _foreign_prefixes(tables["spec_citations"]),
            _implemented_documents(tables["spec_citations"]),
        ),
        RenderPass(_render_pass_job(tables["languages"]), _job_names(gate)),
    )


def _render_pass_job(languages: dict) -> str | None:
    """The job named by `[languages] render_pass`, None when the key is absent."""
    job = languages.get("render_pass")
    if job is not None and not (isinstance(job, str) and job.strip()):
        raise config_entries.ConfigError(f"{config_entries.CONFIG_PATH} needs [languages] render_pass as the non-empty name of a job", LANGUAGES_EXAMPLE)
    return job


def _job_names(gate: dict) -> tuple[str, ...]:
    """The [gate] mandatory_jobs when they are a non-empty list of non-empty names, else none."""
    names = gate.get("mandatory_jobs")
    if isinstance(names, list) and all(isinstance(name, str) and name for name in names):
        return tuple(names)
    return ()


def _mandatory_jobs(gate: dict) -> tuple[str, ...]:
    names = _job_names(gate)
    if not names:
        raise config_entries.ConfigError(f"{config_entries.CONFIG_PATH} needs [gate] mandatory_jobs as a non-empty list of job names", EXAMPLE)
    return names


def _legacy_mode(gate: dict) -> LegacyMode:
    """A missing `legacy` key reports."""
    value = gate.get("legacy", LegacyMode.REPORT.value)
    try:
        return LegacyMode(value)
    except ValueError:
        modes = " or ".join(f'"{mode.value}"' for mode in LegacyMode)
        raise config_entries.ConfigError(f"{config_entries.CONFIG_PATH} [gate] legacy must be {modes}", LEGACY_EXAMPLE) from None


def _declarations(gate: dict) -> tuple[Declaration, ...]:
    return config_entries.unique_entries(
        gate, "loosened", LOOSENED_HEADING, LOOSENED_EXAMPLE, _read_declaration, lambda declaration: declaration.what,
        lambda declaration: f"declares what = {config_entries.toml_string(declaration.what)} twice; one entry per loosening",
    )


def _read_declaration(entry: object, position: int) -> Declaration:
    refuse = config_entries.entry_refusal(LOOSENED_HEADING, position, LOOSENED_EXAMPLE)
    if not isinstance(entry, dict) or set(entry) != DECLARATION_KEYS:
        raise refuse("exactly a what, an item, a date and a reason")
    what, item, date, reason = (entry[key] for key in ("what", "item", "date", "reason"))
    if not (isinstance(what, str) and what.strip()):
        raise refuse("a non-empty what")
    config_entries.refuse_unless_ruled(refuse, item, date, reason)
    return Declaration(what, item, date, reason)


def _advisory_allowances(advisories: dict) -> tuple[AdvisoryAllowance, ...]:
    return config_entries.unique_entries(
        advisories, "allow", ADVISORY_ALLOWANCE_HEADING, ADVISORY_ALLOWANCE_EXAMPLE, _read_advisory_allowance,
        lambda allowance: allowance.key,
        lambda allowance: f"allows id = {config_entries.toml_string(allowance.id)} in lock = {config_entries.toml_string(allowance.lock)} twice; "
        "one entry per advisory and lock file",
        example_on_repeat=False,
    )


def _read_advisory_allowance(entry: object, position: int) -> AdvisoryAllowance:
    refuse = config_entries.entry_refusal(ADVISORY_ALLOWANCE_HEADING, position, ADVISORY_ALLOWANCE_EXAMPLE)
    if not isinstance(entry, dict) or set(entry) != ADVISORY_ALLOWANCE_KEYS:
        raise refuse("exactly an id, a lock, an until, a date, an item and a reason")
    identifier, lock, until, date, item, reason = (
        entry[key] for key in ("id", "lock", "until", "date", "item", "reason")
    )
    if not all(config_entries.is_one_line_text(value) for value in (identifier, lock, reason)):
        raise refuse("a non-empty one-line id, lock and reason")
    if not (isinstance(item, str) and config_entries.ITEM_REFERENCE.fullmatch(item)):
        raise refuse(config_entries.ITEM_REFERENCE_EXPECTED)
    if not (config_entries.is_date_without_time(until) and config_entries.is_date_without_time(date)):
        raise refuse("until and date as TOML dates without a time")
    return AdvisoryAllowance(identifier, lock, until, date, item, reason)


def _generated_files(size: dict, gate: dict) -> tuple[GeneratedFile, ...]:
    jobs = _job_names(gate)
    return config_entries.unique_entries(
        size, "generated", GENERATED_FILE_HEADING, GENERATED_FILE_EXAMPLE,
        lambda entry, position: _read_generated_file(entry, position, jobs), lambda generated: generated.path,
        lambda generated: f"declares path = {config_entries.toml_string(generated.path)} twice; one entry per file",
    )


def _read_generated_file(entry: object, position: int, mandatory_jobs: tuple[str, ...]) -> GeneratedFile:
    refuse = config_entries.entry_refusal(GENERATED_FILE_HEADING, position, GENERATED_FILE_EXAMPLE)
    if not isinstance(entry, dict) or set(entry) != GENERATED_FILE_KEYS:
        raise refuse("exactly a path, a job, an item, a date and a reason")
    path, job, item, date, reason = (entry[key] for key in ("path", "job", "item", "date", "reason"))
    if not config_entries.is_exact_file_path(path):
        raise refuse("path as the exact tracked path of one file: no folder, pattern, .. or absolute path")
    if job not in mandatory_jobs:
        raise refuse("job as one of [gate] mandatory_jobs, the job whose generator check proves the file")
    config_entries.refuse_unless_ruled(refuse, item, date, reason)
    return GeneratedFile(path, job, item, date, reason)


def _foreign_prefixes(spec_citations: dict) -> tuple[ForeignPrefix, ...]:
    return config_entries.unique_entries(
        spec_citations, "foreign", FOREIGN_PREFIX_HEADING, FOREIGN_PREFIX_EXAMPLE, _read_foreign_prefix, lambda foreign: foreign.prefix,
        lambda foreign: f"declares prefix = {config_entries.toml_string(foreign.prefix)} twice; one entry per prefix",
    )


def _read_foreign_prefix(entry: object, position: int) -> ForeignPrefix:
    refuse = config_entries.entry_refusal(FOREIGN_PREFIX_HEADING, position, FOREIGN_PREFIX_EXAMPLE)
    if not isinstance(entry, dict) or set(entry) != FOREIGN_PREFIX_KEYS:
        raise refuse("exactly a prefix and an owner")
    prefix, owner = entry["prefix"], entry["owner"]
    if not (isinstance(prefix, str) and ID_PREFIX_PATTERN.fullmatch(prefix)):
        raise refuse("prefix as capital letters and digits, joined by -, like LANG or REQ-AUTH")
    if not (isinstance(owner, str) and config_entries.REPOSITORY_NAME.fullmatch(owner)):
        raise refuse("owner as <owner>/<repo>")
    return ForeignPrefix(prefix, owner)


def _implemented_documents(spec_citations: dict) -> tuple[ImplementedDocument, ...]:
    return config_entries.unique_entries(
        spec_citations, "implemented", IMPLEMENTED_DOCUMENT_HEADING, IMPLEMENTED_DOCUMENT_EXAMPLE, _read_implemented_document,
        lambda document: document.path,
        lambda document: f"declares path = {config_entries.toml_string(document.path)} twice; one entry per document",
    )


def _read_implemented_document(entry: object, position: int) -> ImplementedDocument:
    refuse = config_entries.entry_refusal(IMPLEMENTED_DOCUMENT_HEADING, position, IMPLEMENTED_DOCUMENT_EXAMPLE)
    if not isinstance(entry, dict) or set(entry) != IMPLEMENTED_DOCUMENT_KEYS:
        raise refuse("exactly a path, an item, a date and a reason")
    path, item, date, reason = (entry[key] for key in ("path", "item", "date", "reason"))
    if not config_entries.is_exact_file_path(path) or PurePosixPath(path).suffix != MARKDOWN_SUFFIX:
        raise refuse(f"path as the exact tracked path of one {MARKDOWN_SUFFIX} file: no folder, pattern, .. or absolute path")
    config_entries.refuse_unless_ruled(refuse, item, date, reason)
    return ImplementedDocument(path, item, date, reason)


def _sonar(gate: dict, legacy: LegacyMode) -> bool:
    """A missing `sonar` key scans; legacy findings judged by SonarCloud need it on."""
    value = gate.get("sonar", True)
    if not isinstance(value, bool):
        raise config_entries.ConfigError(f"{config_entries.CONFIG_PATH} [gate] sonar must be true or false", SONAR_EXAMPLE)
    if not value and legacy is LegacyMode.FAIL:
        raise config_entries.ConfigError(
            f'{config_entries.CONFIG_PATH} [gate] legacy = "fail" judges SonarCloud findings, which [gate] sonar = false turns off',
            SONAR_OFF_EXAMPLE,
        )
    return value


def load_gate_settings(text: str | None, side: ConfigSide = ConfigSide.HEAD) -> GateSettings:
    """mandatory_jobs is judged before legacy, so a config wrong in both is refused for mandatory_jobs. A `quality check`
    never reads a base, so the head side is the default."""
    if text is None:
        raise config_entries.ConfigError(f"{config_entries.CONFIG_PATH} is missing", EXAMPLE)
    document = _validated_document(text, side)
    jobs = _mandatory_jobs(document.gate)
    legacy = _legacy_mode(document.gate)
    return GateSettings(
        jobs, legacy, document.declarations,
        document.allow_lists.advisories, document.allow_lists.generated, document.allow_lists.implemented_documents,
        _sonar(document.gate, legacy), tuple(document.allow_lists.secrets),
        document.allow_lists.root, document.allow_lists.layout,
    )


def _read_config_text(repo: Path) -> str | None:
    """The repository's own config text, None when it has none; the one reader of quality/config.toml on disk."""
    try:
        return repository.read_text(repo, config_entries.CONFIG_PATH)
    except repository.RepositoryFileError as error:
        raise config_entries.ConfigError(str(error)) from error


def read_gate_settings(repo: Path) -> GateSettings:
    return load_gate_settings(_read_config_text(repo))


def runs_sonar(repo: Path) -> bool:
    """A repository without the config scans, and the findings step refuses the missing file."""
    text = _read_config_text(repo)
    return True if text is None else load_gate_settings(text).sonar


def load_declarations(text: str | None) -> Declarations:
    """Every list the repository allows or declares and its render-pass declaration; a missing config declares nothing.

    The whole document is validated, as the gate does: the check and the verdict refuse an unknown key and a malformed
    entry alike.
    """
    if text is None:
        return Declarations()
    document = _validated_document(text, ConfigSide.HEAD)
    return Declarations(document.allow_lists, document.render_pass)


def read_declarations(repo: Path) -> Declarations:
    return load_declarations(_read_config_text(repo))


def load_staged_secret_allowances(text: str | None) -> dict[Allowance, str]:
    """The allow list as the commit hook reads it: only [secrets].

    Every other table and key belongs to the gate, which a newer quality may have grown, and must not stop the
    commit hook of an older one.
    """
    if text is None:
        return {}
    return _parse_allowances(config_entries.table_named(config_entries.parse(text, ALLOWANCE_EXAMPLE), "secrets", ALLOWANCE_EXAMPLE))


def _parse_allowances(secrets: dict) -> dict[Allowance, str]:
    _refuse_unknown_secrets_keys(secrets)
    allowances: dict[Allowance, str] = {}
    for position, entry in config_entries.numbered_entries(secrets, "allow", "[secrets] allow", ALLOWANCE_EXAMPLE, "a list"):
        allowance, reason = _read_allowance(entry, position)
        if allowance in allowances:
            raise config_entries.ConfigError(
                f"{config_entries.CONFIG_PATH} [secrets] allow lists rule = {config_entries.toml_string(allowance.rule)} in file = "
                f"{config_entries.toml_string(allowance.file)} twice for one value"
            )
        allowances[allowance] = reason
    return allowances


def _read_allowance(entry: object, position: int) -> tuple[Allowance, str]:
    refuse = config_entries.entry_refusal(SECRETS_ALLOW, position, ALLOWANCE_EXAMPLE)
    if not isinstance(entry, dict) or set(entry) != ALLOWANCE_KEYS:
        raise refuse("exactly a rule, a file, a secret_sha256 and a reason (the old fingerprint form is gone)")
    rule, file, secret_sha256, reason = (entry[key] for key in ("rule", "file", "secret_sha256", "reason"))
    if not all(isinstance(value, str) and value.strip() for value in (rule, file, reason)):
        raise refuse("a non-empty rule, file and reason")
    if not (isinstance(secret_sha256, str) and SHA256_HEX.fullmatch(secret_sha256)):
        raise refuse("secret_sha256 as 64 lowercase hex digits")
    if config_entries.spans_lines(reason):
        raise config_entries.ConfigError(f"{config_entries.CONFIG_PATH} {config_entries.entry_name(SECRETS_ALLOW, position)} needs a one-line reason")
    return Allowance(rule, file, secret_sha256), reason
