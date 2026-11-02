import copy
import datetime
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest

from quality import advisories, check, cli, scanners
from quality.config import ADVISORY_ALLOWANCE_HEADING, CONFIG_PATH
from quality.output import SEPARATOR
from quality.report import FindingEvent, printable, shell_word
from quality.rules import Area, Rule
from tests.support.advisories import (
    ADVISORY_ID,
    ADVISORY_LINK,
    OSV,
    UV_LOCK_ALLOWANCE,
    allowance_config,
    drop_fixed_versions,
    linked_lock_repo,
    npm_report,
    package_of,
    reporting,
    upgrade_fix,
    uv_lock,
)
from tests.support.check import (
    BlockLine,
    FindingLine,
    FixLine,
    area_block,
    area_row,
    assert_block_prints,
    assert_every_line_starts_with_the_door,
    assert_finding_vocabulary,
    hygiene_run,
)
from tests.support.scanners import OUTPUTS, fake_scanner, lacking

FINDING_REPORT = OUTPUTS / "osv-scanner-finding.json"
RECORDED = package_of(json.loads(FINDING_REPORT.read_text(encoding="utf-8")))
VERSION = RECORDED["package"]["version"]
FIRST_ID = RECORDED["groups"][0]["ids"][0]
ALIAS = RECORDED["groups"][0]["aliases"][0]
FIXED_VERSION = "5.4"
PNPM_LOCK = "lockfileVersion: '9.0'\n"
OTHER_PACKAGE = "other-package"


@dataclass(frozen=True)
class AdvisoryRun:
    repo: Path
    code: int
    out: str
    err: str

    @property
    def areas(self) -> tuple[check.AreaVerdict, ...]:
        return hygiene_run(self.repo).areas

    @property
    def area(self) -> check.AreaVerdict:
        return next(verdict for verdict in self.areas if verdict.area is Area.DEPENDENCIES)


@pytest.fixture
def drive_advisories(build_repo, served_scanners, capsys):
    """`drive(files, report, code=1, stderr="")` runs `hygiene` over a repository tracking `files` (or over the repository
    given), with a fake osv-scanner printing `report` and exiting `code`."""

    def drive(files: dict[str, str] | Path, report: Path = FINDING_REPORT, code: int = 1, stderr: str = "") -> AdvisoryRun:
        repo = files if isinstance(files, Path) else build_repo(files)
        fake_scanner(served_scanners, OSV, reporting(report, code, stderr))
        exit_code = cli.main(["hygiene", str(repo)], clock=lambda: 0.0)
        captured = capsys.readouterr()
        return AdvisoryRun(repo, exit_code, captured.out, captured.err)

    return drive


def rename_package(name: str):
    def edit(report: dict) -> None:
        found = package_of(report)
        found["package"]["name"] = name
        for vulnerability in found["vulnerabilities"]:
            vulnerability["affected"][0]["package"]["name"] = name
    return edit


def rename_affected_entry(name: str):
    def edit(report: dict) -> None:
        for vulnerability in package_of(report)["vulnerabilities"]:
            vulnerability["affected"][0]["package"]["name"] = name
    return edit


def add_fixed_version(version: str):
    def edit(report: dict) -> None:
        for vulnerability in package_of(report)["vulnerabilities"]:
            vulnerability["affected"][0]["ranges"][0]["events"].append({"fixed": version})
    return edit


def add_second_group(report: dict) -> None:
    found = package_of(report)
    second = "GHSA-dddd-eeee-ffff"
    found["groups"].append({**found["groups"][0], "ids": [second], "aliases": []})
    found["vulnerabilities"].append({**copy.deepcopy(found["vulnerabilities"][0]), "id": second, "aliases": []})


def add_second_package(report: dict) -> None:
    """A copy of the report's package under another name, as listed and as osv's record names it."""
    original = package_of(report)
    second = copy.deepcopy(original)
    second["package"]["name"] = OTHER_PACKAGE
    for vulnerability in second["vulnerabilities"]:
        vulnerability["affected"][0]["package"]["name"] = OTHER_PACKAGE
    report["results"][0]["packages"].append(second)


def then(*edits):
    def edit(report: dict) -> None:
        for step in edits:
            step(report)
    return edit


