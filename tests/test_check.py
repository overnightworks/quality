import contextlib
import io
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from quality import check, cli, env_file, installation, process, scanners, sonar
from quality.compose import is_compose_file
from quality.report import CannotJudge, Finding, Unparsable
from quality.rules import Area, Rule
from tests.support.check import CLEAN_LINE, compose, installed_ruff, loosen_requirement
from tests.support.check import hygiene_output as run
from tests.support.scanners import fake_scanner, fake_zizmor, lacking, never_located, nothing_installed


def deleted_compose_repo(build_repo, tmp_path):
    repo = build_repo(compose('- "8080:80"'))
    (repo / "compose.yaml").unlink()
    return repo


def truncated_compose_repo(build_repo, tmp_path):
    return build_repo({"compose.yaml": "services: [\n"})


def non_utf8_filename_repo(build_repo, tmp_path):
    return build_repo({"docs/\udcff.txt": "x\n"})


@pytest.mark.parametrize(
    ("arrange", "expected_code", "expected_text"),
    [
        (lambda build_repo, tmp_path: build_repo({}), 0, CLEAN_LINE),
        (non_utf8_filename_repo, 0, CLEAN_LINE),
        (lambda build_repo, tmp_path: tmp_path, 2, "cannot judge"),
        (lambda build_repo, tmp_path: build_repo({".env": "A=1\n"}), 1, "finding(s)"),
        (lambda build_repo, tmp_path: build_repo({"\udcff.env": "A=1\n"}), 1, "env-file \\udcff.env - "),
        (deleted_compose_repo, 0, CLEAN_LINE),
        (truncated_compose_repo, 2, "cannot judge"),
    ],
    ids=[
        "empty-repository",
        "non-utf8-filename",
        "not-a-git-repository",
        "tracked-env-file",
        "cell 9 and #86 cell 7 non-utf8-env-filename-rendered-safely",
        "#76 cell 5 compose deleted from disk is gone",
        "malformed-compose-yaml",
    ],
)
def test_exit_code_tells_clean_from_cannot_judge(build_repo, tmp_path, capsys, arrange, expected_code, expected_text):
    """#3 rule 1: exit 0 clean, 1 findings, 2 cannot judge."""
    code, output = run(capsys, arrange(build_repo, tmp_path))
    assert code == expected_code
    assert expected_text in output


@pytest.mark.integration
def test_quality_tracks_no_env_or_compose_file():
    """#3 rule 5: the quality repository passes its own hygiene check and tracks no env or compose file."""
    root = Path(__file__).resolve().parents[1]
    tracked = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True, text=True, check=True).stdout
    assert cli.main(["hygiene", str(root)]) == 0
    assert not [name for name in tracked.splitlines() if is_compose_file(name)]
    assert ".env" not in tracked


def test_a_tracked_workflow_missing_from_disk_is_never_handed_to_a_scanner(build_repo, use_mise, served_scanners, capsys):
    """#76 rule 3, cell 10: no workflow scanner is installed, so any name passed on would be cannot judge."""
    use_mise(lacking(served_scanners, scanners.ACTIONLINT))
    repo = build_repo({".github/workflows/w.yml": "on: push\n"})
    (repo / ".github/workflows/w.yml").unlink()
    code = cli.main(["hygiene", str(repo)])
    captured = capsys.readouterr()
    assert (code, "hygiene: clean (" in captured.out) == (0, True), captured.err


BASELINE = {"quality/baseline.toml": ""}
NEW_DEBT = "def long_one():\n" + "    x = 1\n" * 61
ENV_FINDING = "env-file .env - "


def config_linked_out_of_the_repository(build_repo, elsewhere):
    repo = build_repo({".env": "A=1\n"})
    (elsewhere / "config.toml").write_text('[gate]\nmandatory_jobs = ["checks"]\n')
    (repo / "quality").mkdir()
    (repo / "quality/config.toml").symlink_to(elsewhere / "config.toml")
    subprocess.run(["git", "-C", str(repo), "add", "quality/config.toml"], check=True)
    return repo


def files(tracked: dict[str, str]):
    return lambda build_repo, elsewhere: build_repo(tracked)


