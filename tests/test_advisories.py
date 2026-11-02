import contextlib
import datetime
import socketserver
import threading
import time
import urllib.request

import pytest

from quality import advisories, cli, process, scanners
from quality.config import CONFIG_PATH, AdvisoryAllowance
from quality.output import SEPARATOR
from quality.report import FindingEvent
from quality.rules import Area
from tests.support.advisories import (
    ADVISORY_ID,
    ADVISORY_LINK,
    OSV,
    PACKAGE_NAME,
    SAFE,
    UV_LOCK_ALLOWANCE,
    VIRTUAL_ROOT_ONLY,
    advisory,
    allowance_config,
    drop_fixed_versions,
    linked_lock_repo,
    package_of,
    reporting,
    tracked_lock_repo,
    upgrade_fix,
    uv_lock,
    zipped,
)
from tests.support.check import (
    CLEAN_VERDICT,
    area_finding_rows,
    assert_every_line_starts_with_the_door,
    is_clean,
    refused,
)
from tests.support.scanners import OUTPUTS, fake_scanner, lacking

# ---- the real osv-scanner against a fake file:// database (CI runs these; they download the binary) ----

@pytest.mark.scanner
def test_an_affected_lock_is_red_and_names_the_advisory_and_the_fix(build_repo, publish_database, installed_scanners, run_gate):
    """#53 rule 2, rule 3, cells 3 and 21."""
    publish_database()
    code, output = run_gate(tracked_lock_repo(build_repo))
    assert code == 1, output
    ((location, row),) = area_finding_rows(output, Area.DEPENDENCIES)
    assert (location, row.event, row.fix, (PACKAGE_NAME in row.fact, ADVISORY_ID in row.fact)) == ("uv.lock", FindingEvent.AFFECTED, upgrade_fix(), (True, True))


