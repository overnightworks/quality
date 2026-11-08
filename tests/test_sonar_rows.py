import sys
from pathlib import Path

import pytest

from quality import cli, sonar
from quality.report import printable
from tests.support.config_refusals import all_refusals
from tests.support.sonar import (
    CLEAN_PROPERTIES,
    FEATURE_ENVIRONMENT,
    FINISHED_FOR_FEATURE,
    FINISHED_FOR_MAIN,
    LEGACY_COUNT,
    PULL_REQUEST_ENVIRONMENT,
    PUSH_ENVIRONMENT,
    TASK_FIXTURE,
    TOKEN,
    FakeSonar,
    failed_condition,
    gate_error,
    issue,
    issues_page,
    legacy_config,
    legacy_config_absent,
    sonar_answering,
)


def ticks(*seconds: float):
    return iter(seconds).__next__


def drive(capsys, words: list[str], environment: dict[str, str], fake: FakeSonar | None = None, clock=None):
    """One run of a Sonar step through `cli.main`, with the given environment as the door's."""
    code = cli.main(
        ["sonar", *words], transport=fake or sonar_answering(), sleep=lambda seconds: None, clock=clock or ticks(5, 5),
        environment=environment,
    )
    printed = capsys.readouterr()
    assert TOKEN not in printed.out + printed.err
    return code, printed.out, printed.err


def sonar_off_config() -> str:
    return '[gate]\nmandatory_jobs = ["check"]\nsonar = false\n'


def sonar_string_config() -> str:
    return '[gate]\nmandatory_jobs = ["check"]\nsonar = "false"\n'


def without_token() -> dict[str, str]:
    return {name: value for name, value in PULL_REQUEST_ENVIRONMENT.items() if name != "SONAR_TOKEN"}


def push_pair(fact: str) -> str:
    return f"error: {fact}\nnext: push the fix; the gate judges the new commit\n"


def rerun_pair(fact: str) -> str:
    return f"error: {fact}\nnext: fix what the error names, then re-run this run's failed jobs\n"


@pytest.fixture
def properties_checkout(monkeypatch, build_repo):
    """A git checkout holding `files`, as the working directory."""

    def checkout(files: dict[str, str]) -> Path:
        root = build_repo(files)
        monkeypatch.chdir(root)
        return root

    return checkout


@pytest.fixture
def findings_checkout(monkeypatch, tmp_path):
    """A working directory with the config and this run's task file, as the findings step finds them."""

    def checkout(config_text: str | None = legacy_config_absent) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "quality").mkdir()
        if config_text is not None:
            (tmp_path / "quality" / "config.toml").write_text(config_text)
        (tmp_path / sonar.TASK_FILE).parent.mkdir()
        (tmp_path / sonar.TASK_FILE).write_text(TASK_FIXTURE.read_text())

    return checkout


@pytest.mark.parametrize(
    ("files", "row", "arguments"),
    [
        pytest.param({}, "", "", id="no bot config"),
        pytest.param(
            {".github/dependabot.yml": "version: 2\n"},
            "  · scan          excludes       docker:S8431 · .github/dependabot.yml exists\n",
            "-Dsonar.issue.ignore.multicriteria=e1 -Dsonar.issue.ignore.multicriteria.e1.ruleKey=docker:S8431"
            " -Dsonar.issue.ignore.multicriteria.e1.resourceKey=**/*",
            id="a bot config",
        ),
    ],
)
def test_clean_properties_print_their_row_and_the_closing_line(capsys, properties_checkout, elsewhere, files, row, arguments):
    """SONAR-14, SONAR-16, SONAR-100."""
    properties_checkout({"sonar-project.properties": CLEAN_PROPERTIES, **files})
    step_output = elsewhere / "github_output"
    result = drive(capsys, ["properties"], {**PULL_REQUEST_ENVIRONMENT, "GITHUB_OUTPUT": str(step_output)})
    written = step_output.read_text()
    assert (result, written) == (
        (0, f"  ✓ properties    clean          sonar-project.properties\n{row}✓ clean · <1s\n", ""),
        f"scanner-arguments={arguments}\n",
    )


def test_sonar_off_prints_its_row_and_writes_off(capsys, properties_checkout, elsewhere):
    """SONAR-66."""
    properties_checkout({"quality/config.toml": sonar_off_config()})
    step_output = elsewhere / "github_output"
    result = drive(capsys, ["properties"], {**without_token(), "GITHUB_OUTPUT": str(step_output)})
    written = step_output.read_text()
    assert (result, written) == (
        (0, "  · sonar         off            quality/config.toml sets [gate] sonar = false\n✓ off · nothing reaches SonarCloud · <1s\n", ""),
        "sonar=off\n",
    )


