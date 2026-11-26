import json
import shlex
import tomllib

import pytest

from quality import scanners
from quality.report import FindingEvent
from quality.scanners import ScannerError
from tests.support.check import (
    assert_finding_vocabulary,
)
from tests.support.scanners import FINDING_EXIT, recorded, recorded_entry


@pytest.mark.parametrize(
    ("report", "exit_code", "expected"),
    [("gitleaks-clean.json", 0, []), ("gitleaks-finding.json", 7, ["secret deploy/config.txt:3"])],
)
def test_report_becomes_findings(report, exit_code, expected):
    """ROW-2: each leak in a recorded report becomes a secret finding at file:line."""
    findings = scanners.scanner_findings(scanners.GITLEAKS_SCANNER, recorded(report), exit_code, "")
    assert [f"{f.rule} {f.location}" for f in findings] == expected


@pytest.mark.parametrize(
    ("report", "exit_code"),
    [
        ("gitleaks-garbled.json", 7),
        ("gitleaks-garbled.json", 0),
        ("", 0),
        ("{}", 0),
        ('""', 0),
        ('[{"File": "a", "StartLine": 1, "RuleID": "r", "Commit": null}]', 7),
        ("gitleaks-clean.json", 7),
        ("gitleaks-finding.json", 0),
        ("gitleaks-clean.json", 1),
        ("gitleaks-finding.json", 126),
    ],
)
def test_unreadable_or_inconsistent_gitleaks_result_cannot_be_judged(report, exit_code):
    """A garbled, mis-shaped or inconsistent gitleaks result is a ScannerError, never clean."""
    text = recorded(report) if report.endswith(".json") else report
    with pytest.raises(ScannerError):
        scanners.scanner_findings(scanners.GITLEAKS_SCANNER, text, exit_code, "")


def workflow_findings(scanner: str, report_text: str, exit_code: int) -> list:
    return scanners.scanner_findings(next(judge for judge in scanners.WORKFLOW_SCANNERS.values() if judge.name == scanner), report_text, exit_code, "")


@pytest.mark.parametrize(
    ("scanner", "expected"),
    [
        ("actionlint", "workflow-lint .github/workflows/a.yml:4"),
        ("zizmor", "workflow-security .github/workflows/w.yml:7"),
    ],
)
def test_workflow_scanner_reports_become_findings(scanner, expected):
    """A recorded clean report is no finding; a recorded finding is one at file:line."""
    assert workflow_findings(scanner, recorded(f"{scanner}-clean.json"), 0) == []
    findings = workflow_findings(scanner, recorded(f"{scanner}-finding.json"), FINDING_EXIT[scanner])
    assert [f"{f.rule} {f.location}" for f in findings] == [expected]


@pytest.mark.parametrize(("scanner", "event"), [("actionlint", "INVALID"), ("zizmor", "INSECURE")])
def test_a_workflow_finding_carries_the_event_of_its_kind_and_the_vocabulary(scanner, event):
    """O36, ROW-7, ROW-14: one event word per kind, a fact and a fix that end in no full stop and name no rule id."""
    (found,) = workflow_findings(scanner, recorded(f"{scanner}-finding.json"), FINDING_EXIT[scanner])
    assert found.event is FindingEvent[event]
    assert_finding_vocabulary(found)


def test_an_actionlint_finding_names_the_scanner_and_holds_the_whole_message_as_its_one_detail_line():
    """O37: the message of the recorded report stands whole as the finding's detail."""
    (found,) = workflow_findings("actionlint", recorded("actionlint-finding.json"), FINDING_EXIT["actionlint"])
    assert (found.fact, found.detail) == (scanners.ACTIONLINT, (recorded_entry("actionlint")["message"],))


def test_a_zizmor_finding_names_its_audit_and_description_and_its_fix_holds_the_audit_url():
    """O36: the scenario's own values stand in the fact and the fix."""
    (found,) = workflow_findings("zizmor", recorded("zizmor-finding.json"), FINDING_EXIT["zizmor"])
    entry = recorded_entry("zizmor")
    assert (entry["ident"] in found.fact, entry["desc"] in found.fact, entry["url"] in found.fix) == (True, True, True)


@pytest.mark.parametrize("scanner", ["actionlint", "zizmor"])
@pytest.mark.parametrize(
    ("report", "exit_code"),
    [
        ("garbled", 0),
        ("garbled", "finding"),
        ("", 0),
        ("{}", 0),
        ('[{"unexpected": "shape"}]', "finding"),
        ('[{"message": null}]', "finding"),
        ("clean", "finding"),
        ("finding", 0),
        ("clean", 2),
        ("finding", 126),
    ],
)
def test_unreadable_or_inconsistent_workflow_result_cannot_be_judged(scanner, report, exit_code):
    """A garbled, cut-off, mis-shaped or inconsistent result is a ScannerError, never clean."""
    text = recorded(f"{scanner}-{report}.json") if report in ("garbled", "clean", "finding") else report
    code = FINDING_EXIT[scanner] if exit_code == "finding" else exit_code
    with pytest.raises(ScannerError):
        workflow_findings(scanner, text, code)


@pytest.mark.parametrize(
    ("scanner", "field", "value"), [("actionlint", "line", "4"), ("zizmor", "ident", 7)], ids=["actionlint line as text", "zizmor rule as number"],
)
def test_a_workflow_finding_with_a_field_of_the_wrong_type_cannot_be_judged(scanner, field, value):
    """A finding whose field has the wrong type is a ScannerError, never a finding at a made-up place."""
    entries = json.loads(recorded(f"{scanner}-finding.json"))
    entries[0][field] = value
    with pytest.raises(ScannerError):
        workflow_findings(scanner, json.dumps(entries), FINDING_EXIT[scanner])


def test_the_manifest_pins_each_scanner_once():
    """SCANNER-1: the manifest holds each of the three scanners once."""
    with (scanners.MANIFEST_DIRECTORY / "mise.toml").open("rb") as handle:
        tools = tomllib.load(handle)["tools"]
    assert sorted(key.rsplit("/", 1)[-1] for key in tools) == [scanners.ACTIONLINT, scanners.GITLEAKS, scanners.OSV_SCANNER]


def test_the_install_command_names_the_manifest_directory_as_one_shell_word(tmp_path, monkeypatch):
    """SCANNER-2: the install command names the manifest directory as one shell word."""
    monkeypatch.setattr(scanners, "MANIFEST_DIRECTORY", tmp_path / "with space")
    assert shlex.split(scanners.install_command()) == ["mise", "--cd", str(tmp_path / "with space"), "install", "--locked"]


def test_the_install_command_shows_a_path_no_shell_word_can_hold_escaped(tmp_path, monkeypatch):
    """SCANNER-3: a manifest path with a control character is printed escaped on one line."""
    monkeypatch.setattr(scanners, "MANIFEST_DIRECTORY", tmp_path / "a\nb")
    command = scanners.install_command()
    assert "\n" not in command
    assert "a\\x0ab" in command
