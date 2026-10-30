import hashlib
from pathlib import Path

import pytest

from quality import cli

FAKE_KEY = "AKIA" + "QYLPMTYX3QZ5ABCD"  # assembled so quality's own scan stays green
OTHER_KEY = "AKIA" + "QYLPMTYX3QZ5WXYZ"  # a second assembled key, to prove another value stays red
UNKNOWN_KEY_CONFIGS = pytest.mark.parametrize(
    "config",
    [
        '[gate]\nmandatory_jobs = ["checks"]\nwhatever = 1\n',
        '[gate]\nmandatory_jobs = ["checks"]\n[future]\nkey = 1\n',
    ],
    ids=["unknown gate key", "unknown table"],
)


def sha256_of(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def allow_config(*entries: tuple[str, str, str, str]) -> str:
    """Entries are (rule, file, secret, reason); the config holds the secret's hash only."""
    rows = ", ".join(
        f'{{ rule = "{rule}", file = "{file}", secret_sha256 = "{sha256_of(secret)}", reason = "{reason}" }}'
        for rule, file, secret, reason in entries
    )
    return f"[gate]\nmandatory_jobs = [\"checks\"]\n[secrets]\nallow = [{rows}]\n"


def assert_key_found(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Exit 1 alone proves nothing, other rules can fail it too: the secret finding itself is asserted."""
    assert cli.main(["hygiene", str(repo)]) == 1
    assert "secret config.txt:1" in capsys.readouterr().out
