"""The help texts of the quality commands and the area list that CHECK_HELP is built from."""

import textwrap

from quality import baseline_file, fleet, sonar
from quality.rules import Area

QUALITY_HELP = """\
usage: quality COMMAND [-v]

Commands:
  check [AREA ...] [--staged] [--fix] [--dry-run]
      Check this repository against the fleet's rules, with the quality
      release its gate pins.
  merge OWNER/REPO#N [--dry-run]
      Operator only: merge one pull request whose gate is red only for
      legacy findings or loosenings he ruled, after he types yes.
  report
      Measure every fleet repository: size, debt, the gate release it pins.

Run by the gate, not by people:
  verdict
      Decide whether this pull request may merge.
  sonar properties
      Refuse a properties file, token or coverage report the scan must not
      start with.
  sonar findings
      Wait for this run's analysis, then count its open findings.

Options:
  --version   the installed version and the release this repository's gate pins
  -h, --help  show this help

Every command takes -v: the raw scanner output, never a secret.
Exit codes: 0 yes, 1 no, 2 could not answer, 130 interrupted.
"""

AREA_COLUMN = max(map(len, Area))
AREA_HELP_WIDTH = 76


def _area_entry(area: Area) -> str:
    return textwrap.fill(area.help_line, AREA_HELP_WIDTH, initial_indent=f"  {area:<{AREA_COLUMN}}  ", subsequent_indent=" " * (AREA_COLUMN + 4))


AREA_ENTRIES = "\n".join(map(_area_entry, Area))

CHECK_HELP = f"""\
usage: quality check [AREA ...] [--staged] [--fix] [--dry-run] [-v]

Check this repository against the fleet's rules, with the quality release
its gate pins.

Areas (default: all):
{AREA_ENTRIES}

Options:
  --staged      secrets in exactly what the next commit records; the commit
                hook runs: quality check secrets --staged
  --fix         lower the debt ceiling ({baseline_file.BASELINE_PATH}) where code
                shrank, or record it the first time; never raises it
  --dry-run     with --fix: show what would change, write nothing
  -v            show the raw scanner output, never a secret
  -h, --help    show this help

Exit codes: 0 clean, 1 findings, 2 could not check, 130 interrupted.
"""

MERGE_HELP = """\
usage: quality merge OWNER/REPO#N [--dry-run] [-v]

Operator only: merge one pull request whose gate is red only for legacy
findings or loosenings he ruled, after he types yes.

Options:
  --dry-run   show the rows, then stop before the question: nothing changes,
              exit 0 where the door would merge
  -v          show the raw scanner output; the door runs no scanner
  -h, --help  show this help

Exit codes: 0 merged, 1 refused, 2 could not check or do, 130 interrupted.
"""

REPORT_HELP = f"""\
usage: quality report [-v]

Measure every fleet repository {fleet.FLEET_PATH} selects: size, debt, the gate
release it pins. A row per repository goes to stderr, the two markdown tables
to stdout and to the step summary.

Options:
  -v          show the raw scanner output; the report runs no scanner
  -h, --help  show this help

Exit codes: 0 every repository measured, 2 could not answer, 130 interrupted.
"""


VERDICT_HELP = """\
usage: quality verdict [-v]

Run by the gate, not by people: decide whether this pull request may merge,
from the jobs of the running workflow and the loosenings of this checkout
against the pull request's base.

Options:
  -v          show the raw scanner output; the verdict runs no scanner
  -h, --help  show this help

Exit codes: 0 green, 1 red, 2 could not judge, 130 interrupted.
"""

SONAR_HELP = """\
usage: quality sonar COMMAND [-v]

Run by the gate, not by people: the two steps of gate.yml's sonar job.

Commands:
  properties
      Refuse a properties file, token or coverage report the scan must not
      start with, then hand the scan its arguments.
  findings
      Wait for this run's analysis, then judge its open findings and its
      quality gate.

Options:
  -h, --help  show this help

Exit codes: 0 clean, 1 findings, 2 could not judge, 130 interrupted.
"""

SONAR_PROPERTIES_HELP = """\
usage: quality sonar properties [-v]

Run by the gate before the scan: refuse a sonar-project.properties, token,
coverage report or tracked .scannerwork/ the scan must not start with, then
hand the scan its arguments in the step output. With [gate] sonar = false in
quality/config.toml it refuses a properties file or a passed token instead.

Options:
  -v          show the raw scanner output; the step runs no scanner
  -h, --help  show this help

Exit codes: 0 clean, 2 could not judge, 130 interrupted.
"""

SONAR_FINDINGS_HELP = f"""\
usage: quality sonar findings [-v]

Run by the gate after the scan: wait for this run's own analysis
({sonar.TASK_FILE}), then judge its open findings, the default
branch's legacy count and, on a pull request, SonarCloud's quality gate. It
needs quality/config.toml with [gate] mandatory_jobs.

Options:
  -v          show the raw scanner output; the step runs no scanner
  -h, --help  show this help

Exit codes: 0 clean or report only, 1 findings, 2 could not judge,
130 interrupted.
"""
