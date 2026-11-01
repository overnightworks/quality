import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

import pytest

from quality import cli, config, scanners, staged
from quality.report import Example, FindingEvent, NoteEvent
from quality.rules import Rule
from tests.support.check import (
    CopyLine,
    FixLine,
    area_block,
    area_fact,
    assert_block_prints,
    assert_every_line_starts_with_the_door,
    assert_finding_vocabulary,
    decode_block,
    env_row_stands,
    extent_counts,
    heard,
    leading_count,
    refusals,
    rule_findings,
    rule_notes,
)
from tests.support.scanners import OUTPUTS, commit, fake_gitleaks, hygiene_exit_2, scan, serving
from tests.support.secrets import FAKE_KEY, OTHER_KEY, allow_config, assert_key_found, sha256_of

RECORDED_SECRET = "PLACEHOLDER-SECRET-VALUE"  # the Secret in the recorded gitleaks-finding.json
RECORDED_RULE = "aws-access-token"
RECORDED_FILE = "deploy/config.txt"
RECORDED_LOCATION = f"{RECORDED_FILE}:3"


RECORDED_COMMIT = json.loads((OUTPUTS / "gitleaks-finding.json").read_text(encoding="utf-8"))[0]["Commit"]
GATE_ONLY = '[gate]\nmandatory_jobs = ["checks"]\n'


def recorded_leak_repo(build_repo, served_scanners, config_text: str | None) -> Path:
    repo = build_repo({"quality/config.toml": config_text} if config_text is not None else {})
    fake_gitleaks(served_scanners, on_scan=f'cp {OUTPUTS / "gitleaks-finding.json"} "$report"; exit 7')
    return repo


def allowed_locations(repo: Path) -> list[str]:
    return [note.location for note in rule_notes(repo, Rule.LEAK) if note.event is NoteEvent.ALLOWED]


@pytest.mark.parametrize(
    ("config_text", "code", "found", "allowed"),
    [
        (None, 1, [RECORDED_LOCATION], []),
        (allow_config((RECORDED_RULE, RECORDED_FILE, "another value", "other value")), 1, [RECORDED_LOCATION], []),
        (allow_config((RECORDED_RULE, "src/app.py", RECORDED_SECRET, "other file")), 1, [RECORDED_LOCATION], []),
        (allow_config(("generic-api-key", RECORDED_FILE, RECORDED_SECRET, "other rule")), 1, [RECORDED_LOCATION], []),
        (allow_config((RECORDED_RULE, RECORDED_FILE, RECORDED_SECRET, "test data")), 0, [], [RECORDED_LOCATION]),
    ],
    ids=[
        "no-config-is-red-not-exit-2",
        "another-value-at-the-same-place-is-red",
        "the-value-in-another-file-is-red",
        "the-value-under-another-rule-is-red",
        "allowed-value-is-green-and-a-note",
    ],
)
def test_a_secret_finding_is_allowed_only_by_rule_file_and_value(build_repo, served_scanners, capsys, config_text, code, found, allowed):
    """#24 rule 1: a finding is allowed only when rule, file and the hash of its value match one entry."""
    repo = recorded_leak_repo(build_repo, served_scanners, config_text)
    exit_code = cli.main(["hygiene", str(repo)])
    assert exit_code == code
    assert [finding.location for finding in rule_findings(repo, Rule.LEAK)] == found
    assert allowed_locations(repo) == allowed
    assert RECORDED_SECRET not in heard(capsys)


def test_a_recorded_leak_is_one_secret_finding_naming_its_rule_and_commit_and_never_its_value(build_repo, served_scanners):
    """#24 rule 4, O28: the finding carries the secret event, a fact with the rule and the commit's first eight characters."""
    (finding,) = rule_findings(recorded_leak_repo(build_repo, served_scanners, None), Rule.LEAK)
    assert finding.event is FindingEvent.SECRET
    assert (RECORDED_RULE in finding.reason, RECORDED_COMMIT[:8] in finding.reason) == (True, True)
    assert RECORDED_SECRET not in repr(finding)
    assert_finding_vocabulary(finding)


def test_the_entry_to_copy_is_lines_that_read_as_one_allowance_once_a_reason_is_added(build_repo, served_scanners):
    """O31, O33: the fix is an example, not a sentence; its lines are TOML that holds one secrets allowance."""
    (finding,) = rule_findings(recorded_leak_repo(build_repo, served_scanners, None), Rule.LEAK)
    assert isinstance(finding.fix, Example)
    entries = tomllib.loads("\n".join(finding.fix.lines))["secrets"]["allow"]
    assert len(entries) == 1


