"""The SonarCloud job's step before the scan, `quality sonar properties`: the properties and the token are checked and the
scan's arguments reach the step output; it prints its rows and one closing line, or the pair."""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from quality import output, repository
from quality.checkout import require_unchanged_tracked_files, tracked_files
from quality.config import CONFIG_PATH, ConfigError, runs_sonar
from quality.report import FIX_THEN_RERUN, PUSH_THE_FIX, CannotJudge, Instruction, Reason, config_refusal
from quality.sonar import (
    EXIT_CLEAN,
    SCANNER_WORK_DIRECTORY,
    TASK_FILE,
    TOKEN_VARIABLE,
    SonarEvent,
    project_of,
    refused,
    require_token,
)

# Sonar's own sensor drops this rule only when its scanner has indexed a bot config, which a pull-request analysis
# of an unchanged file and a repository that leaves `.github` out of `sonar.sources` do not do. The gate applies
# the same test everywhere, through the scan's arguments.
DEPENDABOT_CONFIG = ".github/dependabot.yml"
DEPENDABOT_EXCEPTION_RULE = "docker:S8431"
IGNORE_CRITERIA_KEY = "sonar.issue.ignore.multicriteria"
IGNORE_CRITERION_PREFIX = "e"
ALL_FILES_PATTERN = "**/*"
ADD_ONLY_STEP = Instruction("let the coverage artifact add report files only, never change a file the commit tracks")
PROPERTIES_FILE = "sonar-project.properties"
COVERAGE_REPORT_KEYS = ("sonar.python.coverage.reportPaths", "sonar.javascript.lcov.reportPaths")
GLOB_CHARACTERS = "*?"
# Ant-style `dir/**` means the files beneath; pathlib's `dir/**` yields directories only.
ANT_TRAILING_ANY = "**"
# Grows only through a quality release. Anything that can steer the scanner's endpoint, proxy, working
# directory or issue handling stays out, because the token travels with every authenticated request.
ALLOWED_PROPERTIES = frozenset(
    {
        "sonar.organization",
        "sonar.projectKey",
        "sonar.projectName",
        "sonar.sources",
        "sonar.tests",
        "sonar.inclusions",
        "sonar.exclusions",
        "sonar.test.inclusions",
        "sonar.test.exclusions",
        "sonar.coverage.exclusions",
        "sonar.cpd.exclusions",
        "sonar.sourceEncoding",
        "sonar.python.version",
        *COVERAGE_REPORT_KEYS,
    }
)
# The scanner expands ${env.NAME} and ${sonar.token} in any value.
SUBSTITUTION_MARKER = "${"
PROPERTY_WHITESPACE = " \t\f"
# Not str.splitlines(): java.util.Properties breaks lines on these three alone.
PROPERTY_LINE_BREAK = re.compile(r"\r\n|\r|\n")
PROPERTY_ESCAPES = {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}


def excluded_rules(dependabot_config_exists: bool) -> frozenset[str]:
    """The rules the scan drops in every file; a rule's file path is no input."""
    return frozenset({DEPENDABOT_EXCEPTION_RULE}) if dependabot_config_exists else frozenset()


def scanner_exclusions(rules: frozenset[str]) -> str:
    """The scan arguments that make the analysis itself drop `rules` in every file; empty for no rule."""
    criteria = {f"{IGNORE_CRITERION_PREFIX}{number}": rule for number, rule in enumerate(sorted(rules), 1)}
    if not criteria:
        return ""
    arguments = [f"{IGNORE_CRITERIA_KEY}={','.join(criteria)}"]
    for criterion, rule in criteria.items():
        arguments += [f"{IGNORE_CRITERIA_KEY}.{criterion}.ruleKey={rule}", f"{IGNORE_CRITERIA_KEY}.{criterion}.resourceKey={ALL_FILES_PATTERN}"]
    return " ".join(f"-D{argument}" for argument in arguments)


