import argparse
import json
import os
import sys
import textwrap
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import NoReturn, TextIO

from quality import (
    __version__,
    fleet,
    installation,
    process,
    ratchet,
    repository,
    scanners,
    staged,
)
from quality.check import (
    DRY_RUN_COMMAND,
    DRY_RUN_WORD,
    FIX_RUN_COMMAND,
    AreaReport,
    AreaVerdict,
    CheckResult,
    DryRun,
    Fix,
    Measurement,
    Outcome,
    Running,
    judge_repository,
    judge_staged_change,
)
from quality.checkout import OutsideWorkTree
from quality.config import ConfigError, read_gate_settings
from quality.merge import GhRunner, RemoteFor, github_remote, run_emergency_merge, run_gh
from quality.output import (
    SCANNER_ARGUMENTS_OUTPUT,
    SEPARATOR,
    STEP_OUTPUT_VARIABLE,
    STEP_SUMMARY_VARIABLE,
    Channel,
    Channels,
    Glyph,
    Row,
    SubLine,
    Verdict,
    append_step_output,
    append_step_summary,
    ask,
    counted,
    emit,
    emit_finding,
    live_line,
    open_channels,
    set_up_streams,
    write_findings_and_notes,
    write_help,
    write_pair,
    write_rows,
    write_scanner_runs,
    write_verdict,
)
from quality.report import CannotJudge, Command, Extent, Instruction, NoteEvent, StagedExtent, Step
from quality.rules import Area, Rule
from quality.sonar import (
    DEPENDABOT_CONFIG,
    TASK_FILE,
    Transport,
    analyse,
    check_properties,
    excluded_rules,
    fetch_json,
    scanner_exclusions,
)
from quality.verdict import (
    PageFetcher,
    UnsettledRun,
    compares_baselines,
    github_fetcher,
    judge,
    read_base,
    read_head,
    read_jobs,
    read_renames,
    read_settled,
)
from quality.web import Sleep

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_CANNOT_JUDGE = 2
CHECK_EXIT = {
    Outcome.CLEAN: EXIT_CLEAN,
    Outcome.MEASURED_ONLY: EXIT_CLEAN,
    Outcome.WOULD_CHANGE: EXIT_FINDINGS,
    Outcome.FINDINGS: EXIT_FINDINGS,
    Outcome.UNPARSABLE: EXIT_CANNOT_JUDGE,
    Outcome.CANNOT_JUDGE: EXIT_CANNOT_JUDGE,
}


INSTALLED_RUFF = partial(installation.installed_executable, scanners.RUFF)
INSTALLED_ZIZMOR = partial(installation.installed_executable, scanners.ZIZMOR)


def main(
    argv: list[str] | None = None,
    fetcher_for: Callable[[str], PageFetcher] = github_fetcher,
    release_tags: fleet.TagReader = fleet.read_release_tags,
    transport: Transport = fetch_json,
    sleep: Sleep = time.sleep,
    gh: GhRunner = run_gh,
    remote_for: RemoteFor = github_remote,
    read_line: Callable[[], str] = lambda: sys.stdin.readline(),
    is_terminal: Callable[[], bool] = lambda: sys.stdin is not None and sys.stdin.isatty(),
    locate_ruff: Callable[[], str] = INSTALLED_RUFF,
    locate_zizmor: Callable[[], str] = INSTALLED_ZIZMOR,
    clock: Callable[[], float] = time.monotonic,
    environment: Mapping[str, str] = os.environ,
) -> int:
    channels = open_channels(environment)
    try:
        arguments = read_invocation(argv)
    except CannotJudge as refusal:
        write_pair(refusal.reason.fact, refusal.reason.next_steps, channels.err)
        return EXIT_CANNOT_JUDGE

    if arguments.command == "emergency-merge":
        return run_emergency_merge(
            arguments.repository,
            arguments.pull_request,
            gh=gh,
            transport=transport,
            ask=partial(ask, channel=channels.err, read_line=read_line),
            is_terminal=is_terminal(),
            now=lambda: datetime.now(UTC),
            environment=environment,
            say=emit,
            channels=channels,
            remote_for=remote_for,
        )
    if arguments.command == "verdict":
        return run_verdict(arguments.path, fetcher_for, sleep)
    if arguments.command == "scoreboard":
        return run_scoreboard(arguments.path, release_tags)
    if arguments.command == "sonar":
        if arguments.sonar_command == "properties":
            return run_sonar_properties()
        return run_sonar_findings(transport, sleep)
    if arguments.staged:
        staged_run = partial(judge_staged_change, Path("."), staged.scan_change, verbose=arguments.verbose)
        return run_check(staged_run, staged.COMMAND, clock, channels)
    fix = _fix_mode(arguments)
    repository_run = partial(judge_repository, Path("."), locate_ruff, locate_zizmor, verbose=arguments.verbose, named=arguments.areas, fix=fix)
    return run_check(repository_run, RERUN_COMMAND[fix], clock, channels)