@pytest.mark.parametrize(
    ("arrange", "expected_code", "expected_lines"),
    [
        (files({**BASELINE, ".env": "A=1\n", "src/bad.py": "def broken(:\n"}), 2,
         [ENV_FINDING, "hygiene: size unparsable - src/bad.py is not valid Python",
          "hygiene: python-security unparsable - ruff cannot parse src/bad.py",
          "hygiene: python-print unparsable - ruff cannot parse src/bad.py",
          "hygiene: 2 finding(s); verdicts: 14 clean, 2 findings, 3 unparsable"]),
        (config_linked_out_of_the_repository, 2,
         [ENV_FINDING, "hygiene: compose-port cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: size cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: secret cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: advisory cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: root-layout cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: file-suffix cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: script-name cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: script-header cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: test-layout cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: test-import cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: python-floor cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: 1 finding(s); verdicts: 7 clean, 1 findings, 11 cannot judge"]),
        (files({**BASELINE, "compose.yaml": "services: [\n", "src/a.py": NEW_DEBT}), 2,
         ["hygiene: compose-port cannot judge - cannot read compose.yaml", "size src/a.py:1 - function long_one"]),
        (files({".env": "A=1\n", **compose('- "8080:80"')}), 1,
         [ENV_FINDING, "compose-port compose.yaml - ", "hygiene: 3 finding(s); verdicts: 16 clean, 3 findings"]),
        (files({"compose-\udcff.yaml": compose('- "8080:80"')["compose.yaml"]}), 1,
         ["compose-port compose-\\udcff.yaml - service web publishes port 8080:80"]),
        (files({**BASELINE, "src/\udcff.py": NEW_DEBT}), 1, ["size src/\\udcff.py:1 - function long_one"]),
        (files({"src/\udcff.py": "def broken(:\n"}), 2,
         ["functions unmeasured: src/\\udcff.py", "hygiene: python-security unparsable - ruff cannot parse src/\\udcff.py",
          "hygiene: python-print unparsable - ruff cannot parse src/\\udcff.py"]),
    ],
    ids=[
        "cell 1 unparsable size hides no env-file finding",
        "cell 2 secret cannot judge hides no env-file finding",
        "cell 3 unreadable compose hides no size finding",
        "cell 4 findings in two rules",
        "cell 7 non-utf8 compose name",
        "cell 8 non-utf8 python name",
        "rule 4 non-utf8 python name in the size report",
    ],
)
def test_every_rule_is_judged_and_the_exit_is_the_worst(
    build_repo, elsewhere, capsys, arrange, expected_code, expected_lines
):
    """#55 rules 1-4, cells 1-8 (cell 9 is the exit-code table's non-utf8 env case): each rule prints its own
    verdict, one rule's refusal hides no other rule's, the exit is the worst verdict's, the clean line names the
    rules, and every path prints backslash-escaped."""
    code = cli.main(["hygiene", str(arrange(build_repo, elsewhere))])
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert code == expected_code, output
    assert [line for line in expected_lines if line not in output] == [], output


def test_a_run_without_flags_judges_every_rule(build_repo, capsys):
    """#228 rule 4: no scan is ever skipped, so a plain run names every rule in run order."""
    code, output = run(capsys, build_repo({}))
    assert (code, f"hygiene: clean ({', '.join(Rule)})" in output) == (0, True), output


def test_a_finding_under_a_rule_its_check_does_not_judge_fails_loud(build_repo, monkeypatch):
    """#55 rule 2: a finding under a rule the check was not given is never dropped into a clean exit."""
    foreign = Finding("workflow-other", ".env", None, "an unlisted rule", "none")
    monkeypatch.setattr(env_file, "check_env_files", lambda tracked: [foreign])
    arguments = ["hygiene", str(build_repo({}))]
    with pytest.raises(ValueError, match="workflow-other"):
        cli.main(arguments)


def test_a_refusing_workflow_scanner_stops_no_other_workflow_rule(build_repo, served_scanners, elsewhere, capsys):
    """#55 rule 1: actionlint cannot judge, yet zizmor still runs and its finding prints under its own rule."""
    zizmor_report = Path(__file__).parent / "fixtures" / "scanner-outputs" / "zizmor-finding.json"
    fake_scanner(served_scanners, scanners.ACTIONLINT, "echo boom >&2; exit 3")
    locate_zizmor = fake_zizmor(elsewhere, on_scan=f"cat '{zizmor_report}'; exit 12")
    repo = build_repo({".github/workflows/w.yml": "on: push\n"})
    code = cli.main(["hygiene", str(repo)], locate_zizmor=locate_zizmor)
    captured = capsys.readouterr()
    assert code == 2
    assert "hygiene: workflow-lint cannot judge - actionlint exited 3" in captured.err
    assert "workflow-security .github/workflows/w.yml:7 - zizmor unpinned-uses" in captured.out


