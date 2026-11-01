import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from quality import cli, scanners
from tests.support.scanners import OUTPUTS, commit, fake_gitleaks, hygiene_exit_2, scan, serving
from tests.support.secrets import FAKE_KEY, OTHER_KEY, allow_config, assert_key_found, sha256_of

RECORDED_SECRET = "PLACEHOLDER-SECRET-VALUE"  # the Secret in the recorded gitleaks-finding.json
RECORDED_RULE = "aws-access-token"
RECORDED_FILE = "deploy/config.txt"


def run_with_recorded_leak(build_repo, served_scanners, capsys, config: str | None) -> tuple[int, str]:
    repo = build_repo({"quality/config.toml": config} if config is not None else {})
    fake_gitleaks(served_scanners, on_scan=f'cp {OUTPUTS / "gitleaks-finding.json"} "$report"; exit 7')
    code = cli.main(["hygiene", str(repo)])
    return code, capsys.readouterr().out


@pytest.mark.parametrize(
    ("config", "code", "printed", "not_printed"),
    [
        (None, 1, "secret deploy/config.txt:3", "allowed"),
        (allow_config((RECORDED_RULE, RECORDED_FILE, "another value", "other value")), 1,
         "secret deploy/config.txt:3", "allowed"),
        (allow_config((RECORDED_RULE, "src/app.py", RECORDED_SECRET, "other file")), 1,
         "secret deploy/config.txt:3", "allowed"),
        (allow_config(("generic-api-key", RECORDED_FILE, RECORDED_SECRET, "other rule")), 1,
         "secret deploy/config.txt:3", "allowed"),
        (allow_config((RECORDED_RULE, RECORDED_FILE, RECORDED_SECRET, "test data")), 0,
         "secret: 1 allowed (deploy/config.txt:3)", " finding"),
    ],
    ids=[
        "no-config-is-red-not-exit-2",
        "another-value-at-the-same-place-is-red",
        "the-value-in-another-file-is-red",
        "the-value-under-another-rule-is-red",
        "allowed-value-is-green-and-printed",
    ],
)
def test_a_secret_finding_is_allowed_only_by_rule_file_and_value(
    build_repo, served_scanners, capsys, config, code, printed, not_printed
):
    """#24 rule 1: a finding is allowed only when rule, file and the hash of its value match one entry."""
    exit_code, out = run_with_recorded_leak(build_repo, served_scanners, capsys, config)
    assert exit_code == code, out
    assert printed in out
    assert not_printed not in out
    assert RECORDED_SECRET not in out
    if code == 0:
        assert RECORDED_RULE in out
        assert "test data" in out  # the entry's reason


def test_a_red_secret_finding_prints_the_hash_to_allow_and_not_the_value(build_repo, served_scanners, capsys):
    """#24 rule 4: the output names the hash a reviewer would put into the allowance, never the value."""
    _, out = run_with_recorded_leak(build_repo, served_scanners, capsys, None)
    assert f"sha256 {sha256_of(RECORDED_SECRET)}, value not shown" in out
    assert "allow this rule, file and sha256 with a reason in quality/config.toml" in out
    assert RECORDED_SECRET not in out


@pytest.mark.parametrize(
    "entry", ['""', "{ fingerprint = \"x\" }", '{ fingerprint = "x", reason = "reason" }', '"x"'],
    ids=["empty", "no-reason", "old-fingerprint-form", "bare-string"],
)
def test_an_invalid_allow_entry_is_exit_2(build_repo, capsys, entry):
    """#24 rule 2: an allow entry that is not rule, file, hash and reason cannot be judged."""
    config = f'[gate]\nmandatory_jobs = ["checks"]\n[secrets]\nallow = [{entry}]\n'
    assert "[secrets] allow entry" in hygiene_exit_2(build_repo({"quality/config.toml": config}), capsys)


@pytest.mark.parametrize(
    "report",
    ['[{"File": "a", "StartLine": 1, "RuleID": "r", "Commit": "c"}]',
     '[{"File": "a", "StartLine": 1, "RuleID": "r", "Commit": "c", "Secret": 5}]'],
    ids=["no-secret", "secret-not-a-string"],
)
def test_a_report_entry_without_a_string_secret_is_exit_2(build_repo, served_scanners, capsys, report):
    """#24 rule 5: a finding whose value cannot be hashed is never clean."""
    fake_gitleaks(served_scanners, on_scan=f"echo '{report}' > \"$report\"; exit 7")
    hygiene_exit_2(build_repo({}), capsys)