def _fix_mode(arguments: argparse.Namespace) -> Fix | None:
    if arguments.dry_run:
        return Fix.DRY_RUN
    return Fix.WRITE if arguments.fix else None


QUALITY_HELP = """\
usage: quality COMMAND

Commands:
  check [AREA ...] [--staged] [--fix] [--dry-run]
      Check this repository against the fleet's rules.
  emergency-merge OWNER/REPO PR
      Operator only: merge one pull request whose gate is red only for
      legacy findings or loosenings he ruled, after he types ja.
  scoreboard [PATH]
      Measure every fleet repository: size, debt, the gate release it pins.

Run by the gate, not by people:
  verdict [PATH]
      Decide whether this pull request may merge.
  sonar properties
      Refuse a properties file, token or coverage report the scan must not
      start with.
  sonar findings
      Wait for this run's analysis, then count its open findings.

Options:
  --version   the installed version
  -h, --help  show this help

Exit codes: 0 yes, 1 no, 2 could not answer, 130 interrupted.
"""

AREA_COLUMN = max(map(len, Area))
AREA_HELP_WIDTH = 76


def _area_entry(area: Area) -> str:
    return textwrap.fill(area.help_line, AREA_HELP_WIDTH, initial_indent=f"  {area:<{AREA_COLUMN}}  ", subsequent_indent=" " * (AREA_COLUMN + 4))


AREA_ENTRIES = "\n".join(map(_area_entry, Area))

CHECK_HELP = f"""\
usage: quality check [AREA ...] [--staged] [--fix] [--dry-run] [-v]

Check this repository against the fleet's rules.

Areas (default: all):
{AREA_ENTRIES}

Options:
  --staged      secrets in exactly what the next commit records; the commit
                hook runs: quality check secrets --staged
  --fix         lower the debt ceiling ({ratchet.BASELINE_PATH}) where code
                shrank, or record it the first time; never raises it
  --dry-run     with --fix: show what would change, write nothing
  -v            show the raw scanner output, never a secret
  -h, --help    show this help

Exit codes: 0 clean, 1 findings, 2 could not check, 130 interrupted.
"""