@pytest.mark.parametrize(
    ("files", "token", "fact"),
    [
        pytest.param(
            {"sonar-project.properties": CLEAN_PROPERTIES + "sonar.host.url=https://elsewhere.example\n"}, True,
            "sonar-project.properties sets 'sonar.host.url', which is not allowed", id="a disallowed key",
        ),
        pytest.param(
            {"sonar-project.properties": CLEAN_PROPERTIES + "sonar.exclusions=${env.HOME}\n"}, True,
            "sonar-project.properties sets 'sonar.exclusions' to a value with a ${...} substitution", id="a substitution",
        ),
        pytest.param(
            {"sonar-project.properties": "sonar.organization=overnightworks\nsonar.projectKey=someone_else\n"}, True,
            "sonar-project.properties sets 'sonar.projectKey' to 'someone_else', not 'overnightworks_quality'", id="a foreign project key",
        ),
        pytest.param(
            {"sonar-project.properties": CLEAN_PROPERTIES, ".scannerwork/report-task.txt": "ceTaskId=x\n"}, True,
            ".scannerwork/ is tracked: a committed report-task.txt would stand in for this run's analysis", id="a tracked scanner directory",
        ),
        pytest.param({"README.md": "x\n"}, True, "sonar-project.properties is missing", id="no properties file"),
        pytest.param(
            {"sonar-project.properties": CLEAN_PROPERTIES + "sonar.python.coverage.reportPaths=coverage.xml\n"}, True,
            "sonar-project.properties sets 'sonar.python.coverage.reportPaths' to 'coverage.xml', but no such report file exists"
            " (did the tests job upload it, and does the caller pass coverage-artifact?)",
            id="a missing coverage report",
        ),
        pytest.param(
            {"quality/config.toml": sonar_off_config(), "sonar-project.properties": CLEAN_PROPERTIES}, False,
            "sonar-project.properties exists while quality/config.toml sets [gate] sonar = false", id="a properties file while sonar is off",
        ),
        pytest.param(
            {"quality/config.toml": sonar_off_config()}, True,
            "SONAR_TOKEN is set while quality/config.toml sets [gate] sonar = false", id="a token while sonar is off",
        ),
    ],
)
def test_a_refused_properties_file_is_the_pair_with_the_push_step_and_writes_nothing(
    capsys, properties_checkout, elsewhere, files, token, fact
):
    """SONAR-13, SONAR-101, SONAR-105."""
    properties_checkout(files)
    step_output = elsewhere / "github_output"
    environment = PULL_REQUEST_ENVIRONMENT if token else without_token()
    result = drive(capsys, ["properties"], {**environment, "GITHUB_OUTPUT": str(step_output)})
    assert (result, step_output.exists()) == ((2, "", push_pair(fact)), False)


@pytest.mark.parametrize("step", ["properties", "findings"])
def test_an_empty_token_is_the_pair_with_its_own_step(capsys, properties_checkout, findings_checkout, elsewhere, step):
    """SONAR-61, SONAR-68."""
    if step == "properties":
        properties_checkout({"sonar-project.properties": CLEAN_PROPERTIES})
    else:
        findings_checkout()
    fake = sonar_answering()
    step_output = elsewhere / "github_output"
    result = drive(capsys, [step], {**PULL_REQUEST_ENVIRONMENT, "SONAR_TOKEN": "", "GITHUB_OUTPUT": str(step_output)}, fake)
    pair = (
        "error: SONAR_TOKEN is empty: an anonymous reader is told zero findings\n"
        "next: set the SONAR_TOKEN secret and pass it to gate.yml, then run the gate again\n"
    )
    assert (result, fake.requests, step_output.exists()) == ((2, "", pair), [], False)


@pytest.mark.parametrize(
    "refusal", [refusal for refusal in all_refusals() if "gate" in refusal.loaders and refusal.text is not None], ids=lambda refusal: refusal.id
)
def test_every_config_sonar_cannot_read_is_its_fact_and_example_with_the_push_step(capsys, findings_checkout, refusal):
    """O10, SONAR-39, SONAR-42, SONAR-102, SONAR-109, VERDICT-17."""
    findings_checkout(refusal.text)
    fake = sonar_answering()
    code, out, err = drive(capsys, ["findings"], PULL_REQUEST_ENVIRONMENT, fake)
    if refusal.example is None:
        expected = [f"error: {printable(refusal.fact)}", "next: push the fix; the gate judges the new commit"]
    else:
        example = [f"        {printable(line)}" for line in refusal.example]
        expected = [
            f"error: {printable(refusal.fact)}", "next:", "    write it in quality/config.toml like this:", *example,
            "    push the fix; the gate judges the new commit",
        ]
    assert (code, out, err.splitlines(), fake.requests) == (2, "", expected, [])


