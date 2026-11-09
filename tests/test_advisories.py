import contextlib
import datetime
import socketserver
import threading
import time
import urllib.request

import pytest

from quality import advisories, process, scanners
from quality.config import CONFIG_PATH, AdvisoryAllowance
from quality.output import SEPARATOR
from quality.report import FindingEvent, LockExtent, NoteEvent
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
    is_clean,
    refused,
)
from tests.support.door import check_door
from tests.support.scanners import OUTPUTS, fake_scanner, lacking

# ---- the real osv-scanner against a fake file:// database (CI runs these; they download the binary) ----

@pytest.mark.scanner
def test_an_affected_lock_is_red_and_names_the_advisory_and_the_fix(build_repo, publish_database, installed_scanners, run_gate):
    """ADVISORY-8, ADVISORY-24, O41: an affected lock is red, its row at the tracked path names the package and the
    advisory, and its fix is the lock kind's hint."""
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
    ids=["several fixed versions", "no fixed event", "an unknown ecosystem beside PyPI"],
)
def test_the_fix_follows_what_osv_lists(build_repo, publish_database, installed_scanners, run_gate, records, in_fact, fix_is):
    """ADVISORY-8, ADVISORY-25, ADVISORY-26, O41: the row and its fix are built from the affected entries osv lists for
    the package."""
    publish_database(records)
    code, output = run_gate(tracked_lock_repo(build_repo))
    assert code == 1, output
    ((_, row),) = area_finding_rows(output, Area.DEPENDENCIES)
    assert (all(word in row.fact for word in in_fact), fix_is(row.fix)) == (True, True), output


def test_a_record_naming_a_pypi_package_in_other_spelling_still_supplies_the_fix(
    build_repo, served_scanners, run_gate, publish_database, edited_report
):
    """ADVISORY-7, O41: a record naming a PyPI package in another spelling (`PyYAML`) still supplies the fix of the
    lock's `pyyaml`."""
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
    """ADVISORY-8, ADVISORY-23: one finding per alias group, naming one of its ids."""
    publish_database([advisory("GHSA-aaaa-bbbb-cccc", aliases=("PYSEC-1",)), advisory("PYSEC-1", aliases=("GHSA-aaaa-bbbb-cccc",))])
    code, output = run_gate(tracked_lock_repo(build_repo))
    assert code == 1
    assert "! 1 finding in 1 area" in output
    ((_, row),) = area_finding_rows(output, Area.DEPENDENCIES)
    assert sum(identifier in row.fact for identifier in ("GHSA-aaaa-bbbb-cccc", "PYSEC-1")) == 1


@pytest.mark.scanner
def test_every_tracked_lock_is_judged_with_its_relative_name(build_repo, publish_database, installed_scanners, run_gate):
    """ADVISORY-1, ADVISORY-6, ADVISORY-8, ADVISORY-24, O43: nested, odd-named and gitignored locks are judged against
    the downloaded database, an untracked one is not."""
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
    ids=["IgnoredVulns", "PackageOverrides ignore"],
)
@pytest.mark.parametrize(
    ("name", "text"), [("uv.lock", uv_lock()), ("tests/requirements.txt", "pyyaml==5.3.1\n")], ids=["uv.lock", "requirements.txt"]
)
def test_a_repository_osv_scanner_config_changes_nothing(
    build_repo, publish_database, installed_scanners, run_gate, repository_config, name, text
):
    """ADVISORY-1, NPM-2: a repository's osv-scanner.toml never silences an advisory, in a uv.lock or a requirements
    file."""
    publish_database()
    repo = build_repo({name: text, "osv-scanner.toml": repository_config})
    code, output = run_gate(repo)
    assert code == 1
    assert [location for location, _ in area_finding_rows(output, Area.DEPENDENCIES)] == [name]


@pytest.mark.scanner
@pytest.mark.parametrize(
    "lock", [uv_lock(SAFE), VIRTUAL_ROOT_ONLY], ids=["a clean lock", "only a virtual root"]
)
def test_a_lock_no_advisory_affects_is_clean(build_repo, publish_database, installed_scanners, run_gate, lock):
    """A lock no advisory affects, or one without registry packages, is clean: exit 0 and a clean dependencies row."""
    publish_database()
    code, output = run_gate(tracked_lock_repo(build_repo, lock))
    assert code == 0, output
    assert is_clean(output)
    assert "✓ dependencies" in output