class DoorParser(argparse.ArgumentParser):
    """argparse reads the words and prints only `--version`: a wrong invocation leaves as the refusal `main` writes, and
    help leaves through `output.py`."""

    commands: Mapping[str, argparse.ArgumentParser] = {}
    pictured_help: str | None = None
    intermixed: bool = False

    def add_subparsers(self, **options: str) -> argparse._SubParsersAction:
        words = super().add_subparsers(metavar="COMMAND", required=True, **options)
        self.commands = words.choices
        return words

    def parse_known_args(
        self, args: Sequence[str] | None = None, namespace: argparse.Namespace | None = None
    ) -> tuple[argparse.Namespace, list[str]]:
        words = sys.argv[1:] if args is None else args
        word = words[0] if words else ""
        if self.commands and not word.startswith("-") and word not in self.commands:
            asked = f"no command {word}" if word else "no command"
            raise CannotJudge(f"{asked}{SEPARATOR}commands: {', '.join(self.commands)}", help_step(self.prog))
        if self.intermixed:
            return self.parse_known_intermixed_args(args, namespace)
        return super().parse_known_args(args, namespace)

    def error(self, message: str) -> NoReturn:
        raise CannotJudge(message, help_step(self.prog))

    def print_help(self, file: TextIO | None = None) -> None:
        write_help(self.pictured_help or self.format_help())


def help_step(prog: str) -> tuple[Step, ...]:
    return (Command(f"{prog} --help"),)


def named_area(word: str) -> Area:
    """The area `word` names; a word that names none is the refusal `main` writes, with the registry's areas."""
    try:
        return Area(word)
    except ValueError as error:
        raise CannotJudge(f"no area {word}{SEPARATOR}areas: {', '.join(Area)}", help_step("quality check")) from error


def read_invocation(argv: list[str] | None) -> argparse.Namespace:
    """The parsed invocation; whatever the command does not take is a refusal, which names that command's help."""
    parser = build_parser()
    arguments, stray = parser.parse_known_args(argv)
    if stray:
        raise CannotJudge(f"unknown argument{SEPARATOR}{' '.join(stray)}", help_step(f"{parser.prog} {arguments.command}"))
    if arguments.command == "check":
        _refuse_what_check_does_not_take(arguments)
    return arguments


def _refuse_what_check_does_not_take(arguments: argparse.Namespace) -> None:
    if arguments.dry_run and not arguments.fix:
        raise CannotJudge(f"{DRY_RUN_WORD} needs --fix{SEPARATOR}nothing checked", (Command(DRY_RUN_COMMAND),))
    dry_run = f" {DRY_RUN_WORD}" if arguments.dry_run else ""
    if arguments.fix and arguments.staged:
        rerun = RERUN_COMMAND[_fix_mode(arguments)]
        raise CannotJudge(f"--fix does not take --staged{SEPARATOR}nothing checked", (Command(rerun),))
    if arguments.fix and arguments.areas and Area.DEBT not in arguments.areas:
        raise CannotJudge(f"--fix changes only the debt ceiling{SEPARATOR}nothing checked", (Command(f"quality check debt --fix{dry_run}"),))
    if arguments.staged and set(arguments.areas) != {Area.SECRETS}:
        raise CannotJudge(f"--staged checks only secrets{SEPARATOR}nothing checked", (Command(staged.COMMAND),))


def build_parser() -> DoorParser:
    parser = DoorParser(prog="quality", color=False)
    parser.pictured_help = QUALITY_HELP
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    check_parser = commands.add_parser("check", allow_abbrev=False)
    check_parser.pictured_help = CHECK_HELP
    check_parser.intermixed = True
    check_parser.add_argument("areas", nargs="*", type=named_area, metavar="AREA")
    check_parser.add_argument("--staged", action="store_true")
    check_parser.add_argument("--fix", action="store_true")
    check_parser.add_argument(DRY_RUN_WORD, dest="dry_run", action="store_true")
    check_parser.add_argument("-v", dest="verbose", action="store_true")
    verdict = commands.add_parser("verdict")
    verdict.add_argument("path", nargs="?", type=Path, default=Path("."))
    sonar = commands.add_parser("sonar").add_subparsers(dest="sonar_command")
    sonar.add_parser(
        "properties",
        help=(
            "run by the gate before the scan: refuse a sonar-project.properties, token, coverage report"
            " or tracked .scannerwork/ the scan must not start with"
        ),
    )
    sonar.add_parser(
        "findings",
        help=(
            f"wait for this run's analysis ({TASK_FILE}), then count its open findings; "
            "needs quality/config.toml with [gate] mandatory_jobs, else exit 2"
        ),
    )
    emergency = commands.add_parser("emergency-merge")
    emergency.add_argument("repository", help="<owner>/<repo>")
    emergency.add_argument("pull_request", type=int)
    scoreboard = commands.add_parser("scoreboard")
    scoreboard.add_argument("path", nargs="?", type=Path, default=Path("."))
    return parser


