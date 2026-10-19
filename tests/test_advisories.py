import contextlib
import hashlib
import io
import json
import shlex
import socketserver
import subprocess
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

import pytest
from conftest import fake_engine

from quality import advisories, cli, engines

OUTPUTS = Path(__file__).parent / "fixtures" / "engine-outputs"
OSV = engines.OSV_SCANNER
ADVISORY_ID = "GHSA-aaaa-bbbb-cccc"
AFFECTED_LINE = f"osv-scanner {ADVISORY_ID} (CVE-2020-1): PyPI pyyaml 5.3.1 is affected."
UPGRADE_LINE = "Fix: upgrade pyyaml to >= 5.4 (uv lock --upgrade-package pyyaml)"
VULNERABLE = (("pyyaml", "5.3.1"),)
SAFE = (("pyyaml", "6.0.2"),)


def uv_lock(packages=VULNERABLE) -> str:
    registry = "".join(
        f'\n[[package]]\nname = "{name}"\nversion = "{version}"\nsource = {{ registry = "https://pypi.org/simple" }}\n'
        for name, version in packages
    )
    root = '[[package]]\nname = "demo"\nversion = "0.1.0"\nsource = { editable = "." }\n'
    return f'version = 1\nrequires-python = ">=3.12"\n\n{root}{registry}'


VIRTUAL_ROOT_ONLY = 'version = 1\n\n[[package]]\nname = "demo"\nversion = "0.1.0"\nsource = { virtual = "." }\n'


def advisory(identifier=ADVISORY_ID, *, fixed=("5.4",), aliases=("CVE-2020-1",), also_affected=()) -> dict:
    ranges = [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": version}]} for version in fixed]
    ranges = ranges or [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}]}]
    return {
        "schema_version": "1.9.0", "id": identifier, "modified": "2026-01-01T00:00:00Z", "aliases": list(aliases),
        "affected": [{"package": {"name": "pyyaml", "ecosystem": "PyPI"}, "ranges": ranges}, *also_affected],
    }


