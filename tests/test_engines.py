import hashlib
import http.server
import io
import os
import re
import subprocess
import tarfile
import threading
from pathlib import Path

import pytest

from fleet_quality import cli, engines
from fleet_quality.engines import EngineError, Pin

OUTPUTS = Path(__file__).parent / "fixtures" / "engine-outputs"
FAKE_KEY = "AKIA" + "QYLPMTYX3QZ5ABCD"  # assembled so quality's own scan stays green


def recorded(name: str) -> str:
    return (OUTPUTS / name).read_text(encoding="utf-8")


def archive_with(binary_text: str) -> bytes:
    buffer = io.BytesIO()
    content = binary_text.encode()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        entry = tarfile.TarInfo("gitleaks")
        entry.size = len(content)
        tar.addfile(entry, io.BytesIO(content))
    return buffer.getvalue()


def local_pin(tmp_path: Path, archive: bytes, version: str = "1.2.3", sha256: str | None = None) -> Pin:
    served = tmp_path / "served.tar.gz"
    served.write_bytes(archive)
    return Pin(version, served.as_uri(), sha256 or hashlib.sha256(archive).hexdigest())


PINNED = engines.read_pins()["gitleaks"].version


def fake_gitleaks(tmp_path: Path, version: str = PINNED, on_scan: str = "exit 0", version_exit: int = 0) -> Path:
    """An engine directory whose gitleaks prints `version`, and on any other command runs `on_scan`."""
    engine_dir = tmp_path / "engines"
    engine_dir.mkdir(parents=True)
    binary = engine_dir / "gitleaks"
    binary.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = version ]; then echo {version}; exit {version_exit}; fi\n'
        'while [ $# -gt 0 ]; do [ "$1" = --report-path ] && report=$2; shift; done\n'
        f"{on_scan}\n"
    )
    binary.chmod(0o755)
    return engine_dir


def hygiene_exit_2(repo: Path, engine_dir: Path, capsys) -> str:
    assert cli.main(["hygiene", "--engines", str(engine_dir), str(repo)]) == 2
    return capsys.readouterr().err