class AreaEvent(StrEnum):
    """What an area row says happened."""

    CLEAN = "clean"
    FINDING = "finding"
    LOWERED = "lowered"
    MOVED = "moved"
    NOT_CHECKED = "not checked"
    NOT_FIXED = "not fixed"
    NOT_RECORDED = "not recorded"
    NOT_SET_UP = "not set up"
    RECORDED = "recorded"
    TO_LOWER = "to lower"
    TO_MOVE = "to move"
    TO_RECORD = "to record"


CEILING_HELD = "ceiling held"
OFFLINE_AUDITS = "offline audits only"
NO_CONFIG_FACT = "no import-linter config"
MEASURED_ONLY_EVENT = {Area.DEBT: AreaEvent.NOT_RECORDED, Area.IMPORTS: AreaEvent.NOT_SET_UP}
CHECK_COMMAND = "quality check"
RERUN_COMMAND = {None: CHECK_COMMAND, **FIX_RUN_COMMAND}
CLONE_PAIR = "clone pair"
STAGED_CHANGE = "staged change"


def refused_next(command: str) -> Instruction:
    return Instruction(f"fix what the ✗ rows name, then run {command} again")


REFUSED_AREAS_NEXT = refused_next(CHECK_COMMAND)


def _in_file(fact: str) -> str:
    return SEPARATOR.join((fact, ratchet.BASELINE_PATH))


def _made(ceiling: ratchet.CeilingFix | DryRun) -> tuple[ratchet.CeilingFix, bool]:
    """The fix the run made of the ceiling, and whether a dry run only planned it."""
    return (ceiling.fix, True) if isinstance(ceiling, DryRun) else (ceiling, False)


def _lowered_fact(ceiling: ratchet.CeilingFix | DryRun) -> str:
    fix, planned = _made(ceiling)
    if fix.fell:
        return _in_file(f"{fix.fell} to lower" if planned else f"{counted(fix.fell, 'entry', 'entries')} lowered")
    if fix.moved:
        return _in_file(f"{fix.moved} to move" if planned else f"{counted(fix.moved, 'entry', 'entries')} moved")
    return "nothing to lower" if planned else "nothing lowered"


def _recorded_extent(fix: ratchet.CeilingFix) -> str:
    return f"{counted(len(fix.ceiling.size), 'entry', 'entries')}, {counted(len(fix.ceiling.clones), CLONE_PAIR)}"


def _planned_row(area: Area, fix: ratchet.CeilingFix) -> Row | None:
    """A dry run's debt row: what would fall, counted, else what would move, else the recording it would write."""
    for count, event in ((fix.fell, AreaEvent.TO_LOWER), (fix.moved, AreaEvent.TO_MOVE)):
        if count:
            return Row(Glyph.WILL_CHANGE, area, event, ratchet.BASELINE_PATH, count=count, counts_plural=False)
    if fix.first:
        new = SEPARATOR.join((_recorded_extent(fix), f"{ratchet.BASELINE_PATH} would be new"))
        return Row(Glyph.WILL_CHANGE, area, AreaEvent.TO_RECORD, new)
    pairs = _in_file(counted(len(fix.ceiling.clones), CLONE_PAIR))
    return Row(Glyph.WILL_CHANGE, area, AreaEvent.TO_RECORD, pairs) if fix.recorded else None


