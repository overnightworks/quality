import io
import stat
import subprocess
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from quality import output, process, sonar_scanner, web
from quality.report import CannotJudge

VERSION = "8.1.0.6389"
FINGERPRINT = "ABCDEF0123456789ABCDEF0123456789ABCDEF01"
OTHER_FINGERPRINT = "0123456789ABCDEF0123456789ABCDEF01234567"
SIGNING_SUB_FINGERPRINT = "FEDCBA9876543210FEDCBA9876543210FEDCBA98"
FOLDER = f"sonar-scanner-{VERSION}-linux-x64"
SIGNATURE_BYTES = b"signature bytes"
KEY_FILE_NAME = "sonarsource-scanner-key.asc"
RERUN_ADVICE = "re-run this run's failed jobs"
FIX_ADVICE = "replace src/quality/sonarsource-scanner-key.asc"


def key_listing(*primaries: str, validity: str = "-") -> str:
    """What `gpg --with-colons --show-keys` prints for a file holding one primary key per fingerprint."""
    return "".join(
        f"pub:{validity}:4096:1:1DB198F93525EC1A:1619438987:1934967538::-:::cSC::::::23::0:\n"
        f"fpr:::::::::{fingerprint}:\n"
        "uid:-::::1777287538::EE80219AD026B011EB96E1EFD11B27597A09835A::A Signer <signer@example.invalid>::::::::::0:\n"
        "sub:-:4096:1:C33B56C147B01975:1783344860:1814880860:::::s::::::23:\n"
        f"fpr:::::::::{SIGNING_SUB_FINGERPRINT}:\n"
        for fingerprint in primaries
    )


def status_lines(*words: str, primary: str = FINGERPRINT, signer: str = SIGNING_SUB_FINGERPRINT) -> str:
    """The `--status-fd` lines of a verification; a word is one line, `VALIDSIG` carries the signer and the primary key."""
    lines = {
        "GOODSIG": f"[GNUPG:] GOODSIG {signer[-16:]} A Signer <signer@example.invalid>",
        "VALIDSIG": f"[GNUPG:] VALIDSIG {signer} 2026-04-21 1776756026 0 4 0 1 10 00 {primary}",
    }
    return "\n".join(lines.get(word, f"[GNUPG:] {word} {signer[-16:]}") for word in words) + "\n"


def scanner_zip(*entries: tuple[str, str], links: tuple[tuple[str, str], ...] = ()) -> bytes:
    """A zip holding the scanner's launcher and `entries` (name, content); each of `links` is a symbolic link (name, target)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{FOLDER}/bin/sonar-scanner", "#!/bin/sh\n")
        for name, content in entries:
            archive.writestr(name, content)
        for name, target in links:
            link = zipfile.ZipInfo(name)
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, target)
    return buffer.getvalue()


ZIP_BYTES = scanner_zip()
GOOD_STATUS = status_lines("GOODSIG", "VALIDSIG")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Scene:
    """The command's whole world: what the key file lists, what the download and gpg answer, and what each call saw."""

    tool_cache: Path
    clock: FakeClock
    listing: str = field(default_factory=lambda: key_listing(FINGERPRINT))
    status: str = GOOD_STATUS
    downloads: dict[str, bytes | Exception] = field(default_factory=dict)
    broken_programs: dict[str, Exception] = field(default_factory=dict)
    exit_codes: dict[str, int] = field(default_factory=dict)
    seconds_taken: dict[str, float] = field(default_factory=dict)
    requested: list[str] = field(default_factory=list)
    programs: list[str] = field(default_factory=list)
    timeouts: dict[str, float] = field(default_factory=dict)
    unzipped_inside: Path | None = None

    def fetch(self, url, unreadable, expires_at, clock, timeout=15):
        self.requested.append(url)
        self.clock.advance(self.seconds_taken.get("download", 0))
        answer = self.downloads.get(url, OSError("connection refused"))
        if isinstance(answer, Exception):
            raise unreadable(answer)
        return answer

    def run(self, command, cwd=None, env=None, timeout=None):
        program = command[0] if command[0] != "gpg" else f"gpg {next(word for word in ('--show-keys', '--import', '--verify') if word in command)}"
        self.programs.append(program)
        self.timeouts[program] = timeout
        self.clock.advance(self.seconds_taken.get(program, 0))
        if program in self.broken_programs:
            raise self.broken_programs[program]
        stdout = {"gpg --show-keys": self.listing, "gpg --verify": self.status}.get(program, "")
        if program == "unzip":
            destination = Path(command[command.index("-d") + 1])
            self.unzipped_inside = destination
            scanner = destination / f"{FOLDER}" / "bin" / "sonar-scanner"
            scanner.parent.mkdir(parents=True)
            scanner.write_text("#!/bin/sh\n")
        return subprocess.CompletedProcess(command, self.exit_codes.get(program, 0), stdout, "")

    @property
    def installed(self) -> Path:
        return self.tool_cache / "sonar-scanner-cli" / "8.1.0-build.6389" / "linux-x64"

    @property
    def marker(self) -> Path:
        return self.installed.with_name("linux-x64.complete")