def zipped(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in entries.items():
            archive.writestr(name, text)
    return buffer.getvalue()


@pytest.fixture
def publish_database(tmp_path_factory, monkeypatch):
    """Points the gate at a file:// advisory database root and publishes the PyPI archive there."""
    root = tmp_path_factory.mktemp("osv-database")
    monkeypatch.setattr(advisories, "DATABASE_ROOT", root.as_uri())

    def publish(records=None, archive: bytes | None = None) -> None:
        records = [advisory()] if records is None else records
        archive = zipped({f"{record['id']}.json": json.dumps(record) for record in records}) if archive is None else archive
        (root / "PyPI").mkdir(exist_ok=True)
        (root / "PyPI" / "all.zip").write_bytes(archive)

    return publish


@pytest.fixture
def run_gate(capsys):
    """Runs the gate over a repository and returns (exit code, all output); no output names the checkout or the engine directory (#53 cell 21)."""
    def run(repo: Path, engine_dir: Path) -> tuple[int, str]:
        code = cli.main(["hygiene", "--engines", str(engine_dir), str(repo)])
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert str(repo) not in output
        assert str(repo).lstrip("/") not in output
        assert str(engine_dir) not in output
        assert str(engine_dir).lstrip("/") not in output
        return code, captured.out + captured.err
    return run


def reporting(report: Path, exit_code: int, stderr: str = "") -> str:
    """Shell for a fake osv-scanner that prints `report` (@LOCK@ read as the last lock passed) and exits."""
    return (
        f'for argument; do lock="${{argument#--lockfile=uv.lock:}}"; done\n'
        f'sed "s|@LOCK@|$lock|" {shlex.quote(str(report))}\necho {shlex.quote(stderr)} >&2\nexit {exit_code}'
    )


@pytest.fixture
def edited_report(tmp_path_factory):
    """Writes the engine-output fixture, edited, outside the fixture repository."""
    def write(fixture: str, edit) -> Path:
        report = json.loads((OUTPUTS / fixture).read_text())
        edit(report)
        path = tmp_path_factory.mktemp("report") / "report.json"
        path.write_text(json.dumps(report))
        return path
    return write


def tracked_lock_repo(build_repo, text: str = uv_lock()) -> Path:
    return build_repo({"uv.lock": text})


# ---- the real osv-scanner against a fake file:// database (CI runs these; they download the binary) ----

@pytest.mark.engine
def test_an_affected_lock_is_red_and_names_the_advisory_and_the_fix(build_repo, publish_database, installed_engines, run_gate):
    """#53 rule 2, rule 3, cells 3 and 21."""
    publish_database()
    code, output = run_gate(tracked_lock_repo(build_repo), installed_engines)
    assert code == 1, output
    assert f"advisory uv.lock - {AFFECTED_LINE}\n  {UPGRADE_LINE}" in output


@pytest.mark.engine
@pytest.mark.parametrize(
    ("records", "expected"),
    [
        ([advisory(fixed=("5.4", "6.0"))], "Fix: upgrade pyyaml to a fixed version: 5.4, 6.0"),
        ([advisory(fixed=())], f"Fix: osv lists no fixed version: remove or replace pyyaml; see https://osv.dev/vulnerability/{ADVISORY_ID}"),
        ([advisory(also_affected=[{"package": {"name": "x", "ecosystem": "Zorkland"}, "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "1"}]}]}])], UPGRADE_LINE),
    ],
    ids=["rule 2 several fixed versions", "cell 5 no fixed event", "cell 26 an unknown ecosystem beside PyPI"],
)
def test_the_fix_follows_what_osv_lists(build_repo, publish_database, installed_engines, run_gate, records, expected):
    """#53 rule 2, cells 5 and 26: the fix line is built from the affected entries osv lists for the package."""
    publish_database(records)
    code, output = run_gate(tracked_lock_repo(build_repo), installed_engines)
    assert code == 1, output
    assert expected in output


@pytest.mark.engine
def test_two_advisories_that_alias_each_other_are_one_finding(build_repo, publish_database, installed_engines, run_gate):
    """#53 rule 2, cell 6: one finding per alias group, the other ids named as aliases."""
    publish_database([advisory("GHSA-aaaa-bbbb-cccc", aliases=("PYSEC-1",)), advisory("PYSEC-1", aliases=("GHSA-aaaa-bbbb-cccc",))])
    code, output = run_gate(tracked_lock_repo(build_repo), installed_engines)
    assert code == 1
    assert "hygiene: 1 finding(s)" in output
    assert "GHSA-aaaa-bbbb-cccc (PYSEC-1)" in output or "PYSEC-1 (GHSA-aaaa-bbbb-cccc)" in output


@pytest.mark.engine
def test_every_tracked_lock_is_judged_with_its_relative_name(build_repo, publish_database, installed_engines, run_gate):
    """#53 rule 1, rule 2, cells 7, 8, 22, 30: nested, odd-named and gitignored locks are scanned, an untracked one is not."""
    repo = build_repo({
        "uv.lock": uv_lock(), "speech/uv.lock": uv_lock(), "a:b/uv.lock": uv_lock(), "-dash/uv.lock": uv_lock(),
        ".gitignore": "uv.lock\n",
    })
    (repo / "extra").mkdir()
    (repo / "extra/uv.lock").write_text(uv_lock())
    publish_database()
    code, output = run_gate(repo, installed_engines)
    assert code == 1
    for name in ("uv.lock", "speech/uv.lock", "a:b/uv.lock", "-dash/uv.lock"):
        assert f"advisory {name} - " in output
    assert "extra/uv.lock" not in output
    assert "(in speech/: uv lock --upgrade-package pyyaml)" in output


@pytest.mark.engine
@pytest.mark.parametrize(
    "repository_config",
    ['[[IgnoredVulns]]\nid = "GHSA-aaaa-bbbb-cccc"\n', '[[PackageOverrides]]\nname = "pyyaml"\necosystem = "PyPI"\nignore = true\n'],
    ids=["cell 11 IgnoredVulns", "cell 12 PackageOverrides ignore"],
)
def test_a_repository_osv_scanner_config_changes_nothing(build_repo, publish_database, installed_engines, run_gate, repository_config):
    """#53 head decision: the caller's osv-scanner.toml is never honoured."""
    publish_database()
    repo = build_repo({"uv.lock": uv_lock(), "osv-scanner.toml": repository_config})
    assert run_gate(repo, installed_engines)[0] == 1


@pytest.mark.engine
@pytest.mark.parametrize(
    "lock", [uv_lock(SAFE), VIRTUAL_ROOT_ONLY], ids=["cell 2 a clean lock", "cell 29 only a virtual root"]
)
def test_a_lock_no_advisory_affects_is_clean(build_repo, publish_database, installed_engines, run_gate, lock):
    """#53 rule 3: clean is exit 0, a lock without registry packages included."""
    publish_database()
    code, output = run_gate(tracked_lock_repo(build_repo, lock), installed_engines)
    assert code == 0, output
    assert "hygiene: clean (" in output
    assert "advisory" in output


@pytest.mark.engine
def test_the_live_database_condemns_pyyaml_5_3_1(build_repo, installed_engines, run_gate):
    """#53 cell 4: the real world, over the network."""
    code, output = run_gate(tracked_lock_repo(build_repo), installed_engines)
    assert code == 1, output
    assert "GHSA-8q59-q68h-6hv4" in output
    assert "upgrade pyyaml to >= 5.4" in output


# ---- no network, no real osv-scanner ----

def osv_engines(clean_engines, on_scan):
    fake_engine(clean_engines, OSV, on_scan)
    return clean_engines


def test_without_a_tracked_lock_nothing_is_downloaded_and_no_osv_binary_is_needed(
    build_repo, clean_engines, run_gate, monkeypatch
):
    """#53 rule 1, cell 1."""
    monkeypatch.setattr(advisories, "DATABASE_ROOT", "file:///no/such/root")
    code, output = run_gate(build_repo({"README.md": "x\n"}), clean_engines)
    assert code == 0, output


@pytest.mark.parametrize("tracked_only", ["missing", "symlink to a directory", "symlink out"])
def test_a_lock_the_tree_cannot_hand_over_is_skipped_or_refused(
    build_repo, clean_engines, run_gate, publish_database, tmp_path, tracked_only
):
    """#53 rule 1, cells 9, 10 and 31."""
    publish_database()
    repo = build_repo({"uv.lock": uv_lock(), "inside/x": "x\n"})
    (repo / "uv.lock").unlink()
    if tracked_only == "symlink to a directory":
        (repo / "uv.lock").symlink_to("inside")
    if tracked_only == "symlink out":
        outside = tmp_path.parent / f"{tmp_path.name}-outside.lock"
        outside.write_text(uv_lock())
        (repo / "uv.lock").symlink_to(outside)
    code, output = run_gate(repo, osv_engines(clean_engines, "exit 99"))
    expected_code, expected_text = {
        "missing": (0, "hygiene: clean"), "symlink to a directory": (2, "uv.lock is not a file"),
        "symlink out": (2, "uv.lock resolves outside the repository"),
    }[tracked_only]
    assert (code, expected_text in output) == (expected_code, True), output


def test_a_gate_run_without_engines_names_advisory_as_skipped_and_downloads_nothing(build_repo, capsys, monkeypatch):
    """#53 rule 4, cell 23."""
    monkeypatch.setattr(advisories, "DATABASE_ROOT", "file:///no/such/root")
    assert cli.main(["hygiene", str(tracked_lock_repo(build_repo))]) == 0
    assert "skipped without --engines: secret, workflow-lint, workflow-security, advisory" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("archive", "expected"),
    [
        (None, "cannot download the PyPI advisory database"),
        (b"not a zip", "is not a valid zip"),
        (zipped({"a.json": "{}"})[:-30], "is not a valid zip"),
        (zipped({"readme.txt": "x"}), "holds no advisory"),
    ],
    ids=["cell 13 unreachable", "cell 14 not a zip", "cell 14 truncated", "cell 28 no json entry"],
)
def test_a_database_that_cannot_be_used_is_cannot_judge(
    build_repo, clean_engines, run_gate, publish_database, archive, expected
):
    """#53 rule 3: never clean, never a fallback."""
    if archive is not None:  # None: nothing is published at the root
        publish_database(archive=archive)
    code, output = run_gate(tracked_lock_repo(build_repo), osv_engines(clean_engines, "exit 99"))
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
def timed_gate(build_repo, clean_engines, run_gate):
    """Runs the gate over a lock whose database download is the subject; returns (exit code, output, seconds it took)."""
    def run() -> tuple[int, str, float]:
        started = time.monotonic()
        code, output = run_gate(tracked_lock_repo(build_repo), osv_engines(clean_engines, "exit 99"))
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
    monkeypatch.setattr(engines, "DOWNLOAD_TIMEOUT_SECONDS", 0.3)
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


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")  # the worker's traceback on stderr is the loud signal
def test_an_unexpected_download_error_is_cannot_judge_at_once(failing_opener, timed_gate, monkeypatch):
    """#53 rule 3: an error nobody anticipated is a refusal that says so, not a wait for the total deadline."""
    monkeypatch.setattr(advisories, "DATABASE_ROOT", failing_opener)
    monkeypatch.setattr(advisories, "DATABASE_DOWNLOAD_DEADLINE_SECONDS", 10)
    code, output, elapsed = timed_gate()
    assert (code, "stopped on an unexpected error" in output, "exceeded" in output, elapsed < 3) == (2, True, False, True), output


def test_a_scratch_directory_that_cannot_be_written_is_cannot_judge(build_repo, clean_engines, run_gate, publish_database, monkeypatch):
    """#53 rule 3: a full or read-only scratch is a refusal, not a traceback."""
    publish_database()
    scratch_directory = engines.scratch_directory

    @contextlib.contextmanager
    def scratch_without_room_for_a_database():  # a file where the database directory belongs; the secret scan never uses that name
        with scratch_directory() as scratch:
            (scratch / "osv-scalibr").touch()
            yield scratch

    monkeypatch.setattr(engines, "scratch_directory", scratch_without_room_for_a_database)
    code, output = run_gate(tracked_lock_repo(build_repo), osv_engines(clean_engines, "exit 99"))
    assert (code, "cannot place the PyPI advisory database in the scratch directory" in output, "<scratch>" in output, "secret cannot judge" in output) == (2, True, True, False), output


@pytest.mark.engine
def test_a_lock_osv_cannot_parse_is_cannot_judge_and_names_neither_form_of_its_path(
    build_repo, publish_database, installed_engines, run_gate
):
    """#53 rule 3, cell 21: osv prints the lock's path without its leading slash."""
    publish_database()
    code, output = run_gate(tracked_lock_repo(build_repo, "this is [not a lock\n"), installed_engines)
    assert (code, "uv.lock" in output) == (2, True), output


def linked_lock_repo(build_repo) -> Path:
    """uv.lock, and speech/uv.lock as a tracked link to it: two tracked names for one resolved file."""
    repo = build_repo({"uv.lock": uv_lock(), "speech/keep": "x\n"})
    (repo / "speech/uv.lock").symlink_to("../uv.lock")
    subprocess.run(["git", "-C", str(repo), "add", "speech/uv.lock"], check=True)
    return repo


def test_two_tracked_names_for_one_lock_are_each_judged(build_repo, clean_engines, run_gate, publish_database):
    """#53 rule 2: a finding for every tracked name, not only the last one that resolves to the file."""
    publish_database()
    engine_dir = osv_engines(clean_engines, reporting(OUTPUTS / "osv-scanner-finding.json", 1))
    code, output = run_gate(linked_lock_repo(build_repo), engine_dir)
    assert code == 1, output
    assert "advisory uv.lock - " in output
    assert "advisory speech/uv.lock - " in output


@pytest.mark.engine
def test_two_tracked_names_for_one_lock_are_each_judged_by_the_real_osv(build_repo, publish_database, installed_engines, run_gate):
    """#53 rule 2: the same, with the real osv-scanner reporting however it reports a repeated lock."""
    publish_database()
    code, output = run_gate(linked_lock_repo(build_repo), installed_engines)
    assert code == 1, output
    assert "advisory uv.lock - " in output
    assert "advisory speech/uv.lock - " in output


def test_a_missing_osv_binary_is_cannot_judge(build_repo, clean_engines, run_gate, publish_database):
    """#53 rule 3, cell 15."""
    publish_database()
    code, output = run_gate(tracked_lock_repo(build_repo), clean_engines)
    assert (code, "cannot run" in output) == (2, True), output


def test_an_osv_binary_at_the_wrong_version_names_both_versions(
    build_repo, clean_engines, run_gate, publish_database
):
    """#53 rule 3, cell 16."""
    publish_database()
    (clean_engines / OSV).write_text('#!/bin/sh\necho "osv-scanner version: 9.9.9"\n')
    (clean_engines / OSV).chmod(0o755)
    code, output = run_gate(tracked_lock_repo(build_repo), clean_engines)
    assert (code, "'9.9.9'" in output, engines.read_pins()[OSV].version in output) == (2, True, True), output


@pytest.mark.parametrize("exit_code", [2, 127, 128])
def test_an_unexpected_osv_exit_is_cannot_judge_with_the_code_and_stderr(
    build_repo, clean_engines, run_gate, publish_database, exit_code
):
    """#53 rule 3, cell 17 (127 is the missing database, cell 27)."""
    publish_database()
    engine_dir = osv_engines(clean_engines, f'echo "boom reason" >&2\nexit {exit_code}')
    code, output = run_gate(tracked_lock_repo(build_repo), engine_dir)
    assert code == 2
    assert f"exited {exit_code}" in output
    assert "boom reason" in output


def package_of(report: dict) -> dict:
    return report["results"][0]["packages"][0]


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
         "rule 3 groups is a string",
         "cell 20 a source quality did not pass", "rule 3 version is an object", "rule 3 ecosystem is a number",
         "rule 3 an id is a number", "rule 3 affected is a string", "rule 3 ranges is an object", "rule 3 events is a string",
         "rule 3 fixed is a number"],
)
def test_a_report_that_disagrees_with_the_exit_is_cannot_judge(
    build_repo, clean_engines, run_gate, publish_database, edited_report, fixture, edit, exit_code, expected
):
    """#53 rule 3."""
    publish_database()
    if fixture.endswith("garbled.json"):
        report = OUTPUTS / fixture
    else:
        report = edited_report(fixture, edit)
    code, output = run_gate(tracked_lock_repo(build_repo), osv_engines(clean_engines, reporting(report, exit_code)))
    assert (code, expected in output) == (2, True), output


def test_a_package_osv_cannot_match_to_the_advisory_gets_only_the_advisory_link(
    build_repo, clean_engines, run_gate, publish_database, edited_report
):
    """#53 rule 2: with no `affected` entry for the package, quality never claims that no fix exists."""
    publish_database()
    def rename_affected(report):
        for vulnerability in report["results"][0]["packages"][0]["vulnerabilities"]:
            vulnerability["affected"][0]["package"]["name"] = "something-else"
    report = edited_report("osv-scanner-finding.json", rename_affected)
    code, output = run_gate(tracked_lock_repo(build_repo), osv_engines(clean_engines, reporting(report, 1)))
    assert code == 1
    assert f"Fix: see https://osv.dev/vulnerability/{ADVISORY_ID}" in output


def test_a_hanging_osv_is_cannot_judge_within_its_time_limit(
    build_repo, clean_engines, run_gate, publish_database, monkeypatch
):
    """#53 rule 3, cell 32."""
    publish_database()
    monkeypatch.setattr(advisories, "ADVISORY_SCAN_TIMEOUT_SECONDS", 0.5)
    code, output = run_gate(tracked_lock_repo(build_repo), osv_engines(clean_engines, "exec sleep 30"))
    assert (code, "did not finish within 0.5 seconds" in output) == (2, True), output


def test_a_fake_osv_clean_report_for_a_tracked_lock_is_clean(build_repo, clean_engines, run_gate, publish_database):
    """#53 cell 2 without the real binary."""
    publish_database()
    engine_dir = osv_engines(clean_engines, reporting(OUTPUTS / "osv-scanner-clean.json", 0))
    code, output = run_gate(tracked_lock_repo(build_repo, uv_lock(SAFE)), engine_dir)
    assert code == 0, output


# ---- engines install: the bare binary (rule 5, cell 24) ----

@pytest.fixture
def bare_binary_pin(tmp_path, monkeypatch):
    """A pin file with one `format = "binary"` engine whose download is a local script; returns (download, pin(sha256))."""
    version = engines.read_pins()[OSV].version
    download = tmp_path / "osv-download"
    download.write_text(f'#!/bin/sh\necho "osv-scanner version: {version}"\n')
    pin_file = tmp_path / "pins.toml"

    def pin(sha256: str) -> None:
        pin_file.write_text(
            f'[{OSV}]\nversion = "{version}"\nurl = "{download.as_uri()}"\n'
            f'sha256 = "{sha256}"\nformat = "binary"\n'
        )

    monkeypatch.setattr(engines, "PIN_FILE", pin_file)
    return download, pin


def test_install_puts_a_verified_bare_binary_in_place_and_leaves_it_alone_the_second_time(bare_binary_pin, tmp_path, capsys):
    """#53 rule 5, cell 24."""
    download, pin = bare_binary_pin
    pin(hashlib.sha256(download.read_bytes()).hexdigest())
    target = tmp_path / "engines"
    assert cli.main(["engines", "install", str(target)]) == 0
    assert (target / OSV).read_bytes() == download.read_bytes()
    assert (target / OSV).stat().st_mode & 0o111
    assert cli.main(["engines", "install", str(target)]) == 0
    assert f"{OSV} {engines.read_pins()[OSV].version} already installed" in capsys.readouterr().out


def test_a_pin_with_an_unknown_format_is_refused_naming_the_pin_and_the_value(bare_binary_pin, tmp_path, capsys):
    """#53 rule 5: a typo in a pin's format fails where it is written."""
    download, pin = bare_binary_pin
    pin(hashlib.sha256(download.read_bytes()).hexdigest())
    engines.PIN_FILE.write_text(engines.PIN_FILE.read_text().replace('"binary"', '"bianry"'))
    assert cli.main(["engines", "install", str(tmp_path / "engines")]) == 2
    error = capsys.readouterr().err
    assert OSV in error
    assert "'bianry'" in error


def test_install_refuses_a_bare_binary_with_the_wrong_checksum_and_installs_nothing(bare_binary_pin, tmp_path, capsys):
    """#53 rule 5, cell 24."""
    _, pin = bare_binary_pin
    pin("0" * 64)
    target = tmp_path / "engines"
    assert cli.main(["engines", "install", str(target)]) == 2
    assert not (target / OSV).exists()
    assert "the pin says" in capsys.readouterr().err