def _ceiling_row(area: Area, ceiling: ratchet.CeilingFix | DryRun) -> Row | None:
    fix, planned = _made(ceiling)
    if planned:
        return _planned_row(area, fix)
    pairs = counted(len(fix.ceiling.clones), CLONE_PAIR)
    if fix.first:
        return Row(Glyph.CLEAN, area, AreaEvent.RECORDED, SEPARATOR.join((_recorded_extent(fix), f"{ratchet.BASELINE_PATH} is new")))
    if fix.fell:
        return Row(Glyph.CLEAN, area, AreaEvent.LOWERED, _in_file(counted(fix.fell, "entry", "entries")))
    if fix.moved:
        return Row(Glyph.CLEAN, area, AreaEvent.MOVED, _in_file(counted(fix.moved, "entry", "entries")))
    return Row(Glyph.CLEAN, area, AreaEvent.RECORDED, _in_file(pairs)) if fix.recorded else None


def _ceiling_words(ceiling: ratchet.CeilingFix | DryRun | None, findings: int) -> str:
    """The verdict's words for the ceiling, done or, under a dry run, what would be done."""
    if ceiling is None:
        return ""
    fix, planned = _made(ceiling)
    if fix.fell:
        return f"{counted(fix.fell, 'ceiling entry', 'ceiling entries')} would fall" if planned else f"ceiling lowered by {counted(fix.fell, 'entry', 'entries')}"
    if fix.moved:
        return f"{counted(fix.moved, 'ceiling entry', 'ceiling entries')} would move" if planned else f"ceiling moved {counted(fix.moved, 'entry', 'entries')}"
    if fix.changed:
        return "ceiling would be recorded" if planned else "ceiling recorded"
    return "" if findings else "nothing to lower"


def _refused_row(verdict: AreaVerdict, facts: tuple[str, ...]) -> Row:
    if len(facts) == 1:
        event = AreaEvent.NOT_FIXED if verdict.only_the_fix_refused else AreaEvent.NOT_CHECKED
        return Row(Glyph.COULD_NOT_CHECK, verdict.area, event, facts[0], descriptive=True)
    sub_lines = tuple(SubLine(Glyph.COULD_NOT_CHECK, fact) for fact in facts)
    return Row(Glyph.COULD_NOT_CHECK, verdict.area, AreaEvent.NOT_CHECKED, f"{len(facts)} reasons", sub_lines=sub_lines)


def _finding_row(verdict: AreaVerdict, extent: str) -> Row:
    fact = extent if _judged_a_staged_change(verdict) else ""
    if isinstance(verdict.ceiling, ratchet.CeilingFix | DryRun):
        fact = _lowered_fact(verdict.ceiling)
    return Row(Glyph.NEEDS_YOU, verdict.area, AreaEvent.FINDING, fact, count=len(verdict.findings))


def area_row(verdict: AreaVerdict, extent: str) -> Row:
    facts = tuple(dict.fromkeys(reason.fact for reason in verdict.reasons))
    if facts:
        return _refused_row(verdict, facts)
    if verdict.findings:
        return _finding_row(verdict, extent)
    if isinstance(verdict.ceiling, ratchet.CeilingFix | DryRun) and (row := _ceiling_row(verdict.area, verdict.ceiling)):
        return row
    if verdict.outcome is Outcome.MEASURED_ONLY:
        if MEASURED_ONLY_EVENT[verdict.area] is AreaEvent.NOT_SET_UP:
            return Row(Glyph.NOTE, verdict.area, AreaEvent.NOT_SET_UP, extent)
        partly = "" if verdict.measurement is Measurement.MEASURED_ONLY else "partly "
        mode = f"{partly}{Outcome.MEASURED_ONLY.value}"
        return Row(Glyph.NOTE, verdict.area, AreaEvent.NOT_RECORDED, SEPARATOR.join(filter(None, (extent, mode))), fix=ratchet.FIX_COMMAND)
    allowed = sum(note.event is NoteEvent.ALLOWED for note in verdict.notes)
    facts = [extent, f"{allowed} {NoteEvent.ALLOWED}" if allowed else ""]
    return Row(Glyph.CLEAN, verdict.area, AreaEvent.CLEAN, SEPARATOR.join(fact for fact in facts if fact))