@pytest.mark.parametrize("step", ["properties", "findings"])
def test_a_config_reads_alike_in_both_steps(capsys, properties_checkout, findings_checkout, step):
    """O10, SONAR-102, VERDICT-17."""
    if step == "properties":
        properties_checkout({"quality/config.toml": sonar_string_config(), "sonar-project.properties": CLEAN_PROPERTIES})
    else:
        findings_checkout(sonar_string_config())
    pair = (
        "error: quality/config.toml [gate] sonar must be true or false\n"
        "next:\n"
        "    write it in quality/config.toml like this:\n"
        "        [gate]\n"
        "        sonar = false\n"
        "    push the fix; the gate judges the new commit\n"
    )
    assert drive(capsys, [step], PULL_REQUEST_ENVIRONMENT) == (2, "", pair)


@pytest.mark.parametrize(
    ("files", "environment"),
    [
        pytest.param({"sonar-project.properties": CLEAN_PROPERTIES}, PULL_REQUEST_ENVIRONMENT, id="sonar on"),
        pytest.param({"quality/config.toml": sonar_off_config()}, without_token(), id="sonar off"),
    ],
)
def test_an_unwritable_step_output_is_the_pair_and_claims_nothing(capsys, properties_checkout, elsewhere, files, environment):
    """SONAR-103."""
    properties_checkout(files)
    result = drive(capsys, ["properties"], {**environment, "GITHUB_OUTPUT": str(elsewhere)})
    assert result == (2, "", rerun_pair(f"GITHUB_OUTPUT is not writable · [Errno 21] Is a directory: '{elsewhere}'"))


def test_a_clean_pull_request_prints_its_analysis_new_legacy_and_gate_rows(capsys, findings_checkout):
    """SONAR-36, SONAR-107."""
    findings_checkout()
    assert drive(capsys, ["findings"], PULL_REQUEST_ENVIRONMENT, sonar_answering(legacy=17)) == (
        0,
        ("  · analysis      pull request   #7 · overnightworks_quality\n"
        "  ✓ new           none\n"
        "  · legacy        17 open        on main\n"
        "  ✓ quality gate  passed\n"
        "✓ clean · new 0 · legacy 17 · <1s\n"),
        "",
    )


def test_new_findings_stand_under_the_new_row_with_one_fix(capsys, findings_checkout):
    """SONAR-35, SONAR-36."""
    findings_checkout()
    assert drive(capsys, ["findings"], PULL_REQUEST_ENVIRONMENT, sonar_answering(issues=issues_page(2, 1, 2))) == (
        1,
        ("  · analysis      pull request   #7 · overnightworks_quality\n"
        "  ! new           2 findings\n"
        "    ! src/a1.py:1  open  python:S1: m\n"
        "    ! src/a2.py:2  open  python:S1: m\n"
        "      fix: fix it in the code; an accepted or false-positive mark in SonarCloud still counts\n"
        "  · legacy        0 open         on main\n"
        "  ✓ quality gate  passed\n"
        "! red · new 2 · legacy 0 · <1s\n"),
        "",
    )


def test_a_failed_quality_gate_names_each_condition_below_its_row(capsys, findings_checkout):
    """SONAR-6."""
    findings_checkout()
    gate = gate_error(
        failed_condition(),
        {"status": "OK", "metricKey": "new_bugs", "comparator": "GT", "actualValue": "0", "errorThreshold": "0"},
        failed_condition("new_duplicated_lines_density", "GT", "5.5", "3"),
    )
    assert drive(capsys, ["findings"], PULL_REQUEST_ENVIRONMENT, sonar_answering(gate=gate)) == (
        1,
        ("  · analysis      pull request   #7 · overnightworks_quality\n"
        "  ✓ new           none\n"
        "  · legacy        0 open         on main\n"
        "  ! quality gate  failed         2 conditions\n"
        "    ! new_coverage is 70.0, the gate needs at least 80\n"
        "    ! new_duplicated_lines_density is 5.5, the gate needs at most 3\n"
        "! red · new 0 · legacy 0 · quality gate failed · <1s\n"),
        "",
    )