def points_only_to_the_advisory(name: str):
    return lambda fix: fix.endswith(ADVISORY_LINK) and name not in fix and printable(name) not in fix


def rows_and_fixes_of(block: list[BlockLine]) -> tuple[int, int]:
    return sum(isinstance(line, FindingLine) for line in block), sum(isinstance(line, FixLine) for line in block)


@pytest.mark.parametrize(
    ("edit", "name", "fixed", "fix_is"),
    [
        (lambda report: None, "pyyaml", (FIXED_VERSION,), lambda fix: fix == upgrade_fix()),
        (add_fixed_version("6.0"), "pyyaml", (FIXED_VERSION, "6.0"), lambda fix: fix == upgrade_fix()),
        (drop_fixed_versions, "pyyaml", (), lambda fix: "pyyaml" in fix and fix.endswith(ADVISORY_LINK)),
        (rename_affected_entry("something-else"), "pyyaml", None, points_only_to_the_advisory("pyyaml")),
        (
            then(rename_package("py;yaml"), add_fixed_version("6.0")), "py;yaml", (FIXED_VERSION, "6.0"),
            lambda fix: fix == upgrade_fix(package=shell_word("py;yaml")),
        ),
        (
            then(rename_package("py\x1byaml"), add_fixed_version("6.0")), "py\x1byaml", (FIXED_VERSION, "6.0"),
            points_only_to_the_advisory("py\x1byaml"),
        ),
    ],
    ids=["one fixed version", "several", "none listed", "no affected entry for the package", "a shell metacharacter", "an escape character"],
)
def test_each_kind_of_advisory_finding_carries_its_event_fact_and_fix(drive_advisories, edited_report, edit, name, fixed, fix_is):
    """Decision 3, O41: the event, the fact from the report's own values and the fix per kind."""
    run = drive_advisories({"uv.lock": uv_lock()}, edited_report("osv-scanner-finding.json", edit))
    (finding,) = run.area.findings
    package, identifier, *state = finding.reason.split(SEPARATOR)
    assert (finding.location, finding.event, package, identifier) == ("uv.lock", FindingEvent.AFFECTED, f"{name} {VERSION}", FIRST_ID)
    if fixed is None:
        assert state == []
    elif fixed:
        position = 0
        for version in fixed:
            position = state[0].index(version, position) + len(version)
    else:
        assert state == [advisories.NO_FIXED_VERSION]
    assert fix_is(finding.fix)
    assert_finding_vocabulary(finding)
    assert_block_prints(area_block(run.out, Area.DEPENDENCIES), run.area.findings, ())
    assert (run.code, run.err) == (1, "")


def test_the_row_names_one_id_of_a_group_with_aliases(drive_advisories):
    """O42: the row holds the group's first id and no alias."""
    run = drive_advisories({"uv.lock": uv_lock()})
    assert (FIRST_ID in run.out, ALIAS in run.out + run.err, run.err) == (True, False, "")


def test_the_dependencies_block_prints_what_the_rule_returned(drive_advisories):
    """Decision 4: the rows and the fix equal the rule's findings."""
    run = drive_advisories({"uv.lock": uv_lock()})
    assert_block_prints(area_block(run.out, Area.DEPENDENCIES), run.area.findings, ())
    assert (run.out.splitlines()[0].split()[1], run.code, run.err) == (next(iter(Area)), 1, "")


def test_no_printed_line_starts_with_the_rule_id(drive_advisories):
    """Decision 12: quality adds no rule-id label to a finding."""
    run = drive_advisories({"uv.lock": uv_lock()})
    assert not any(line.lstrip().startswith(Rule.ADVISORY) for line in (run.out + run.err).splitlines())
    assert run.err == ""


PNPM_PACKAGE = npm_report("lodash", "lodash")