def _judged_a_staged_change(verdict: AreaVerdict) -> bool:
    return any(isinstance(judged.extent, StagedExtent) for judged in verdict.verdicts)


def _imports_extent(verdict: AreaVerdict, extents: Mapping[Rule, Extent | None]) -> str:
    return NO_CONFIG_FACT if verdict.measurement is Measurement.MEASURED_ONLY else counted(extents[Rule.IMPORT_CONTRACT], "contract")


def _secrets_extent(verdict: AreaVerdict, extents: Mapping[Rule, Extent | None]) -> str:
    judged = extents[Rule.LEAK]
    if isinstance(judged, StagedExtent):
        return SEPARATOR.join((STAGED_CHANGE, counted(judged.files, "file")))
    return counted(judged, "commit")


def _dependencies_extent(verdict: AreaVerdict, extents: Mapping[Rule, Extent | None]) -> str:
    judged = extents[Rule.ADVISORY]
    return SEPARATOR.join((counted(judged.lockfiles, "lockfile"), counted(judged.packages, "package")))


def _debt_extent(verdict: AreaVerdict, extents: Mapping[Rule, Extent | None]) -> str:
    entries, pairs = counted(extents[Rule.SIZE], "entry", "entries"), counted(extents[Rule.CLONE], CLONE_PAIR)
    match verdict.measurement:
        case Measurement.MEASURED_ONLY:
            return f"{entries} over the thresholds"
        case Measurement.PARTLY_MEASURED_ONLY:
            return pairs
        case Measurement.ENFORCED:
            return f"{CEILING_HELD}{SEPARATOR}{entries}, {pairs}"


def _workflows_extent(verdict: AreaVerdict, extents: Mapping[Rule, Extent | None]) -> str:
    return SEPARATOR.join((counted(extents[Rule.WORKFLOW_LINT], "workflow"), OFFLINE_AUDITS))


def _code_extent(verdict: AreaVerdict, extents: Mapping[Rule, Extent | None]) -> str:
    return SEPARATOR.join((counted(len(verdict.verdicts), "rule"), counted(extents[Rule.PYTHON_SECURITY], "Python file")))


JUDGED_EXTENT: dict[Area, Callable[[AreaVerdict, Mapping[Rule, Extent | None]], str]] = {
    Area.DEBT: _debt_extent,
    Area.IMPORTS: _imports_extent,
    Area.SECRETS: _secrets_extent,
    Area.DEPENDENCIES: _dependencies_extent,
    Area.WORKFLOWS: _workflows_extent,
    Area.CODE: _code_extent,
}


def area_extent(verdict: AreaVerdict, tracked_paths: int) -> str:
    """What an area's row says it covered: the rules and files of `files`, then what the area's rules judged;
    nothing for an area that could not check."""
    if verdict.area is Area.FILES:
        return SEPARATOR.join((counted(len(verdict.verdicts), "rule"), counted(tracked_paths, "file")))
    extents = {rule_verdict.rule: rule_verdict.extent for rule_verdict in verdict.verdicts}
    if verdict.reasons or None in extents.values():
        return ""
    return JUDGED_EXTENT[verdict.area](verdict, extents)


def measured_notes(areas: tuple[AreaVerdict, ...]) -> tuple[str, ...]:
    """What the verdict says of an area whose rules only measured: it is not enforced, and measured where it measured something."""
    words = {Measurement.MEASURED_ONLY: "measured", Measurement.PARTLY_MEASURED_ONLY: "partly measured"}
    return tuple(
        f"{verdict.area} not enforced" if MEASURED_ONLY_EVENT[verdict.area] is AreaEvent.NOT_SET_UP
        else f"{verdict.area} {words[verdict.measurement]}, not enforced"
        for verdict in areas if verdict.measurement in words
    )