def test_the_scan_passes_the_gates_own_config(build_repo, served_scanners):
    """#16: gitleaks is told to use quality's config, not the repository's."""
    fake_gitleaks(served_scanners, on_scan='echo "[]" > "$report"; exit 0')
    repo = build_repo({".gitleaks.toml": "[allowlist]\n"})
    scanners.scan_secrets(repo, {})
    given = (served_scanners / "gitleaks.arguments").read_text().split()
    assert given[given.index("--config") + 1] == str(scanners.GITLEAKS_CONFIG)


@pytest.mark.scanner
def test_a_repository_config_and_ignore_file_do_not_silence_the_scan(build_repo, installed_scanners, capsys):
    """#16: a committed .gitleaks.toml, a .gitleaksignore holding the real fingerprint and gitleaks:allow
    have no effect."""
    repo = build_repo({"config.txt": f"aws_access_key_id = {FAKE_KEY}  # gitleaks:allow\n"})
    first_commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    (repo / ".gitleaks.toml").write_text('[extend]\nuseDefault = true\n[allowlist]\npaths = [\'\'\'config.txt\'\'\']\n')
    (repo / ".gitleaksignore").write_text(f"{first_commit}:config.txt:aws-access-token:1\n")
    commit(repo, "try to silence the scan")
    assert_key_found(repo, capsys)


def allow_fake_key(repo: Path, file: str = "config.txt") -> None:
    (repo / "quality").mkdir(exist_ok=True)
    (repo / "quality" / "config.toml").write_text(allow_config(("aws-access-token", file, FAKE_KEY, "test key")))
    commit(repo, "allow the fixture key")


