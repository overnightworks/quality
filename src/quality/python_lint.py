"""The python-security and python-print rules: one run of quality's own pinned ruff reports the `S` and `T20` codes in
every tracked Python file, and quality alone judges each noqa and output owner, so neither the repository's ruff
settings nor its noqa comments switch a code off."""

import io
import json
import os
import re
import subprocess
import tokenize
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import requires
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

from quality import DISTRIBUTION, layout, process, ratchet, repository
from quality.report import Finding

SECURITY_RULE = "python-security"
PRINT_RULE = "python-print"
SECURITY_FAMILY = "S"
PRINT_FAMILY = "T20"
SELECTED_CODES = f"{SECURITY_FAMILY},{PRINT_FAMILY}"
# Exempt in a test: an assert is how a test checks, a password there is a fake, and a test fixes its own argv.
TEST_EXEMPT_CODES = frozenset({"S101", "S105", "S106", "S107", "S603", "S607"})
PYTHON_SUFFIX = ".py"
RUFF_DISTRIBUTION = "ruff"
RUN_TIMEOUT_SECONDS = 600
ARGUMENT_BYTES_PER_RUN = 100_000  # far below Linux's ARG_MAX, which the argument list shares with the environment
JUDGED_EXIT_CODES = frozenset({0, 1})
SYNTAX_ERROR = "invalid-syntax"
LINT_FAILURE = "Failed to lint"
NOQA_DIRECTIVE = re.compile(r"\s*noqa\b", re.IGNORECASE)
REASONED_NOQA = re.compile(r" ?noqa: (?P<codes>[A-Z]+\d+(?:, [A-Z]+\d+)*) - (?P<reason>[^,]+)", re.ASCII)
OWNER_PREFIX = "# ruff:"
BYTE_ORDER_MARK = "﻿"


class LintError(Exception):
    """The rule cannot judge; the message names each file repository-relative."""


class UnparsablePython(LintError):
    """ruff cannot parse a tracked Python file."""


@dataclass(frozen=True)
class PythonFile:
    """A candidate as the repository reader resolved it inside the repository, and its UTF-8 text."""

    path: Path
    text: str


@dataclass(frozen=True)
class RuffFinding:
    """One finding of ruff's report: its file repository-relative, and the line its noqa must stand on."""

    path: str
    code: str
    message: str
    line: int
    noqa_line: int


@dataclass(frozen=True)
class Lint:
    """One ruff run: its findings, the text of every judged file, and which of them are test files."""

    records: tuple[RuffFinding, ...]
    texts: Mapping[str, str]
    test_files: frozenset[str]


@dataclass(frozen=True)
class ReasonedNoqa:
    codes: frozenset[str]
    reason: str


@dataclass(frozen=True)
class OutputOwner:
    """A file-level `# ruff: noqa: <T20 code>[, ...] - <reason>` on its own line."""

    line: int
    noqa: ReasonedNoqa


@dataclass(frozen=True)
class PrintResult:
    """The python-print findings and one report line per honoured output owner."""

    findings: list[Finding]
    reports: tuple[str, ...]


def candidates(modes: Mapping[str, str]) -> list[str]:
    """Every tracked regular `.py` file: no link, and nothing inside a gitlink, which the index never lists."""
    return [path for path, mode in modes.items() if mode in process.REGULAR_FILE_MODES and path.endswith(PYTHON_SUFFIX)]


def read_files(root: Path, modes: Mapping[str, str]) -> dict[str, PythonFile]:
    """Every candidate resolved inside the repository and read the way Python reads it, which must be UTF-8 text; one
    the checkout does not hold cannot be judged, so the rules never certify less."""
    files = {}
    for name in candidates(modes):
        try:
            content = repository.read_bytes(root, name)
            if content is None:
                raise LintError(f"cannot read {name}: the checkout does not hold it")
            files[name] = PythonFile(repository.resolve_file(root, name), source_text(name, content))
        except repository.RepositoryFileError as error:
            raise LintError(str(error)) from error
    return files