def refusal_error(areas: tuple[AreaVerdict, ...]) -> str:
    refused = [verdict for verdict in areas if verdict.reasons]
    found = sum(len(verdict.findings) for verdict in areas)
    held = sum(len(verdict.findings) for verdict in refused)
    error = f"{len(refused)} of {counted(len(areas), 'area')} could not check"
    if not found:
        return error
    return f"{error} · {counted(found, 'finding')}, {held} of them in those areas" if held else f"{error} · {counted(found, 'finding')} in the others"


def next_steps(areas: tuple[AreaVerdict, ...], general: Instruction = REFUSED_AREAS_NEXT) -> tuple[Step, ...]:
    """Each distinct step of the run's refusals once, in row order; the general instruction last where a refusal carries
    no step, and alone where none does."""
    reasons = [reason for verdict in areas for reason in verdict.reasons]
    steps = tuple(dict.fromkeys(step for reason in reasons for step in reason.next_steps))
    return steps if reasons and all(reason.next_steps for reason in reasons) else (*steps, general)


def write_area(out: Channel, verdict: AreaVerdict, tracked_paths: int) -> None:
    write_rows((area_row(verdict, area_extent(verdict, tracked_paths)),), out)
    write_findings_and_notes(verdict.findings, verdict.notes, out)
    write_scanner_runs(verdict.scanner_runs, out)


def _spoken_folder(folder: Path) -> str:
    home = Path(os.path.expanduser("~"))
    return str(Path("~", folder.relative_to(home))) if folder.is_relative_to(home) else str(folder)


def run_check(judged: Callable[[AreaReport, Running], CheckResult], command: str, clock: Callable[[], float], channels: Channels) -> int:
    """Runs `judged`, the repository's judging or the staged change's, and writes its rows, its verdict and its pair;
    `command` is what a person runs to ask again."""
    started = clock()
    try:
        result = judged(partial(write_area, channels.out), live_line(channels.err))
    except OutsideWorkTree as error:
        write_pair(f"not inside a git work tree · {_spoken_folder(error.path.absolute())}", (Instruction(f"run {command} inside a repository"),), channels.err)
        return EXIT_CANNOT_JUDGE
    except CannotJudge as error:
        write_pair(error.reason.fact, error.reason.next_steps or (Instruction(f"fix what the error names, then run {command} again"),), channels.err)
        return EXIT_CANNOT_JUDGE
    exit_code = CHECK_EXIT[result.worst]
    if exit_code == EXIT_CANNOT_JUDGE:
        write_pair(refusal_error(result.areas), next_steps(result.areas, refused_next(command)), channels.err)
        return exit_code
    outcomes = Counter(verdict.outcome for verdict in result.areas)
    write_verdict(
        Verdict(
            findings=len(result.findings),
            areas_with_findings=outcomes[Outcome.FINDINGS],
            clean_areas=outcomes[Outcome.CLEAN],
            areas=len(result.areas),
            notes=measured_notes(result.areas),
            seconds=clock() - started,
            ceiling=_ceiling_words(result.ceiling, len(result.findings)),
            would_change=isinstance(result.ceiling, DryRun) and result.ceiling.would_change,
        ),
        channels.out,
    )
    return exit_code


