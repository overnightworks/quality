import json
import os
import shlex
import signal
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path

import pytest

from quality import config, scanners, staged
from quality.report import Example, FindingEvent, NoteEvent, printable
from quality.rules import Rule
from tests.support.check import (
    CopyLine,
    DetailLine,
    FixLine,
    NoteLine,
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
from tests.support.door import CHECK_WORD, MODULE_DOOR, check_door, door_in
from tests.support.scanners import OUTPUTS, check_exit_2, commit, fake_gitleaks, flagging, scan, serving, vetting
from tests.support.secrets import FAKE_KEY, HOSTILE_FILE, OTHER_KEY, allow_config, assert_key_found, sha256_of

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
    exit_code = check_door(repo)
    assert exit_code == code
    assert [finding.location for finding in rule_findings(repo, Rule.LEAK)] == found
    assert allowed_locations(repo) == allowed
    assert RECORDED_SECRET not in heard(capsys)


def test_a_recorded_leak_is_one_secret_finding_naming_its_rule_and_commit_and_never_its_value(build_repo, served_scanners):
    """#24 rule 4, O28: the finding carries the secret event, a fact with the rule and the commit's first eight characters."""
    (finding,) = rule_findings(recorded_leak_repo(build_repo, served_scanners, None), Rule.LEAK)
    assert finding.event is FindingEvent.SECRET
    assert (RECORDED_RULE in finding.fact, RECORDED_COMMIT[:8] in finding.fact) == (True, True)
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
    assert check_door(repo) == 1
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
    assert "[secrets] allow entry" in check_exit_2(build_repo({"quality/config.toml": config}), capsys)


@pytest.mark.parametrize(
    "report",
    ['[{"File": "a", "StartLine": 1, "RuleID": "r", "Commit": "c"}]',
     '[{"File": "a", "StartLine": 1, "RuleID": "r", "Commit": "c", "Secret": 5}]'],
    ids=["no-secret", "secret-not-a-string"],
)
def test_a_report_entry_without_a_string_secret_is_exit_2(build_repo, served_scanners, capsys, report):
    """#24 rule 5: a finding whose value cannot be hashed is never clean."""
    fake_gitleaks(served_scanners, on_scan=f"echo '{report}' > \"$report\"; exit 7")
    check_exit_2(build_repo({}), capsys)


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


def leak_scan_words(command: str, repo: Path) -> list[str]:
    """The words of a command that scans `repo` for secrets; scan-staged gets a staged copy of the key to find."""
    if command == "scan-staged":
        (repo / "staged.txt").write_text(f"aws_access_key_id = {FAKE_KEY}\n")
        subprocess.run(["git", "-C", str(repo), "add", "staged.txt"], check=True)
    return [command]


SECRET_COMMANDS = [CHECK_WORD, "scan-staged"]


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
    exit_code = door_in(keyed_repo, *leak_scan_words(command, keyed_repo))
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
    argv = [*MODULE_DOOR, *leak_scan_words(command, keyed_repo)]
    scanner = subprocess.Popen(argv, cwd=keyed_repo, env=os.environ | {"TMPDIR": str(scratch_parent)}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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


@pytest.mark.scanner
@pytest.mark.parametrize("command", SECRET_COMMANDS)
def test_an_allowance_no_finding_used_is_one_unused_note_at_the_config(build_repo, installed_scanners, capsys, command):
    """#25 rule 1, quality#5 finding 34: an entry that matches nothing is named, not silent, in both doors."""
    repo = build_repo({"quality/config.toml": allow_config(("aws-access-token", "no\\u001bthere.txt", FAKE_KEY, "stale entry"))})
    code = door_in(repo, command)
    streams = capsys.readouterr()
    printed = streams.out
    whole_run = command == CHECK_WORD
    notes = rule_notes(repo, Rule.LEAK) if whole_run else list(staged.scan_staged(repo).notes)
    block = area_block(printed, "secrets") if whole_run else decode_block(printed.splitlines())
    (note,) = notes
    assert (code, note.event, note.path) == (0, NoteEvent.UNUSED, config.CONFIG_PATH)
    assert ("aws-access-token" in note.fact, STALE_FILE in note.fact) == (True, True)
    assert_block_prints(block, [], notes)
    assert FAKE_KEY not in streams.out + streams.err
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
    streams = capsys.readouterr()
    lines = streams.out.splitlines()
    printed = streams.out + streams.err
    assert [key in printed for key in (FAKE_KEY, OTHER_KEY)] == [False, False]
    prelude = lines[:next(number for number, line in enumerate(lines) if line.split()[1:2] == ["files"])]
    assert [line for line in prelude if line.lstrip().startswith(Rule.LEAK)] == []
    found, notes = rule_findings(repo, Rule.LEAK), rule_notes(repo, Rule.LEAK)
    assert ([finding.location for finding in found], [note.event for note in notes]) == (["other.txt:1"], [NoteEvent.ALLOWED, NoteEvent.UNUSED])
    allowed = notes[0]
    assert ("aws-access-token" in allowed.fact, "test key" in allowed.fact) == (True, True)
    assert_block_prints(area_block("\n".join(lines), "secrets"), found, notes)


@pytest.mark.parametrize(
    "answer", ["echo 'fatal: refused' >&2; exit 1", "echo many; exit 0"], ids=["git-fails", "git-prints-no-number"]
)
def test_a_commit_count_git_cannot_give_refuses_the_secret_rule_alone(build_repo, served_scanners, fake_git, capsys, answer):
    """O30: the extent of a clean row is never guessed; the secret rule cannot judge, the other rows stand."""
    repo = build_repo({".env": "KEY=1\n"})
    fake_git(f'case "$*" in *rev-list*--count*) {answer};; esac')
    out = check_exit_2(repo, capsys)
    assert set(refusals(repo)) == {Rule.LEAK}
    assert env_row_stands(out)


def assert_no_value(value: str, printed: str, summary: Path) -> None:
    """Neither `value` nor its escaped form stands in `printed`, and no step summary was written."""
    assert [form for form in (value, printable(value)) if form in printed] == []
    assert not summary.exists()


def gitleaks_exit_rows(printed: str) -> list[tuple[str, str]]:
    """Each `check` exit row under the secrets row, as its scanner and fact."""
    return [(line.location, line.row.fact) for line in area_block(printed, "secrets") if isinstance(line, NoteLine) and line.row.event is NoteEvent.EXITED]


def recorded_leak_with(edited_report, **fields: str) -> Path:
    """The recorded gitleaks report with these fields of its one leak set."""
    return edited_report("gitleaks-finding.json", lambda report: report[0].update(fields))


def leak_with_stderr(report: Path, *lines: str) -> str:
    """Shell for a gitleaks scan that reports the leak in `report`, writes `lines` to stderr and exits with leaks found."""
    return f"cp '{report}' \"$report\"; printf '%s\\n' {shlex.join(lines)} >&2; exit {scanners.GITLEAKS_LEAKS_FOUND}"


NOT_CLEARED_PAIR = "7 · 2 stderr lines withheld · not cleared by gitleaks"
FAILING_PAIR = "3 · 1 stderr line withheld · a failing gitleaks is never quoted"


@pytest.mark.parametrize(
    ("id_", "allowed", "expected", "vetted"),
    [
        ("a finding", False, (1, NOT_CLEARED_PAIR), True),
        ("an allowed finding", True, (0, NOT_CLEARED_PAIR), True),
        ("gitleaks failing with stderr", False, (2, FAILING_PAIR), False),
        ("stderr beside a report", False, (2, FAILING_PAIR), False),
        ("a report that is not UTF-8", False, (2, "7"), False),
        ("a report that is no JSON", False, (2, "7"), False),
        ("the value on gitleaks' stdout", False, (0, "0"), False),
        ("a value in a commit message", False, (1, "7"), False),
    ],
)
def test_a_planted_value_reaches_no_stream_of_a_verbose_run(
    build_repo, served_scanners, edited_report, tmp_path, monkeypatch, capsys, id_, allowed, expected, vetted
):
    """The row's cases under `-v`: the value stands in no stream and no summary, and gitleaks' own row stands with its exit."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    leak = recorded_leak_with(edited_report, Secret=FAKE_KEY)
    git_scans = {
        "a finding": leak_with_stderr(leak, "3:14PM INF scanned", f"ERR [git] {FAKE_KEY}"),
        "gitleaks failing with stderr": FAILING_REPORTS["stderr-without-report"],
        "stderr beside a report": FAILING_REPORTS["stderr-beside-a-report"],
        "a report that is not UTF-8": FAILING_REPORTS["report-not-utf8"],
        "a report that is no JSON": f'printf \'[{{"Secret":"{FAKE_KEY}"\' > "$report"; exit 7',
        "the value on gitleaks' stdout": f'echo "[]" > "$report"; echo {FAKE_KEY}',
        "a value in a commit message": f"cp '{recorded_leak_with(edited_report, Message=FAKE_KEY)}' \"$report\"; exit 7",
    }
    git_scans["an allowed finding"] = git_scans["a finding"]
    config_text = allow_config((RECORDED_RULE, RECORDED_FILE, FAKE_KEY, "a planted value")) if allowed else None
    fake_gitleaks(served_scanners, on_scan=vetting(flagging(FAKE_KEY), on_git=git_scans[id_]))
    repo = build_repo({"quality/config.toml": config_text} if config_text is not None else {})
    code = door_in(repo, CHECK_WORD, "-v", clock=lambda: 0.0)
    printed = capsys.readouterr()
    assert (code, gitleaks_exit_rows(printed.out)) == (expected[0], [(scanners.GITLEAKS, expected[1])])
    assert_no_value(FAKE_KEY, printed.out + printed.err, summary)
    assert (served_scanners / "gitleaks.vetted").exists() == vetted


@pytest.mark.parametrize(
    "id_", ["a finding", "gitleaks failing with stderr", "a report that is not UTF-8", "a staged key"]
)
def test_a_planted_value_reaches_no_stream_of_the_staged_door(build_repo, served_scanners, edited_report, tmp_path, monkeypatch, capsys, id_):
    """The staged door takes no `-v`: nothing a scanner writes to either stream reaches a stream of the door or a summary."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    on_scan = {
        "a finding": leak_with_stderr(recorded_leak_with(edited_report, Secret=FAKE_KEY), f"ERR [git] {FAKE_KEY}"),
        "gitleaks failing with stderr": FAILING_REPORTS["stderr-without-report"],
        "a report that is not UTF-8": FAILING_REPORTS["report-not-utf8"],
        "a staged key": f'echo "[]" > "$report"; echo {FAKE_KEY}; echo {FAKE_KEY} >&2',
    }[id_]
    fake_gitleaks(served_scanners, on_scan=on_scan)
    repo = build_repo({})
    door_in(repo, *leak_scan_words("scan-staged", repo))
    printed = capsys.readouterr()
    assert_no_value(FAKE_KEY, printed.out + printed.err, summary)


def test_a_value_in_a_file_name_stands_no_more_often_under_v(build_repo, served_scanners, edited_report, capsys):
    """A file's name in a finding's row is a route older than `-v`; a stderr line naming the file adds none."""
    name = f"docs/{FAKE_KEY}.txt"
    leak = recorded_leak_with(edited_report, File=name)
    fake_gitleaks(served_scanners, on_scan=vetting(flagging(FAKE_KEY), on_git=leak_with_stderr(leak, f"WRN leak in {name}")))
    repo = build_repo({})
    door_in(repo, CHECK_WORD, clock=lambda: 0.0)
    plain = capsys.readouterr().out
    door_in(repo, CHECK_WORD, "-v", clock=lambda: 0.0)
    assert capsys.readouterr().out.count(FAKE_KEY) == plain.count(FAKE_KEY)


@pytest.mark.scanner
@pytest.mark.parametrize(
    ("files", "fact", "said"),
    [
        pytest.param({"docs/a.md": "nothing\n"}, "0", "no leaks found", id="clean"),
        pytest.param({"config.txt": f"aws_access_key_id = {FAKE_KEY}\n"}, "7", "leaks found: 1", id="a finding"),
    ],
)
def test_gitleaks_real_stderr_stands_whole_under_v(build_repo, installed_scanners, capsys, files, fact, said):
    """A bump of gitleaks that changes what it writes to stderr turns this red."""
    door_in(build_repo(files), CHECK_WORD, "-v", clock=lambda: 0.0)
    printed = capsys.readouterr()
    block = area_block(printed.out, "secrets")
    details = [line.detail for line in block if isinstance(line, DetailLine)]
    assert gitleaks_exit_rows(printed.out) == [(scanners.GITLEAKS, fact)]
    assert any(said in detail for detail in details)
    assert FAKE_KEY not in printed.out + printed.err
