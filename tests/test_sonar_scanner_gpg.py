import io
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from quality import output, sonar_scanner

pytestmark = pytest.mark.integration

VERSION = "8.1.0.6389"
FOLDER = f"sonar-scanner-{VERSION}-linux-x64"
ZIP_URL = f"{sonar_scanner.BINARIES_BASE_URL}/sonar-scanner-cli-{VERSION}-linux-x64.zip"
SIGNED_LONG_AGO = "20230601T120000"
CREATED_LONG_AGO = "20230101T120000"


@dataclass(frozen=True)
class Signer:
    """A throwaway key in its own short home: gpg's socket path must stay under the system limit, which `tmp_path` may not."""

    home: Path
    fingerprint: str

    def gpg(self, *arguments: str, stdin: bytes | None = None) -> bytes:
        command = ["gpg", "--homedir", str(self.home), "--batch", "--yes", "--pinentry-mode", "loopback", "--passphrase", "", *arguments]
        return subprocess.run(command, check=True, capture_output=True, input=stdin).stdout  # noqa: S603 - a fixed argv of a test

    @property
    def subkey(self) -> str:
        listing = self.gpg("--with-colons", "--list-keys").decode().splitlines()
        return [line.split(":")[9] for line in listing if line.startswith("fpr:")][-1]

    def public_key(self) -> bytes:
        return self.gpg("--armor", "--export", self.fingerprint)

    def sign(self, payload: bytes, at: str | None = None, with_key: str | None = None) -> bytes:
        faked = ["--faked-system-time", at] if at else []
        return self.gpg(*faked, "--armor", "--detach-sign", "--local-user", with_key or self.fingerprint, stdin=payload)


@pytest.fixture
def make_signer() -> Iterator[Callable[..., Signer]]:
    homes: list[Path] = []

    def make(*, subkey_expiry: str | None = None, primary_expiry: str = "never", created: str | None = None) -> Signer:
        home = Path(tempfile.mkdtemp(prefix="g"))
        homes.append(home)
        home.chmod(0o700)
        faked = ["--faked-system-time", created] if created else []
        signer = Signer(home, "")
        signer.gpg(*faked, "--quick-generate-key", "Throwaway <throwaway@example.invalid>", "ed25519", "cert,sign", primary_expiry)
        fingerprint = next(
            line.split(":")[9] for line in signer.gpg("--with-colons", "--list-keys").decode().splitlines() if line.startswith("fpr:")
        )
        signer = Signer(home, fingerprint)
        if subkey_expiry:
            signer.gpg(*faked, "--quick-add-key", fingerprint, "ed25519", "sign", subkey_expiry)
        return signer

    yield make
    for home in homes:
        subprocess.run(["gpgconf", "--homedir", str(home), "--kill", "all"], check=False, capture_output=True)  # noqa: S607, S603 - stops the agent the signing started
        shutil.rmtree(home, ignore_errors=True)


def scanner_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{FOLDER}/bin/sonar-scanner", "#!/bin/sh\n")
    return buffer.getvalue()


def run_command(tmp_path: Path, capsys, key_file_content: bytes, fingerprint: str, payload: bytes, signature: bytes) -> tuple[int, str, str]:
    key_file = tmp_path / "key.asc"
    key_file.write_bytes(key_file_content)
    answers = {ZIP_URL: payload, f"{ZIP_URL}.asc": signature}

    def fetch(url, unreadable, expires_at, clock, timeout=15):
        return answers[url]

    environment = {
        sonar_scanner.VERSION_VARIABLE: VERSION,
        sonar_scanner.FINGERPRINT_VARIABLE: fingerprint,
        sonar_scanner.TOOL_CACHE_VARIABLE: str(tmp_path / "cache"),
    }
    code = sonar_scanner.run_scanner(environment, lambda: 0.0, output.open_channels({}), fetch=fetch, key_file=key_file)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def installed(tmp_path: Path) -> bool:
    return (tmp_path / "cache" / "sonar-scanner-cli" / "8.1.0-build.6389" / "linux-x64.complete").exists()