@pytest.mark.scanner
@pytest.mark.parametrize(
    ("name", "text", "folder"),
    [("uv.lock", uv_lock(), None), ("tests/requirements.txt", "pyyaml==5.3.1 --hash=sha256:" + "ab" * 32 + "\n", "tests")],
    ids=["uv.lock", "tests/requirements.txt"],
)
def test_the_live_database_condemns_pyyaml_5_3_1(build_repo, installed_scanners, live_database, run_gate, name, text, folder):
    """ADVISORY-8, ADVISORY-18, O41: the live database condemns pyyaml 5.3.1 in a uv.lock and in a requirements file,
    fixed in 5.4."""
    code, output = run_gate(build_repo({name: text}))
    assert code == 1, output
    hint = upgrade_fix(name)
    assert any(
        (location, row.event) == (name, FindingEvent.AFFECTED)
        and (row.fix == hint if folder is None else row.fix.endswith(hint) and folder in row.fix)
        and all(word in row.fact for word in (PACKAGE_NAME, "5.3.1", "5.4"))
        for location, row in area_finding_rows(output, Area.DEPENDENCIES)
    ), output


# ---- no network, no real osv-scanner ----


def test_without_a_tracked_lock_nothing_is_downloaded_and_no_osv_binary_is_needed(
    build_repo, served_scanners, use_mise, run_gate, monkeypatch
):
    """SCANNER-13: a repository with no tracked lock needs no osv-scanner and downloads no database."""
    use_mise(lacking(served_scanners, OSV))
    monkeypatch.setattr(advisories, "DATABASE_ROOT", "file:///no/such/root")
    code, output = run_gate(build_repo({"README.md": "x\n"}))
    assert code == 0, output


@pytest.mark.parametrize("name", ["uv.lock", "tests/requirements.txt"])
@pytest.mark.parametrize("tracked_only", ["missing", "symlink to a directory", "symlink out"])
def test_a_lock_the_tree_cannot_hand_over_is_skipped_or_refused(
    build_repo, served_scanners, run_gate, publish_database, tmp_path, tracked_only, name
):
    """ADVISORY-2, ADVISORY-3, ADVISORY-4: a tracked lock the tree no longer holds is skipped; one that links out of the
    repository or to a directory is exit 2 and names why."""
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
    ids=["unreachable", "not a zip", "truncated zip", "no json entry", "an invalid json entry"],
)
def test_a_database_that_cannot_be_used_is_cannot_judge(
    build_repo, served_scanners, run_gate, database_root, publish_database, archive, expected
):
    """ADVISORY-9, ADVISORY-11, ADVISORY-12, ADVISORY-19: a database that is unreachable, not a valid zip, without an
    advisory or with an entry that is not valid JSON is exit 2 naming it, never clean and never a fallback."""
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
    """ADVISORY-9: the download has a total time limit, whatever part of the response the server drags out."""
    serve_stalling(opening, trickles=trickles)
    monkeypatch.setattr(advisories, "DATABASE_DOWNLOAD_DEADLINE_SECONDS", 0.5)
    code, output, elapsed = timed_gate()
    assert (code, "download exceeded" in output, elapsed < 3) == (2, True, True), output


