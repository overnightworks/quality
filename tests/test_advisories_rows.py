import copy
import datetime
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest

from quality import advisories, check, cli, scanners
from quality.config import ADVISORY_ALLOWANCE_HEADING, CONFIG_PATH
from quality.output import SEPARATOR, Glyph
from quality.report import FindingEvent, NoteEvent, printable, shell_word
from quality.rules import Area, Rule
from tests.support.advisories import (
    ADVISORY_ID,
    ADVISORY_LINK,
    OSV,
    PACKAGE_NAME,
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
    area_fact,
    area_row,
    assert_block_prints,
    assert_every_line_starts_with_the_door,
    assert_finding_vocabulary,
    extent_counts,
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


def entry(
    identifier: str = ADVISORY_ID, lock: str = "uv.lock", until: datetime.date = datetime.date(2999, 12, 31),
    reason: str = UV_LOCK_ALLOWANCE["reason"],
) -> str:
    return allowance_config(**UV_LOCK_ALLOWANCE | {"identifier": identifier, "lock": lock, "reason": reason}, until=until)


def no_advisories(report: dict) -> None:
    """Every listed package of every result without a group or a vulnerability record."""
    for result in report["results"]:
        for found in result["packages"]:
            found.pop("groups")
            found.pop("vulnerabilities")


def without_vulnerability_records(report: dict) -> None:
    for result in report["results"]:
        for found in result["packages"]:
            found["vulnerabilities"] = []


def copy_listed_as(name: str | None = None, ecosystem: str | None = None):
    """Lists a copy of the first package again, under another name or in another ecosystem."""
    def edit(report: dict) -> None:
        copied = copy.deepcopy(package_of(report))
        copied["package"].update({key: value for key, value in (("name", name), ("ecosystem", ecosystem)) if value})
        report["results"][0]["packages"].append(copied)
    return edit


def listed_in_results(*picks: tuple[int, ...]):
    """Rebuilds the results as one copy of the first result per pick, each holding the packages at the picked indexes."""
    def edit(report: dict) -> None:
        first = report["results"][0]
        report["results"] = [
            {**copy.deepcopy(first), "packages": [copy.deepcopy(first["packages"][index]) for index in pick]} for pick in picks
        ]
    return edit


def assert_in_order(text: str, *parts: str) -> None:
    position = 0
    for part in parts:
        position = text.index(part, position) + len(part)


def report_naming(repo: Path, name: str, edit) -> Path:
    """A report file whose first result names the resolved path of `name` in `repo`, after `edit`."""
    report = json.loads(FINDING_REPORT.read_text(encoding="utf-8"))
    report["results"][0]["source"]["path"] = str((repo / name).resolve())
    edit(report)
    report_file = repo / "report.json"
    report_file.write_text(json.dumps(report), encoding="utf-8")
    return report_file


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
        assert_in_order(state[0], *fixed)
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


@pytest.mark.parametrize(("config", "code"), [(None, 1), (entry(), 0)], ids=["no allowance", "an allowance"])
def test_no_printed_line_starts_with_the_rule_id(drive_advisories, config, code):
    """Decision 12: quality adds no rule-id label to a finding or a note."""
    run = drive_advisories({"uv.lock": uv_lock(), **({"quality/config.toml": config} if config else {})})
    assert not any(line.lstrip().startswith(Rule.ADVISORY) for line in (run.out + run.err).splitlines())
    assert (run.code, run.err) == (code, "")


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


def test_a_refusing_osv_scanner_prints_no_note_and_no_extent_and_the_exact_pair(drive_advisories):
    """A rule that cannot judge refuses only itself: its row names the reason, and neither an extent, a note nor a line of its own stands for it."""
    run = drive_advisories({"uv.lock": uv_lock(), "quality/config.toml": entry()}, code=3, stderr="boom")
    row = area_row(run.out, Area.DEPENDENCIES)
    assert (row.event, row.fact.endswith("boom"), extent_counts(row.fact), area_block(run.out, Area.DEPENDENCIES)) == (cli.AreaEvent.NOT_CHECKED, True, [3], [])
    assert not any(line.lstrip().startswith(Rule.ADVISORY) for line in run.out.splitlines())
    assert (run.code, run.err) == (2, f"error: {cli.refusal_error(run.areas)}\nnext: {cli.REFUSED_AREAS_NEXT.text}\n")


def test_a_refused_area_leaves_the_note_row_and_the_exact_pair(drive_advisories, use_mise, served_scanners):
    """Decision 6: another area's refusal leaves the dependencies note standing and counts the refused area in the error line."""
    use_mise(lacking(served_scanners, scanners.GITLEAKS))
    run = drive_advisories({"uv.lock": uv_lock(), "quality/config.toml": entry()})
    assert_block_prints(area_block(run.out, Area.DEPENDENCIES), (), run.area.notes)
    assert (len(run.area.notes), run.code, run.err) == (1, 2, f"error: {cli.refusal_error(run.areas)}\nnext: {scanners.install_command()}\n")


@pytest.mark.parametrize(
    ("files", "edit", "counts"),
    [
        ({"uv.lock": uv_lock()}, then(no_advisories), [1, 1]),
        ({"uv.lock": uv_lock()}, then(add_second_package, no_advisories), [1, 2]),
        ({"uv.lock": uv_lock()}, then(copy_listed_as(), no_advisories), [1, 1]),
        ({"uv.lock": uv_lock()}, lambda report: report.update(results=[]), [1, 0]),
        (
            {"src/frontend/pnpm-lock.yaml": PNPM_LOCK},
            then(npm_report("lodash.merge", "lodash.merge"), copy_listed_as("lodash-merge"), no_advisories), [1, 2],
        ),
        ({"uv.lock": uv_lock()}, then(copy_listed_as(ecosystem="npm"), no_advisories), [1, 2]),
        ({"uv.lock": uv_lock()}, then(add_second_package, listed_in_results((0,), (1,)), no_advisories), [1, 2]),
        ({"uv.lock": uv_lock()}, then(listed_in_results((0,), (0,)), no_advisories), [1, 1]),
    ],
    ids=[
        "one package", "two packages", "one package listed twice", "a report listing nothing", "two npm spellings",
        "one name in two ecosystems", "two results listing different packages", "two results listing the same package",
    ],
)
def test_a_clean_row_counts_the_lockfiles_and_the_packages_the_report_lists(drive_advisories, edited_report, files, edit, counts):
    """O48: a package counts once per tracked file, by ecosystem, name as the ecosystem compares it and version."""
    run = drive_advisories(files, edited_report("osv-scanner-finding.json", edit), code=0)
    assert (area_row(run.out, Area.DEPENDENCIES).glyph, extent_counts(area_fact(run.out, Area.DEPENDENCIES))) == (Glyph.CLEAN, counts)
    assert (run.code, run.err) == (0, "")


def test_the_packages_of_two_lock_files_add_up(drive_advisories, build_repo):
    """O48: each tracked lock counts the packages the report lists for it."""
    other = "src/speech/uv.lock"
    repo = build_repo({"uv.lock": uv_lock(), other: uv_lock()})

    def second_lock(report: dict) -> None:
        add_second_package(report)
        report["results"].append(
            {"source": {"path": str((repo / other).resolve()), "type": "lockfile"}, "packages": [copy.deepcopy(package_of(report))]}
        )

    run = drive_advisories(repo, report_naming(repo, "uv.lock", then(second_lock, no_advisories)), code=0)
    assert extent_counts(area_fact(run.out, Area.DEPENDENCIES)) == [2, 3]
    assert (run.code, run.err) == (0, "")


def test_a_lock_reached_by_two_tracked_names_counts_for_each(drive_advisories, build_repo, edited_report):
    """O48: one resolved file under two tracked names is two lock files with a package each."""
    repo = build_repo({"uv.lock": uv_lock()})
    (repo / "src/speech").mkdir(parents=True)
    (repo / "src/speech/uv.lock").symlink_to("../../uv.lock")
    subprocess.run(["git", "-C", str(repo), "add", "src/speech/uv.lock"], check=True)
    run = drive_advisories(repo, edited_report("osv-scanner-finding.json", no_advisories), code=0)
    assert extent_counts(area_fact(run.out, Area.DEPENDENCIES)) == [2, 2]
    assert (run.code, run.err) == (0, "")


def test_a_package_a_requirements_file_lists_as_a_pin_and_as_a_wheel_counts_once(drive_advisories, build_repo):
    """O48: the pin and the wheel line of one package are read from two files under one tracked name."""
    wheel = f"{PACKAGE_NAME} @ https://example.invalid/{PACKAGE_NAME}-6.0.2-py3-none-any.whl --hash=sha256:{'ab' * 32}"
    name = "docs/requirements.txt"
    repo = build_repo({name: f"{PACKAGE_NAME}==6.0.2\n{wheel}\n"})

    def pin_and_wheel(report: dict) -> None:
        package_of(report)["package"]["version"] = "6.0.2"
        report["results"].append({**copy.deepcopy(report["results"][0]), "source": {"path": "@LOCK@", "type": "lockfile"}})

    run = drive_advisories(repo, report_naming(repo, name, then(pin_and_wheel, no_advisories)), code=0)
    assert extent_counts(area_fact(run.out, Area.DEPENDENCIES)) == [1, 1]
    assert (run.code, run.err) == (0, "")


def test_two_packages_that_differ_only_in_their_ecosystem_stay_two_findings(drive_advisories, edited_report):
    """The finding's identity is its lock, ecosystem, ids, fact and fix: equal facts in two ecosystems are two findings."""
    edit = then(copy_listed_as(ecosystem="npm"), without_vulnerability_records)
    run = drive_advisories({"uv.lock": uv_lock()}, edited_report("osv-scanner-finding.json", edit))
    rows = [line for line in area_block(run.out, Area.DEPENDENCIES) if isinstance(line, FindingLine)]
    assert (len(run.area.findings), len(rows), run.code, run.err) == (2, 2, 1, "")


def distant() -> datetime.date:
    return datetime.date(2999, 12, 31)


def second_group_id(report_file: Path) -> str:
    return package_of(json.loads(report_file.read_text(encoding="utf-8")))["groups"][1]["ids"][0]


@pytest.mark.parametrize(
    ("lock", "files", "edit", "package", "version"),
    [
        ("uv.lock", {"uv.lock": uv_lock()}, lambda report: None, PACKAGE_NAME, VERSION),
        ("uv.lock", {"uv.lock": uv_lock()}, drop_fixed_versions, PACKAGE_NAME, VERSION),
        ("uv.lock", {"uv.lock": uv_lock()}, rename_affected_entry("something-else"), PACKAGE_NAME, VERSION),
        ("src/speech/uv.lock", {"src/speech/uv.lock": uv_lock()}, lambda report: None, PACKAGE_NAME, VERSION),
        ("src/frontend/pnpm-lock.yaml", {"src/frontend/pnpm-lock.yaml": PNPM_LOCK}, PNPM_PACKAGE, "lodash", "4.6.0"),
        ("docs/requirements.txt", {"docs/requirements.txt": f"{PACKAGE_NAME}=={VERSION}\n"}, lambda report: None, PACKAGE_NAME, VERSION),
    ],
    ids=["a recorded fix", "no fixed version", "no affected entry for the package", "a nested uv.lock", "a pnpm lock", "a requirements file"],
)
@pytest.mark.parametrize("named", [FIRST_ID, ALIAS], ids=["the group's first id", "an alias"])
def test_an_allowed_advisory_stands_as_a_note_row_under_the_clean_row(drive_advisories, edited_report, lock, files, edit, package, version, named):
    """O15, O49: the allowance is matched by any id of the group, and its note holds the package, the group's first id, the date, the item and the reason."""
    run = drive_advisories({"quality/config.toml": entry(named, lock), **files}, edited_report("osv-scanner-finding.json", edit))
    (note,) = run.area.notes
    assert (note.location, note.event) == (lock, NoteEvent.ALLOWED)
    assert_in_order(note.fact, package, version, FIRST_ID, distant().isoformat(), UV_LOCK_ALLOWANCE["item"], UV_LOCK_ALLOWANCE["reason"])
    assert extent_counts(area_fact(run.out, Area.DEPENDENCIES)) == [1, 1, 1]
    assert ALIAS not in run.out
    assert_block_prints(area_block(run.out, Area.DEPENDENCIES), (), run.area.notes)
    assert (run.code, run.err) == (0, "")


def test_two_allowed_advisories_stand_as_two_note_rows_and_the_row_counts_them(drive_advisories, edited_report):
    """O15: one note per allowed group, in the order of the groups, and the row counts them."""
    report = edited_report("osv-scanner-finding.json", add_second_group)
    second = second_group_id(report)
    run = drive_advisories({"uv.lock": uv_lock(), "quality/config.toml": entry() + entry(second)}, report)
    first_note, second_note = run.area.notes
    assert (FIRST_ID in first_note.fact, second in second_note.fact) == (True, True)
    assert extent_counts(area_fact(run.out, Area.DEPENDENCIES)) == [1, 1, 2]
    assert_block_prints(area_block(run.out, Area.DEPENDENCIES), (), run.area.notes)
    assert (run.code, run.err) == (0, "")


def test_an_allowed_advisory_stands_after_the_findings_and_their_fix(drive_advisories, edited_report):
    """O15: the note follows the last `fix:`, and an area with a finding keeps its row without an extent."""
    run = drive_advisories({"uv.lock": uv_lock(), "quality/config.toml": entry()}, edited_report("osv-scanner-finding.json", add_second_group))
    row_line = next(line for line in run.out.splitlines() if line.split()[1:2] == [Area.DEPENDENCIES])
    assert_block_prints(area_block(run.out, Area.DEPENDENCIES), run.area.findings, run.area.notes)
    assert (row_line.split()[0], extent_counts(row_line.split(Area.DEPENDENCIES, 1)[1]), NoteEvent.ALLOWED in row_line) == (Glyph.NEEDS_YOU, [1], False)
    assert (len(run.area.findings), len(run.area.notes), run.code, run.err) == (1, 1, 1, "")


def test_an_allowance_that_ran_out_leaves_no_note(drive_advisories):
    """An allowance past its date is an affected row, and no note stands for it."""
    run = drive_advisories({"uv.lock": uv_lock(), "quality/config.toml": entry(until=datetime.date(2000, 1, 1))})
    rows = [line for line in area_block(run.out, Area.DEPENDENCIES) if isinstance(line, FindingLine)]
    assert (run.area.notes, [row.row.event for row in rows], run.err) == ((), [FindingEvent.AFFECTED], "")


def test_an_allowed_advisory_leaves_no_line_before_the_area_rows(drive_advisories):
    """The prelude holds no allowed line: the first line is the first area's row."""
    run = drive_advisories({"uv.lock": uv_lock(), "quality/config.toml": entry()})
    assert (hygiene_run(run.repo).reports, run.out.splitlines()[0].split()[1], run.err) == ((), next(iter(Area)), "")


def test_the_reports_hold_only_the_lines_of_python_print(drive_advisories):
    """The prelude keeps the output owner of python-print, and the allowed advisory stands under its row."""
    owner = "# ruff: noqa: T201 - the command line owns its output\nprint(1)\n"
    run = drive_advisories({"src/door.py": owner, "uv.lock": uv_lock(), "quality/config.toml": entry()})
    (report,) = hygiene_run(run.repo).reports
    assert (report.startswith(Rule.PYTHON_PRINT), len(run.area.notes), run.err) == (True, 1, "")
    assert_block_prints(area_block(run.out, Area.DEPENDENCIES), (), run.area.notes)


@pytest.mark.parametrize(
    ("name", "identifier", "reason"),
    [
        ("src/a\x1b\u202eb/uv.lock", ADVISORY_ID, UV_LOCK_ALLOWANCE["reason"]),
        ("uv.lock", "GHSA-\x1b[8m", UV_LOCK_ALLOWANCE["reason"]),
        ("uv.lock", ADVISORY_ID, "ok\x1b[8m versteckt\u202e"),
    ],
    ids=["a lock path", "an id", "a reason"],
)
def test_an_allowed_advisory_with_control_characters_prints_escaped_and_forges_no_line(drive_advisories, build_repo, name, identifier, reason):
    """The location and the fact of a note pass through `printable`."""
    repo = build_repo({name: uv_lock(), "quality/config.toml": entry(identifier, name, reason=reason)})

    def rename_group(report: dict) -> None:
        package_of(report)["groups"][0].update(ids=[identifier], aliases=[])

    run = drive_advisories(repo, report_naming(repo, name, rename_group))
    assert_block_prints(area_block(run.out, Area.DEPENDENCIES), (), run.area.notes)
    assert_every_line_starts_with_the_door((run.out + run.err).splitlines())
    assert (len(run.area.notes), run.code, run.err) == (1, 0, "")