@pytest.fixture
def scene(tmp_path) -> Scene:
    clock = FakeClock()
    arranged = Scene(tmp_path / "tool-cache", clock)
    base = sonar_scanner.BINARIES_BASE_URL
    arranged.downloads = {
        f"{base}/sonar-scanner-cli-{VERSION}-linux-x64.zip": ZIP_BYTES,
        f"{base}/sonar-scanner-cli-{VERSION}-linux-x64.zip.asc": SIGNATURE_BYTES,
    }
    return arranged


@pytest.fixture
def drive(scene, capsys, tmp_path) -> Callable[..., tuple[int, str, str]]:
    """Runs the command in `scene` and returns its exit code, stdout and stderr."""
    key_file = tmp_path / "key.asc"
    key_file.write_text("a key file\n")

    def run(overrides: dict[str, str | None] | None = None, **limits) -> tuple[int, str, str]:
        environment = {
            sonar_scanner.VERSION_VARIABLE: VERSION,
            sonar_scanner.FINGERPRINT_VARIABLE: FINGERPRINT,
            sonar_scanner.TOOL_CACHE_VARIABLE: str(scene.tool_cache),
        } | (overrides or {})
        environment = {name: value for name, value in environment.items() if value is not None}
        code = sonar_scanner.run_scanner(
            environment, scene.clock, output.open_channels({}),
            fetch=scene.fetch, run=scene.run, sleep=scene.clock.advance, key_file=key_file, **limits,
        )
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return run


def test_a_good_signature_installs_the_scanner_where_the_action_finds_it_and_ends_clean(scene, drive):
    """SONAR-114, SONAR-118: the folder of the zip stands in the tool cache and the marker beside it"""
    code, out, err = drive()
    assert (code, err) == (0, "")
    assert (scene.installed / "bin" / "sonar-scanner").is_file()
    assert scene.marker.is_file()
    assert out.splitlines()[-1].startswith("✓ clean")


def test_a_present_marker_downloads_nothing_and_leaves_the_install_alone(scene, drive):
    """SONAR-118: a scanner already in the cache is reported and nothing is fetched or run"""
    scene.installed.mkdir(parents=True)
    scene.marker.touch()
    code, out, err = drive()
    assert (code, err, scene.requested, scene.programs) == (0, "", [], [])
    assert "cached" in out


@pytest.mark.parametrize(
    "status",
    [
        status_lines("ERRSIG"),
        status_lines("GOODSIG", "VALIDSIG", primary=OTHER_FINGERPRINT),
        status_lines("GOODSIG"),
        status_lines("VALIDSIG"),
        "",
    ],
    ids=["key not in the file", "signature of another primary key", "no VALIDSIG", "no GOODSIG", "no status at all"],
)
def test_a_signature_that_does_not_verify_names_the_key_file_and_installs_nothing(scene, drive, status):
    """SONAR-117: each failed verification is one line naming the key file and what to replace, exit 2, nothing in the cache"""
    scene.status = status
    code, out, err = drive()
    assert (code, out) == (2, "")
    assert (KEY_FILE_NAME in err, FIX_ADVICE in err, len(err.strip().splitlines())) == (True, True, 2)
    assert not scene.tool_cache.exists()


