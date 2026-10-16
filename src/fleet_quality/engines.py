import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from fleet_quality.report import Finding

SECRET = "secret"
GITLEAKS = "gitleaks"
GITLEAKS_NO_LEAKS = 0
GITLEAKS_LEAKS_FOUND = 7  # gitleaks exits 1 on its own errors too, so leaks get a code of their own
DOWNLOAD_TIMEOUT_SECONDS = 120
PIN_FILE = Path(__file__).with_name("engines.toml")


class EngineError(Exception):
    """An engine cannot be installed or trusted; this is never a clean result."""


@dataclass(frozen=True)
class Pin:
    version: str
    url: str
    sha256: str


def read_pins(pin_file: Path = PIN_FILE) -> dict[str, Pin]:
    with pin_file.open("rb") as handle:
        return {name: Pin(**fields) for name, fields in tomllib.load(handle).items()}


def install(directory: Path, pins: dict[str, Pin]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, pin in pins.items():
        archive = _download_verified(name, pin)
        (directory / name).write_bytes(_unpack_binary(name, archive))
        (directory / name).chmod(0o755)


def _download_verified(name: str, pin: Pin) -> bytes:
    try:
        with urllib.request.urlopen(pin.url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:  # follows redirects
            archive = response.read()
    except (OSError, ValueError) as error:
        raise EngineError(f"cannot download {name} from {pin.url}: {error}") from error
    actual = hashlib.sha256(archive).hexdigest()
    if actual != pin.sha256:
        raise EngineError(f"{name} download has sha256 {actual}, the pin says {pin.sha256}")
    return archive


def _unpack_binary(name: str, archive: bytes) -> bytes:
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            member = tar.extractfile(name)
            if member is None:
                raise EngineError(f"the {name} archive has no file {name}")
            return member.read()
    except (tarfile.TarError, KeyError) as error:
        raise EngineError(f"cannot unpack {name}: {error}") from error


def scan_secrets(root: Path, engine_dir: Path, pins: dict[str, Pin]) -> list[Finding]:
    binary = (engine_dir / GITLEAKS).resolve()
    _verify_version(binary, pins[GITLEAKS].version)
    with tempfile.TemporaryDirectory() as scratch:
        report = Path(scratch) / "report.json"
        command = [
            str(binary), "git", "--redact", "--no-banner", "--no-color",
            "--exit-code", str(GITLEAKS_LEAKS_FOUND),
            "--report-format", "json", "--report-path", str(report), str(root),
        ]
        exit_code = _run(command).returncode
        if not report.is_file():
            raise EngineError(f"{GITLEAKS} exited {exit_code} without writing its report")
        return secret_findings(report.read_text(encoding="utf-8"), exit_code)


def _verify_version(binary: Path, pinned: str) -> None:
    reported = _run([str(binary), "version"]).stdout.strip()
    if reported != pinned:
        raise EngineError(f"{binary} is version {reported!r}, the pin says {pinned!r}")


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as error:
        raise EngineError(f"cannot run {command[0]}: {error}") from error


def secret_findings(report_text: str, exit_code: int) -> list[Finding]:
    if exit_code not in (GITLEAKS_NO_LEAKS, GITLEAKS_LEAKS_FOUND):
        raise EngineError(f"{GITLEAKS} exited {exit_code}, neither 'no leaks' nor 'leaks found'")
    try:
        leaks = [(leak["File"], leak["StartLine"], leak["RuleID"], leak["Commit"]) for leak in json.loads(report_text)]
    except (ValueError, KeyError, TypeError) as error:
        raise EngineError(f"cannot read the {GITLEAKS} report: {error!r}") from error
    if bool(leaks) != (exit_code == GITLEAKS_LEAKS_FOUND):
        raise EngineError(f"{GITLEAKS} exited {exit_code} but its report holds {len(leaks)} leak(s)")
    return [
        Finding(
            SECRET, path, line,
            f"{GITLEAKS} rule {rule} matched in commit {commit[:8]} (secret redacted).",
            "rotate the secret; deleting it from the tree leaves it in history.",
        )
        for path, line, rule, commit in leaks
    ]