@pytest.mark.scanner
def test_one_value_on_two_lines_is_two_rows_and_one_fix(build_repo, installed_scanners, capsys):
    """Rule 7: one value on two lines of one file is two rows under one `fix:` and one entry."""
    repo = build_repo({"config.txt": f"aws_access_key_id = {FAKE_KEY}\nsecond = {FAKE_KEY}\n"})
    assert cli.main(["hygiene", str(repo)]) == 1
    block = area_block(capsys.readouterr().out, "secrets")
    findings = rule_findings(repo, Rule.LEAK)
    assert_block_prints(block, findings, rule_notes(repo, Rule.LEAK))
    assert len(findings) == 2
    assert sum(isinstance(line, FixLine) for line in block) == 1
    assert sum(isinstance(line, CopyLine) for line in block) == len(findings[0].fix.lines)


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
    printed = capsys.readouterr().out
    allowed = [note.location for note in rule_notes(repo, Rule.LEAK) if note.event is NoteEvent.ALLOWED]
    assert sorted(allowed) == ["docs/config.txt:1", "docs/config.txt:4"]
    assert extent_counts(area_fact(printed, "secrets"))[-1] == len(allowed)


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
    assert [finding.location for finding in rule_findings(repo, Rule.LEAK)] == [placed]
    assert FAKE_KEY not in heard(capsys)


@pytest.mark.scanner
def test_a_key_on_an_allowance_line_of_the_config_stays_red(build_repo, installed_scanners, capsys):
    """#24 rule 1: only the hash of an allowance is exempt; a real key on the same line is still found."""
    line = f'# {FAKE_KEY} secret_sha256 = "{sha256_of(FAKE_KEY)}"\n'
    repo = build_repo({"quality/config.toml": allow_config() + line})
    assert scan(repo) == 1
    assert [finding.path for finding in rule_findings(repo, Rule.LEAK)] == [config.CONFIG_PATH]
    assert FAKE_KEY not in heard(capsys)