def test_a_key_file_with_an_expired_subkey_that_did_not_sign_installs(scene, drive):
    """SONAR-117: gpg prints KEYEXPIRED for any expired subkey in the file; only the signature's own status decides"""
    scene.status = status_lines("KEYEXPIRED", "GOODSIG", "VALIDSIG")
    code, _, err = drive()
    assert (code, err, scene.marker.is_file()) == (0, "", True)


def test_a_signature_by_an_expired_subkey_says_to_name_a_release_signed_by_a_valid_one(scene, drive):
    """SONAR-117: replacing the key file alone cannot help; the line says to set the version and re-export the file"""
    scene.status = status_lines("KEYEXPIRED", "EXPKEYSIG", "GOODSIG", "VALIDSIG")
    code, out, err = drive()
    assert (code, out) == (2, "")
    assert (KEY_FILE_NAME in err, sonar_scanner.VERSION_VARIABLE in err, "re-export" in err, FIX_ADVICE in err) == (True, True, True, False)
    assert not scene.tool_cache.exists()


@pytest.mark.parametrize(
    ("status", "steps"),
    [
        (status_lines("GOODSIG", "VALIDSIG", "REVKEYSIG"), (f"set {sonar_scanner.VERSION_VARIABLE}", "re-export")),
        (status_lines("BADSIG"), (RERUN_ADVICE, f"set {sonar_scanner.VERSION_VARIABLE}")),
    ],
    ids=["revoked subkey", "bad signature"],
)
def test_a_signature_that_a_new_key_file_cannot_mend_names_the_steps_that_can(scene, drive, status, steps):
    """SONAR-117: a revoked signing subkey and a zip whose bytes do not match are not cured by replacing the key file alone"""
    scene.status = status
    code, out, err = drive()
    assert (code, out, FIX_ADVICE in err) == (2, "", False)
    assert all(step in err for step in steps)
    assert not scene.tool_cache.exists()


def test_a_signature_by_a_signing_subkey_of_the_pinned_primary_key_installs(scene, drive):
    """SONAR-117: the first field of VALIDSIG is the subkey and is not compared; the last is the primary key and is"""
    scene.status = status_lines("GOODSIG", "VALIDSIG", signer=SIGNING_SUB_FINGERPRINT, primary=FINGERPRINT)
    assert drive()[0] == 0


@pytest.mark.parametrize(
    "listing",
    [
        key_listing(FINGERPRINT, OTHER_FINGERPRINT),
        key_listing(OTHER_FINGERPRINT),
        key_listing(FINGERPRINT, validity="e"),
        key_listing(FINGERPRINT, validity="r"),
        "",
    ],
    ids=["two primary keys", "another fingerprint", "expired key", "revoked key", "no key"],
)
def test_a_key_file_that_is_not_exactly_the_pinned_key_is_refused_before_any_download(scene, drive, listing):
    """SONAR-116: one line names the key file and says to replace it, exit 2, and no request goes out"""
    scene.listing = listing
    code, out, err = drive()
    assert (code, out, scene.requested) == (2, "", [])
    assert (KEY_FILE_NAME in err, FIX_ADVICE in err) == (True, True)


@pytest.mark.parametrize(
    ("variable", "value", "named"),
    [
        (sonar_scanner.VERSION_VARIABLE, None, sonar_scanner.VERSION_VARIABLE),
        (sonar_scanner.VERSION_VARIABLE, "8.1.0", sonar_scanner.VERSION_VARIABLE),
        (sonar_scanner.VERSION_VARIABLE, "8.1.0.x", sonar_scanner.VERSION_VARIABLE),
        (sonar_scanner.FINGERPRINT_VARIABLE, None, sonar_scanner.FINGERPRINT_VARIABLE),
        (sonar_scanner.FINGERPRINT_VARIABLE, FINGERPRINT[:-1], sonar_scanner.FINGERPRINT_VARIABLE),
        (sonar_scanner.FINGERPRINT_VARIABLE, "Z" * 40, sonar_scanner.FINGERPRINT_VARIABLE),
        (sonar_scanner.TOOL_CACHE_VARIABLE, None, sonar_scanner.TOOL_CACHE_VARIABLE),
    ],
    ids=["no version", "three-part version", "version with a letter", "no fingerprint", "short fingerprint", "fingerprint that is no hex", "no tool cache"],
)
def test_a_missing_or_malformed_input_is_exit_2_naming_the_variable_and_the_step(scene, drive, variable, value, named):
    """SONAR-123: the line names the variable and the step that sets it; nothing is fetched"""
    code, out, err = drive({variable: value})
    assert (code, out, scene.requested) == (2, "", [])
    assert (named in err, sonar_scanner.INSTALL_STEP in err) == (True, True)