def test_a_server_that_goes_silent_is_a_timeout_not_the_total_deadline(serve_stalling, timed_gate, monkeypatch):
    """ADVISORY-9: a server that stops sending is a refusal naming a timeout; the total deadline is named only when
    it is the cause. Every attempt times out, so the refusal comes after the last one."""
    serve_stalling(BODY_BEGUN + b"xx", trickles=False)
    monkeypatch.setattr(advisories, "DOWNLOAD_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(advisories, "RETRY_DELAYS_SECONDS", (0, 0, 0))
    monkeypatch.setattr(advisories, "DATABASE_DOWNLOAD_DEADLINE_SECONDS", 10)
    code, output, elapsed = timed_gate()
    assert (code, "timed out" in output, "after 4 attempts" in output, "exceeded" in output, elapsed < 3) == (2, True, True, False, True), output


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
    """An error nobody anticipated while downloading is a refusal naming its cause at once, not a wait for the total
    deadline."""
    monkeypatch.setattr(advisories, "DATABASE_ROOT", failing_opener)
    monkeypatch.setattr(advisories, "DATABASE_DOWNLOAD_DEADLINE_SECONDS", 10)
    with monkeypatch.context() as production_hook:  # pytest replaces the hook that prints the worker's traceback
        production_hook.setattr(threading, "excepthook", threading.__excepthook__)
        code, output, elapsed = timed_gate()
    assert (code, "stopped on an unexpected error" in output, "RuntimeError: boom" in output, "exceeded" in output, elapsed < 3) == (
        2, True, True, False, True), output


def test_a_scratch_directory_that_cannot_be_written_is_cannot_judge(build_repo, served_scanners, run_gate, publish_database, monkeypatch):
    """ADVISORY-10: a full or read-only scratch directory is a refusal naming the database, not a traceback."""
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
    """A scratch directory that cannot take the wheel pins of a requirements file is a refusal naming that file, not a
    traceback."""
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
    """ADVISORY-13: an osv-scanner that exits 127 on a lock it cannot parse is exit 2 naming the lock, never green."""
    publish_database()
    publish_database(ecosystem="npm")
    code, output = run_gate(build_repo({name: text}))
    assert (code, name in output) == (2, True), output


def test_two_tracked_names_for_one_lock_are_each_judged(build_repo, served_scanners, run_gate, publish_database):
    """ADVISORY-23, ADVISORY-24: a finding for every tracked name, not only the last one that resolves to the file."""
    publish_database()
    fake_scanner(served_scanners, OSV, reporting(OUTPUTS / "osv-scanner-finding.json", 1))
    code, output = run_gate(linked_lock_repo(build_repo))
    assert code == 1, output
    assert sorted(location for location, _ in area_finding_rows(output, Area.DEPENDENCIES)) == ["speech/uv.lock", "uv.lock"]


@pytest.mark.scanner
def test_two_tracked_names_for_one_lock_are_each_judged_by_the_real_osv(build_repo, publish_database, installed_scanners, run_gate):
    """ADVISORY-8, ADVISORY-23, ADVISORY-24: the same, with the real osv-scanner reporting however it reports a repeated
    lock."""
    publish_database()
    code, output = run_gate(linked_lock_repo(build_repo))
    assert code == 1, output
    assert sorted(location for location, _ in area_finding_rows(output, Area.DEPENDENCIES)) == ["speech/uv.lock", "uv.lock"]


def test_an_osv_binary_at_the_wrong_version_names_both_versions(build_repo, served_scanners, capsys, publish_database):
    """SCANNER-10: an osv-scanner at another version is exit 2 naming both versions."""
    publish_database()
    (served_scanners / OSV).write_text('#!/bin/sh\necho "osv-scanner version: 9.9.9"\n')
    (served_scanners / OSV).chmod(0o755)
    code = check_door(tracked_lock_repo(build_repo))
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert (code, f"{OSV} 9.9.9 found, the release pins {scanners.pinned_tool(OSV).version}" in output) == (2, True), output


@pytest.mark.parametrize("exit_code", [2, 127, 128])
def test_an_unexpected_osv_exit_is_cannot_judge_with_the_code_and_stderr(
    build_repo, served_scanners, run_gate, publish_database, exit_code
):
    """ADVISORY-13: an osv-scanner exit other than 0 or 1, 127 (a missing database) included, is exit 2 naming the
    code and quoting stderr."""
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
    ids=["exit 0 with vulnerabilities", "exit 1 without", "not JSON", "wrong shape",
         "results is an empty object", "results is an empty string", "packages is an object",
         "groups is a string", "a source quality did not pass", "version is an object",
         "ecosystem is a number", "an id is a number", "vulnerabilities without groups",
         "groups without vulnerabilities", "affected is a string", "ranges is an object", "events is a string",
         "fixed is a number"],
)
def test_a_report_that_disagrees_with_the_exit_is_cannot_judge(
    build_repo, served_scanners, run_gate, publish_database, edited_report, fixture, edit, exit_code, expected
):
    """ADVISORY-15, ADVISORY-16, ADVISORY-17: a report that is not the expected JSON, that disagrees with the exit or
    that names a lock quality did not pass is exit 2, never clean."""
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
    """ADVISORY-5: a tracked file of zero bytes is exit 2, since osv-scanner exits 0 on it without judging."""
    publish_database()
    publish_database(ecosystem="npm")
    fake_scanner(served_scanners, OSV, reporting(OUTPUTS / "osv-scanner-clean.json", 0))
    code, output = run_gate(build_repo({name: ""}))
    assert (code, f"{name} is empty" in output) == (2, True), output