@pytest.mark.parametrize(
    ("files", "edit", "rows_and_fixes"),
    [
        ({"uv.lock": uv_lock()}, add_second_group, (2, 1)),
        ({"uv.lock": uv_lock()}, add_second_package, (2, 2)),
        ({"src/frontend/pnpm-lock.yaml": PNPM_LOCK}, then(PNPM_PACKAGE, add_second_package), (2, 1)),
        ({"docs/requirements.txt": f"pyyaml=={VERSION}\n{OTHER_PACKAGE}=={VERSION}\n"}, add_second_package, (2, 1)),
    ],
    ids=["two groups of one uv package", "two uv packages", "two pnpm packages", "two requirements pins"],
)
def test_equal_fixes_share_one_fix_line_and_different_ones_do_not(drive_advisories, edited_report, files, edit, rows_and_fixes):
    """Rule 7: one `fix:` per run of equal fixes, and a uv fix names its package."""
    run = drive_advisories(files, edited_report("osv-scanner-finding.json", edit))
    block = area_block(run.out, Area.DEPENDENCIES)
    assert_block_prints(block, run.area.findings, ())
    assert (rows_and_fixes_of(block), run.code, run.err) == (rows_and_fixes, 1, "")


@pytest.mark.parametrize(
    ("lock", "files", "edit", "package"),
    [
        ("uv.lock", {"uv.lock": uv_lock()}, lambda report: None, "pyyaml"),
        ("src/speech/uv.lock", {"src/speech/uv.lock": uv_lock()}, lambda report: None, "pyyaml"),
        ("src/frontend/pnpm-lock.yaml", {"src/frontend/pnpm-lock.yaml": PNPM_LOCK}, PNPM_PACKAGE, "lodash"),
        ("docs/requirements.txt", {"docs/requirements.txt": f"pyyaml=={VERSION}\n"}, lambda report: None, "pyyaml"),
    ],
    ids=["uv.lock", "a nested uv.lock", "a nested pnpm lock", "a requirements file"],
)
def test_the_fix_of_each_lock_kind_holds_its_hint_and_its_folder(drive_advisories, edited_report, lock, files, edit, package):
    """O43, O44: the root lock's fix is the hint, a nested lock's ends with it and names its directory."""
    run = drive_advisories(files, edited_report("osv-scanner-finding.json", edit))
    (finding,) = run.area.findings
    directory = str(PurePosixPath(lock).parent)
    hint = upgrade_fix(lock, package)
    assert (finding.location, finding.fix == hint if directory == "." else (finding.fix.endswith(hint) and directory in finding.fix)) == (lock, True)
    assert_block_prints(area_block(run.out, Area.DEPENDENCIES), run.area.findings, ())
    assert (run.code, run.err) == (1, "")


def test_a_nested_lock_keeps_its_folder_when_the_package_has_no_shell_word(drive_advisories, edited_report):
    """O41, O43: the link replaces the command, and the folder stays."""
    name, lock = "py\x1byaml", "src/speech/uv.lock"
    run = drive_advisories({lock: uv_lock()}, edited_report("osv-scanner-finding.json", then(rename_package(name), add_fixed_version("6.0"))))
    (finding,) = run.area.findings
    assert (points_only_to_the_advisory(name)(finding.fix), str(PurePosixPath(lock).parent) in finding.fix) == (True, True)
    assert_block_prints(area_block(run.out, Area.DEPENDENCIES), run.area.findings, ())
    assert (run.code, run.err) == (1, "")


def test_two_tracked_names_of_one_lock_print_one_row_each_and_align(drive_advisories, build_repo):
    """Decision 4: one row per tracked name, the events at one column."""
    run = drive_advisories(linked_lock_repo(build_repo))
    rows = [line for line in area_block(run.out, Area.DEPENDENCIES) if isinstance(line, FindingLine)]
    columns = {line.text.index(line.row.event.value, line.text.index(line.location) + len(line.location)) for line in rows}
    assert (sorted(line.location for line in rows), len(columns), run.err) == (["speech/uv.lock", "uv.lock"], 1, "")


def entry(identifier: str = ADVISORY_ID, lock: str = "uv.lock", until: datetime.date = datetime.date(2999, 12, 31)) -> str:
    return allowance_config(**UV_LOCK_ALLOWANCE | {"identifier": identifier, "lock": lock}, until=until)