def test_a_failed_download_is_retried_while_the_deadline_has_time_and_then_ends_naming_the_url(scene, drive):
    """SONAR-115: an unreachable host ends with one line naming the URL and the re-run, exit 2, installing nothing"""
    zip_url = f"{sonar_scanner.BINARIES_BASE_URL}/sonar-scanner-cli-{VERSION}-linux-x64.zip"
    scene.downloads[zip_url] = OSError("connection refused")
    code, out, err = drive()
    assert (code, out) == (2, "")
    assert (zip_url in err, RERUN_ADVICE in err, len(err.strip().splitlines())) == (True, True, 2)
    assert len(scene.requested) > 1
    assert scene.clock.now - 1000.0 >= sonar_scanner.COMMAND_DEADLINE_SECONDS
    assert not scene.tool_cache.exists()


def test_a_download_that_fails_once_is_retried_and_installs(scene, drive):
    """SONAR-115: a failed attempt is retried while the deadline has time left"""
    zip_url = f"{sonar_scanner.BINARIES_BASE_URL}/sonar-scanner-cli-{VERSION}-linux-x64.zip"
    answers = iter([OSError("reset"), ZIP_BYTES])
    original = scene.fetch

    def flaky(url, unreadable, expires_at, clock, timeout=15):
        if url == zip_url:
            answer = next(answers)
            if isinstance(answer, Exception):
                raise unreadable(answer)
            return answer
        return original(url, unreadable, expires_at, clock, timeout)

    scene.fetch = flaky
    assert drive()[0] == 0


@pytest.mark.parametrize(
    ("program", "program_name"),
    [("gpg --show-keys", "gpg"), ("gpg --import", "gpg"), ("gpg --verify", "gpg"), ("unzip", "unzip")],
)
def test_a_program_that_does_not_finish_ends_with_one_line_naming_it_and_the_rerun(scene, drive, program, program_name):
    """SONAR-115: gpg and unzip each end the run with a line that names the program and says to re-run, exit 2"""
    scene.broken_programs[program] = process.ProcessError(f"{program_name} did not finish within 10 seconds")
    code, out, err = drive()
    assert (code, out) == (2, "")
    assert (program_name in err, RERUN_ADVICE in err, len(err.strip().splitlines())) == (True, True, 2)
    assert not scene.marker.exists()


@pytest.mark.parametrize("phase", ["download", "gpg --import", "gpg --verify"])
def test_a_deadline_spent_in_an_earlier_phase_stops_the_next_program_unstarted(scene, drive, phase):
    """SONAR-115: one deadline covers the whole command: a spent deadline starts no further program and names the one it stopped"""
    scene.seconds_taken[phase] = sonar_scanner.COMMAND_DEADLINE_SECONDS
    code, out, err = drive()
    assert (code, out) == (2, "")
    stopped = {"download": "gpg", "gpg --import": "gpg", "gpg --verify": "unzip"}[phase]
    assert (stopped in err, RERUN_ADVICE in err) == (True, True)
    assert not scene.marker.exists()


def test_every_program_gets_no_more_than_its_own_limit_and_the_time_left(scene, drive):
    """SONAR-115: gpg, each of its three calls, and unzip are limited by their constant and by what is left of the deadline"""
    scene.seconds_taken["download"] = (sonar_scanner.COMMAND_DEADLINE_SECONDS - 3) / 2  # two downloads
    drive()
    assert scene.timeouts["gpg --show-keys"] == sonar_scanner.GPG_TIMEOUT_SECONDS
    assert scene.timeouts["gpg --import"] == scene.timeouts["gpg --verify"] == scene.timeouts["unzip"] == 3


