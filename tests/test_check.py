import subprocess
from collections import Counter
from pathlib import Path

import pytest

from quality import check, cli, config, env_file, installation, scanners
from quality.compose import is_compose_file
from quality.report import CannotJudge, Command, Example, Finding, Reason, Unparsable, printable
from quality.rules import Area, Rule
from tests.support.architecture import KEPT_CONTRACT
from tests.support.check import (
    CLEAN_VERDICT,
    compose,
    env_row_stands,
    installed_ruff,
    is_clean,
    loosen_requirement,
    note_rows,
    refusal_row,
    refused,
    rows_of,
    rule_findings,
    rule_notes,
)
from tests.support.check import hygiene_output as run
from tests.support.config_refusals import GATE, Refusal, all_refusals
from tests.support.generated_files import generated_config
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
        (lambda build_repo, tmp_path: build_repo({}), 0, CLEAN_VERDICT),
        (non_utf8_filename_repo, 0, CLEAN_VERDICT),
        (lambda build_repo, tmp_path: tmp_path, 2, "error: not inside a git work tree"),
        (lambda build_repo, tmp_path: build_repo({".env": "A=1\n"}), 1, "findings in 1 area · "),
        (lambda build_repo, tmp_path: build_repo({"\udcff.env": "A=1\n"}), 1, printable("\udcff.env")),
        (deleted_compose_repo, 0, CLEAN_VERDICT),
        (truncated_compose_repo, 2, "✗ files "),
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
    assert (code, is_clean(captured.out)) == (0, True), captured.err


BASELINE = {"quality/baseline.toml": ""}
NEW_DEBT = "def long_one():\n" + "    x = 1\n" * 61


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
    ("arrange", "expected_code", "expected_lines", "rows"),
    [
        (files({**BASELINE, ".env": "A=1\n", "src/bad.py": "def broken(:\n"}), 2,
         [f"{refusal_row('debt')}src/bad.py is not valid Python",
          f"{refusal_row('code')}ruff cannot parse src/bad.py",
          f"{refusal_row('code')}ruff cannot parse src/bad.py",
          "error: 2 of 7 areas could not check · 2 findings in the others"], [".env"]),
        (config_linked_out_of_the_repository, 2,
         [f"{refusal_row('files')}quality/config.toml resolves outside the repository",
          f"{refusal_row('debt')}quality/config.toml resolves outside the repository",
          f"{refusal_row('secrets')}quality/config.toml resolves outside the repository",
          f"{refusal_row('dependencies')}quality/config.toml resolves outside the repository",
          "error: 4 of 7 areas could not check · 1 finding, 1 of them in those areas"], [".env"]),
        (files({**BASELINE, "compose.yaml": "services: [\n", "src/a.py": NEW_DEBT}), 2,
         [f"{refusal_row('files')}cannot read compose.yaml"], ["src/a.py:1"]),
        (files({".env": "A=1\n", **KEPT_CONTRACT, **compose('- "8080:80"')}), 1,
         ["! 3 findings in 1 area · 5 areas clean · debt measured, not enforced · <1s"], [".env", "compose.yaml"]),
        (files({"compose-\udcff.yaml": compose('- "8080:80"')["compose.yaml"]}), 1, [], [printable("compose-\udcff.yaml")]),
        (files({**BASELINE, "src/\udcff.py": NEW_DEBT}), 1, [], ["src/\\udcff.py:1"]),
        (files({"src/\udcff.py": "def broken(:\n"}), 2,
         [f"{refusal_row('code')}ruff cannot parse src/\\udcff.py",
          f"{refusal_row('code')}ruff cannot parse src/\\udcff.py"], []),
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
    build_repo, elsewhere, capsys, arrange, expected_code, expected_lines, rows
):
    """#55 rules 1-4, cells 1-8 (cell 9 is the exit-code table's non-utf8 env case): each rule prints its own
    verdict, one rule's refusal hides no other rule's, the exit is the worst verdict's, the clean line names the
    rules, and every path prints backslash-escaped."""
    repo = arrange(build_repo, elsewhere)
    code = cli.main(["hygiene", str(repo)], clock=lambda: 0.0)
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert code == expected_code, output
    assert [line for line in expected_lines if line not in output] == [], output
    repo_findings = [found for rule in (Rule.ENV_FILE, Rule.COMPOSE_PORT, Rule.SIZE) for found in rule_findings(repo, rule)]
    assert [found.location for found in repo_findings] == rows
    assert [len(rows_of(output, found)) for found in repo_findings] == [1] * len(rows), output


