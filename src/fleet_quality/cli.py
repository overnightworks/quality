import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

from fleet_quality import __version__
from fleet_quality.config import ConfigError, read_mandatory_jobs
from fleet_quality.hygiene import RULE_IDS, CannotJudge, run_hygiene
from fleet_quality.report import render
from fleet_quality.verdict import (
    PageFetcher,
    github_fetcher,
    judge,
    read_base_mandatory_jobs,
    read_jobs,
)

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_CANNOT_JUDGE = 2


def main(argv: list[str] | None = None, fetcher_for: Callable[[str], PageFetcher] = github_fetcher) -> int:
    parser = argparse.ArgumentParser(prog="fleet-quality")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    hygiene = commands.add_parser("hygiene", help="check a git repository for fleet hygiene")
    hygiene.add_argument("path", nargs="?", type=Path, default=Path("."))
    verdict = commands.add_parser("verdict", help="judge the mandatory jobs of the running workflow run")
    verdict.add_argument("path", nargs="?", type=Path, default=Path("."))
    arguments = parser.parse_args(argv)

    if arguments.command == "verdict":
        return run_verdict(arguments.path, fetcher_for)
    try:
        findings = run_hygiene(arguments.path)
    except CannotJudge as error:
        print(f"hygiene: cannot judge - {error}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    if not findings:
        print(f"hygiene: clean ({len(RULE_IDS)} rules)")
        return EXIT_CLEAN
    for finding in findings:
        print(render(finding))
    print(f"hygiene: {len(findings)} finding(s)")
    return EXIT_FINDINGS


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