def test_a_package_osv_cannot_match_to_the_advisory_gets_only_the_advisory_link(
    build_repo, served_scanners, run_gate, publish_database, edited_report
):
    """ADVISORY-27: with no `affected` entry for the package, quality never claims that no fix exists."""
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
    """ADVISORY-14: an osv-scanner that runs past its time limit is exit 2 naming the limit."""
    publish_database()
    monkeypatch.setattr(advisories, "ADVISORY_SCAN_TIMEOUT_SECONDS", 0.5)
    fake_scanner(served_scanners, OSV, "exec sleep 30")
    code, output = run_gate(tracked_lock_repo(build_repo))
    assert (code, "did not finish within 0.5 seconds" in output) == (2, True), output


def test_a_fake_osv_clean_report_for_a_tracked_lock_is_clean(build_repo, served_scanners, run_gate, publish_database):
    """A clean osv-scanner report for a tracked lock is clean."""
    publish_database()
    fake_scanner(served_scanners, OSV, reporting(OUTPUTS / "osv-scanner-clean.json", 0))
    code, output = run_gate(tracked_lock_repo(build_repo, uv_lock(SAFE)))
    assert code == 0, output


# ---- the allowances, applied by the rule ----

TODAY = datetime.date(2027, 1, 31)
QWEN = "speech/providers/qwen/uv.lock"


def affected(lock=QWEN, ids=("GHSA-462x-pgg6-55fp", "CVE-2026-112395"), fact=None) -> advisories.Affected:
    fact = fact or SEPARATOR.join(("pillow 12.3.0", "GHSA-462x-pgg6-55fp"))
    return advisories.Affected(lock, advisories.PYTHON_ECOSYSTEM, frozenset(ids), fact, "upgrade pillow")


def torch() -> advisories.Affected:
    return affected(fact=SEPARATOR.join(("torch 2.6.0", "GHSA-462x-pgg6-55fp")))


def allowing(identifier="GHSA-462x-pgg6-55fp", lock=QWEN, until=TODAY) -> AdvisoryAllowance:
    return AdvisoryAllowance(identifier, lock, until, datetime.date(2026, 10, 21), "#371", "no image input")


def allowed(lock=QWEN, package="pillow 12.3.0", until=TODAY) -> tuple[str, NoteEvent, tuple[str, ...]]:
    return lock, NoteEvent.ALLOWED, (package, "GHSA-462x-pgg6-55fp", until.isoformat(), "#371", "no image input")


def red(lock=QWEN, *words) -> tuple[str, FindingEvent, tuple[str, ...]]:
    return lock, FindingEvent.AFFECTED, words


def orphaned(identifier="GHSA-462x-pgg6-55fp", lock=QWEN) -> tuple[str, FindingEvent, tuple[str, ...]]:
    return CONFIG_PATH, FindingEvent.UNUSED, (identifier, lock)


YESTERDAY = TODAY - datetime.timedelta(days=1)
VOXCPM = "speech/providers/voxcpm/uv.lock"
LINKED = "speech/providers/linked/uv.lock"