def test_a_failed_unzip_leaves_neither_the_scanner_nor_the_marker(scene, drive):
    """SONAR-118: the scanner appears whole or not at all"""
    scene.broken_programs["unzip"] = process.ProcessError("unzip failed")
    drive()
    assert not scene.installed.exists()
    assert not scene.marker.exists()


@pytest.mark.parametrize(
    ("entries", "links"),
    [
        ((), ((f"{FOLDER}/bin/escape", "../../../../etc/passwd"),)),
        ((), ((f"{FOLDER}/d/up", ".."), (f"{FOLDER}/bin/chained", "../d/up/../outside"))),
        ((), ((f"{FOLDER}/a", "d"), (f"{FOLDER}/d/up", ".."), (f"{FOLDER}/bin/deep", "../a/up/../outside"))),
        ((), ((f"{FOLDER}/a", "b"), (f"{FOLDER}/a/escape", "../../../outside"))),
        ((), ((f"{FOLDER}/loop/a", "b"), (f"{FOLDER}/loop/b", "a"))),
        ((), ((f"{FOLDER}/loop", "loop"),)),
        ((), ((f"{FOLDER}/a/b/c/shallow", "../../.."), (f"{FOLDER}/a/b/c/esc", "shallow/../../x"))),
        (((f"{FOLDER}/d/up/../../x", "x"),), ((f"{FOLDER}/d/up", ".."),)),
        (((f"{FOLDER}/d/./x", "x"),), ()),
        ((), ((f"{FOLDER}/bin/absolute", "/etc/passwd"),)),
        ((), ((f"{FOLDER}/bin/rooted", f"/{FOLDER}/outside"),)),
        (((f"{FOLDER}/../outside", "x"),), ()),
        (((f"/{FOLDER}/absolute", "x"),), ()),
        ((("other-folder/file", "x"),), ()),
        (((f"{FOLDER}\\..\\outside", "x"),), ()),
    ],
    ids=["symlink out of the folder", "symlink out through a link to the parent", "symlink out through two links", "symlink under a linked folder", "links that loop", "link to itself", "chain of two links", "entry through a link to the parent", "entry with a dot component", "absolute symlink", "absolute symlink that starts with the folder name", "entry with a parent component", "absolute entry", "entry beside the folder", "entry with a backslash"],
)
def test_an_archive_with_an_entry_that_leaves_its_folder_is_refused_before_unzip_runs(scene, drive, entries, links):
    """SONAR-118: a signed zip may still hold a path or a symbolic link that reaches outside the scanner's folder; nothing is unpacked or installed"""
    scene.downloads[f"{sonar_scanner.BINARIES_BASE_URL}/sonar-scanner-cli-{VERSION}-linux-x64.zip"] = scanner_zip(*entries, links=links)
    code, out, err = drive()
    assert (code, out) == (2, "")
    assert ("leaves" in err, len(err.strip().splitlines())) == (True, 2)
    assert "unzip" not in scene.programs
    assert not scene.marker.exists()


def test_an_archive_that_is_no_zip_is_refused_with_one_line(scene, drive):
    """SONAR-118: bytes that carry a valid signature but are no zip end in a named line, not a traceback"""
    scene.downloads[f"{sonar_scanner.BINARIES_BASE_URL}/sonar-scanner-cli-{VERSION}-linux-x64.zip"] = b"not a zip"
    code, out, err = drive()
    assert (code, out, len(err.strip().splitlines())) == (2, "", 2)
    assert "unzip" not in scene.programs


def test_a_symbolic_link_that_stays_inside_the_folder_installs(scene, drive):
    """SONAR-118: a link between two files of the scanner's own folder, also through another link, is not an escape"""
    scene.downloads[f"{sonar_scanner.BINARIES_BASE_URL}/sonar-scanner-cli-{VERSION}-linux-x64.zip"] = scanner_zip(
        links=(
            (f"{FOLDER}/bin/launcher", "sonar-scanner"), (f"{FOLDER}/lib/current", "../bin/sonar-scanner"),
            (f"{FOLDER}/lib/again", "current"), (f"{FOLDER}/bin/deep/up", "../../lib/again"),
        )
    )
    assert drive()[0] == 0