def test_a_zip_signed_by_the_pinned_key_installs(make_signer, tmp_path, capsys):
    """SONAR-114, SONAR-117: gpg verifies the signature against the key file and the scanner stands in the cache"""
    signer = make_signer()
    payload = scanner_zip()
    code, _, err = run_command(tmp_path, capsys, signer.public_key(), signer.fingerprint, payload, signer.sign(payload))
    assert (code, err, installed(tmp_path)) == (0, "", True)
    assert (tmp_path / "cache" / "sonar-scanner-cli" / "8.1.0-build.6389" / "linux-x64" / "bin" / "sonar-scanner").is_file()


def test_a_zip_signed_by_a_signing_subkey_of_the_pinned_key_installs(make_signer, tmp_path, capsys):
    """SONAR-117: VALIDSIG names the subkey first and the primary key last; only the primary key is pinned"""
    signer = make_signer(subkey_expiry="never")
    payload = scanner_zip()
    code, _, err = run_command(tmp_path, capsys, signer.public_key(), signer.fingerprint, payload, signer.sign(payload, with_key=f"{signer.subkey}!"))
    assert (code, err, installed(tmp_path)) == (0, "", True)


def test_a_zip_changed_after_signing_is_refused_and_installs_nothing(make_signer, tmp_path, capsys):
    """SONAR-117: a bad signature is exit 2 naming the key file, and the cache stays empty"""
    signer = make_signer()
    payload = scanner_zip()
    code, out, err = run_command(tmp_path, capsys, signer.public_key(), signer.fingerprint, payload + b"x", signer.sign(payload))
    assert (code, out, installed(tmp_path)) == (2, "", False)
    assert sonar_scanner.KEY_FILE_NAME in err
    assert not (tmp_path / "cache").exists()


def test_a_signature_by_a_key_that_is_not_in_the_file_is_refused(make_signer, tmp_path, capsys):
    """SONAR-117: the zip was signed by another key than the file holds"""
    pinned, stranger = make_signer(), make_signer()
    payload = scanner_zip()
    code, out, err = run_command(tmp_path, capsys, pinned.public_key(), pinned.fingerprint, payload, stranger.sign(payload))
    assert (code, out, installed(tmp_path)) == (2, "", False)
    assert sonar_scanner.KEY_FILE_NAME in err


def test_a_signature_by_an_expired_signing_subkey_is_refused(make_signer, tmp_path, capsys):
    """SONAR-117: the primary key is valid, the signing subkey expired after it signed; gpg still prints VALIDSIG and exits 0"""
    signer = make_signer(subkey_expiry="1y", created=CREATED_LONG_AGO)
    payload = scanner_zip()
    signature = signer.sign(payload, at=SIGNED_LONG_AGO, with_key=f"{signer.subkey}!")
    code, out, err = run_command(tmp_path, capsys, signer.public_key(), signer.fingerprint, payload, signature)
    assert (code, out, installed(tmp_path)) == (2, "", False)
    assert sonar_scanner.KEY_FILE_NAME in err


def test_a_key_file_with_two_primary_keys_is_refused(make_signer, tmp_path, capsys):
    """SONAR-116: the file holds exactly one primary key"""
    pinned, other = make_signer(), make_signer()
    payload = scanner_zip()
    code, out, err = run_command(
        tmp_path, capsys, pinned.public_key() + other.public_key(), pinned.fingerprint, payload, pinned.sign(payload)
    )
    assert (code, out, installed(tmp_path)) == (2, "", False)
    assert sonar_scanner.KEY_FILE_NAME in err


def test_a_key_file_of_another_fingerprint_than_the_pinned_one_is_refused(make_signer, tmp_path, capsys):
    """SONAR-116: the key file's primary key must be the one the step names"""
    signer, named = make_signer(), make_signer()
    payload = scanner_zip()
    code, out, err = run_command(tmp_path, capsys, signer.public_key(), named.fingerprint, payload, signer.sign(payload))
    assert (code, out, installed(tmp_path)) == (2, "", False)
    assert sonar_scanner.KEY_FILE_NAME in err


def test_an_expired_primary_key_is_refused(make_signer, tmp_path, capsys):
    """SONAR-116, SONAR-117: a key file whose primary key has expired is refused"""
    signer = make_signer(primary_expiry="1y", created=CREATED_LONG_AGO)
    payload = scanner_zip()
    code, out, err = run_command(tmp_path, capsys, signer.public_key(), signer.fingerprint, payload, signer.sign(payload, at=SIGNED_LONG_AGO))
    assert (code, out, installed(tmp_path)) == (2, "", False)
    assert sonar_scanner.KEY_FILE_NAME in err