@pytest.mark.parametrize("case", ["not installed", "recorded file deleted", "another version", "failing version", "unpinned"])
def test_a_zizmor_quality_cannot_find_or_trust_refuses_only_workflow_security(
    build_repo, served_scanners, elsewhere, monkeypatch, capsys, case
):
    """#248 rule 4: a zizmor that is missing, at another version or unable to report one cannot judge and names the
    reinstall command; a pin quality's own metadata lacks names the defect and no command; no other rule refuses."""
    repo = build_repo({".env": "A=1\n", ".github/workflows/w.yml": "on: push\n"})
    for name in ("another", "failing", "pinned"):
        (elsewhere / name).mkdir()
    failing = elsewhere / "failing" / "zizmor"
    failing.write_text("#!/bin/sh\nexit 1\n")
    failing.chmod(0o755)
    locators = {
        "not installed": nothing_installed,
        "recorded file deleted": lambda: str(elsewhere / "gone" / "zizmor"),
        "another version": fake_zizmor(elsewhere / "another", "echo '[]'", version="0.0.1"),
        "failing version": lambda: str(failing),
        "unpinned": fake_zizmor(elsewhere / "pinned", "echo '[]'"),
    }
    if case == "unpinned":
        loosen_requirement(elsewhere / "metadata", monkeypatch, "zizmor")
    code = cli.main(["hygiene", str(repo)], locate_zizmor=locators[case])
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert code == 2, output
    assert "hygiene: workflow-security cannot judge - " in output, output
    assert "workflow-lint cannot judge" not in output
    assert "env-file .env - " in output
    assert (installation.REINSTALL in output) is (case != "unpinned"), output
    if case == "unpinned":
        assert str(installation.UnpinnedDependency("zizmor")) in output
    versions_named = ["0.0.1", installation.required_version("zizmor")] if case == "another version" else []
    assert [version for version in versions_named if version not in output] == [], output


def test_a_zizmor_planted_in_the_scanner_directory_is_never_run(build_repo, served_scanners, elsewhere, capsys):
    """#248 rule 4, no bridge: the verdict comes from quality's installation, not from the directory mise serves."""
    marker = elsewhere / "ran"
    planted = served_scanners / "zizmor"
    planted.write_text(f"#!/bin/sh\ntouch {marker}\n")
    planted.chmod(0o755)
    (elsewhere / "installed").mkdir()
    repo = build_repo({".github/workflows/w.yml": "on: push\n"})
    code = cli.main(["hygiene", str(repo)], locate_zizmor=fake_zizmor(elsewhere / "installed", "echo '[]'"))
    assert (code, marker.exists()) == (0, False), capsys.readouterr()


MODULE_DOOR = [sys.executable, "-m", "quality.cli"]
CONSOLE_SCRIPT_DOOR = [str(Path(sys.executable).parent / "quality")]


def quality_door(*arguments: str, cwd: Path, door: list[str] = MODULE_DOOR, **streams) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*door, *arguments], cwd=cwd, text=True, check=False, **streams)


@pytest.fixture
def env_and_broken_compose(build_repo) -> Path:
    return build_repo({".env": "A=1\n", "compose.yaml": "services: [\n"})