def test_a_python_file_with_a_non_utf8_name_the_size_rule_cannot_parse_is_one_escaped_note(build_repo, capsys):
    """#290 O22: the note row stands at the escaped path, with the note's own event and fact."""
    repo = build_repo({"src/\udcff.py": "def broken(:\n"})
    _, output = run(capsys, repo)
    (note,) = rule_notes(repo, Rule.SIZE)
    assert [(row.event, row.fact) for row in note_rows(output, note.location)] == [(note.event, note.fact)], output


def test_a_run_without_flags_judges_every_rule(build_repo, capsys):
    """#228 rule 4: no scan is ever skipped, so a plain run judges every rule in run order."""
    verdicts = check.run_hygiene(build_repo({}), installed_ruff, never_located).verdicts
    assert ([verdict.rule for verdict in verdicts], [verdict.refusal for verdict in verdicts]) == (list(Rule), [None] * len(Rule))


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
    assert f"{refusal_row('workflows')}actionlint exited 3" in captured.out
    assert "workflow-security .github/workflows/w.yml:7 - zizmor unpinned-uses" in captured.out


@pytest.mark.parametrize(
    ("case", "reinstall_shown", "fragment"),
    [
        ("not installed", True, None),
        ("recorded file deleted", True, None),
        ("another version", True, f"zizmor 0.0.1 found, the release pins {installation.required_version('zizmor')}"),
        ("failing version", True, " zizmor version exited 1"),
        ("unpinned", False, str(installation.UnpinnedDependency("zizmor"))),
    ],
)
def test_a_zizmor_quality_cannot_find_or_trust_refuses_only_workflow_security(
    build_repo, served_scanners, elsewhere, monkeypatch, capsys, case, reinstall_shown, fragment
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
    refusing = [verdict.rule for verdict in check.run_hygiene(repo, installed_ruff, locators[case]).verdicts if verdict.refusal]
    assert refusing == [Rule.WORKFLOW_SECURITY]
    code = cli.main(["hygiene", str(repo)], locate_zizmor=locators[case])
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert code == 2, output
    assert refused(output, "workflows"), output
    assert env_row_stands(output)
    lines = [line.strip() for line in output.splitlines()]
    expected_once = int(reinstall_shown)
    for choice in scanners.REINSTALL.choices:
        assert (lines.count(choice.text), output.count(choice.text)) == (expected_once, expected_once), output
    assert fragment is None or fragment in output


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


def test_an_index_record_quality_cannot_read_refuses_exactly_the_rules_that_read_index_modes(build_repo, fake_git):
    repo = build_repo({})
    fake_git(r"""case " $* " in *" -s "*) printf '100644 no-tab\0'; exit 0;; esac""")
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
    assert area.reasons == (Reason("second"), Reason("first"))


def test_an_area_keeps_two_reasons_with_one_fact_apart_when_their_steps_differ():
    area = area_of(
        check.RuleVerdict(Rule.ENV_FILE, refusal=CannotJudge("same", (Command("one"),))),
        check.RuleVerdict(Rule.COMPOSE_PORT, refusal=CannotJudge("same", (Command("two"),))),
        check.RuleVerdict(Rule.CLAUDE_MD, refusal=CannotJudge("same", (Command("one"),))),
    )
    assert area.reasons == (Reason("same", (Command("one"),)), Reason("same", (Command("two"),)))


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


def test_every_rule_a_malformed_config_refuses_carries_its_fact_and_its_example_as_fields(build_repo):
    """#279 acceptance 3: the adapter turns a ConfigError into a refusal whose reason holds the error's fact and an
    Example step; the refusals of one run are all the same reason."""
    repo = build_repo({"quality/config.toml": GATE + generated_config(job="other")})
    reasons = {verdict.refusal.reason for area in check.run_hygiene(repo, installed_ruff, never_located).areas for verdict in area.verdicts if verdict.refusal}
    fact = f"{config.CONFIG_PATH} [[size.generated]] entry 1 needs job as one of [gate] mandatory_jobs, the job whose generator check proves the file"
    assert reasons == {Reason(fact, (Example(check.CONFIG_EXAMPLE_LABEL, config.GENERATED_FILE_EXAMPLE),))}


def test_a_config_refusal_without_an_example_carries_no_step(build_repo):
    """#279 acceptance 3: an error without an example becomes a refusal without a step."""
    repo = build_repo({"quality/config.toml": "[secrets]\nignore = []\n"})
    reasons = {verdict.refusal.reason for area in check.run_hygiene(repo, installed_ruff, never_located).areas for verdict in area.verdicts if verdict.refusal}
    assert reasons == {Reason(f"{config.CONFIG_PATH} has unknown key ignore in [secrets] - only allow exists")}


HYGIENE_REFUSALS = [refusal for refusal in all_refusals() if refusal.reaches_hygiene]
CONFIG_AREAS = ("files", "debt", "secrets", "dependencies")


def hygiene_over(build_repo, capsys, refusal: Refusal) -> tuple[int, list[str], list[str]]:
    repo = build_repo({".env": "A=1\n", **({} if refusal.directory else {"quality/config.toml": refusal.text})})
    if refusal.directory:
        (repo / "quality" / "config.toml").mkdir(parents=True)
    code = cli.main(["hygiene", str(repo)], clock=lambda: 0.0)
    captured = capsys.readouterr()
    return code, captured.out.splitlines(), captured.err.splitlines()


@pytest.mark.parametrize("refusal", HYGIENE_REFUSALS, ids=[refusal.id for refusal in HYGIENE_REFUSALS])
def test_hygiene_names_a_config_refusal_on_its_rows_and_shows_the_example_once_under_next(build_repo, capsys, refusal):
    """#279 O10, acceptance 4: each refused area's row ends in the fact; the example's label and lines stand once on
    stderr, four spaces deep; a refusal without one gets the general instruction and no label."""
    code, out, err = hygiene_over(build_repo, capsys, refusal)
    assert code == 2
    assert [f"{refusal_row(area)}{refusal.fact}" in out for area in CONFIG_AREAS] == [True] * 4, out
    assert env_row_stands("\n".join(out))
    label = f"next: {check.CONFIG_EXAMPLE_LABEL}:"
    if refusal.example is None:
        assert f"next: {cli.REFUSED_AREAS_NEXT.text}" in err
        assert label not in err
        return
    indented = [f"    {line}" for line in refusal.example]
    assert err.count(label) == 1
    start = err.index(label) + 1
    assert err[start:start + len(indented)] == indented
    assert not set(indented) & set(out)
    assert Counter(line for line in err if line in indented) == Counter(indented)


def test_a_path_in_conflict_counts_once_among_the_tracked_paths(build_repo):
    """O16: git lists an unmerged path once per stage; the extent counts distinct paths."""
    repo = build_repo({"README.md": "x\n", "a.txt": "x\n"})
    blob = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD:a.txt"], capture_output=True, text=True, check=True).stdout.strip()
    stages = "".join(f"100644 {blob} {stage}\ta.txt\n" for stage in (1, 2, 3))
    subprocess.run(["git", "-C", str(repo), "update-index", "--index-info"], input=f"0 {'0' * 40}\ta.txt\n{stages}", text=True, check=True)
    assert check.run_hygiene(repo, installed_ruff, cli.INSTALLED_ZIZMOR).tracked_paths == 2