def test_an_unzip_that_stops_partway_with_an_error_leaves_neither_the_scanner_nor_the_marker(scene, drive):
    """SONAR-118: a non-zero exit of unzip is one line naming it and the re-run, exit 2; the half-written folder is not installed"""
    scene.exit_codes["unzip"] = 50
    code, out, err = drive()
    assert (code, out) == (2, "")
    assert ("unzip" in err, RERUN_ADVICE in err, len(err.strip().splitlines())) == (True, True, 2)
    assert not scene.installed.exists()
    assert not scene.marker.exists()


def test_a_scanner_directory_found_without_its_marker_is_replaced(scene, drive):
    """SONAR-118: a half-installed directory of an earlier run does not fail every later run"""
    (scene.installed / "stray").mkdir(parents=True)
    code, _, err = drive()
    assert (code, err) == (0, "")
    assert not (scene.installed / "stray").exists()
    assert (scene.installed / "bin" / "sonar-scanner").is_file()
    assert scene.marker.is_file()


def test_a_tool_cache_that_cannot_be_written_is_one_named_line_and_exit_2(scene, drive):
    """SONAR-118: a filesystem error (here a file where the tool cache's directory belongs) ends in a line naming the re-run, not a traceback"""
    scene.tool_cache.write_text("a file, not a directory")
    code, out, err = drive()
    assert (code, out) == (2, "")
    assert (RERUN_ADVICE in err, len(err.strip().splitlines())) == (True, 2)


def test_the_scanner_is_unpacked_beside_its_target_and_nothing_else_is_left_in_the_cache(scene, drive):
    """SONAR-118: the temporary directory shares the cache's file system and is gone after the move"""
    drive()
    assert scene.unzipped_inside.parent == scene.installed.parent
    assert sorted(path.name for path in scene.installed.parent.iterdir()) == ["linux-x64", "linux-x64.complete"]


class Listener:
    """A local HTTP server whose answer to every request is `respond`; `release` frees a handler that waits."""

    def __init__(self, stub_server, respond) -> None:
        self.release = threading.Event()
        self.origin, self.heard = stub_server(lambda handler: respond(handler, self.release))


def never_answers(handler, release) -> None:
    release.wait(30)


def one_byte_at_a_time(handler, release) -> None:
    handler.send_response(200)
    handler.send_header("Content-Length", "100000")
    handler.end_headers()
    try:
        while not release.wait(0.05):
            handler.wfile.write(b"x")
            handler.wfile.flush()
    except OSError:
        return


@pytest.mark.parametrize("respond", [never_answers, one_byte_at_a_time], ids=["accepts and never answers", "sends a byte at a time"])
def test_a_server_that_stalls_ends_the_command_within_its_deadline_with_the_named_line(stub_server, tmp_path, capsys, respond):
    """SONAR-115: the one deadline cuts a host that never answers and a host that drips, with a line naming the URL, exit 2"""
    listener = Listener(stub_server, respond)
    deadline_seconds = 1.0
    environment = {
        sonar_scanner.VERSION_VARIABLE: VERSION,
        sonar_scanner.FINGERPRINT_VARIABLE: FINGERPRINT,
        sonar_scanner.TOOL_CACHE_VARIABLE: str(tmp_path / "cache"),
    }
    started = time.monotonic()
    try:
        code = sonar_scanner.run_scanner(
            environment, time.monotonic, output.open_channels({}), run=lambda command, **_: subprocess.CompletedProcess(command, 0, key_listing(FINGERPRINT), ""),
            base_url=listener.origin, deadline_seconds=deadline_seconds, key_file=tmp_path / "key.asc",
        )
    finally:
        listener.release.set()
    elapsed = time.monotonic() - started
    err = capsys.readouterr().err
    assert (code, listener.origin in err, RERUN_ADVICE in err) == (2, True, True)
    assert elapsed < deadline_seconds + 5


def test_a_read_that_starts_after_the_deadline_asks_nobody():
    """SONAR-115: a spent deadline ends the read before a request goes out"""
    refusal = CannotJudge("spent")
    with pytest.raises(CannotJudge) as raised:
        web.fetch_within("http://unreachable.invalid/zip", lambda error: refusal, 10.0, lambda: 10.0)
    assert raised.value is refusal