def source_text(name: str, content: bytes) -> str:
    """The UTF-8 text ruff reads; a file Python reads differently or cannot read is not judged, since ruff would
    lint other code than Python runs."""
    try:
        python_reads = ratchet.python_text(content)
        ruff_reads = content.decode("utf-8")
    except ratchet.PARSE_ERRORS as error:
        raise LintError(f"cannot read {name}: {error}") from error
    translated = ruff_reads.replace("\r\n", "\n").replace("\r", "\n").removeprefix(BYTE_ORDER_MARK)
    if translated != python_reads:
        raise LintError(f"cannot read {name}: its PEP 263 encoding makes Python read other text than UTF-8")
    return ruff_reads


def lint(root: Path, modes: Mapping[str, str], target: Version, locate_ruff: Callable[[], str]) -> Lint:
    """ruff's one run over every candidate, which both rules judge."""
    files = read_files(root, modes)
    texts = {name: file.text for name, file in files.items()}
    test_files = frozenset(layout.test_import_candidates(modes))
    if not files:
        return Lint((), texts, test_files)  # nothing to lint: skip locating ruff and its version check
    return Lint(tuple(_run_ruff(root, {name: file.path for name, file in files.items()}, target, locate_ruff)), texts, test_files)


def judge_security(lint: Lint) -> list[Finding]:
    """The `S` records that stay red: an exempt code in a test file is green, and so is one whose noqa_row carries a
    reasoned noqa naming it. Findings are sorted by path, line and code."""
    comments: dict[str, dict[int, str]] = {}
    findings = []
    for record in _family(lint, SECURITY_FAMILY):
        if record.path in lint.test_files and record.code in TEST_EXEMPT_CODES:
            continue
        if record.path not in comments:
            comments[record.path] = _line_comments(_comment_tokens(record.path, lint.texts[record.path]))
        if not _cleared_by_noqa(record.code, comments[record.path].get(record.noqa_line, "")):
            findings.append(_security_finding(record))
    return _sorted(findings)


def judge_print(lint: Lint) -> PrintResult:
    """The `T20` records an output owner of their file or a reasoned noqa at noqa_row does not clear, and each owner:
    reported where it clears a finding, a finding where it clears none. Only a file with a print record or an owner
    prefix is tokenized."""
    records: dict[str, list[RuffFinding]] = {}
    for record in _family(lint, PRINT_FAMILY):
        records.setdefault(record.path, []).append(record)
    owning = {name for name, text in lint.texts.items() if OWNER_PREFIX in text}
    findings: list[Finding] = []
    reports: list[str] = []
    for name in sorted(records.keys() | owning):
        file_findings, file_reports = _judge_prints(name, _comment_tokens(name, lint.texts[name]), records.get(name, []))
        findings += file_findings
        reports += file_reports
    return PrintResult(_sorted(findings), tuple(reports))


def _judge_prints(name: str, tokens: Sequence[tokenize.TokenInfo], records: Sequence[RuffFinding]) -> tuple[list[Finding], list[str]]:
    owners = [owner for token in tokens if (owner := _output_owner(token)) is not None]
    owned = {code for owner in owners for code in owner.noqa.codes}
    comments = _line_comments(tokens)
    findings = [
        _print_finding(record)
        for record in records
        if record.code not in owned and not _cleared_by_noqa(record.code, comments.get(record.noqa_line, ""))
    ]
    found = {record.code for record in records}
    reports = [f"{PRINT_RULE}: output owner {name} - {owner.noqa.reason.strip()}" for owner in owners if owner.noqa.codes & found]
    findings += [_stale_owner(name, owner) for owner in owners if not owner.noqa.codes & found]
    return findings, reports


def _family(lint: Lint, family: str) -> list[RuffFinding]:
    return [record for record in lint.records if record.code.startswith(family)]


def _sorted(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda finding: (finding.path, finding.line or 0, finding.reason))