def check_properties(root: Path, environment: Mapping[str, str]) -> None:
    """Bind the scan to the caller's own project before the token is handed to the scanner."""
    require_token(environment)
    project = project_of(environment)
    if any(Path(name).parts[0] == SCANNER_WORK_DIRECTORY for name in tracked_files(root)):
        raise CannotJudge(f"{SCANNER_WORK_DIRECTORY}/ is tracked: a committed {TASK_FILE.name} would stand in for this run's analysis")
    properties = read_properties(root)
    for key, value in properties:
        if key not in ALLOWED_PROPERTIES:
            raise CannotJudge(f"{PROPERTIES_FILE} sets {key!r}, which is not allowed")
        if SUBSTITUTION_MARKER in value:
            raise CannotJudge(f"{PROPERTIES_FILE} sets {key!r} to a value with a {SUBSTITUTION_MARKER}...}} substitution")
    chosen = dict(properties)
    for key, expected in (("sonar.organization", project.owner), ("sonar.projectKey", project.key)):
        if chosen.get(key) != expected:
            raise CannotJudge(f"{PROPERTIES_FILE} sets {key!r} to {chosen.get(key)!r}, not {expected!r}")
    for key in COVERAGE_REPORT_KEYS:
        for entry in report_entries(chosen.get(key, "")):
            require_report_exists(root, key, entry)


def report_entries(value: str) -> list[str]:
    return [entry for entry in (part.strip() for part in value.split(",")) if entry]


def require_report_exists(root: Path, key: str, entry: str) -> None:
    """A named report that is absent when the scan starts would be a silent 0.0% coverage."""
    path = Path(entry)
    if path.is_absolute() or ".." in path.parts:
        raise CannotJudge(f"{PROPERTIES_FILE} sets {key!r} to {entry!r}, which lies outside the checkout")
    if any(character in entry for character in GLOB_CHARACTERS):
        if any(ANT_TRAILING_ANY in part and part != ANT_TRAILING_ANY for part in path.parts):
            raise CannotJudge(f"{PROPERTIES_FILE} sets {key!r} to {entry!r}, where {ANT_TRAILING_ANY!r} is not a whole path segment")
        pattern = f"{entry}/*" if path.name == ANT_TRAILING_ANY else entry
        found = any(match.is_file() for match in root.glob(pattern))
    else:
        found = (root / path).is_file()
    if not found:
        raise CannotJudge(f"{PROPERTIES_FILE} sets {key!r} to {entry!r}, but no such report file exists (did the tests job upload it, and does the caller pass coverage-artifact?)")


def read_properties(root: Path) -> list[tuple[str, str]]:
    """The java.util.Properties grammar: separators, continuations and escapes in keys and values."""
    try:
        text = repository.read_text(root, PROPERTIES_FILE)
    except repository.RepositoryFileError as error:
        raise CannotJudge(str(error)) from error
    if text is None:
        raise CannotJudge(f"{PROPERTIES_FILE} is missing")
    return [parse_property(line) for line in logical_property_lines(text)]


def logical_property_lines(text: str) -> list[str]:
    logical: list[str] = []
    pending: str | None = None
    for physical in PROPERTY_LINE_BREAK.split(text):
        line = physical.lstrip(PROPERTY_WHITESPACE)
        if pending is None and (not line or line[0] in "#!"):
            continue
        joined = (pending or "") + line
        trailing_backslashes = len(joined) - len(joined.rstrip("\\"))
        pending = joined[:-1] if trailing_backslashes % 2 else None
        if pending is None:
            logical.append(joined)
    if pending is not None:
        logical.append(pending)  # java.util.Properties drops a final unpaired backslash
    return logical


def parse_property(line: str) -> tuple[str, str]:
    key, position = decode_property_text(line, 0, stop_at=PROPERTY_WHITESPACE + "=:")
    position = skip_property_whitespace(line, position)
    if line[position : position + 1] in ("=", ":"):
        position = skip_property_whitespace(line, position + 1)
    value, _ = decode_property_text(line, position)
    return key, value


def skip_property_whitespace(line: str, position: int) -> int:
    while line[position : position + 1] and line[position] in PROPERTY_WHITESPACE:
        position += 1
    return position


