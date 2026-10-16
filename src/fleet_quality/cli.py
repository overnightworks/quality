import argparse
import sys
from pathlib import Path

from fleet_quality import __version__
from fleet_quality.hygiene import RULE_IDS, CannotJudge, run_hygiene
from fleet_quality.report import render

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_CANNOT_JUDGE = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fleet-quality")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    hygiene = commands.add_parser("hygiene", help="check a git repository for fleet hygiene")
    hygiene.add_argument("path", nargs="?", type=Path, default=Path("."))
    arguments = parser.parse_args(argv)

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


if __name__ == "__main__":
    sys.exit(main())