class Redirector(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append(dict(self.headers))
        if self.server.redirect_to:
            self.send_response(302)
            self.send_header("Location", self.server.redirect_to)
            self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(self.server.body)

    def log_message(self, *_):
        pass


@pytest.fixture
def serve():
    servers = []

    def start(body: bytes = b"", redirect_to: str = "") -> http.server.HTTPServer:
        server = http.server.HTTPServer(("127.0.0.1", 0), Redirector)
        server.body, server.redirect_to, server.requests = body, redirect_to, []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def url_of(server: http.server.HTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_port}/gitleaks.tar.gz"


def test_pin_file_names_gitleaks_with_a_full_checksum():
    """#13 rule 1: the pin file names the version, a download of that version, and a sha256."""
    pin = engines.read_pins()["gitleaks"]
    assert pin.version
    assert pin.version in pin.url
    assert re.fullmatch(r"[0-9a-f]{64}", pin.sha256)


def test_install_unpacks_a_verified_download_as_an_executable(tmp_path):
    """#13 rule 1: a download that matches its checksum is installed as an executable."""
    pins = {"gitleaks": local_pin(tmp_path, archive_with("#!/bin/sh\n"))}
    engines.install(tmp_path / "bin", pins)
    assert os.access(tmp_path / "bin" / "gitleaks", os.X_OK)


def test_install_follows_a_redirect_to_another_host_without_sending_credentials(tmp_path, serve):
    """#13 rule 1 and fact 3: a redirect to another host is followed, and no credential travels with it."""
    archive = archive_with("#!/bin/sh\n")
    target = serve(body=archive)
    release = serve(redirect_to=url_of(target))
    pins = {"gitleaks": Pin("1.2.3", url_of(release), hashlib.sha256(archive).hexdigest())}
    engines.install(tmp_path / "bin", pins)
    sent = {name.lower() for request in release.requests + target.requests for name in request}
    assert os.access(tmp_path / "bin" / "gitleaks", os.X_OK)
    assert not sent & {"authorization", "cookie"}


def test_a_redirected_download_with_the_wrong_checksum_installs_nothing(tmp_path, serve):
    """#13 rule 1 and fact 3: integrity comes from the checksum, whatever host the redirect leads to."""
    target = serve(body=archive_with("tampered"))
    release = serve(redirect_to=url_of(target))
    pins = {"gitleaks": Pin("1.2.3", url_of(release), "0" * 64)}
    with pytest.raises(EngineError, match="sha256"):
        engines.install(tmp_path / "bin", pins)
    assert not (tmp_path / "bin" / "gitleaks").exists()


def test_wrong_checksum_is_exit_2_and_installs_nothing(tmp_path, capsys):
    """#13 rule 1: a wrong checksum is exit 2 and leaves no binary."""
    pin = local_pin(tmp_path, archive_with("x"), sha256="0" * 64)
    assert cli.main(["engines", "install", str(tmp_path / "bin")], read_pins=lambda: {"gitleaks": pin}) == 2
    assert "sha256" in capsys.readouterr().err
    assert not (tmp_path / "bin" / "gitleaks").exists()


def test_unreachable_download_is_exit_2(tmp_path, capsys):
    """#13 rule 1: a download that cannot be fetched is exit 2."""
    pin = Pin("1.2.3", (tmp_path / "missing.tar.gz").as_uri(), "0" * 64)
    assert cli.main(["engines", "install", str(tmp_path / "bin")], read_pins=lambda: {"gitleaks": pin}) == 2
    assert "cannot download" in capsys.readouterr().err


def test_scan_refuses_an_installed_binary_at_another_version(tmp_path, build_repo, capsys):
    """#13 rule 2: a binary at another version is exit 2 naming both versions."""
    err = hygiene_exit_2(build_repo({}), fake_gitleaks(tmp_path / "fake", "9.9.9"), capsys)
    assert "'9.9.9'" in err
    assert repr(PINNED) in err


def test_scan_never_falls_back_to_a_gitleaks_on_path(tmp_path, build_repo, monkeypatch, capsys):
    """#13 rule 2: a gitleaks on PATH is never used in place of the installed one."""
    on_path = fake_gitleaks(tmp_path / "other")
    monkeypatch.setenv("PATH", f"{on_path}:{os.environ['PATH']}")
    assert "cannot run" in hygiene_exit_2(build_repo({}), tmp_path / "empty", capsys)


def test_a_version_command_that_fails_is_exit_2_even_when_it_prints_the_pin(tmp_path, build_repo, capsys):
    """#13 rule 2: an engine whose version command exits non-zero is not trusted."""
    engine_dir = fake_gitleaks(tmp_path / "fake", version_exit=3)
    assert "version exited 3" in hygiene_exit_2(build_repo({}), engine_dir, capsys)


@pytest.mark.parametrize(
    ("on_scan", "expected"),
    [
        ("echo boom >&2; exit 3", "exited 3 without writing its report: boom"),
        ("exit 0", "exited 0 without writing its report"),
        ('echo "[]" > "$report"; echo boom >&2; exit 3', "exited 3, neither 'no leaks' nor 'leaks found': boom"),
        ('printf "\\377\\376" > "$report"; exit 0', "cannot read the gitleaks report"),
    ],
)
def test_an_engine_that_crashes_or_leaves_no_usable_report_is_exit_2(tmp_path, build_repo, capsys, on_scan, expected):
    """#13 rule 3: a missing, undecodable or exit-code-inconsistent report is exit 2, with the engine's stderr."""
    assert expected in hygiene_exit_2(build_repo({}), fake_gitleaks(tmp_path / "fake", on_scan=on_scan), capsys)


@pytest.mark.parametrize(
    ("report", "exit_code", "expected"),
    [
        ("gitleaks-clean.json", 0, []),
        ("gitleaks-finding.json", 7, ["secret deploy/config.txt:3"]),
    ],
)
def test_report_becomes_findings(report, exit_code, expected):
    """#13 rule 3: each leak in a recorded report becomes a secret finding at file:line."""
    findings = engines.secret_findings(recorded(report), exit_code)
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
    """#13 rule 3: a garbled, mis-shaped or inconsistent result is an EngineError, never clean."""
    text = recorded(report) if report.endswith(".json") else report
    with pytest.raises(EngineError):
        engines.secret_findings(text, exit_code)


@pytest.fixture(scope="session")
def installed_engines(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("engines")
    assert cli.main(["engines", "install", str(directory)]) == 0
    return directory


def scan(repo: Path, installed_engines: Path) -> int:
    return cli.main(["hygiene", "--engines", str(installed_engines), str(repo)])


def commit(repo: Path, message: str) -> None:
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", message], check=True)


@pytest.mark.engine
def test_clean_history_passes(build_repo, installed_engines):
    """#13 rule 4: a repository without a secret passes."""
    assert scan(build_repo({"notes.txt": "nothing here\n"}), installed_engines) == 0


@pytest.mark.engine
def test_committed_secret_fails(build_repo, installed_engines, capsys):
    """#13 rule 4: a committed key fails, reported at file:line with the secret redacted."""
    repo = build_repo({"config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    assert scan(repo, installed_engines) == 1
    out = capsys.readouterr().out
    assert "secret config.txt:1" in out
    assert FAKE_KEY not in out


@pytest.mark.engine
def test_secret_removed_in_a_later_commit_still_fails(build_repo, installed_engines):
    """#13 rules 4 and 5: a key removed in a later commit still fails because it lives in history."""
    repo = build_repo({"config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    (repo / "config.txt").write_text("aws_access_key_id = removed\n")
    commit(repo, "remove the key")
    assert scan(repo, installed_engines) == 1


@pytest.mark.engine
def test_installed_binary_reports_the_pinned_version(build_repo, installed_engines):
    """#13 rule 2: the installed real gitleaks passes the version check against the pin."""
    assert engines.scan_secrets(build_repo({}), installed_engines, engines.read_pins()) == []