def _comment_tokens(name: str, text: str) -> list[tokenize.TokenInfo]:
    """The comments as Python's tokenizer reads them, so a `#` inside a string is no comment."""
    readline = io.StringIO(text.removeprefix(BYTE_ORDER_MARK), newline=None).readline
    try:
        return [token for token in tokenize.generate_tokens(readline) if token.type == tokenize.COMMENT]
    except (tokenize.TokenError, SyntaxError) as error:
        raise LintError(f"cannot read the comments of {name} ({error})") from error


def _line_comments(tokens: Sequence[tokenize.TokenInfo]) -> dict[int, str]:
    return {token.start[0]: token.string for token in tokens}


def _output_owner(token: tokenize.TokenInfo) -> OutputOwner | None:
    """The owner a comment declares: on its own line, as ruff requires of a file-level directive, the one directive in
    the comment, reasoned, and naming `T20` codes alone."""
    if token.line[: token.start[1]].strip() or not token.string.startswith(OWNER_PREFIX) or "#" in token.string[1:]:
        return None
    noqa = _reasoned_noqa(token.string.removeprefix(OWNER_PREFIX))
    if noqa is None or not all(code.startswith(PRINT_FAMILY) for code in noqa.codes):
        return None
    return OutputOwner(token.start[0], noqa)


def _cleared_by_noqa(code: str, comment: str) -> bool:
    """Whether the comment's one noqa directive is reasoned and names `code`; the reason runs up to the next `#`, holds a
    letter and no comma. ruff reads only the first directive, so a second one, reasoned or bare, keeps the finding red."""
    directives = [segment for segment in comment.split("#")[1:] if NOQA_DIRECTIVE.match(segment)]
    if len(directives) != 1:
        return False
    noqa = _reasoned_noqa(directives[0])
    return noqa is not None and code in noqa.codes


def _reasoned_noqa(directive: str) -> ReasonedNoqa | None:
    """The codes and reason of a reasoned noqa, None when the directive is not one."""
    reasoned = REASONED_NOQA.fullmatch(directive)
    if reasoned is None or not any(character.isalpha() for character in reasoned["reason"]):
        return None
    return ReasonedNoqa(frozenset(reasoned["codes"].split(", ")), reasoned["reason"])


def _security_finding(record: RuffFinding) -> Finding:
    return Finding(
        SECURITY_RULE, record.path, record.line, f"{record.code} {record.message}",
        f"remove the cause, or where it is deliberate end line {record.noqa_line} with "
        f"`# noqa: {record.code} - <reason>`, a reason with a letter and no comma.",
    )


def _print_finding(record: RuffFinding) -> Finding:
    return Finding(
        PRINT_RULE, record.path, record.line, f"{record.code} {record.message}",
        f"write through the program's one output owner; in that owner's file, declare it on a line of its own with "
        f"`# ruff: noqa: {record.code} - <reason>`, a reason with a letter and no comma.",
    )


def _stale_owner(name: str, owner: OutputOwner) -> Finding:
    codes = ", ".join(sorted(owner.noqa.codes))
    return Finding(
        PRINT_RULE, name, owner.line, f"declares an output owner for {codes}, but the file has no such finding.",
        "delete the line.",
    )


def _run_ruff(root: Path, paths: Mapping[str, Path], target: Version, locate_ruff: Callable[[], str]) -> list[RuffFinding]:
    """ruff's findings in `paths` (name to resolved path), in batches the argument list holds."""
    binary = _pinned_ruff(locate_ruff)
    names = _report_names(paths)
    return [
        record
        for batch in _batches(sorted(paths), ARGUMENT_BYTES_PER_RUN)
        for record in read_report(_ruff_report(binary, root, batch, target), names)
    ]


def _report_names(paths: Mapping[str, Path]) -> dict[str, str]:
    """Each resolved path as ruff's report prints it, as UTF-8 with U+FFFD for a byte that is none, to its name; two
    paths printed alike cannot be told apart."""
    names: dict[str, str] = {}
    for name, path in paths.items():
        printed = os.fsencode(path).decode("utf-8", "replace")
        if printed in names:
            raise LintError(f"ruff's report cannot tell {names[printed]} from {name}")
        names[printed] = name
    return names


