import argparse
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from functools import partial
from pathlib import Path

from quality import (
    __version__,
    checkout,
    clones,
    fleet,
    installation,
    process,
    python_lint,
    ratchet,
    repository,
    scanners,
    staged,
)
from quality.check import (
    AreaVerdict,
    Measurement,
    Outcome,
    run_hygiene,
)
from quality.checkout import OutsideWorkTree
from quality.config import Allowance, ConfigError, read_allow_lists, read_gate_settings
from quality.merge import Ask, GhRunner, RemoteFor, github_remote, run_emergency_merge, run_gh
from quality.output import (
    Glyph,
    Row,
    SubLine,
    Verdict,
    append_step_summary,
    counted,
    emit,
    emit_finding,
    set_up_streams,
    write_pair,
    write_rows,
    write_verdict,
)
from quality.report import CannotJudge, Finding, headline
from quality.rules import Rule
from quality.sonar import (
    DEPENDABOT_CONFIG,
    TASK_FILE,
    Transport,
    analyse,
    check_properties,
    fetch_json,
    uncounted_rules,
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
HYGIENE_EXIT = {
    Outcome.CLEAN: EXIT_CLEAN,
    Outcome.MEASURED_ONLY: EXIT_CLEAN,
    Outcome.FINDINGS: EXIT_FINDINGS,
    Outcome.UNPARSABLE: EXIT_CANNOT_JUDGE,
    Outcome.CANNOT_JUDGE: EXIT_CANNOT_JUDGE,
}


INSTALLED_RUFF = partial(installation.installed_executable, python_lint.RUFF_DISTRIBUTION)
INSTALLED_ZIZMOR = partial(installation.installed_executable, scanners.ZIZMOR)
ONLINE_AUDITS_NOTE = f"{Rule.WORKFLOW_SECURITY} runs offline, so zizmor's online-only audits do not run"


def main(
    argv: list[str] | None = None,
    fetcher_for: Callable[[str], PageFetcher] = github_fetcher,
    release_tags: fleet.TagReader = fleet.read_release_tags,
    transport: Transport = fetch_json,
    sleep: Sleep = time.sleep,
    gh: GhRunner = run_gh,
    remote_for: RemoteFor = github_remote,
    ask: Ask = input,
    is_terminal: Callable[[], bool] = lambda: sys.stdin.isatty(),
    locate_ruff: Callable[[], str] = INSTALLED_RUFF,
    locate_zizmor: Callable[[], str] = INSTALLED_ZIZMOR,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    arguments = build_parser().parse_args(argv)

    if arguments.command == "emergency-merge":
        return run_emergency_merge(
            arguments.repository,
            arguments.pull_request,
            gh=gh,
            transport=transport,
            ask=ask,
            is_terminal=is_terminal(),
            now=lambda: datetime.now(UTC),
            environment=os.environ,
            say=emit,
            remote_for=remote_for,
        )
    if arguments.command == "verdict":
        return run_verdict(arguments.path, fetcher_for, sleep)
    if arguments.command == "scoreboard":
        return run_scoreboard(arguments.path, release_tags)
    if arguments.command == "baseline":
        return run_baseline_write(arguments.path)
    if arguments.command == "scan-staged":
        return run_scan_staged(arguments.path)
    if arguments.command == "sonar":
        if arguments.sonar_command == "properties":
            return run_sonar_properties()
        return run_sonar_findings(transport, sleep)
    return run_hygiene_check(arguments.path, locate_ruff, locate_zizmor, clock)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quality")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    hygiene = commands.add_parser("hygiene", help="check a git repository for fleet hygiene")
    hygiene.add_argument("path", nargs="?", type=Path, default=Path("."))
    baseline = commands.add_parser("baseline", help="manage the baseline").add_subparsers(
        dest="baseline_command", required=True
    )
    baseline.add_parser("write", help=f"write the measured size debt and clone pairs to {ratchet.BASELINE_PATH}").add_argument(
        "path", nargs="?", type=Path, default=Path("."), help="a folder of the git repository to measure; its top level is measured (default: the current one)"
    )
    scan_staged = commands.add_parser("scan-staged", help="scan the staged change of a repository for secrets")
    scan_staged.add_argument("path", nargs="?", type=Path, default=Path("."))
    verdict = commands.add_parser("verdict", help="judge the mandatory jobs of the running workflow run")
    verdict.add_argument("path", nargs="?", type=Path, default=Path("."))
    sonar = commands.add_parser("sonar", help="check SonarCloud for open findings").add_subparsers(
        dest="sonar_command", required=True
    )
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
    emergency = commands.add_parser(
        "emergency-merge",
        help="operator only: switch a repository's quality_gate off, merge one pull request with its head pinned, switch it on",
    )
    emergency.add_argument("repository", help="<owner>/<repo>")
    emergency.add_argument("pull_request", type=int)
    scoreboard = commands.add_parser(
        "scoreboard",
        help=(
            "measure the size, the gate and CodeQL release and what each gate does with each rule"
            " of the repositories in quality/fleet.toml into two tables"
        ),
    )
    scoreboard.add_argument("path", nargs="?", type=Path, default=Path("."))
    return parser


class AreaEvent(StrEnum):
    """What an area row says happened."""

    CLEAN = "clean"
    FINDING = "finding"
    NOT_CHECKED = "not checked"
    NOT_RECORDED = "not recorded"


START_FAILURE_NEXT = "fix what the error names, then run quality hygiene again"
REFUSED_AREAS_NEXT = "fix what the ✗ rows name, then run quality hygiene again"


def area_row(verdict: AreaVerdict) -> Row:
    if verdict.reasons:
        if len(verdict.reasons) == 1:
            return Row(Glyph.COULD_NOT_CHECK, verdict.area, AreaEvent.NOT_CHECKED, verdict.reasons[0])
        sub_lines = tuple(SubLine(Glyph.COULD_NOT_CHECK, reason) for reason in verdict.reasons)
        return Row(Glyph.COULD_NOT_CHECK, verdict.area, AreaEvent.NOT_CHECKED, f"{len(verdict.reasons)} reasons", sub_lines=sub_lines)
    if verdict.findings:
        return Row(Glyph.NEEDS_YOU, verdict.area, AreaEvent.FINDING, count=len(verdict.findings))
    if verdict.outcome is Outcome.MEASURED_ONLY:
        partly = "" if verdict.measurement is Measurement.MEASURED_ONLY else "partly "
        return Row(Glyph.NOTE, verdict.area, AreaEvent.NOT_RECORDED, f"{partly}measured only")
    return Row(Glyph.CLEAN, verdict.area, AreaEvent.CLEAN)


def measured_notes(areas: tuple[AreaVerdict, ...]) -> tuple[str, ...]:
    """What the verdict says of an area whose rules only measured: it is not enforced."""
    words = {Measurement.MEASURED_ONLY: "measured", Measurement.PARTLY_MEASURED_ONLY: "partly measured"}
    return tuple(f"{verdict.area} {words[verdict.measurement]}, not enforced" for verdict in areas if verdict.measurement in words)


def refusal_error(areas: tuple[AreaVerdict, ...]) -> str:
    refused = [verdict for verdict in areas if verdict.reasons]
    found = sum(len(verdict.findings) for verdict in areas)
    held = sum(len(verdict.findings) for verdict in refused)
    error = f"{len(refused)} of {len(areas)} areas could not check"
    if not found:
        return error
    return f"{error} · {counted(found, 'finding')}, {held} of them in those areas" if held else f"{error} · {counted(found, 'finding')} in the others"


def run_hygiene_check(path: Path, locate_ruff: Callable[[], str], locate_zizmor: Callable[[], str], clock: Callable[[], float]) -> int:
    started = clock()
    try:
        result = run_hygiene(path, locate_ruff, locate_zizmor)
    except OutsideWorkTree as error:
        write_pair(f"not inside a git work tree · {error.path}", "run quality hygiene inside a repository")
        return EXIT_CANNOT_JUDGE
    except CannotJudge as error:
        write_pair(str(error), START_FAILURE_NEXT)
        return EXIT_CANNOT_JUDGE
    print_secret_allowances(result.allowed_secrets, result.unused_allowances)
    emit(*result.reports, ONLINE_AUDITS_NOTE)
    for verdict in result.areas:
        write_rows((area_row(verdict),))
        for finding in verdict.findings:
            emit_finding(finding)
    exit_code = HYGIENE_EXIT[result.worst]
    if exit_code == EXIT_CANNOT_JUDGE:
        write_pair(refusal_error(result.areas), REFUSED_AREAS_NEXT)
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
        )
    )
    return exit_code


