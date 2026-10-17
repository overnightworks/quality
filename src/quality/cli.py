import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from quality import __version__, engines, ratchet, staged
from quality.config import Allowance, ConfigError, read_gate_settings
from quality.emergency import Ask, GhRunner, run_emergency_merge, run_gh
from quality.hygiene import (
    CannotJudge,
    active_rules,
    measure_size,
    run_hygiene,
    skipped_rules,
    tracked_files,
    write_repository_text,
)
from quality.report import Finding, render
from quality.sonar import TASK_FILE, Sleep, Transport, analyse, fetch_json, preflight
from quality.verdict import (
    NO_BASE_SETTINGS,
    PageFetcher,
    github_fetcher,
    judge,
    read_base_settings,
    read_jobs,
)

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_CANNOT_JUDGE = 2


def main(
    argv: list[str] | None = None,
    fetcher_for: Callable[[str], PageFetcher] = github_fetcher,
    read_pins: Callable[[], dict[str, engines.Pin]] = engines.read_pins,
    transport: Transport = fetch_json,
    sleep: Sleep = time.sleep,
    gh: GhRunner = run_gh,
    ask: Ask = input,
    is_terminal: Callable[[], bool] = lambda: sys.stdin.isatty(),
) -> int:
    parser = argparse.ArgumentParser(prog="quality")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    hygiene = commands.add_parser("hygiene", help="check a git repository for fleet hygiene")
    hygiene.add_argument("path", nargs="?", type=Path, default=Path("."))
    hygiene.add_argument("--engines", type=Path, help="directory holding the installed engines; enables the secret and workflow scans")
    baseline = commands.add_parser("baseline", help="manage the size baseline").add_subparsers(
        dest="baseline_command", required=True
    )
    baseline.add_parser("write", help=f"write the measured size debt to {ratchet.BASELINE_PATH}").add_argument(
        "path", nargs="?", type=Path, default=Path("."), help="the git repository to measure (default: the current one)"
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
    arguments = parser.parse_args(argv)

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
        )
    if arguments.command == "verdict":
        return run_verdict(arguments.path, fetcher_for)
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


def run_hygiene_check(path: Path, engine_dir: Path | None) -> int:
    try:
        result = run_hygiene(path, engine_dir)
    except CannotJudge as error:
        print(f"hygiene: cannot judge - {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    findings = result.findings
    print_secret_allowances(result.allowed_secrets, result.unused_allowances)
    if result.size_report:
        print(result.size_report)
    if not findings:
        print(f"hygiene: clean ({len(active_rules(engine_dir))} rules){coverage_note(engine_dir)}")
        return EXIT_CLEAN
    for finding in findings:
        print(render(finding))
    print(f"hygiene: {len(findings)} finding(s){coverage_note(engine_dir)}")
    return EXIT_FINDINGS


def print_secret_allowances(allowed: list[Finding], unused: dict[Allowance, str]) -> None:
    if allowed:
        print(f"{engines.LEAK_RULE}: {len(allowed)} allowed ({', '.join(finding.location for finding in allowed)})")
        for finding in allowed:
            print(f"  allowed {finding.location} - {finding.reason}")
    for entry, reason in unused.items():
        print(f"{engines.LEAK_RULE}: unused allowance {entry.rule} in {entry.file} (sha256 {entry.secret_sha256}) - {reason}")


def coverage_note(engine_dir: Path | None) -> str:
    skipped = skipped_rules(engine_dir)
    if skipped:
        return f"; skipped without --engines: {', '.join(skipped)}"
    return f"; {engines.WORKFLOW_SECURITY} runs offline, so zizmor's online-only audits do not run"


def run_baseline_write(path: Path) -> int:
    try:
        entries = ratchet.debt(measure_size(path, tracked_files(path)).complete())
        write_repository_text(path, ratchet.BASELINE_PATH, ratchet.render_baseline(entries))
    except (CannotJudge, ratchet.SizeError) as error:
        print(f"baseline: not written - {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    count = "1 entry" if len(entries) == 1 else f"{len(entries)} entries"
    print(f"baseline: wrote {count} to {path / ratchet.BASELINE_PATH}")
    return EXIT_CLEAN


def run_scan_staged(path: Path, engine_dir: Path, read_pins: Callable[[], dict[str, engines.Pin]]) -> int:
    try:
        result = staged.scan_staged(path, engine_dir, read_pins())
    except (engines.EngineError, ConfigError) as error:
        print(f"scan-staged: cannot judge - {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    print_secret_allowances(result.allowed, result.unused_allowances)
    for finding in result.findings:
        print(render(finding))
    print(f"scan-staged: {len(result.findings)} finding(s)" if result.findings else "scan-staged: clean")
    return EXIT_FINDINGS if result.findings else EXIT_CLEAN


def run_engines_install(directory: Path, read_pins: Callable[[], dict[str, engines.Pin]]) -> int:
    pins = read_pins()
    try:
        downloaded = engines.install(directory, pins)
    except engines.EngineError as error:
        print(f"engines: cannot install - {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    for name, pin in pins.items():
        print(f"{name} {pin.version} {'installed' if downloaded[name] else 'already installed'}")
    return EXIT_CLEAN


def run_engines_verify(directory: Path, read_pins: Callable[[], dict[str, engines.Pin]]) -> int:
    problems = engines.verification_problems(directory, read_pins())
    if problems:
        print("\n".join(f"engines: {problem}" for problem in problems), file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    print(f"engines: every pinned engine in {directory} is at its pinned version")
    return EXIT_CLEAN


def run_sonar_preflight(path: Path) -> int:
    try:
        preflight(path, os.environ)
    except CannotJudge as error:
        print(f"sonar: cannot judge - {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    print("sonar: preflight clean")
    return EXIT_CLEAN


def run_sonar_findings(transport: Transport, sleep: Sleep) -> int:
    try:
        result = analyse(os.environ, read_gate_settings(Path(".")).legacy, transport, sleep)
    except (CannotJudge, ConfigError) as error:
        print(f"sonar: cannot judge - {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    for finding in result.findings.listed:
        print(render(finding))
    print(result.summary(), file=sys.stderr if result.fails_the_run else sys.stdout)
    return EXIT_FINDINGS if result.fails_the_run else EXIT_CLEAN


def run_verdict(path: Path, fetcher_for: Callable[[str], PageFetcher]) -> int:
    try:
        settings = read_gate_settings(path)
        base_sha = os.environ.get("GATE_BASE_SHA", "")
        base_settings = read_base_settings(path, base_sha) if base_sha else NO_BASE_SETTINGS
        jobs = read_jobs(
            os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_RUN_ID"], fetcher_for(os.environ["GITHUB_TOKEN"])
        )
        gate_results = {name: need["result"] for name, need in json.loads(os.environ["GATE_NEEDS"]).items()}
        result = judge(settings, base_settings, jobs, gate_results, int(os.environ["GITHUB_RUN_ATTEMPT"]))
    except (ConfigError, CannotJudge, KeyError, ValueError) as error:
        report = f"verdict: cannot judge - {error}"
        code = EXIT_CANNOT_JUDGE
    else:
        report = "verdict: green" if result.green else "\n".join((*result.problems, "verdict: red"))
        code = EXIT_CLEAN if result.green else EXIT_FINDINGS
    print(report, file=sys.stderr if code == EXIT_CANNOT_JUDGE else sys.stdout)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(report + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