@pytest.mark.parametrize("door", [MODULE_DOOR, CONSOLE_SCRIPT_DOOR], ids=["module", "console script"])
def test_a_merged_pipe_shows_the_hygiene_lines_in_run_order(env_and_broken_compose, door):
    """#86 rule 3, cells 10 and 12a: reports, the finding with its fix, `cannot judge`, then the summary."""
    done = quality_door(
        "hygiene", ".", cwd=env_and_broken_compose, door=door, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    prefixes = ["size: ", "clone: ", "import-contract: ", "env-file .env - ", "  Fix: ", "hygiene: compose-port cannot judge - ", "root-layout .env - ", "  Fix: ", "hygiene: 2 finding(s)"]
    lines = done.stdout.splitlines()
    assert all(line.startswith(prefix) for line, prefix in zip(lines, prefixes, strict=True)), done.stdout


def test_discarding_stderr_hides_exactly_the_cannot_judge_line(env_and_broken_compose):
    """#86 rule 3, cell 11: no line changes its stream; the exit stays 2."""
    done = quality_door("hygiene", ".", cwd=env_and_broken_compose, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert (done.returncode, "compose-port cannot judge" in done.stdout, "env-file .env" in done.stdout) == (2, False, True)


SONAR_FINDINGS_SCRIPT = """
import sys
sys.path.insert(0, {repository_root!r})
from tests.support.sonar import issues_page, sonar_answering
from quality import cli
cli.run(lambda: cli.main(["sonar", "findings"], transport=sonar_answering(issues=issues_page(2, 1, 2)), sleep=lambda seconds: None))
"""


@pytest.mark.integration
def test_a_merged_pipe_lists_the_sonar_findings_before_the_red_summary(tmp_path):
    """#86 rule 3, cell 12: the findings precede the summary through `2>&1`."""
    from tests.support.sonar import PULL_REQUEST_ENVIRONMENT, TASK_FIXTURE, legacy_config_absent

    (tmp_path / "quality").mkdir()
    (tmp_path / "quality" / "config.toml").write_text(legacy_config_absent)
    (tmp_path / sonar.TASK_FILE).parent.mkdir()
    (tmp_path / sonar.TASK_FILE).write_text(TASK_FIXTURE.read_text())
    script = tmp_path / "findings.py"
    script.write_text(textwrap.dedent(SONAR_FINDINGS_SCRIPT).format(repository_root=str(Path(__file__).parents[1])))
    done = subprocess.run(
        [sys.executable, str(script)], cwd=tmp_path, text=True, check=False, env={**os.environ, **PULL_REQUEST_ENVIRONMENT},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    prefixes = ["sonar src/a1.py:1 - ", "  Fix: ", "sonar src/a2.py:2 - ", "  Fix: ", "sonar: new 2"]
    lines = done.stdout.splitlines()
    assert (done.returncode, all(line.startswith(prefix) for line, prefix in zip(lines, prefixes, strict=True))) == (1, True), done.stdout


def test_a_replaced_stdout_still_prints_the_help():
    """#86 rule 3: `main` leaves the process's streams alone, so a captured stdout works."""
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), pytest.raises(SystemExit) as stopped:
        cli.main(["--help"])
    assert (stopped.value.code, "hygiene" in captured.getvalue()) == (0, True)


@pytest.mark.integration
def test_a_closed_stdout_stream_is_cannot_judge_with_its_diagnostic(tmp_path, capsys, monkeypatch):
    """#86 rule 3: a closed stdout object never crashes `main`; the diagnostic reaches stderr."""
    monkeypatch.setenv("NO_COLOR", "1")  # Python 3.14's argparse otherwise probes the closed stdout for colour
    closed = io.TextIOWrapper(io.BytesIO())
    closed.close()
    with contextlib.redirect_stdout(closed):
        code = cli.main(["hygiene", str(tmp_path / "missing")])
    assert (code, "hygiene: cannot judge" in capsys.readouterr().err) == (2, True)


@pytest.mark.integration
def test_a_closed_stdout_is_cannot_judge_and_never_a_crash(tmp_path):
    """#86 rule 3: with fd 1 closed the door still exits 2 with its cannot-judge line."""
    done = subprocess.run(
        ["bash", "-c", '"$PY" -m quality.cli hygiene "$MISSING" >&-'],
        env={**os.environ, "PY": sys.executable, "MISSING": str(tmp_path / "missing")},
        text=True, check=False, stderr=subprocess.PIPE,
    )
    assert (done.returncode, "hygiene: cannot judge" in done.stderr) == (2, True), done.stderr


def test_an_index_record_quality_cannot_read_refuses_exactly_the_rules_that_read_index_modes(build_repo, monkeypatch):
    repo = build_repo({})
    real_run = process.run

    def unreadable_modes(command, *args, **kwargs):
        if command[-4:] == ["ls-files", "-z", "-s", "--"]:
            return subprocess.CompletedProcess(command, 0, stdout=b"100644 no-tab\0")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(process, "run", unreadable_modes)
    verdicts = check.run_hygiene(repo, installed_ruff, never_located).verdicts
    refused = [(verdict.rule, str(verdict.refusal)) for verdict in verdicts if verdict.refusal is not None]
    assert [rule for rule, _ in refused] == [
        "file-suffix", "script-name", "script-header", "test-layout", "test-import", "python-floor", "python-security", "python-print",
    ]
    assert all("git ls-files -s printed an index record quality cannot read" in reason for _, reason in refused)


def finding_of(rule: Rule) -> Finding:
    return Finding(rule, "a", None, "reason", "fix")


def area_of(*verdicts: check.RuleVerdict) -> check.AreaVerdict:
    return check.AreaVerdict(Area.FILES, verdicts)


@pytest.mark.parametrize(
    ("verdicts", "expected"),
    [
        ([check.RuleVerdict(Rule.ENV_FILE), check.RuleVerdict(Rule.COMPOSE_PORT)], check.Outcome.CLEAN),
        ([check.RuleVerdict(Rule.SIZE, measured_only=True), check.RuleVerdict(Rule.CLONE)], check.Outcome.MEASURED_ONLY),
        ([check.RuleVerdict(Rule.SIZE, measured_only=True), check.RuleVerdict(Rule.CLONE, (finding_of(Rule.CLONE),))], check.Outcome.FINDINGS),
        ([check.RuleVerdict(Rule.ENV_FILE, (finding_of(Rule.ENV_FILE),)), check.RuleVerdict(Rule.COMPOSE_PORT, refusal=Unparsable("x"))], check.Outcome.UNPARSABLE),
        ([check.RuleVerdict(Rule.ENV_FILE, refusal=Unparsable("x")), check.RuleVerdict(Rule.COMPOSE_PORT, refusal=CannotJudge("y"))], check.Outcome.CANNOT_JUDGE),
    ],
    ids=["clean", "measured only", "a finding beats measured only", "a refusal beats a finding", "the worst refusal"],
)
def test_an_area_is_as_bad_as_its_worst_rule(verdicts, expected):
    assert area_of(*verdicts).outcome is expected


def test_a_measured_only_rule_with_a_finding_is_a_finding():
    assert check.RuleVerdict(Rule.CLONE, (finding_of(Rule.CLONE),), measured_only=True).outcome is check.Outcome.FINDINGS


def test_an_area_keeps_the_findings_of_its_rules_also_when_one_of_them_refused():
    kept = finding_of(Rule.ENV_FILE)
    area = area_of(check.RuleVerdict(Rule.ENV_FILE, (kept,)), check.RuleVerdict(Rule.COMPOSE_PORT, refusal=CannotJudge("y")))
    assert area.findings == (kept,)


def test_an_area_names_each_distinct_reason_once_in_run_order():
    area = area_of(
        check.RuleVerdict(Rule.ENV_FILE, refusal=CannotJudge("second")),
        check.RuleVerdict(Rule.COMPOSE_PORT, refusal=CannotJudge("first")),
        check.RuleVerdict(Rule.CLAUDE_MD, refusal=Unparsable("second")),
    )
    assert area.reasons == ("second", "first")


@pytest.mark.parametrize(
    ("verdicts", "expected"),
    [
        ([check.RuleVerdict(Rule.SIZE, measured_only=True), check.RuleVerdict(Rule.CLONE, measured_only=True)], check.Measurement.MEASURED_ONLY),
        ([check.RuleVerdict(Rule.SIZE), check.RuleVerdict(Rule.CLONE, measured_only=True)], check.Measurement.PARTLY_MEASURED_ONLY),
        ([check.RuleVerdict(Rule.SIZE, refusal=CannotJudge("x")), check.RuleVerdict(Rule.CLONE, measured_only=True)], check.Measurement.MEASURED_ONLY),
        ([check.RuleVerdict(Rule.SIZE), check.RuleVerdict(Rule.CLONE)], check.Measurement.ENFORCED),
    ],
    ids=["all", "some", "the rules that judged", "none"],
)
def test_an_area_tells_whether_all_or_some_of_its_judging_rules_only_measured(verdicts, expected):
    assert area_of(*verdicts).measurement is expected


def test_a_run_holds_one_area_verdict_per_area_in_area_order(build_repo):
    result = check.run_hygiene(build_repo({}), installed_ruff, never_located)
    assert [verdict.area for verdict in result.areas] == list(Area)
    assert [rule for area in result.areas for rule in (v.rule for v in area.verdicts)] == sorted(Rule, key=lambda rule: list(Area).index(rule.area))