@pytest.mark.scanner
@pytest.mark.parametrize(
    ("records", "in_fact", "fix_is"),
    [
        ([advisory(fixed=("5.4", "6.0"))], ("5.4", "6.0"), lambda fix: fix == upgrade_fix()),
        ([advisory(fixed=())], (advisories.NO_FIXED_VERSION,), lambda fix: PACKAGE_NAME in fix and fix.endswith(ADVISORY_LINK)),
        (
            [advisory(also_affected=[{"package": {"name": "x", "ecosystem": "Zorkland"}, "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "1"}]}]}])],
            ("5.4",), lambda fix: fix == upgrade_fix(),
        ),
    ],
    ids=["rule 2 several fixed versions", "cell 5 no fixed event", "cell 26 an unknown ecosystem beside PyPI"],
)
def test_the_fix_follows_what_osv_lists(build_repo, publish_database, installed_scanners, run_gate, records, in_fact, fix_is):
    """The row and its fix are built from the affected entries osv lists for the package."""
    publish_database(records)
    code, output = run_gate(tracked_lock_repo(build_repo))
    assert code == 1, output
    ((_, row),) = area_finding_rows(output, Area.DEPENDENCIES)
    assert (all(word in row.fact for word in in_fact), fix_is(row.fix)) == (True, True), output


def test_a_record_naming_a_pypi_package_in_other_spelling_still_supplies_the_fix(
    build_repo, served_scanners, run_gate, publish_database, edited_report
):
    """#145 I2: Python's name rule applies to PyPI, so a record naming `PyYAML` fixes the lock's `pyyaml`."""
    publish_database()
    report = edited_report(
        "osv-scanner-finding.json",
        lambda report: package_of(report)["vulnerabilities"][0]["affected"][0]["package"].update(name="PyYAML"),
    )
    fake_scanner(served_scanners, OSV, reporting(report, 1))
    code, output = run_gate(tracked_lock_repo(build_repo))
    assert code == 1, output
    assert [row.fix for _, row in area_finding_rows(output, Area.DEPENDENCIES)] == [upgrade_fix()]


@pytest.mark.scanner
def test_two_advisories_that_alias_each_other_are_one_finding(build_repo, publish_database, installed_scanners, run_gate):
    """#53 rule 2, cell 6: one finding per alias group, the other ids named as aliases."""
    publish_database([advisory("GHSA-aaaa-bbbb-cccc", aliases=("PYSEC-1",)), advisory("PYSEC-1", aliases=("GHSA-aaaa-bbbb-cccc",))])
    code, output = run_gate(tracked_lock_repo(build_repo))
    assert code == 1
    assert "! 1 finding in 1 area" in output
    ((_, row),) = area_finding_rows(output, Area.DEPENDENCIES)
    assert sum(identifier in row.fact for identifier in ("GHSA-aaaa-bbbb-cccc", "PYSEC-1")) == 1


@pytest.mark.scanner
def test_every_tracked_lock_is_judged_with_its_relative_name(build_repo, publish_database, installed_scanners, run_gate):
    """#53 rule 1, rule 2, cells 7, 8, 22, 30: nested, odd-named and gitignored locks are scanned, an untracked one is not."""
    repo = build_repo({
        "uv.lock": uv_lock(), "speech/uv.lock": uv_lock(), "a:b/uv.lock": uv_lock(), "-dash/uv.lock": uv_lock(),
        ".gitignore": "uv.lock\n",
    })
    (repo / "extra").mkdir()
    (repo / "extra/uv.lock").write_text(uv_lock())
    publish_database()
    code, output = run_gate(repo)
    assert code == 1
    rows = dict(area_finding_rows(output, Area.DEPENDENCIES))
    assert sorted(rows) == sorted(("uv.lock", "speech/uv.lock", "a:b/uv.lock", "-dash/uv.lock"))
    assert "extra/uv.lock" not in output
    assert (rows["speech/uv.lock"].fix.endswith(upgrade_fix()), "speech" in rows["speech/uv.lock"].fix) == (True, True)


@pytest.mark.scanner
@pytest.mark.parametrize(
    "repository_config",
    ['[[IgnoredVulns]]\nid = "GHSA-aaaa-bbbb-cccc"\n', '[[PackageOverrides]]\nname = "pyyaml"\necosystem = "PyPI"\nignore = true\n'],
    ids=["cell 11 IgnoredVulns", "cell 12 PackageOverrides ignore"],
)
@pytest.mark.parametrize(
    ("name", "text"), [("uv.lock", uv_lock()), ("tests/requirements.txt", "pyyaml==5.3.1\n")], ids=["uv.lock", "requirements.txt"]
)
def test_a_repository_osv_scanner_config_changes_nothing(
    build_repo, publish_database, installed_scanners, run_gate, repository_config, name, text
):
    """#53 head decision: the caller's osv-scanner.toml is never honoured; #176 R2 for a requirements file."""
    publish_database()
    repo = build_repo({name: text, "osv-scanner.toml": repository_config})
    code, output = run_gate(repo)
    assert code == 1
    assert [location for location, _ in area_finding_rows(output, Area.DEPENDENCIES)] == [name]


@pytest.mark.scanner
@pytest.mark.parametrize(
    "lock", [uv_lock(SAFE), VIRTUAL_ROOT_ONLY], ids=["cell 2 a clean lock", "cell 29 only a virtual root"]
)
def test_a_lock_no_advisory_affects_is_clean(build_repo, publish_database, installed_scanners, run_gate, lock):
    """#53 rule 3: clean is exit 0, a lock without registry packages included."""
    publish_database()
    code, output = run_gate(tracked_lock_repo(build_repo, lock))
    assert code == 0, output
    assert is_clean(output)
    assert "✓ dependencies" in output


@pytest.mark.scanner
@pytest.mark.parametrize(
    ("name", "text"), [("uv.lock", uv_lock()), ("tests/requirements.txt", "pyyaml==5.3.1 --hash=sha256:" + "ab" * 32 + "\n")],
    ids=["uv.lock", "tests/requirements.txt"],
)
def test_the_live_database_condemns_pyyaml_5_3_1(build_repo, installed_scanners, live_database, run_gate, name, text):
    """#53 cell 4 and #176 canary: the real world, over the network."""
    code, output = run_gate(build_repo({name: text}))
    assert code == 1, output
    assert any(
        (location, row.event) == (name, FindingEvent.AFFECTED)
        and row.fix == upgrade_fix(name)
        and all(word in row.fact for word in (PACKAGE_NAME, "5.3.1", "5.4"))
        for location, row in area_finding_rows(output, Area.DEPENDENCIES)
    ), output


# ---- no network, no real osv-scanner ----


def test_without_a_tracked_lock_nothing_is_downloaded_and_no_osv_binary_is_needed(
    build_repo, served_scanners, use_mise, run_gate, monkeypatch
):
    """#53 rule 1, cell 1."""
    use_mise(lacking(served_scanners, OSV))
    monkeypatch.setattr(advisories, "DATABASE_ROOT", "file:///no/such/root")
    code, output = run_gate(build_repo({"README.md": "x\n"}))
    assert code == 0, output


@pytest.mark.parametrize("name", ["uv.lock", "tests/requirements.txt"])
@pytest.mark.parametrize("tracked_only", ["missing", "symlink to a directory", "symlink out"])
def test_a_lock_the_tree_cannot_hand_over_is_skipped_or_refused(
    build_repo, served_scanners, run_gate, publish_database, tmp_path, tracked_only, name
):
    """#53 rule 1, cells 9, 10 and 31; #176 C2 for a requirements file."""
    publish_database()
    repo = build_repo({name: uv_lock(), "docs/x.txt": "x\n"})
    (repo / name).unlink()
    if tracked_only == "symlink to a directory":
        (repo / name).symlink_to(".")
    if tracked_only == "symlink out":
        outside = tmp_path.parent / f"{tmp_path.name}-outside.lock"
        outside.write_text(uv_lock())
        (repo / name).symlink_to(outside)
    fake_scanner(served_scanners, OSV, "exit 99")
    code, output = run_gate(repo)
    expected_code, expected_text = {
        "missing": (0, CLEAN_VERDICT), "symlink to a directory": (2, f"{name} is not a file"),
        "symlink out": (2, f"{name} resolves outside the repository"),
    }[tracked_only]
    assert (code, expected_text in output) == (expected_code, True), output


@pytest.mark.parametrize(
    ("archive", "expected"),
    [
        (None, "cannot download the PyPI advisory database"),
        (b"not a zip", "is not a valid zip"),
        (zipped({"a.json": "{}"})[:-30], "is not a valid zip"),
        (zipped({"readme.txt": "x"}), "holds no advisory"),
        (zipped({"a.json": "{}", "b.json": "{not json"}), "holds an entry that is not valid JSON"),
    ],
    ids=["cell 13 unreachable", "cell 14 not a zip", "cell 14 truncated", "cell 28 no json entry", "#5 127 an invalid json entry"],
)
def test_a_database_that_cannot_be_used_is_cannot_judge(
    build_repo, served_scanners, run_gate, database_root, publish_database, archive, expected
):
    """#53 rule 3: never clean, never a fallback."""
    if archive is None:  # nothing is published at the root
        (database_root / "PyPI" / "all.zip").unlink()
    else:
        publish_database(archive=archive)
    fake_scanner(served_scanners, OSV, "exit 99")
    code, output = run_gate(tracked_lock_repo(build_repo))
    assert (code, expected in output) == (2, True), output


class Stalling(socketserver.StreamRequestHandler):
    """Sends `opening`, then one byte at a time (or nothing more) until the test ends or `lifetime` passes, then hangs up."""
    opening = b""
    trickles = False
    lifetime = 5  # seconds: a broken downloader fails the test on its elapsed time instead of stalling the CI job
    finished = threading.Event()

    def handle(self):
        expires = time.monotonic() + self.lifetime
        try:
            self.wfile.write(self.opening)
            while not self.finished.wait(0.05) and time.monotonic() < expires:
                if self.trickles:
                    self.wfile.write(b"x")
                self.wfile.flush()
        except OSError:
            pass  # the client gave up, which is what the tests expect


@pytest.fixture
def serve_stalling(monkeypatch):
    """Serves a Stalling handler and points the gate's database root at it; the server is stopped at teardown."""
    stopping = []

    def serve(opening: bytes, *, trickles: bool) -> None:
        handler = type("Stall", (Stalling,), {"opening": opening, "trickles": trickles, "finished": threading.Event()})
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        serving = threading.Thread(target=server.serve_forever)
        serving.start()
        stopping.append((handler, server, serving))
        monkeypatch.setattr(advisories, "DATABASE_ROOT", f"http://127.0.0.1:{server.server_address[1]}")

    yield serve
    for handler, server, serving in stopping:
        handler.finished.set()
        server.shutdown()
        serving.join()
        server.server_close()


@pytest.fixture
def timed_gate(build_repo, served_scanners, run_gate):
    """Runs the gate over a lock whose database download is the subject; returns (exit code, output, seconds it took)."""
    def run() -> tuple[int, str, float]:
        started = time.monotonic()
        fake_scanner(served_scanners, OSV, "exit 99")
        code, output = run_gate(tracked_lock_repo(build_repo))
        return code, output, time.monotonic() - started
    return run


BODY_BEGUN = b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n"


@pytest.mark.parametrize(
    ("opening", "trickles"),
    [(BODY_BEGUN, True), (b"HTTP/1.1 200 OK\r\nX-Slow: ", True)],
    ids=["body trickles", "header trickles"],
)
def test_a_server_that_trickles_bytes_hits_the_download_deadline(serve_stalling, timed_gate, monkeypatch, opening, trickles):
    """#53 rule 3: the download has a total time limit, whatever part of the response the server drags out."""
    serve_stalling(opening, trickles=trickles)
    monkeypatch.setattr(advisories, "DATABASE_DOWNLOAD_DEADLINE_SECONDS", 0.5)
    code, output, elapsed = timed_gate()
    assert (code, "download exceeded" in output, elapsed < 3) == (2, True, True), output


def test_a_server_that_goes_silent_is_a_timeout_not_the_total_deadline(serve_stalling, timed_gate, monkeypatch):
    """#53 rule 3: a stalled receive is named a timeout; the total deadline is named only when it is the cause."""
    serve_stalling(BODY_BEGUN + b"xx", trickles=False)
    monkeypatch.setattr(advisories, "DOWNLOAD_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(advisories, "DATABASE_DOWNLOAD_DEADLINE_SECONDS", 10)
    code, output, elapsed = timed_gate()
    assert (code, "timed out" in output, "exceeded" in output, elapsed < 3) == (2, True, False, True), output


@pytest.fixture
def failing_opener():
    """Makes every `boom://` open raise an error the downloader does not expect, through urllib's public opener API."""
    class Boom(urllib.request.BaseHandler):
        def boom_open(self, request):
            raise RuntimeError("boom")

    urllib.request.install_opener(urllib.request.build_opener(Boom()))
    yield "boom://database"
    urllib.request.install_opener(None)


def test_an_unexpected_download_error_is_cannot_judge_at_once(failing_opener, timed_gate, monkeypatch):
    """#53 rule 3: an error nobody anticipated is a refusal that names its cause, not a wait for the total deadline."""
    monkeypatch.setattr(advisories, "DATABASE_ROOT", failing_opener)
    monkeypatch.setattr(advisories, "DATABASE_DOWNLOAD_DEADLINE_SECONDS", 10)
    with monkeypatch.context() as production_hook:  # pytest replaces the hook that prints the worker's traceback
        production_hook.setattr(threading, "excepthook", threading.__excepthook__)
        code, output, elapsed = timed_gate()
    assert (code, "stopped on an unexpected error" in output, "RuntimeError: boom" in output, "exceeded" in output, elapsed < 3) == (
        2, True, True, False, True), output


def test_a_scratch_directory_that_cannot_be_written_is_cannot_judge(build_repo, served_scanners, run_gate, publish_database, monkeypatch):
    """#53 rule 3: a full or read-only scratch is a refusal, not a traceback."""
    publish_database()
    scratch_directory = process.scratch_directory

    @contextlib.contextmanager
    def scratch_without_room_for_a_database():  # a file where the database directory belongs; the secret scan never uses that name
        with scratch_directory() as scratch:
            (scratch / "osv-scalibr").touch()
            yield scratch

    monkeypatch.setattr(process, "scratch_directory", scratch_without_room_for_a_database)
    fake_scanner(served_scanners, OSV, "exit 99")
    code, output = run_gate(tracked_lock_repo(build_repo))
    assert (code, "cannot place the PyPI advisory database in the scratch directory" in output, "<scratch>" in output, refused(output, "secrets")) == (2, True, True, False), output


def test_a_wheel_pin_file_that_cannot_be_written_is_cannot_judge(build_repo, served_scanners, run_gate, publish_database, monkeypatch):
    """#176 fail-closed: a full or read-only scratch while the wheel pins are written is a refusal naming the tracked file, not a traceback."""
    publish_database()
    scratch_directory = process.scratch_directory

    @contextlib.contextmanager
    def scratch_without_room_for_wheel_pins():  # a directory where the wheel pin file belongs
        with scratch_directory() as scratch:
            (scratch / "wheels-1.txt").mkdir()
            yield scratch

    monkeypatch.setattr(process, "scratch_directory", scratch_without_room_for_wheel_pins)
    wheel = "pkg @ https://h/pkg-1.0-py3-none-any.whl --hash=sha256:" + "ab" * 32 + "\n"
    fake_scanner(served_scanners, OSV, "exit 99")
    code, output = run_gate(build_repo({"tests/requirements.txt": wheel}))
    assert (code, "tests/requirements.txt: cannot write the wheel pins for the scan" in output, "Traceback" in output) == (2, True, False), output


@pytest.mark.scanner
@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("uv.lock", "this is [not a lock\n"),
        ("src/frontend/pnpm-lock.yaml", "lockfileVersion: '9.0'\npackages: [unclosed\n"),
    ],
    ids=["uv.lock", "pnpm-lock.yaml"],
)
def test_a_lock_osv_cannot_parse_is_cannot_judge_and_names_neither_form_of_its_path(
    build_repo, publish_database, installed_scanners, run_gate, name, text
):
    """#53 rule 3, cell 21 and #145 S5: osv prints the lock's path without its leading slash; a broken lock is never green."""
    publish_database()
    publish_database(ecosystem="npm")
    code, output = run_gate(build_repo({name: text}))
    assert (code, name in output) == (2, True), output


def test_two_tracked_names_for_one_lock_are_each_judged(build_repo, served_scanners, run_gate, publish_database):
    """#53 rule 2: a finding for every tracked name, not only the last one that resolves to the file."""
    publish_database()
    fake_scanner(served_scanners, OSV, reporting(OUTPUTS / "osv-scanner-finding.json", 1))
    code, output = run_gate(linked_lock_repo(build_repo))
    assert code == 1, output
    assert sorted(location for location, _ in area_finding_rows(output, Area.DEPENDENCIES)) == ["speech/uv.lock", "uv.lock"]


@pytest.mark.scanner
def test_two_tracked_names_for_one_lock_are_each_judged_by_the_real_osv(build_repo, publish_database, installed_scanners, run_gate):
    """#53 rule 2: the same, with the real osv-scanner reporting however it reports a repeated lock."""
    publish_database()
    code, output = run_gate(linked_lock_repo(build_repo))
    assert code == 1, output
    assert sorted(location for location, _ in area_finding_rows(output, Area.DEPENDENCIES)) == ["speech/uv.lock", "uv.lock"]


def test_an_osv_binary_at_the_wrong_version_names_both_versions(build_repo, served_scanners, capsys, publish_database):
    """#53 rule 3, cell 16."""
    publish_database()
    (served_scanners / OSV).write_text('#!/bin/sh\necho "osv-scanner version: 9.9.9"\n')
    (served_scanners / OSV).chmod(0o755)
    code = cli.main(["hygiene", str(tracked_lock_repo(build_repo))])
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert (code, f"{OSV} 9.9.9 found, the release pins {scanners.pinned_tool(OSV).version}" in output) == (2, True), output


@pytest.mark.parametrize("exit_code", [2, 127, 128])
def test_an_unexpected_osv_exit_is_cannot_judge_with_the_code_and_stderr(
    build_repo, served_scanners, run_gate, publish_database, exit_code
):
    """#53 rule 3, cell 17 (127 is the missing database, cell 27)."""
    publish_database()
    fake_scanner(served_scanners, OSV, f'echo "boom reason" >&2\nexit {exit_code}')
    code, output = run_gate(tracked_lock_repo(build_repo))
    assert code == 2
    assert f"exited {exit_code}" in output
    assert "boom reason" in output


@pytest.mark.parametrize(
    ("fixture", "edit", "exit_code", "expected"),
    [
        ("osv-scanner-finding.json", lambda report: None, 0, "exited 0 but its report holds 1 finding(s)"),
        ("osv-scanner-clean.json", lambda report: None, 1, "exited 1 but its report holds 0 finding(s)"),
        ("osv-scanner-garbled.json", lambda report: None, 0, "cannot read the osv-scanner report"),
        ("osv-scanner-clean.json", lambda report: report.update(results=5), 0, "cannot read the osv-scanner report"),
        ("osv-scanner-clean.json", lambda report: report.update(results={}), 0, "cannot read the osv-scanner report"),
        ("osv-scanner-clean.json", lambda report: report.update(results=""), 0, "cannot read the osv-scanner report"),
        ("osv-scanner-finding.json", lambda report: report["results"][0].update(packages={}), 1, "cannot read the osv-scanner report"),
        ("osv-scanner-finding.json", lambda report: report["results"][0]["packages"][0].update(groups="x"), 1,
         "cannot read the osv-scanner report"),
        ("osv-scanner-finding.json", lambda report: report["results"][0]["source"].update(path="/elsewhere/uv.lock"), 1,
         "names a lock file quality did not pass"),
        ("osv-scanner-finding.json", lambda report: package_of(report)["package"].update(version={}) or
         package_of(report).update(groups=[], vulnerabilities=[]), 0, "cannot read the osv-scanner report"),
        ("osv-scanner-finding.json", lambda report: package_of(report)["package"].update(ecosystem=7), 1,
         "cannot read the osv-scanner report"),
        ("osv-scanner-finding.json", lambda report: package_of(report)["groups"][0].update(ids=["a", 7]), 1,
         "cannot read the osv-scanner report"),
        ("osv-scanner-finding.json", lambda report: package_of(report).pop("groups"), 1,
         "cannot read the osv-scanner report"),
        ("osv-scanner-finding.json", lambda report: package_of(report).pop("vulnerabilities"), 1,
         "cannot read the osv-scanner report"),
        ("osv-scanner-finding.json", lambda report: package_of(report)["vulnerabilities"][0].update(affected="x"), 1,
         "cannot read the osv-scanner report"),
        ("osv-scanner-finding.json", lambda report: package_of(report)["vulnerabilities"][0]["affected"][0].update(ranges={}), 1,
         "cannot read the osv-scanner report"),
        ("osv-scanner-finding.json", lambda report: package_of(report)["vulnerabilities"][0]["affected"][0]["ranges"][0].update(events="x"),
         1, "cannot read the osv-scanner report"),
        ("osv-scanner-finding.json",
         lambda report: package_of(report)["vulnerabilities"][0]["affected"][0]["ranges"][0]["events"][1].update(fixed=5), 1,
         "cannot read the osv-scanner report"),
    ],
    ids=["cell 18 exit 0 with vulnerabilities", "cell 18 exit 1 without", "cell 19 not JSON", "cell 19 wrong shape",
         "rule 3 results is an empty object", "rule 3 results is an empty string", "rule 3 packages is an object",
         "rule 3 groups is a string", "cell 20 a source quality did not pass", "rule 3 version is an object",
         "rule 3 ecosystem is a number", "rule 3 an id is a number", "rule 3 vulnerabilities without groups",
         "rule 3 groups without vulnerabilities", "rule 3 affected is a string", "rule 3 ranges is an object", "rule 3 events is a string",
         "rule 3 fixed is a number"],
)
def test_a_report_that_disagrees_with_the_exit_is_cannot_judge(
    build_repo, served_scanners, run_gate, publish_database, edited_report, fixture, edit, exit_code, expected
):
    """#53 rule 3."""
    publish_database()
    if fixture.endswith("garbled.json"):
        report = OUTPUTS / fixture
    else:
        report = edited_report(fixture, edit)
    fake_scanner(served_scanners, OSV, reporting(report, exit_code))
    code, output = run_gate(tracked_lock_repo(build_repo))
    assert (code, expected in output) == (2, True), output


@pytest.mark.parametrize("name", ["uv.lock", "src/frontend/pnpm-lock.yaml", "tests/requirements.txt"])
def test_a_tracked_file_of_zero_bytes_is_cannot_judge(build_repo, served_scanners, run_gate, publish_database, name):
    """#5 145: osv-scanner finds no package source in an empty file and exits 0, which is not a judgement."""
    publish_database()
    publish_database(ecosystem="npm")
    fake_scanner(served_scanners, OSV, reporting(OUTPUTS / "osv-scanner-clean.json", 0))
    code, output = run_gate(build_repo({name: ""}))
    assert (code, f"{name} is empty" in output) == (2, True), output


def test_a_package_osv_cannot_match_to_the_advisory_gets_only_the_advisory_link(
    build_repo, served_scanners, run_gate, publish_database, edited_report
):
    """#53 rule 2: with no `affected` entry for the package, quality never claims that no fix exists."""
    publish_database()
    def rename_affected(report):
        for vulnerability in report["results"][0]["packages"][0]["vulnerabilities"]:
            vulnerability["affected"][0]["package"]["name"] = "something-else"
    report = edited_report("osv-scanner-finding.json", rename_affected)
    fake_scanner(served_scanners, OSV, reporting(report, 1))
    code, output = run_gate(tracked_lock_repo(build_repo))
    assert code == 1
    ((_, row),) = area_finding_rows(output, Area.DEPENDENCIES)
    assert (row.fix.endswith(ADVISORY_LINK), PACKAGE_NAME in row.fix) == (True, False)
    assert row.fact.count(SEPARATOR) == 1, row.fact


def test_a_hanging_osv_is_cannot_judge_within_its_time_limit(
    build_repo, served_scanners, run_gate, publish_database, monkeypatch
):
    """#53 rule 3, cell 32."""
    publish_database()
    monkeypatch.setattr(advisories, "ADVISORY_SCAN_TIMEOUT_SECONDS", 0.5)
    fake_scanner(served_scanners, OSV, "exec sleep 30")
    code, output = run_gate(tracked_lock_repo(build_repo))
    assert (code, "did not finish within 0.5 seconds" in output) == (2, True), output


def test_a_fake_osv_clean_report_for_a_tracked_lock_is_clean(build_repo, served_scanners, run_gate, publish_database):
    """#53 cell 2 without the real binary."""
    publish_database()
    fake_scanner(served_scanners, OSV, reporting(OUTPUTS / "osv-scanner-clean.json", 0))
    code, output = run_gate(tracked_lock_repo(build_repo, uv_lock(SAFE)))
    assert code == 0, output


# ---- #116: the allowances, applied by the rule ----

TODAY = datetime.date(2027, 1, 31)
QWEN = "speech/providers/qwen/uv.lock"
PILLOW = "GHSA-462x-pgg6-55fp (CVE-2026-112395): PyPI pillow 12.3.0"


def affected(
    lock=QWEN, subject=PILLOW, ids=("GHSA-462x-pgg6-55fp", "CVE-2026-112395"),
    fact=None,
) -> advisories.Affected:
    fact = fact or SEPARATOR.join(("pillow 12.3.0", "GHSA-462x-pgg6-55fp"))
    return advisories.Affected(lock, subject, frozenset(ids), fact, "upgrade pillow")


def allowing(identifier="GHSA-462x-pgg6-55fp", lock=QWEN, until=TODAY) -> AdvisoryAllowance:
    return AdvisoryAllowance(identifier, lock, until, datetime.date(2026, 10, 21), "#371", "no image input")


def allowed_line(lock=QWEN, subject=PILLOW, until=TODAY) -> str:
    return f"  allowed {lock} {subject} until {until.isoformat()} (#371) - no image input"


def red(lock=QWEN, *words) -> tuple[str, FindingEvent, tuple[str, ...]]:
    return lock, FindingEvent.AFFECTED, words


def orphaned(identifier="GHSA-462x-pgg6-55fp", lock=QWEN) -> tuple[str, FindingEvent, tuple[str, ...]]:
    return CONFIG_PATH, FindingEvent.UNUSED, (identifier, lock)


YESTERDAY = TODAY - datetime.timedelta(days=1)
VOXCPM = "speech/providers/voxcpm/uv.lock"
LINKED = "speech/providers/linked/uv.lock"
TORCH = "GHSA-462x-pgg6-55fp (CVE-2026-112395): PyPI torch 2.6.0"
TORCH_FACT = SEPARATOR.join(("torch 2.6.0", "GHSA-462x-pgg6-55fp"))


@pytest.mark.parametrize(
    ("groups", "allowances", "findings", "allowed"),
    [
        ([affected()], [allowing(until=TODAY + datetime.timedelta(days=90))], [],
         [allowed_line(until=TODAY + datetime.timedelta(days=90))]),
        ([affected()], [allowing()], [], [allowed_line()]),
        ([affected()], [allowing(until=YESTERDAY)],
         [red(QWEN, "2027-01-30", "#371")], []),
        ([affected()], [allowing("CVE-2026-112395")], [], [allowed_line()]),
        ([affected(), affected(VOXCPM)], [allowing()], [red(VOXCPM)], [allowed_line()]),
        ([affected()], [allowing(lock=VOXCPM)], [red(), orphaned(lock=VOXCPM)], []),
        ([], [allowing()], [orphaned()], []),
        ([affected(), affected(LINKED)], [allowing()], [red(LINKED)], [allowed_line()]),
        ([affected(), affected(subject=TORCH, fact=TORCH_FACT)], [allowing()], [], [allowed_line(), allowed_line(subject=TORCH)]),
        ([affected(), affected(subject=TORCH, fact=TORCH_FACT)], [allowing(), allowing("CVE-2026-112395")],
         [red(), red(QWEN, "torch"), (CONFIG_PATH, FindingEvent.CONFLICT, ("GHSA-462x-pgg6-55fp", "CVE-2026-112395", QWEN))], []),
        ([affected()], [], [red()], []),
    ],
    ids=[
        "cell 1 until in the future", "cell 2 until is today", "cell 3 until was yesterday", "cell 4 the CVE alias",
        "cell 5 the same advisory in an unallowed lock", "cell 6 an allowance for a lock the advisory misses",
        "cell 7 an advisory no longer reported", "cell 9 the other tracked name of one lock",
        "cell 10 two packages in one lock", "cell 27 two allowances on one group", "no allowance",
    ],
)
def test_an_allowance_allows_its_advisory_in_its_lock_until_its_date(groups, allowances, findings, allowed):
    """#116 R3, E2, E4, E5, E10, E12: an allowance matching any id of a group in its lock allows it until `until`
    (inclusive), then it is red with its date and item; one matching nothing is red, and two on one group allow it
    for neither."""
    judged = advisories.apply_allowances(groups, allowances, TODAY)
    assert ([(finding.location, finding.event) for finding in judged.findings], list(judged.allowed)) == ([found[:2] for found in findings], allowed)
    for finding, (_, _, words) in zip(judged.findings, findings, strict=True):
        assert all(word in finding.reason for word in words)


@pytest.mark.parametrize(
    "edit", [lambda report: None, drop_fixed_versions], ids=["cell 1 allowed until 2999", "cell 11 no fixed version"]
)
def test_hygiene_prints_each_allowed_advisory_before_the_verdict(
    build_repo, served_scanners, run_gate, publish_database, edited_report, edit
):
    """#116 R3, E4, E10, E11: through hygiene, an allowed advisory is printed with its date, item and reason
    and leaves the rule clean."""
    publish_database()
    report = edited_report("osv-scanner-finding.json", edit)
    repo = build_repo({"uv.lock": uv_lock(), "quality/config.toml": allowance_config(**UV_LOCK_ALLOWANCE, until=datetime.date(2999, 12, 31))})
    fake_scanner(served_scanners, OSV, reporting(report, 1))
    code, output = run_gate(repo)
    allowed_lines = [
        "advisory: 1 allowed",
        f"  allowed uv.lock {ADVISORY_ID} (CVE-2020-1): PyPI pyyaml 5.3.1 until 2999-12-31 (#116) - fixture",
    ]
    lines = output.splitlines()
    assert code == 0, output
    assert lines[lines.index(allowed_lines[0]):][:len(allowed_lines)] == allowed_lines, output
    assert is_clean(lines[-1]), output


def test_hygiene_prints_an_allowance_that_ran_out_as_an_affected_row(build_repo, served_scanners, run_gate, publish_database):
    """Once the date has passed the advisory is red with the date and item, in the upgrade's row."""
    publish_database()
    config = allowance_config(**UV_LOCK_ALLOWANCE, until=datetime.date(2000, 1, 1))
    fake_scanner(served_scanners, OSV, reporting(OUTPUTS / "osv-scanner-finding.json", 1))
    code, output = run_gate(build_repo({"uv.lock": uv_lock(), "quality/config.toml": config}))
    ((location, row),) = area_finding_rows(output, Area.DEPENDENCIES)
    assert (code, location, row.event, row.fix) == (1, "uv.lock", FindingEvent.AFFECTED, upgrade_fix()), output
    assert all(word in row.fact for word in ("2000-01-01", UV_LOCK_ALLOWANCE["item"]))


def test_hygiene_escapes_the_allowance_in_its_allowed_line(
    build_repo, served_scanners, run_gate, publish_database, edited_report
):
    """#116 cell 25: an allowance's id and reason print escaped in the allowed line."""
    publish_database()
    report = edited_report("osv-scanner-finding.json", lambda report: package_of(report)["groups"][0].update(
        ids=["GHSA-\x1b[8m"], aliases=[]))
    config = allowance_config(
        **UV_LOCK_ALLOWANCE | {"identifier": "GHSA-\x1b[8m", "reason": "ok\x1b[8m versteckt"},
        until=datetime.date(2999, 12, 31),
    )
    repo = build_repo({"uv.lock": uv_lock(), "quality/config.toml": config})
    fake_scanner(served_scanners, OSV, reporting(report, 1))
    code, output = run_gate(repo)
    assert code == 0, output
    assert "  allowed uv.lock GHSA-\\x1b[8m: PyPI pyyaml 5.3.1 until 2999-12-31 (#116) - ok\\x1b[8m versteckt" in output
    assert_every_line_starts_with_the_door(output.splitlines())


def test_an_allowance_without_a_tracked_lock_is_red_with_no_osv_scanner_and_no_download(
    build_repo, served_scanners, use_mise, capsys, monkeypatch
):
    """#116 E5, cell 8: with no tracked lock an allowance matches nothing and is red, with no osv-scanner and no download."""
    use_mise(lacking(served_scanners, OSV))
    monkeypatch.setattr(advisories, "DATABASE_ROOT", "file:///no/such/root")
    repo = build_repo({"README.md": "x\n", "quality/config.toml": allowance_config(**UV_LOCK_ALLOWANCE, until=datetime.date(2999, 12, 31))})
    assert cli.main(["hygiene", str(repo)]) == 1
    ((location, row),) = area_finding_rows(capsys.readouterr().out, Area.DEPENDENCIES)
    assert (location, row.event, ADVISORY_ID in row.fact, "uv.lock" in row.fact) == (CONFIG_PATH, FindingEvent.UNUSED, True, True)
