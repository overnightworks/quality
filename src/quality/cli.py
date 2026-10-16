import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

from quality import __version__, engines, staged
from quality.config import Allowance, ConfigError, read_mandatory_jobs
from quality.hygiene import CannotJudge, active_rules, run_hygiene, skipped_rules
from quality.report import Finding, render
from quality.sonar import TASK_FILE, Sleep, Transport, analyse, fetch_json, preflight
from quality.verdict import (
    PageFetcher,
    github_fetcher,
    judge,
    read_base_mandatory_jobs,
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
) -> int:
    parser = argparse.ArgumentParser(prog="quality")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    hygiene = commands.add_parser("hygiene", help="check a git repository for fleet hygiene")
    hygiene.add_argument("path", nargs="?", type=Path, default=Path("."))
    hygiene.add_argument("--engines", type=Path, help="directory holding the installed engines; enables the secret and workflow scans")
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
    sonar.add_parser("findings", help=f"wait for this run's analysis ({TASK_FILE}), then count its open findings")
    arguments = parser.parse_args(argv)

    if arguments.command == "verdict":
        return run_verdict(arguments.path, fetcher_for)
    if arguments.command == "scan-staged":
        return run_scan_staged(arguments.path, arguments.engines, read_pins)
    if arguments.command == "sonar":
        if arguments.sonar_command == "preflight":
            return run_sonar_preflight(arguments.path)
        return run_sonar_findings(transport, sleep)
    if arguments.command == "engines":
        run_engines = run_engines_install if arguments.engines_command == "install" else run_engines_verify
        return run_engines(arguments.directory, read_pins)
    try:
        result = run_hygiene(arguments.path, arguments.engines)
    except CannotJudge as error:
        print(f"hygiene: cannot judge - {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    findings = result.findings
    print_secret_allowances(result.allowed_secrets, result.unused_allowances)
    if not findings:
        print(f"hygiene: clean ({len(active_rules(arguments.engines))} rules){coverage_note(arguments.engines)}")
        return EXIT_CLEAN
    for finding in findings:
        print(render(finding))
    print(f"hygiene: {len(findings)} finding(s){coverage_note(arguments.engines)}")
    return EXIT_FINDINGS


def print_secret_allowances(allowed: list[Finding], unused: dict[Allowance, str]) -> None:
    if allowed:
        print(f"{engines.SECRET}: {len(allowed)} allowed ({', '.join(finding.location for finding in allowed)})")
        for finding in allowed:
            print(f"  allowed {finding.location} - {finding.reason}")
    for entry, reason in unused.items():
        print(f"{engines.SECRET}: unused allowance {entry.rule} in {entry.file} (sha256 {entry.secret_sha256}) - {reason}")


def coverage_note(engine_dir: Path | None) -> str:
    skipped = skipped_rules(engine_dir)
    if skipped:
        return f"; skipped without --engines: {', '.join(skipped)}"
    return f"; {engines.WORKFLOW_SECURITY} runs offline, so zizmor's online-only audits do not run"


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
        result = analyse(os.environ, transport, sleep)
    except CannotJudge as error:
        print(f"sonar: cannot judge - {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    for finding in result.findings.listed:
        print(render(finding))
    print(result.summary(), file=sys.stderr if result.fails_the_run else sys.stdout)
    return EXIT_FINDINGS if result.fails_the_run else EXIT_CLEAN


def run_verdict(path: Path, fetcher_for: Callable[[str], PageFetcher]) -> int:
    try:
        mandatory = read_mandatory_jobs(path)
        base_sha = os.environ.get("GATE_BASE_SHA", "")
        base_mandatory = read_base_mandatory_jobs(path, base_sha) if base_sha else ()
        jobs = read_jobs(
            os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_RUN_ID"], fetcher_for(os.environ["GITHUB_TOKEN"])
        )
        gate_results = {name: need["result"] for name, need in json.loads(os.environ["GATE_NEEDS"]).items()}
        result = judge(mandatory, base_mandatory, jobs, gate_results, int(os.environ["GITHUB_RUN_ATTEMPT"]))
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