def decode_property_text(line: str, position: int, stop_at: str = "") -> tuple[str, int]:
    decoded: list[str] = []
    while position < len(line) and line[position] not in stop_at:
        character = line[position]
        position += 1
        if character != "\\":
            decoded.append(character)
            continue
        escaped = line[position]
        position += 1
        if escaped == "u":
            digits = line[position : position + 4]
            if len(digits) < 4 or any(digit not in "0123456789abcdefABCDEF" for digit in digits):
                raise CannotJudge(f"{PROPERTIES_FILE} has a malformed unicode escape in {line!r}")
            decoded.append(chr(int(digits, 16)))
            position += 4
        else:
            decoded.append(PROPERTY_ESCAPES.get(escaped, escaped))
    return "".join(decoded), position


def run_properties(environment: Mapping[str, str], clock: Callable[[], float], channels: output.Channels) -> int:
    """Runs before the scan: the properties and the token are checked, the scan's arguments or `sonar=off` reach the
    step output, then the rows and the closing line; a refusal is the pair and writes no step output."""
    started = clock()
    try:
        judged = _judged_properties(environment)
    except CannotJudge as refusal:
        return refused(refusal.reason, PUSH_THE_FIX, channels)
    try:
        output.append_step_output(*judged.step_output, environment)
    except OSError as error:
        return refused(Reason(f"{output.STEP_OUTPUT_VARIABLE} is not writable{output.SEPARATOR}{error}"), FIX_THEN_RERUN, channels)
    output.write_rows(judged.rows, channels.out)
    output.write_closing_line(output.Glyph.CLEAN, (*judged.closing, output.format_duration(clock() - started)), channels.out)
    return EXIT_CLEAN


@dataclass(frozen=True)
class JudgedProperties:
    """What the properties step found: its rows, the step output it hands the scan and its closing line's parts."""

    rows: tuple[output.Row, ...]
    step_output: tuple[str, str]
    closing: tuple[str, ...]


def _judged_properties(environment: Mapping[str, str]) -> JudgedProperties:
    root = Path(".")
    try:
        require_unchanged_tracked_files(root, ADD_ONLY_STEP)
        if not runs_sonar(root):
            refuse_scan_wiring(environment)
            off = output.Row(output.Glyph.NOTE, "sonar", SonarEvent.OFF, f"{CONFIG_PATH} sets [gate] sonar = false")
            return JudgedProperties((off,), (output.SONAR_OUTPUT, output.SONAR_OFF), ("off", "nothing reaches SonarCloud"))
        check_properties(root, environment)
        excluded = excluded_rules(repository.is_on_disk(root, DEPENDABOT_CONFIG))
    except ConfigError as error:
        raise config_refusal(error, PUSH_THE_FIX) from error
    except repository.RepositoryFileError as error:
        raise CannotJudge(str(error)) from error
    rows = [output.Row(output.Glyph.CLEAN, "properties", SonarEvent.CLEAN, PROPERTIES_FILE)]
    if excluded:
        dropped = f"{', '.join(sorted(excluded))}{output.SEPARATOR}{DEPENDABOT_CONFIG} exists"
        rows.append(output.Row(output.Glyph.NOTE, "scan", SonarEvent.EXCLUDES, dropped))
    return JudgedProperties(tuple(rows), (output.SCANNER_ARGUMENTS_OUTPUT, scanner_exclusions(excluded)), ("clean",))


def refuse_scan_wiring(environment: Mapping[str, str]) -> None:
    """With SonarCloud off, a properties file or a passed token would let an older gate scan."""
    if repository.is_on_disk(Path("."), PROPERTIES_FILE):
        raise CannotJudge(f"{PROPERTIES_FILE} exists while {CONFIG_PATH} sets [gate] sonar = false")
    if environment.get(TOKEN_VARIABLE):
        raise CannotJudge(f"{TOKEN_VARIABLE} is set while {CONFIG_PATH} sets [gate] sonar = false")
