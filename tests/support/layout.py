"""Helpers of the layout rules' tests."""

import re
import subprocess

import pytest

from quality.report import Finding, FindingEvent
from quality.rules import Area, Rule
from tests.support.check import assert_row_prints_finding, hygiene_output, hygiene_run, rows_of, rule_findings

CONFIG = "quality/config.toml"
GUIDANCE = {"AGENTS.md": "# guidance\n"}
EXAMPLE_SHA = "1" * 40


def declaring(path: str, reason: str = "the build reads it by this name", rule: str = Rule.FILE_SUFFIX) -> str:
    return f'[[layout.allow]]\npath = "{path}"\nrule = "{rule}"\nreason = "{reason}"\n'


FILES_RULE_IDS = frozenset(rule.value for rule in Rule if rule.area is Area.FILES)


def rule_ids_in(text: str) -> set[str]:
    """The ids of the `files` area's rules that occur in `text` as words."""
    return set(re.findall(r"[a-z]+(?:-[a-z]+)*", text)) & FILES_RULE_IDS


def assert_row_is_kind(output: str, finding: Finding, event: FindingEvent, *named: str) -> None:
    """`finding` is of kind `event`, its fact names the scenario's own `named` values, neither fact nor fix ends in a period,
    no rule id stands in event or fact, and `output` holds the one row equal to it."""
    (row,) = rows_of(output, finding)
    assert finding.event is event
    assert [name for name in named if name not in finding.reason] == []
    assert not finding.reason.endswith(".")
    assert not finding.fix.endswith(".")
    assert rule_ids_in(f"{finding.event} {finding.reason}") == set()
    assert_row_prints_finding(row, finding)


def assert_prints_as_kind(capsys, repo, rule: Rule, event: FindingEvent, *named: str) -> Finding:
    """The one finding of `rule` over `repo` is of kind `event` and the door prints it as its row (`assert_row_is_kind`)."""
    (finding,) = rule_findings(repo, rule)
    assert_row_is_kind(hygiene_output(capsys, repo)[1], finding, event, *named)
    return finding


def assert_prints_escaped_row(capsys, repo, rule: Rule, location: str) -> None:
    """The one finding of `rule` over `repo` prints as one row at the escaped `location`, and no raw control reaches the output."""
    (finding,) = rule_findings(repo, rule)
    output = hygiene_output(capsys, repo)[1]
    assert (finding.location, len(rows_of(output, finding))) == (location, 1)
    assert "\x01" not in output


def assert_empty_name_has_its_own_fact(capsys, repo, rule: Rule, empty: str, named: str, name: str) -> None:
    """The fact for the path `empty` is of kind `misplaced` and is not the fact for `named` with its `name` taken out."""
    found = {finding.path: finding for finding in rule_findings(repo, rule)}
    assert_row_is_kind(hygiene_output(capsys, repo)[1], found[empty], FindingEvent.MISPLACED)
    assert found[empty].reason != found[named].reason.replace(name, "")


def in_index(repo, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(repo), *arguments], check=True)


def red(repo, rule: Rule = Rule.FILE_SUFFIX) -> list[str]:
    """The locations of the findings the rule runner returns for `rule` over `repo`; a run that cannot judge `rule` fails the test."""
    (verdict,) = (verdict for verdict in hygiene_run(repo).verdicts if verdict.rule is rule)
    assert verdict.refusal is None
    return [finding.location for finding in verdict.findings]


def empty_files(*paths: str) -> dict[str, str]:
    """The given tracked paths, all empty."""
    return dict.fromkeys(paths, "")


def case(identifier: str, files: dict[str, str], expected: list[str]):
    """One row of a family that maps tracked `files` to the paths a rule reports."""
    return pytest.param(files, expected, id=identifier)