@pytest.mark.scanner
def test_an_allowed_value_stays_green_at_a_shifted_line_and_across_commits(build_repo, installed_scanners, capsys):
    """#24 rule 1: one entry covers the value at line 1, at a later line and in every commit that holds it."""
    repo = build_repo({"docs/config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    (repo / "docs/config.txt").write_text("removed\n")
    commit(repo, "remove the key")
    (repo / "docs/config.txt").write_text(f"# one\n# two\n# three\naws_access_key_id = {FAKE_KEY}\n")
    commit(repo, "key again, three lines further down")
    allow_fake_key(repo, "docs/config.txt")
    assert scan(repo) == 0
    summary = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("secret: 2 allowed ("))
    assert set(summary.removeprefix("secret: 2 allowed (").removesuffix(")").split(", ")) == {"docs/config.txt:1", "docs/config.txt:4"}


@pytest.mark.scanner
@pytest.mark.parametrize(
    ("write", "placed"),
    [
        (lambda repo: (repo / "config.txt").write_text("aws_access_key_id = " + OTHER_KEY + "\n"), "config.txt:1"),
        (lambda repo: (repo / "src.txt").write_text(f"aws_access_key_id = {FAKE_KEY}\n"), "src.txt:1"),
    ],
    ids=["another-value-at-the-same-place", "the-value-in-another-file"],
)
def test_an_allowance_never_spreads_to_another_value_or_file(build_repo, installed_scanners, capsys, write, placed):
    """#24 rule 1: the allowed value stays allowed only in its file; any other value or file is red."""
    repo = build_repo({"config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    allow_fake_key(repo)
    write(repo)
    commit(repo, "a second leak")
    assert scan(repo) == 1
    assert f"secret {placed}" in capsys.readouterr().out


@pytest.mark.scanner
def test_a_key_on_an_allowance_line_of_the_config_stays_red(build_repo, installed_scanners, capsys):
    """#24 rule 1: only the hash of an allowance is exempt; a real key on the same line is still found."""
    line = f'# {FAKE_KEY} secret_sha256 = "{sha256_of(FAKE_KEY)}"\n'
    repo = build_repo({"quality/config.toml": allow_config() + line})
    assert scan(repo) == 1
    assert "secret quality/config.toml:" in capsys.readouterr().out


@pytest.mark.scanner
def test_a_repository_without_quality_config_is_scanned_and_its_key_is_red(build_repo, installed_scanners, capsys):
    """#24 rule 3: no quality/config.toml means no allowances, and the scan still judges."""
    repo = build_repo({"config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    assert not (repo / "quality").exists()
    assert scan(repo) == 1
    assert "secret config.txt:1" in capsys.readouterr().out


FAILING_REPORTS = {
    "gitleaks-fails": f'printf \'[{{"Secret":"{FAKE_KEY}"}}]\' > "$report"; exit 1',
    "gitleaks-stalls": f'printf \'[{{"Secret":"{FAKE_KEY}"}}]\' > "$report"; exec sleep 30',
    "parsing-raises": f'printf \'[{{"Secret":"{FAKE_KEY}"}}]\' > "$report"; exit 7',
    "report-not-utf8": f'printf \'[{{"Secret":"{FAKE_KEY}"}}]\\342\\202\' > "$report"; exit 7',
}


def leak_scan_command(command: str, repo: Path) -> list[str]:
    """The argv of a command that scans `repo` for secrets; scan-staged gets a staged copy of the key to find."""
    if command == "scan-staged":
        (repo / "staged.txt").write_text(f"aws_access_key_id = {FAKE_KEY}\n")
        subprocess.run(["git", "-C", str(repo), "add", "staged.txt"], check=True)
    return [command, str(repo)]


SECRET_COMMANDS = ["hygiene", "scan-staged"]


@pytest.mark.scanner
@pytest.mark.parametrize("command", SECRET_COMMANDS)
@pytest.mark.parametrize("failure", ["none", *FAILING_REPORTS])
def test_the_key_reaches_no_output_and_no_file_and_the_scratch_directory_is_gone(
    keyed_repo, installed_scanners, use_mise, tmp_path, monkeypatch, capsys, failure, command
):
    """#24 rule 4, #25 rule 1: after a normal run, a gitleaks failure, a stall and a parse error nothing holds the key."""
    scratch_parent = tmp_path / "scratch-parent"
    scratch_parent.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch_parent))
    if failure == "gitleaks-stalls":
        monkeypatch.setattr(scanners, "SECRET_SCAN_TIMEOUT_SECONDS", 1)
    if failure != "none":
        failing = tmp_path / "failing"
        failing.mkdir()
        fake_gitleaks(failing, on_scan=FAILING_REPORTS[failure])
        use_mise(serving(failing))
    exit_code = cli.main(leak_scan_command(command, keyed_repo))
    assert exit_code == (1 if failure == "none" else 2)
    heard = capsys.readouterr()
    assert FAKE_KEY not in heard.out + heard.err
    assert list(scratch_parent.iterdir()) == []


@pytest.mark.integration
@pytest.mark.parametrize("command", SECRET_COMMANDS)
def test_a_sigterm_mid_scan_leaves_no_scratch_directory(keyed_repo, served_scanners, tmp_path, command):
    """#25 rule 1, quality#5 finding 44: a SIGTERM while gitleaks holds the unredacted report deletes the scratch directory."""
    scratch_parent = tmp_path / "scratch-parent"
    scratch_parent.mkdir()
    fake_gitleaks(served_scanners, on_scan=FAILING_REPORTS["gitleaks-stalls"])
    argv = [sys.executable, "-m", "quality.cli", *leak_scan_command(command, keyed_repo)]
    scanner = subprocess.Popen(argv, env=os.environ | {"TMPDIR": str(scratch_parent)}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 30
        while not list(scratch_parent.glob("*/report.json")):
            assert time.monotonic() < deadline, "gitleaks never wrote its report"
            time.sleep(0.05)
        scanner.terminate()
        assert scanner.wait(timeout=30) == 128 + signal.SIGTERM
    finally:
        scanner.kill()
        scanner.communicate()
    assert list(scratch_parent.iterdir()) == []


@pytest.mark.scanner
@pytest.mark.parametrize("command", SECRET_COMMANDS)
def test_an_allowance_no_finding_used_is_printed(build_repo, installed_scanners, capsys, command):
    """#25 rule 1, quality#5 finding 34: a quality/config.toml entry that matches nothing is named, not silent."""
    repo = build_repo({"quality/config.toml": allow_config(("aws-access-token", "nothere.txt", FAKE_KEY, "stale entry"))})
    code = cli.main([command, str(repo)])
    assert code == 0
    assert (
        f"secret: unused allowance aws-access-token in nothere.txt (sha256 {sha256_of(FAKE_KEY)}) - stale entry"
        in capsys.readouterr().out
    )