@pytest.mark.parametrize(
    ("with_lock", "config", "expected", "words"),
    [
        (True, entry(until=datetime.date(2000, 1, 1)), [("uv.lock", FindingEvent.AFFECTED)], ["2000-01-01", UV_LOCK_ALLOWANCE["item"], FIXED_VERSION]),
        (
            True, entry() + entry(ALIAS),
            [("uv.lock", FindingEvent.AFFECTED), (CONFIG_PATH, FindingEvent.CONFLICT)], [ADVISORY_ID, ALIAS, "uv.lock"],
        ),
        (
            True, entry("GHSA-dddd-eeee-ffff"),
            [("uv.lock", FindingEvent.AFFECTED), (CONFIG_PATH, FindingEvent.UNUSED)], ["GHSA-dddd-eeee-ffff", "uv.lock"],
        ),
        (False, entry(), [(CONFIG_PATH, FindingEvent.UNUSED)], [ADVISORY_ID, "uv.lock"]),
    ],
    ids=["an allowance that ran out", "two allowances on one group", "an allowance no advisory matches", "an allowance with no lock"],
)
def test_each_allowance_finding_stands_as_a_row(drive_advisories, with_lock, config, expected, words):
    """O45, O46: a ran-out allowance stays an affected row, a conflict and an unused entry stand at the config."""
    files = {"quality/config.toml": config, **({"uv.lock": uv_lock()} if with_lock else {})}
    run = drive_advisories(files)
    findings = run.area.findings
    assert [(finding.location, finding.event) for finding in findings] == expected
    assert all(word in findings[-1].reason for word in words)
    assert ADVISORY_ALLOWANCE_HEADING in findings[-1].fix or findings[-1].event is FindingEvent.AFFECTED
    for finding in findings:
        assert_finding_vocabulary(finding)
    assert_block_prints(area_block(run.out, Area.DEPENDENCIES), findings, ())
    assert (run.code, run.err) == (1, "")


def test_a_lock_path_and_an_allowance_with_control_characters_print_escaped_and_forge_no_line(drive_advisories, build_repo, tmp_path):
    """The location, the fix's folder and an allowance's fact pass through `printable`."""
    name = "src/a\x1b\u202eb/uv.lock"
    repo = build_repo({name: uv_lock(), "quality/config.toml": entry("GHSA-\x1b[8m", "a\u202eb/uv.lock")})
    report = json.loads(FINDING_REPORT.read_text(encoding="utf-8"))
    report["results"][0]["source"]["path"] = str((repo / name).resolve())
    report_file = tmp_path / "escaped-report.json"
    report_file.write_text(json.dumps(report), encoding="utf-8")
    run = drive_advisories(repo, report_file)
    block = area_block(run.out, Area.DEPENDENCIES)
    assert_block_prints(block, run.area.findings, ())
    assert printable(str(PurePosixPath(name).parent)) in next(line.text for line in block if isinstance(line, FixLine))
    assert_every_line_starts_with_the_door((run.out + run.err).splitlines())
    assert (run.code, run.err) == (1, "")


def test_a_refused_area_leaves_the_dependencies_rows_and_the_pair_names_them_in_the_others(drive_advisories, use_mise, served_scanners):
    """Decision 6: another area's refusal prints the dependencies rows and counts them in the error line."""
    use_mise(lacking(served_scanners, scanners.GITLEAKS))
    run = drive_advisories({"quality/config.toml": entry()})
    assert_block_prints(area_block(run.out, Area.DEPENDENCIES), run.area.findings, ())
    assert (run.code, run.err) == (2, f"error: {cli.refusal_error(run.areas)}\nnext: {scanners.install_command()}\n")


def test_a_refusing_osv_scanner_prints_no_row_under_its_area_and_the_exact_pair(drive_advisories):
    """A rule that cannot judge refuses only itself: its row names the reason and nothing stands under it."""
    run = drive_advisories({"uv.lock": uv_lock()}, code=3, stderr="boom")
    row = area_row(run.out, Area.DEPENDENCIES)
    assert (row.event, row.fact.endswith("boom"), area_block(run.out, Area.DEPENDENCIES)) == (cli.AreaEvent.NOT_CHECKED, True, [])
    assert (run.code, run.err) == (2, f"error: {cli.refusal_error(run.areas)}\nnext: {cli.REFUSED_AREAS_NEXT.text}\n")
