import datetime
import io
import json
import shlex
import zipfile
from pathlib import Path

from quality import engines

OSV = engines.OSV_SCANNER
ADVISORY_ID = "GHSA-aaaa-bbbb-cccc"
UV_LOCK_ALLOWANCE = {"identifier": ADVISORY_ID, "lock": "uv.lock", "item": "#116", "reason": "fixture"}
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


def advisory(
    identifier=ADVISORY_ID, *, fixed=("5.4",), aliases=("CVE-2020-1",), also_affected=(), package="pyyaml", ecosystem="PyPI"
) -> dict:
    ranges = [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": version}]} for version in fixed]
    ranges = ranges or [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}]}]
    return {
        "schema_version": "1.9.0", "id": identifier, "modified": "2026-01-01T00:00:00Z", "aliases": list(aliases),
        "affected": [{"package": {"name": package, "ecosystem": ecosystem}, "ranges": ranges}, *also_affected],
    }


def zipped(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in entries.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def reporting(report: Path, exit_code: int, stderr: str = "") -> str:
    """Shell for a fake osv-scanner that prints `report` (@LOCK@ read as the last lock passed) and exits."""
    return (
        f'for argument; do lock="${{argument#--lockfile=*:}}"; done\n'
        f'sed "s|@LOCK@|$lock|" {shlex.quote(str(report))}\necho {shlex.quote(stderr)} >&2\nexit {exit_code}'
    )


def package_of(report: dict) -> dict:
    return report["results"][0]["packages"][0]


def tracked_lock_repo(build_repo, text: str = uv_lock()) -> Path:
    return build_repo({"uv.lock": text})


def allowance_config(
    identifier: str | None = "GHSA-462x-pgg6-55fp",
    lock: str | None = "speech/providers/qwen/uv.lock",
    until: datetime.date | str | None = datetime.date(2027, 1, 31),
    date: datetime.date | str | None = datetime.date(2026, 10, 21),
    item: str | None = "overnightworks/agent-presentator#371",
    reason: str | None = "gradio caps pillow<13; no image input",
) -> str:
    """One [[advisories.allow]] entry; a string is written as a TOML string, a date as a TOML date, None drops the key."""
    values = {"id": identifier, "lock": lock, "until": until, "date": date, "item": item, "reason": reason}
    entries = "".join(
        f"{key} = {value.isoformat() if isinstance(value, datetime.date) else json.dumps(value)}\n"
        for key, value in values.items() if value is not None
    )
    return "[[advisories.allow]]\n" + entries