@pytest.mark.parametrize(
    ("gate", "code", "rows", "closing"),
    [
        pytest.param(
            {"projectStatus": {"status": "OK", "conditions": [], "ignoredConditions": True}}, 0,
            "  ✓ quality gate  passed         coverage and duplication not judged: fewer than 20 new lines\n",
            "✓ clean · new 0 · legacy 0 · <1s\n",
            id="passed",
        ),
        pytest.param(
            gate_error(failed_condition(), ignoredConditions=True), 1,
            "  ! quality gate  failed         1 condition · coverage and duplication not judged: fewer than 20 new lines\n"
            "    ! new_coverage is 70.0, the gate needs at least 80\n",
            "! red · new 0 · legacy 0 · quality gate failed · <1s\n",
            id="failed",
        ),
    ],
)
def test_ignored_conditions_are_the_gate_rows_fact(capsys, findings_checkout, gate, code, rows, closing):
    """SONAR-12."""
    findings_checkout()
    assert drive(capsys, ["findings"], PULL_REQUEST_ENVIRONMENT, sonar_answering(gate=gate)) == (
        code,
        ("  · analysis      pull request   #7 · overnightworks_quality\n"
        "  ✓ new           none\n"
        f"  · legacy        0 open         on main\n{rows}{closing}"),
        "",
    )


@pytest.mark.parametrize(
    ("environment", "task", "out"),
    [
        pytest.param(
            PULL_REQUEST_ENVIRONMENT, None,
            "  · analysis      pull request   #7 · overnightworks_quality\n"
            "  ✓ new           none\n"
            "  · legacy        unknown        main not analysed yet\n"
            "  ✓ quality gate  passed\n"
            "✓ clean · new 0 · legacy unknown · <1s\n",
            id="pull request",
        ),
        pytest.param(
            PUSH_ENVIRONMENT, FINISHED_FOR_MAIN,
            "  · analysis      default branch main · overnightworks_quality\n"
            "  ✓ open          none\n"
            "  · legacy        unknown        main not analysed yet\n"
            "✓ clean · 0 open on main · legacy unknown · <1s\n",
            id="default branch",
        ),
    ],
)
def test_an_unanalysed_default_branch_reads_legacy_unknown(capsys, findings_checkout, environment, task, out):
    """SONAR-45, SONAR-67."""
    findings_checkout()
    fake = sonar_answering(branches=()) if task is None else sonar_answering(task=task, branches=())
    assert (drive(capsys, ["findings"], environment, fake), fake.urls_to(LEGACY_COUNT)) == ((0, out, ""), [])


def test_legacy_that_fails_the_run_is_its_needs_you_row(capsys, findings_checkout):
    """SONAR-41."""
    findings_checkout(legacy_config("fail"))
    assert drive(capsys, ["findings"], PULL_REQUEST_ENVIRONMENT, sonar_answering(legacy=1)) == (
        1,
        ("  · analysis      pull request   #7 · overnightworks_quality\n"
        "  ✓ new           none\n"
        '  ! legacy        1 open         on main · [gate] legacy = "fail"\n'
        "  ✓ quality gate  passed\n"
        "! red · new 0 · legacy 1 · <1s\n"),
        "",
    )


@pytest.mark.parametrize(
    ("issues", "code", "listing", "closing"),
    [
        pytest.param(issues_page(0), 0, "  ✓ open          none\n", "✓ clean · 0 open on main · <1s\n", id="none"),
        pytest.param(
            issues_page(1, 1), 1,
            "  ! open          1 finding\n"
            "    ! src/a1.py:1  open  python:S1: m\n"
            "      fix: fix it in the code; an accepted or false-positive mark in SonarCloud still counts\n",
            "! red · 1 open on main · <1s\n",
            id="findings",
        ),
    ],
)
def test_the_default_branch_prints_its_open_findings(capsys, findings_checkout, issues, code, listing, closing):
    """SONAR-37, SONAR-108."""
    findings_checkout()
    fake = sonar_answering(task=FINISHED_FOR_MAIN, issues=issues)
    result = drive(capsys, ["findings"], PUSH_ENVIRONMENT, fake)
    assert result == (code, f"  · analysis      default branch main · overnightworks_quality\n{listing}{closing}", "")


def test_another_branch_lists_its_findings_as_notes_and_exits_0(capsys, findings_checkout):
    """SONAR-38, SONAR-108."""
    findings_checkout()
    fake = sonar_answering(task=FINISHED_FOR_FEATURE, issues=issues_page(2, 1, 2))
    assert drive(capsys, ["findings"], FEATURE_ENVIRONMENT, fake) == (
        0,
        ("  · analysis      other branch   feat/a · overnightworks_quality\n"
        "  · open          2 findings     report only\n"
        "    · src/a1.py:1  open  python:S1: m\n"
        "    · src/a2.py:2  open  python:S1: m\n"
        "✓ report only · 2 open on feat/a · <1s\n"),
        "",
    )


