import hashlib
import io
import os
import subprocess
import tarfile
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


def fake_gitleaks(tmp_path: Path, version: str) -> tuple[Path, dict[str, Pin]]:
    engine_dir = tmp_path / "engines"
    engine_dir.mkdir(parents=True)
    binary = engine_dir / "gitleaks"
    binary.write_text(f"#!/bin/sh\necho {version}\n")
    binary.chmod(0o755)
    return engine_dir, {"gitleaks": Pin("1.2.3", "unused", "unused")}


def test_pin_file_names_gitleaks_with_a_full_checksum():
    pin = engines.read_pins()["gitleaks"]
    assert (pin.version, len(pin.sha256)) == ("8.30.1", 64)


def test_install_unpacks_a_verified_download_as_an_executable(tmp_path):
    pins = {"gitleaks": local_pin(tmp_path, archive_with("#!/bin/sh\n"))}
    engines.install(tmp_path / "bin", pins)
    assert os.access(tmp_path / "bin" / "gitleaks", os.X_OK)


def test_wrong_checksum_is_exit_2_and_installs_nothing(tmp_path, capsys, monkeypatch):
    pin = local_pin(tmp_path, archive_with("x"), sha256="0" * 64)
    monkeypatch.setattr(engines, "read_pins", lambda: {"gitleaks": pin})
    assert cli.main(["engines", "install", str(tmp_path / "bin")]) == 2
    assert "sha256" in capsys.readouterr().err
    assert not (tmp_path / "bin" / "gitleaks").exists()


def test_unreachable_download_is_exit_2(tmp_path, monkeypatch, capsys):
    pin = Pin("1.2.3", (tmp_path / "missing.tar.gz").as_uri(), "0" * 64)
    monkeypatch.setattr(engines, "read_pins", lambda: {"gitleaks": pin})
    assert cli.main(["engines", "install", str(tmp_path / "bin")]) == 2
    assert "cannot download" in capsys.readouterr().err


def test_scan_refuses_an_installed_binary_at_another_version(tmp_path, build_repo):
    engine_dir, pins = fake_gitleaks(tmp_path / "fake", "9.9.9")
    with pytest.raises(EngineError, match="'9.9.9'.*'1.2.3'"):
        engines.scan_secrets(build_repo({}), engine_dir, pins)


def test_scan_never_falls_back_to_a_gitleaks_on_path(tmp_path, build_repo, monkeypatch):
    on_path, _ = fake_gitleaks(tmp_path / "other", "1.2.3")
    monkeypatch.setenv("PATH", f"{on_path}:{os.environ['PATH']}")
    with pytest.raises(EngineError, match="cannot run"):
        engines.scan_secrets(build_repo({}), tmp_path / "empty", {"gitleaks": Pin("1.2.3", "u", "s")})


@pytest.mark.parametrize(
    ("report", "exit_code", "expected"),
    [
        ("gitleaks-clean.json", 0, []),
        ("gitleaks-finding.json", 7, ["secret deploy/config.txt:3"]),
    ],
)
def test_report_becomes_findings(report, exit_code, expected):
    findings = engines.secret_findings(recorded(report), exit_code)
    assert [f"{f.rule} {f.location}" for f in findings] == expected


@pytest.mark.parametrize(
    ("report", "exit_code"),
    [
        ("gitleaks-garbled.json", 7),
        ("gitleaks-garbled.json", 0),
        ("", 0),
        ("gitleaks-clean.json", 7),
        ("gitleaks-finding.json", 0),
        ("gitleaks-clean.json", 1),
        ("gitleaks-finding.json", 126),
    ],
)
def test_unreadable_or_inconsistent_gitleaks_result_cannot_be_judged(report, exit_code):
    text = recorded(report) if report else ""
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
    assert scan(build_repo({"notes.txt": "nothing here\n"}), installed_engines) == 0


@pytest.mark.engine
def test_committed_secret_fails(build_repo, installed_engines, capsys):
    repo = build_repo({"config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    assert scan(repo, installed_engines) == 1
    out = capsys.readouterr().out
    assert "secret config.txt:1" in out
    assert FAKE_KEY not in out


@pytest.mark.engine
def test_secret_removed_in_a_later_commit_still_fails(build_repo, installed_engines):
    repo = build_repo({"config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    (repo / "config.txt").write_text("aws_access_key_id = removed\n")
    commit(repo, "remove the key")
    assert scan(repo, installed_engines) == 1


@pytest.mark.engine
def test_installed_binary_reports_the_pinned_version(build_repo, installed_engines):
    assert engines.scan_secrets(build_repo({}), installed_engines, engines.read_pins()) == []
