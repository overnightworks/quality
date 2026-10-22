import argparse
import json
import os
import secrets
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from quality import __version__, clones, engines, ratchet, repository, scoreboard, staged
from quality.config import Allowance, ConfigError, read_gate_settings
from quality.emergency import Ask, GhRunner, RemoteFor, github_remote, run_emergency_merge, run_gh
from quality.hygiene import (
    CannotJudge,
    HygieneResult,
    Outcome,
    read_baseline,
    read_sources,
    run_hygiene,
    skipped_rules,
    tracked_files,
    work_tree_top,
)
from quality.report import Finding, headline, printable, render
from quality.sonar import (
    DEPENDABOT_CONFIG,
    TASK_FILE,
    Sleep,
    Transport,
    analyse,
    fetch_json,
    preflight,
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

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_CANNOT_JUDGE = 2
HYGIENE_EXIT = {
    Outcome.CLEAN: EXIT_CLEAN,
    Outcome.FINDINGS: EXIT_FINDINGS,
    Outcome.UNPARSABLE: EXIT_CANNOT_JUDGE,
    Outcome.CANNOT_JUDGE: EXIT_CANNOT_JUDGE,
}


def main(
    argv: list[str] | None = None,
    fetcher_for: Callable[[str], PageFetcher] = github_fetcher,
    read_pins: Callable[[], dict[str, engines.Pin]] = engines.read_pins,
    release_tags: scoreboard.TagReader = scoreboard.read_release_tags,
    transport: Transport = fetch_json,
    sleep: Sleep = time.sleep,
    gh: GhRunner = run_gh,
    remote_for: RemoteFor = github_remote,
    ask: Ask = input,
    is_terminal: Callable[[], bool] = lambda: sys.stdin.isatty(),
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
        return run_scan_staged(arguments.path, arguments.engines, read_pins)
    if arguments.command == "sonar":
        if arguments.sonar_command == "preflight":
            return run_sonar_preflight(arguments.path)
        return run_sonar_findings(transport, sleep)
    if arguments.command == "engines":
        run_engines = run_engines_install if arguments.engines_command == "install" else run_engines_verify
        return run_engines(arguments.directory, read_pins)
    return run_hygiene_check(arguments.path, arguments.engines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quality")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    hygiene = commands.add_parser("hygiene", help="check a git repository for fleet hygiene")
    hygiene.add_argument("path", nargs="?", type=Path, default=Path("."))
    hygiene.add_argument("--engines", type=Path, help="directory holding the installed engines; enables the secret and workflow scans")
    baseline = commands.add_parser("baseline", help="manage the baseline").add_subparsers(
        dest="baseline_command", required=True
    )
    baseline.add_parser("write", help=f"write the measured size debt and clone pairs to {ratchet.BASELINE_PATH}").add_argument(
        "path", nargs="?", type=Path, default=Path("."), help="a folder of the git repository to measure; its top level is measured (default: the current one)"
    )
    scan_staged = commands.add_parser("scan-staged", help="scan the staged change of a repository for secrets")
    scan_staged.add_argument("path", nargs="?", type=Path, default=Path("."))
    scan_staged.add_argument("--engines", type=Path, required=True, help="directory holding the installed engines")
    engine_commands = commands.add_parser("engines", help="manage the pinned engines").add_subparsers(
        dest="engines_command", required=True
    )
    engine_commands.add_parser("install", help="download, verify and unpack the pinned engines that are not yet right").add_argument(
        "directory", type=Path
    )
    engine_commands.add_parser("verify", help="check that every pinned engine reports its pinned version").add_argument(
        "directory", type=Path
    )
    verdict = commands.add_parser("verdict", help="judge the mandatory jobs of the running workflow run")
    verdict.add_argument("path", nargs="?", type=Path, default=Path("."))
    sonar = commands.add_parser("sonar", help="check SonarCloud for open findings").add_subparsers(
        dest="sonar_command", required=True
    )
    sonar.add_parser("preflight", help="refuse a token or properties file that could steer the scan").add_argument(
        "path", nargs="?", type=Path, default=Path(".")
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
    fleet = commands.add_parser(
        "scoreboard",
        help=(
            "measure the size, the gate and CodeQL release and what each gate does with each rule"
            " of the repositories in quality/fleet.toml into two tables"
        ),
    )
    fleet.add_argument("path", nargs="?", type=Path, default=Path("."))
    return parser


def emit(*lines: str, file: TextIO | None = None) -> None:
    """The one print boundary: every line the door prints leaves through `printable`, and the door never splits text,
    so a line of repository text cannot become several lines."""
    for line in lines:
        print(printable(line), file=file)


def run_hygiene_check(path: Path, engine_dir: Path | None) -> int:
    try:
        result = run_hygiene(path, engine_dir)
    except CannotJudge as error:
        emit(f"hygiene: {error.label} - {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    print_secret_allowances(result.allowed_secrets, result.unused_allowances)
    for report in result.reports:
        emit(report)
    for verdict in result.verdicts:
        for finding in verdict.findings:
            print(render(finding))
        if verdict.refusal is not None:
            emit(f"hygiene: {verdict.rule} {verdict.refusal.label} - {verdict.refusal}", file=sys.stderr)
    emit(f"hygiene: {hygiene_summary(result)}{coverage_note(engine_dir)}")
    return HYGIENE_EXIT[result.worst]


def hygiene_summary(result: HygieneResult) -> str:
    """The clean line names the rules it judged; any other line counts the findings and the rules per verdict."""
    if result.worst is Outcome.CLEAN:
        return f"clean ({', '.join(verdict.rule for verdict in result.verdicts)})"
    outcomes = Counter(verdict.outcome for verdict in result.verdicts)
    counts = ", ".join(f"{outcomes[outcome]} {outcome.value}" for outcome in Outcome if outcomes[outcome])
    return f"{len(result.findings)} finding(s); verdicts: {counts}"


def print_secret_allowances(allowed: list[Finding], unused: dict[Allowance, str]) -> None:
    if allowed:
        emit(f"{engines.LEAK_RULE}: {len(allowed)} allowed ({', '.join(finding.location for finding in allowed)})")
        for finding in allowed:
            emit(f"  allowed {finding.location} - {finding.reason}")
    for entry, reason in unused.items():
        emit(f"{engines.LEAK_RULE}: unused allowance {entry.rule} in {entry.file} (sha256 {entry.secret_sha256}) - {reason}")


def coverage_note(engine_dir: Path | None) -> str:
    skipped = skipped_rules(engine_dir)
    if skipped:
        return f"; skipped without --engines: {', '.join(skipped)}"
    return f"; {engines.WORKFLOW_SECURITY} runs offline, so zizmor's online-only audits do not run"


def run_baseline_write(path: Path) -> int:
    try:
        top = work_tree_top(path)
        sources = read_sources(top, tracked_files(top))
        written = ratchet.Baseline(ratchet.debt(ratchet.measure(sources).complete()), clones.find_pairs(sources).pairs)
        loosenings = describe_loosenings(top, written)
        repository.write_text(top, ratchet.BASELINE_PATH, ratchet.render_baseline(written))
    except (CannotJudge, repository.RepositoryFileError, ratchet.SizeError) as error:
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
        previous = read_baseline(path)
    except (CannotJudge, ratchet.SizeError) as error:
        return [f"the previous {ratchet.BASELINE_PATH} could not be read ({error}), so raises cannot be named"]
    if previous is None:
        return []
    lines = [entry.describe() for entry in ratchet.raised_entries(previous.size, written.size)]
    if previous.clones is not None:
        lines += [f"adds clone {pair.label}" for pair in ratchet.added_pairs(previous.clones, written.clones)]
    return lines


def run_scan_staged(path: Path, engine_dir: Path, read_pins: Callable[[], dict[str, engines.Pin]]) -> int:
    try:
        result = staged.scan_staged(path, engine_dir, read_pins())
    except (engines.EngineError, ConfigError) as error:
        emit(f"scan-staged: cannot judge - {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    print_secret_allowances(result.allowed, result.unused_allowances)
    for finding in result.findings:
        print(render(finding))
    emit(f"scan-staged: {len(result.findings)} finding(s)" if result.findings else "scan-staged: clean")
    return EXIT_FINDINGS if result.findings else EXIT_CLEAN


def run_engines_install(directory: Path, read_pins: Callable[[], dict[str, engines.Pin]]) -> int:
    try:
        pins = read_pins()
        downloaded = engines.install(directory, pins)
    except engines.EngineError as error:
        emit(f"engines: cannot install - {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    for name, pin in pins.items():
        emit(f"{name} {pin.version} {'installed' if downloaded[name] else 'already installed'}")
    return EXIT_CLEAN


def run_engines_verify(directory: Path, read_pins: Callable[[], dict[str, engines.Pin]]) -> int:
    try:
        problems = engines.verification_problems(directory, read_pins())
    except engines.EngineError as error:
        problems = [str(error)]
    if problems:
        emit(*(f"engines: {problem}" for problem in problems), file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    emit(f"engines: every pinned engine in {directory} is at its pinned version")
    return EXIT_CLEAN


def run_sonar_preflight(path: Path) -> int:
    try:
        preflight(path, os.environ)
    except CannotJudge as error:
        emit(f"sonar: cannot judge - {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    emit("sonar: preflight clean")
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
        print(render(finding))
    emit(*(headline(finding) for finding in result.findings.set_aside))
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


def run_scoreboard(path: Path, release_tags: scoreboard.TagReader) -> int:
    try:
        fleet = scoreboard.read_fleet(path)
    except scoreboard.FleetError as error:
        emit(f"scoreboard: cannot judge - {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    rows = scoreboard.measure_fleet(fleet)
    try:
        tags = release_tags()
    except scoreboard.ReleaseTagsError as error:
        tags = error
    report = scoreboard.render(rows, datetime.now(UTC).date(), tags)
    emit(*report)
    append_step_summary(report)
    return EXIT_CANNOT_JUDGE if scoreboard.cannot_judge(rows, tags) else EXIT_CLEAN


def append_step_summary(report: tuple[str, ...]) -> None:
    """In a GitHub Actions step, the run summary gets the same escaped lines as the log."""
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("".join(f"{printable(line)}\n" for line in report))


def run(door: Callable[[], int] = main, environment: Mapping[str, str] = os.environ) -> None:
    # stderr is line-buffered since Python 3.9, but a pipe makes stdout block-buffered, so `2>&1` showed stderr first.
    # stdout is None when fd 1 was closed before Python started.
    if sys.stdout is not None:
        sys.stdout.reconfigure(line_buffering=True)
    if environment.get("GITHUB_ACTIONS") == "true":
        stop_workflow_commands()
    sys.exit(door())


def stop_workflow_commands() -> None:
    """Repository text can spell a workflow command (`::warning::`, `##[add-mask]`), and the runner would run it.

    The stop lasts the rest of the step: the runner keeps the pause per step (actions/runner v2.338.0, `Handler.cs`),
    and the door never prints the line that lifts it. The runner reads the two pipes without order and handles stderr
    first (`ProcessInvoker.cs`), so each stream starts with the stop. The token is new per run and appears only in this
    line, which the runner masks.
    """
    stop = f"::stop-commands::{secrets.token_hex(16)}"
    for stream in (sys.stderr, sys.stdout):
        if stream is not None:
            print(stop, file=stream, flush=True)


if __name__ == "__main__":
    run()