def run_sonar_properties() -> int:
    try:
        check_properties(Path("."), os.environ)
        excluded = excluded_rules(repository.is_on_disk(Path("."), DEPENDABOT_CONFIG))
    except (CannotJudge, repository.RepositoryFileError) as error:
        emit(f"sonar: cannot judge - {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    try:
        append_step_output(SCANNER_ARGUMENTS_OUTPUT, scanner_exclusions(excluded))
    except OSError as error:
        emit(f"sonar: cannot judge - {STEP_OUTPUT_VARIABLE} is not writable: {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    emit("sonar: properties clean")
    if excluded:
        emit(f"sonar: the scan excludes {', '.join(sorted(excluded))} ({DEPENDABOT_CONFIG} exists)")
    return EXIT_CLEAN


def run_sonar_findings(transport: Transport, sleep: Sleep) -> int:
    try:
        legacy_mode = read_gate_settings(Path(".")).legacy
        result = analyse(os.environ, legacy_mode, transport, sleep)
    except (CannotJudge, ConfigError) as error:
        emit(f"sonar: cannot judge - {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    for finding in result.findings.listed:
        emit_finding(finding)
    failed_conditions, disclosure = result.gate_lines()
    emit(*failed_conditions, file=sys.stderr)
    emit(*disclosure)
    emit(result.summary(), file=sys.stderr if result.fails_the_run else sys.stdout)
    return EXIT_FINDINGS if result.fails_the_run else EXIT_CLEAN


def summarised(door: str, report: tuple[str, ...]) -> bool:
    """Whether the run summary took the report; an unwritable summary is a fact on stderr, not a traceback."""
    try:
        append_step_summary(report)
    except OSError as error:
        emit(f"{door}: cannot judge - {STEP_SUMMARY_VARIABLE} is not writable: {error}", file=sys.stderr)
        return False
    return True


def run_verdict(path: Path, fetcher_for: Callable[[str], PageFetcher], sleep: Sleep) -> int:
    try:
        head = read_head(path)
        base_sha = os.environ.get("GATE_BASE_SHA", "")
        base = read_base(path, base_sha) if base_sha else None
        renames = read_renames(path, base_sha, "HEAD") if compares_baselines(base, head) else ratchet.NO_RENAMES
        gate_results = {name: need["result"] for name, need in json.loads(os.environ["GATE_NEEDS"]).items()}
        current_attempt = int(os.environ["GITHUB_RUN_ATTEMPT"])
        repository_name, run_id = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_RUN_ID"]
        fetch = fetcher_for(os.environ["GITHUB_TOKEN"])
        settled = read_settled(
            lambda: read_jobs(repository_name, run_id, fetch),
            lambda jobs: judge(head, base, jobs, gate_results, current_attempt, renames),
            sleep,
        )
    except (CannotJudge, KeyError, ValueError) as error:
        preceding = error.preceding_lines if isinstance(error, UnsettledRun) else ()
        report = (*preceding, f"verdict: cannot judge - {error}")
        code = EXIT_CANNOT_JUDGE
    else:
        report = settled.lines()
        code = EXIT_CLEAN if settled.verdict.green else EXIT_FINDINGS
    emit(*report, file=sys.stderr if code == EXIT_CANNOT_JUDGE else sys.stdout)
    return code if summarised("verdict", report) else EXIT_CANNOT_JUDGE


def run_scoreboard(path: Path, release_tags: fleet.TagReader) -> int:
    try:
        listed = fleet.read_fleet(path)
    except fleet.FleetError as error:
        emit(f"scoreboard: cannot judge - {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    rows = fleet.measure_fleet(listed)
    try:
        tags = release_tags()
    except fleet.ReleaseTagsError as error:
        tags = error
    report = fleet.render(rows, datetime.now(UTC).date(), tags)
    emit(*report)
    if not summarised("scoreboard", report) or fleet.cannot_judge(rows, tags):
        return EXIT_CANNOT_JUDGE
    return EXIT_CLEAN


def run(
    door: Callable[[], int] = main, environment: Mapping[str, str] = os.environ, send_signal: Callable[[int, int], None] = os.kill,
) -> None:
    set_up_streams(environment)
    try:
        exit_code = door()
    except KeyboardInterrupt:
        process.exit_as_interrupted(send_signal)
    sys.exit(exit_code)


if __name__ == "__main__":
    run()
