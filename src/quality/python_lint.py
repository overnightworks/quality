"""The python-security rule: quality's own pinned ruff reports the `S` codes in every tracked Python file, and quality
alone judges each noqa, so neither the repository's ruff settings nor its noqa comments switch a code off."""

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

from quality import DISTRIBUTION, engines, layout, ratchet
from quality.report import Finding

RULE = "python-security"
SELECTED_CODES = "S"
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


def candidates(modes: Mapping[str, str]) -> list[str]:
    """Every tracked regular `.py` file: no link, and nothing inside a gitlink, which the index never lists."""
    return [path for path, mode in modes.items() if mode in engines.REGULAR_FILE_MODES and path.endswith(PYTHON_SUFFIX)]


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


def check_security(
    root: Path, files: Mapping[str, PythonFile], modes: Mapping[str, str], target: Version, locate_ruff: Callable[[], str]
) -> list[Finding]:
    """The findings ruff reports in `files` that neither a test exemption nor a reasoned noqa clears."""
    if not files:
        return []  # `ruff check --` without a path would lint the working directory
    records = _run_ruff(root, {name: file.path for name, file in files.items()}, target, locate_ruff)
    texts = {name: file.text for name, file in files.items()}
    return judge(records, texts, frozenset(layout.test_import_candidates(modes)))


def judge(records: Sequence[RuffFinding], texts: Mapping[str, str], test_files: frozenset[str]) -> list[Finding]:
    """The records that stay red: an exempt code in a test file is green, and so is one whose noqa_row carries a
    reasoned noqa naming it. Findings are sorted by path, line and code."""
    comments: dict[str, dict[int, str]] = {}
    findings = []
    for record in records:
        if record.path in test_files and record.code in TEST_EXEMPT_CODES:
            continue
        if record.path not in comments:
            comments[record.path] = _comments(record.path, texts[record.path])
        if not _cleared_by_noqa(record.code, comments[record.path].get(record.noqa_line, "")):
            findings.append(_finding(record))
    return sorted(findings, key=lambda finding: (finding.path, finding.line or 0, finding.reason))


def _comments(name: str, text: str) -> dict[int, str]:
    """Each line's comment as Python's tokenizer reads it, so a `#` inside a string is no comment."""
    readline = io.StringIO(text.removeprefix(BYTE_ORDER_MARK), newline=None).readline
    try:
        return {token.start[0]: token.string for token in tokenize.generate_tokens(readline) if token.type == tokenize.COMMENT}
    except (tokenize.TokenError, SyntaxError) as error:
        raise LintError(f"cannot read the comments of {name} ({error})") from error


def _cleared_by_noqa(code: str, comment: str) -> bool:
    """Whether the comment names `code` in a reasoned noqa while every noqa directive in it is reasoned; the reason runs
    up to the next `#`, holds a letter and no comma."""
    directives = [segment for segment in comment.split("#")[1:] if NOQA_DIRECTIVE.match(segment)]
    named = [_reasoned_codes(directive) for directive in directives]
    if not named or None in named:
        return False
    return any(code in codes for codes in named if codes is not None)


def _reasoned_codes(directive: str) -> frozenset[str] | None:
    """The codes a reasoned noqa names, None when the directive is not one."""
    reasoned = REASONED_NOQA.fullmatch(directive)
    if reasoned is None or not any(character.isalpha() for character in reasoned["reason"]):
        return None
    return frozenset(reasoned["codes"].split(", "))


def _finding(record: RuffFinding) -> Finding:
    return Finding(
        RULE, record.path, record.line, f"{record.code} {record.message}",
        f"remove the cause, or where it is deliberate end line {record.noqa_line} with "
        f"`# noqa: {record.code} - <reason>`, a reason with a letter and no comma.",
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
    asked = _ruff(binary, ["--version"], None, engines.VERSION_TIMEOUT_SECONDS)
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
        raise LintError(f"ruff exited {done.returncode}{engines.stderr_tail(done.stderr)}")
    failures = [line for line in done.stderr.splitlines() if LINT_FAILURE in line]
    if failures:
        raise LintError(f"ruff could not lint every file: {failures[0]}")
    return done.stdout


def _ruff(binary: str, arguments: list[str], cwd: Path | None, timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return engines.run([binary, *arguments], cwd=cwd, timeout=timeout)
    except engines.EngineError as error:
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
    if not (isinstance(code, str) and isinstance(message, str) and _is_line(row) and _is_line(noqa_row)):
        raise LintError(f"ruff printed a record quality cannot read in its report on {_name(entry, names)}")
    return RuffFinding(_name(entry, names), code, message, row, noqa_row)


def _is_line(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _name(entry: dict, names: Mapping[str, str]) -> str:
    filename = entry.get("filename")
    if not isinstance(filename, str) or filename not in names:
        raise LintError("ruff reported a file quality did not give it")
    return names[filename]