def _pinned_ruff(locate_ruff: Callable[[], str]) -> str:
    """The ruff binary of quality's own installation, never one on PATH, at the version quality pins."""
    try:
        binary = locate_ruff()
    except FileNotFoundError as error:
        raise LintError("quality's installation holds no ruff binary") from error
    asked = _ruff(binary, ["--version"], None, process.VERSION_TIMEOUT_SECONDS)
    reported = (asked.stdout.split() or [""])[-1] if asked.returncode == 0 else ""
    pin = _ruff_pin()
    if f"=={reported}" != pin:
        raise LintError(f"{binary} reports ruff {reported or 'no version'}, quality pins ruff{pin}")
    return binary


def _ruff_pin() -> str:
    """The specifier of quality's own requirement on ruff, as its installed metadata states it."""
    requirements = (Requirement(line) for line in requires(DISTRIBUTION) or ())
    return next((str(requirement.specifier) for requirement in requirements if requirement.name == RUFF_DISTRIBUTION), "")


def _batches(names: Sequence[str], limit: int) -> Iterator[list[str]]:
    batch: list[str] = []
    size = 0
    for name in names:
        length = len(os.fsencode(name)) + 1
        if batch and size + length > limit:
            yield batch
            batch, size = [], 0
        batch.append(name)
        size += length
    if batch:
        yield batch


def _ruff_report(binary: str, root: Path, names: Sequence[str], target: Version) -> str:
    """ruff's JSON report on `names`, run from the repository's resolved top so every warning names a file as the
    repository does; any file ruff fails to lint makes the run untrustworthy, since ruff then reports nothing for it."""
    arguments = [
        "check", "--isolated", "--no-cache", "--ignore-noqa", "--select", SELECTED_CODES,
        "--target-version", f"py{target.major}{target.minor}", "--output-format", "json", "--", *names,
    ]
    done = _ruff(binary, arguments, Path(os.path.realpath(root)), RUN_TIMEOUT_SECONDS)
    if done.returncode not in JUDGED_EXIT_CODES:
        raise LintError(f"ruff exited {done.returncode}{process.stderr_tail(done.stderr)}")
    failures = [line for line in done.stderr.splitlines() if LINT_FAILURE in line]
    if failures:
        raise LintError(f"ruff could not lint every file: {failures[0]}")
    return done.stdout


def _ruff(binary: str, arguments: list[str], cwd: Path | None, timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return process.run([binary, *arguments], cwd=cwd, timeout=timeout)
    except process.ProcessError as error:
        raise LintError(str(error)) from error


def read_report(report: str, names: Mapping[str, str]) -> list[RuffFinding]:
    """ruff's JSON report, each file named through `names` (ruff's resolved path to the repository-relative name)."""
    try:
        entries = json.loads(report)
    except (ValueError, RecursionError) as error:
        raise LintError(f"ruff printed a report quality cannot read ({error})") from error
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise LintError("ruff printed a report quality cannot read (not a list of records)")
    unparsable = sorted({_name(entry, names) for entry in entries if entry.get("code") == SYNTAX_ERROR})
    if unparsable:
        raise UnparsablePython(f"ruff cannot parse {', '.join(unparsable)}")
    return [_record(entry, names) for entry in entries]


def _record(entry: dict, names: Mapping[str, str]) -> RuffFinding:
    location = entry.get("location")
    row = location.get("row") if isinstance(location, dict) else None
    code, message, noqa_row = entry.get("code"), entry.get("message"), entry.get("noqa_row")
    selected = isinstance(code, str) and code.startswith((SECURITY_FAMILY, PRINT_FAMILY))
    if not (selected and isinstance(message, str) and _is_line(row) and _is_line(noqa_row)):
        raise LintError(f"ruff printed a record quality cannot read in its report on {_name(entry, names)}")
    return RuffFinding(_name(entry, names), code, message, row, noqa_row)


def _is_line(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _name(entry: dict, names: Mapping[str, str]) -> str:
    filename = entry.get("filename")
    if not isinstance(filename, str) or filename not in names:
        raise LintError("ruff reported a file quality did not give it")
    return names[filename]