def abandoned_task() -> FakeSonar:
    return sonar_answering(task={"status": "FAILED"})


def unreadable_legacy_count() -> FakeSonar:
    fake = sonar_answering()
    fake.answers[LEGACY_COUNT] = [{"issues": []}]
    return fake


def a_warning_gate() -> FakeSonar:
    return sonar_answering(gate={"projectStatus": {"status": "WARN", "conditions": []}})


@pytest.mark.parametrize(
    ("answering", "fact"),
    [
        pytest.param(abandoned_task, "SonarCloud analysis task ended FAILED", id="an abandoned task"),
        pytest.param(
            unreadable_legacy_count, "SonarCloud answered https://sonarcloud.io/api/issues/search with an unreadable body",
            id="an unreadable legacy count",
        ),
        pytest.param(a_warning_gate, "SonarCloud's quality gate is 'WARN', which is neither OK nor ERROR", id="a gate that is neither OK nor ERROR"),
    ],
)
def test_a_findings_refusal_is_the_pair_with_the_rerun_step(capsys, findings_checkout, answering, fact):
    """SONAR-7, SONAR-29, SONAR-79, SONAR-101, SONAR-105."""
    findings_checkout()
    assert drive(capsys, ["findings"], PULL_REQUEST_ENVIRONMENT, answering()) == (2, "", rerun_pair(fact))


def test_a_finding_message_prints_as_one_escaped_row(capsys, findings_checkout):
    findings_checkout()
    hostile = {**issue(1), "message": "a\nb\x1b[31m\u202ec"}
    code, out, err = drive(capsys, ["findings"], PULL_REQUEST_ENVIRONMENT, sonar_answering(issues={"total": 1, "issues": [hostile]}))
    rows = [line for line in out.splitlines() if line.startswith("    ! src/a1.py:1")]
    assert (code, rows, "\u202e" in out, "\x1b" in out, err) == (1, ["    ! src/a1.py:1  open  python:S1: a\\x0ab\\x1b[31m\\u202ec"], False, False, "")


def test_a_branch_name_prints_escaped_in_its_analysis_row_and_closing_line(capsys, findings_checkout):
    findings_checkout()
    branch = "feat/\u202ea"
    fake = sonar_answering(task={**FINISHED_FOR_FEATURE, "branch": branch}, issues=issues_page(1, 1))
    code, out, err = drive(capsys, ["findings"], {**FEATURE_ENVIRONMENT, "GITHUB_REF_NAME": branch}, fake)
    assert (code, out, err, "\u202e" in out) == (
        0,
        ("  · analysis      other branch   feat/\\u202ea · overnightworks_quality\n"
        "  · open          1 finding      report only\n"
        "    · src/a1.py:1  open  python:S1: m\n"
        "✓ report only · 1 open on feat/\\u202ea · <1s\n"),
        "",
        False,
    )


@pytest.mark.parametrize("step", ["properties", "findings"])
def test_both_steps_take_v_and_print_the_same(capsys, properties_checkout, findings_checkout, elsewhere, step):
    """SONAR-106."""
    if step == "properties":
        properties_checkout({"sonar-project.properties": CLEAN_PROPERTIES})
    else:
        findings_checkout()
    plain_output, verbose_output = elsewhere / "plain", elsewhere / "verbose"
    plain = drive(capsys, [step], {**PULL_REQUEST_ENVIRONMENT, "GITHUB_OUTPUT": str(plain_output)})
    verbose = drive(capsys, [step, "-v"], {**PULL_REQUEST_ENVIRONMENT, "GITHUB_OUTPUT": str(verbose_output)})
    assert (verbose, plain_output.exists() and plain_output.read_text()) == (plain, verbose_output.exists() and verbose_output.read_text())
    assert plain[0] == 0


def test_the_duration_runs_from_the_door_start(capsys, findings_checkout):
    findings_checkout()
    code, out, _ = drive(capsys, ["findings"], PULL_REQUEST_ENVIRONMENT, clock=ticks(5, 9))
    assert (code, out.splitlines()[-1]) == (0, "✓ clean · new 0 · legacy 0 · 4s")


def test_a_closed_stderr_moves_no_sonar_refusal_to_stdout(capsys, monkeypatch, findings_checkout):
    findings_checkout()
    monkeypatch.setattr(sys, "stderr", None)
    code, out, _ = drive(capsys, ["findings"], PULL_REQUEST_ENVIRONMENT, abandoned_task())
    assert (code, out) == (2, "")