@pytest.mark.parametrize(
    ("groups", "allowances", "findings", "notes"),
    [
        ([affected()], [allowing(until=TODAY + datetime.timedelta(days=90))], [],
         [allowed(until=TODAY + datetime.timedelta(days=90))]),
        ([affected()], [allowing()], [], [allowed()]),
        ([affected()], [allowing(until=YESTERDAY)],
         [red(QWEN, "2027-01-30", "#371")], []),
        ([affected()], [allowing("CVE-2026-112395")], [], [allowed()]),
        ([affected(), affected(VOXCPM)], [allowing()], [red(VOXCPM)], [allowed()]),
        ([affected()], [allowing(lock=VOXCPM)], [red(), orphaned(lock=VOXCPM)], []),
        ([], [allowing()], [orphaned()], []),
        ([affected(), affected(LINKED)], [allowing()], [red(LINKED)], [allowed()]),
        ([affected(), torch()], [allowing()], [], [allowed(), allowed(package="torch 2.6.0")]),
        ([affected(), torch()], [allowing(), allowing("CVE-2026-112395")],
         [red(), red(QWEN, "torch"), (CONFIG_PATH, FindingEvent.CONFLICT, ("GHSA-462x-pgg6-55fp", "CVE-2026-112395", QWEN))], []),
        ([affected()], [], [red()], []),
    ],
    ids=[
        "until in the future", "until is today", "until was yesterday", "the CVE alias",
        "the same advisory in an unallowed lock", "an allowance for a lock the advisory misses",
        "an advisory no longer reported", "the other tracked name of one lock",
        "two packages in one lock", "two allowances on one group", "no allowance",
    ],
)
def test_an_allowance_allows_its_advisory_in_its_lock_until_its_date(groups, allowances, findings, notes):
    """ALLOW-2, ALLOW-4, ALLOW-5, ALLOW-6, ALLOW-7, ALLOW-8, ALLOW-9, O45, O46: an allowance matching any id of a group
    in its lock allows it until `until` (inclusive), then it is red with its date and item; one matching nothing is
    red, and two on one group are a finding at the config and allow it for neither."""
    extent = LockExtent(1, 1)
    judged = advisories.apply_allowances(groups, allowances, TODAY, extent)
    assert [(finding.location, finding.event) for finding in judged.findings] == [found[:2] for found in findings]
    assert [(note.location, note.event) for note in judged.notes] == [found[:2] for found in notes]
    assert judged.extent == extent
    for finding, (_, _, words) in zip(judged.findings, findings, strict=True):
        assert all(word in finding.fact for word in words)
    for note, (_, _, words) in zip(judged.notes, notes, strict=True):
        positions = [note.fact.index(word) for word in words]
        assert positions == sorted(positions)


def test_check_prints_an_allowance_that_ran_out_as_an_affected_row(build_repo, served_scanners, run_gate, publish_database):
    """ALLOW-4, O45: once the date has passed the advisory is red with the date and item, in the upgrade's row."""
    publish_database()
    config = allowance_config(**UV_LOCK_ALLOWANCE, until=datetime.date(2000, 1, 1))
    fake_scanner(served_scanners, OSV, reporting(OUTPUTS / "osv-scanner-finding.json", 1))
    code, output = run_gate(build_repo({"uv.lock": uv_lock(), "quality/config.toml": config}))
    ((location, row),) = area_finding_rows(output, Area.DEPENDENCIES)
    assert (code, location, row.event, row.fix) == (1, "uv.lock", FindingEvent.AFFECTED, upgrade_fix()), output
    assert all(word in row.fact for word in ("2000-01-01", UV_LOCK_ALLOWANCE["item"]))


def test_an_allowance_without_a_tracked_lock_is_red_with_no_osv_scanner_and_no_download(
    build_repo, served_scanners, use_mise, capsys, monkeypatch
):
    """ALLOW-5, ALLOW-11: with no tracked lock an allowance matches nothing and is red, with no osv-scanner and no
    download."""
    use_mise(lacking(served_scanners, OSV))
    monkeypatch.setattr(advisories, "DATABASE_ROOT", "file:///no/such/root")
    repo = build_repo({"README.md": "x\n", "quality/config.toml": allowance_config(**UV_LOCK_ALLOWANCE, until=datetime.date(2999, 12, 31))})
    assert check_door(repo) == 1
    ((location, row),) = area_finding_rows(capsys.readouterr().out, Area.DEPENDENCIES)
    assert (location, row.event, ADVISORY_ID in row.fact, "uv.lock" in row.fact) == (CONFIG_PATH, FindingEvent.UNUSED, True, True)