def print_secret_allowances(allowed: list[Finding], unused: dict[Allowance, str]) -> None:
    if allowed:
        emit(f"{Rule.LEAK}: {len(allowed)} allowed ({', '.join(finding.location for finding in allowed)})")
        for finding in allowed:
            emit(f"  allowed {finding.location} - {finding.reason}")
    for entry, reason in unused.items():
        emit(f"{Rule.LEAK}: unused allowance {entry.rule} in {entry.file} (sha256 {entry.secret_sha256}) - {reason}")


def run_baseline_write(path: Path) -> int:
    try:
        top = checkout.work_tree_top(path)
        sources = ratchet.read_sources(top, checkout.tracked_files(top))
        generated = read_allow_lists(top).generated
        tree = ratchet.measure(sources)
        ratchet.complete(tree)
        measured = ratchet.without_generated(tree, generated).complete()
        written = ratchet.Baseline(ratchet.debt(measured), clones.find_pairs(sources).pairs)
        loosenings = describe_loosenings(top, written)
        repository.write_text(top, ratchet.BASELINE_PATH, ratchet.render_baseline(written))
    except (CannotJudge, ConfigError, repository.RepositoryFileError, ratchet.SizeError) as error:
        emit(f"baseline: not written - {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    for loosening in loosenings:
        emit(f"baseline: {loosening}")
    entries = "1 entry" if len(written.size) == 1 else f"{len(written.size)} entries"
    pairs = "1 clone pair" if len(written.clones) == 1 else f"{len(written.clones)} clone pairs"
    emit(f"baseline: wrote {entries} and {pairs} to {top / ratchet.BASELINE_PATH}")
    return EXIT_CLEAN


def describe_loosenings(path: Path, written: ratchet.Baseline) -> list[str]:
    """Each entry and clone pair the write raises or adds against the previous baseline; an adoption has none to name.

    An unreadable previous baseline is overwritten, so the write can only say it cannot name the raises.
    """
    try:
        previous = ratchet.read_baseline(path)
    except (CannotJudge, ratchet.SizeError) as error:
        return [f"the previous {ratchet.BASELINE_PATH} could not be read ({error}), so raises cannot be named"]
    if previous is None:
        return []
    lines = [entry.describe() for entry in ratchet.raised_entries(previous.size, written.size)]
    if previous.clones is not None:
        lines += [f"adds clone {pair.label}" for pair in ratchet.added_pairs(previous.clones, written.clones)]
    return lines


def run_scan_staged(path: Path) -> int:
    try:
        result = staged.scan_staged(path)
    except (process.ProcessError, ConfigError) as error:
        emit(f"scan-staged: cannot judge - {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    print_secret_allowances(result.allowed, result.unused_allowances)
    for finding in result.findings:
        emit_finding(finding)
    emit(f"scan-staged: {len(result.findings)} finding(s)" if result.findings else "scan-staged: clean")
    return EXIT_FINDINGS if result.findings else EXIT_CLEAN


def run_sonar_properties() -> int:
    try:
        check_properties(Path("."), os.environ)
    except CannotJudge as error:
        emit(f"sonar: cannot judge - {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    emit("sonar: properties clean")
    return EXIT_CLEAN


def run_sonar_findings(transport: Transport, sleep: Sleep) -> int:
    try:
        legacy_mode = read_gate_settings(Path(".")).legacy
        uncounted = uncounted_rules(repository.is_on_disk(Path("."), DEPENDABOT_CONFIG))
        result = analyse(os.environ, legacy_mode, uncounted, transport, sleep)
    except (CannotJudge, ConfigError, repository.RepositoryFileError) as error:
        emit(f"sonar: cannot judge - {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    for finding in result.findings.listed:
        emit_finding(finding)
    emit(*(headline(finding) for finding in result.findings.set_aside))
    failed_conditions, disclosure = result.gate_lines()
    emit(*failed_conditions, file=sys.stderr)
    emit(*disclosure)
    emit(result.summary(), file=sys.stderr if result.fails_the_run else sys.stdout)
    return EXIT_FINDINGS if result.fails_the_run else EXIT_CLEAN


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
    append_step_summary(report)
    return code


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
    append_step_summary(report)
    return EXIT_CANNOT_JUDGE if fleet.cannot_judge(rows, tags) else EXIT_CLEAN


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