@pytest.mark.scanner
def test_a_repository_without_quality_config_is_scanned_and_its_key_is_red(build_repo, installed_scanners, capsys):
    """#24 rule 3: no quality/config.toml means no allowances, and the scan still judges."""
    repo = build_repo({"config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    assert not (repo / "quality").exists()
    assert scan(repo) == 1
    assert [finding.location for finding in rule_findings(repo, Rule.LEAK)] == ["config.txt:1"]
    assert FAKE_KEY not in heard(capsys)


FAILING_REPORTS = {
    "gitleaks-fails": f'printf \'[{{"Secret":"{FAKE_KEY}"}}]\' > "$report"; exit 1',
    "gitleaks-stalls": f'printf \'[{{"Secret":"{FAKE_KEY}"}}]\' > "$report"; exec sleep 30',
    "parsing-raises": f'printf \'[{{"Secret":"{FAKE_KEY}"}}]\' > "$report"; exit 7',
    "report-not-utf8": f'printf \'[{{"Secret":"{FAKE_KEY}"}}]\\342\\202\' > "$report"; exit 7',
    "stderr-without-report": f'echo "aws_access_key_id = {FAKE_KEY}" >&2; exit 3',
    "stderr-beside-a-report": f'printf \'[{{"Secret":"{FAKE_KEY}"}}]\' > "$report"; echo "aws_access_key_id = {FAKE_KEY}" >&2; exit 3',
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


STALE_FILE = "no\x1bthere.txt"
HOSTILE_FILE = 'k\x1b"\u202e.txt'


@pytest.mark.scanner
@pytest.mark.parametrize("command", SECRET_COMMANDS)
def test_an_allowance_no_finding_used_is_one_unused_note_at_the_config(build_repo, installed_scanners, capsys, command):
    """#25 rule 1, quality#5 finding 34: an entry that matches nothing is named, not silent, in both doors."""
    repo = build_repo({"quality/config.toml": allow_config(("aws-access-token", "no\\u001bthere.txt", FAKE_KEY, "stale entry"))})
    code = cli.main([command, str(repo)])
    printed = capsys.readouterr().out
    hygiene = command == "hygiene"
    notes = rule_notes(repo, Rule.LEAK) if hygiene else list(staged.scan_staged(repo).notes)
    block = area_block(printed, "secrets") if hygiene else decode_block(printed.splitlines())
    (note,) = notes
    assert (code, note.event, note.path) == (0, NoteEvent.UNUSED, config.CONFIG_PATH)
    assert ("aws-access-token" in note.fact, STALE_FILE in note.fact) == (True, True)
    assert_block_prints(block, [], notes)
    assert FAKE_KEY not in printed
    assert_every_line_starts_with_the_door(printed.splitlines())


@pytest.mark.scanner
@pytest.mark.parametrize("name", ["docs/config.txt", f"docs/{HOSTILE_FILE}"], ids=["plain-path", "esc-quote-and-bidi-path"])
def test_the_printed_entry_with_a_reason_allows_exactly_that_finding(build_repo, installed_scanners, capsys, name):
    """O31, acceptance 3: the lines under `fix:`, with a reason added and committed, turn the finding into one allowed note."""
    repo = build_repo({name: f"aws_access_key_id = {FAKE_KEY}\n", config.CONFIG_PATH: GATE_ONLY})
    assert scan(repo) == 1
    first = capsys.readouterr()
    (finding,) = rule_findings(repo, Rule.LEAK)
    entry = [line.copy for line in area_block(first.out, "secrets") if isinstance(line, CopyLine)]
    with (repo / config.CONFIG_PATH).open("a", encoding="utf-8") as allowing:
        allowing.write("\n".join(entry) + '\nreason = "scenario reason"\n')
    commit(repo, "allow what the door printed")
    assert scan(repo) == 0
    second = capsys.readouterr()
    assert (rule_findings(repo, Rule.LEAK), allowed_locations(repo)) == ([], [finding.location])
    assert FAKE_KEY not in first.out + first.err + second.out + second.err


@pytest.mark.scanner
def test_the_clean_secrets_row_counts_the_commits_the_scenario_made(build_repo, installed_scanners, capsys):
    """O30: one commit reads as one."""
    assert scan(build_repo({"docs/notes.txt": "nothing here\n"})) == 0
    assert leading_count(area_fact(capsys.readouterr().out, "secrets")) == 1


@pytest.mark.scanner
def test_the_clean_secrets_row_counts_every_commit_and_every_allowed_leak(build_repo, installed_scanners, capsys):
    """O30, acceptance 5: three commits with a key removed in the second; the row ends with the allowed notes."""
    repo = build_repo({"docs/config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    (repo / "docs/config.txt").write_text("removed\n")
    commit(repo, "remove the key")
    allow_fake_key(repo, "docs/config.txt")
    assert scan(repo) == 0
    counts = extent_counts(area_fact(capsys.readouterr().out, "secrets"))
    assert (counts[0], counts[-1]) == (3, len(allowed_locations(repo)))


@pytest.mark.scanner
def test_nothing_about_secrets_prints_before_the_area_rows(build_repo, installed_scanners, capsys):
    """O15: an allowed key, an unused entry and a red key print as rows under the secrets row, none before the first area row."""
    entries = (("aws-access-token", "config.txt", FAKE_KEY, "test key"), ("aws-access-token", "nothere.txt", FAKE_KEY, "stale entry"))
    repo = build_repo({
        "config.txt": f"aws_access_key_id = {FAKE_KEY}\n", "other.txt": f"aws_access_key_id = {OTHER_KEY}\n",
        config.CONFIG_PATH: allow_config(*entries),
    })
    assert scan(repo) == 1
    lines = capsys.readouterr().out.splitlines()
    prelude = lines[:next(number for number, line in enumerate(lines) if line.split()[1:2] == ["files"])]
    assert [line for line in prelude if line.lstrip().startswith(Rule.LEAK)] == []
    found, notes = rule_findings(repo, Rule.LEAK), rule_notes(repo, Rule.LEAK)
    assert ([finding.location for finding in found], sorted(note.event for note in notes)) == (["other.txt:1"], [NoteEvent.ALLOWED, NoteEvent.UNUSED])
    assert_block_prints(area_block("\n".join(lines), "secrets"), found, notes)


@pytest.mark.parametrize(
    "answer", ["echo 'fatal: refused' >&2; exit 1", "echo many; exit 0"], ids=["git-fails", "git-prints-no-number"]
)
def test_a_commit_count_git_cannot_give_refuses_the_secret_rule_alone(build_repo, served_scanners, fake_git, capsys, answer):
    """O30: the extent of a clean row is never guessed; the secret rule cannot judge, the other rows stand."""
    repo = build_repo({".env": "KEY=1\n"})
    fake_git(f'case "$*" in *rev-list*--count*) {answer};; esac')
    out = hygiene_exit_2(repo, capsys)
    assert set(refusals(repo)) == {Rule.LEAK}
    assert env_row_stands(out)
