import os
import shlex
from pathlib import Path, PurePath
from urllib.parse import parse_qs, urlparse

import pytest

from quality import output, sonar_properties
from quality.config import CONFIG_PATH
from quality.gh import GhFailed
from quality.report import FindingEvent
from quality.sonar import finding_from_issue
from tests.support.merge import (
    MERGE_SHA,
    FakeGh,
    changed_nothing,
    drive,
)
from tests.support.merge import sonar as merge_sonar
from tests.support.sonar import (
    FEATURE_ENVIRONMENT,
    FINISHED_FOR_FEATURE,
    FINISHED_FOR_MAIN,
    KEY,
    LEGACY_COUNT,
    PUSH_ENVIRONMENT,
    legacy_config,
    run_cli,
    run_properties,
    sonar_answering,
)

MESSAGE = "Use either the version tag or the digest for the image instead of both"
FIX = "fix it in the code; an accepted or false-positive mark in SonarCloud still counts"
COUNTED_LINE = f"  ! new           1 finding\n    ! Dockerfile:3  open  docker:S8431: {MESSAGE}\n      fix: {FIX}\n"


def bot_issue(rule: str = sonar_properties.DEPENDABOT_EXCEPTION_RULE) -> dict:
    return {"rule": rule, "component": f"{KEY}:Dockerfile", "line": 3, "textRange": {"startLine": 3}, "message": MESSAGE}


def write_bot_config() -> None:
    Path(".github").mkdir(exist_ok=True)
    Path(".github/dependabot.yml").write_text("version: 2\n")


@pytest.fixture(autouse=True)
def checkout(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "quality").mkdir()


def findings_of(*issues: dict, legacy: int = 0, **kwargs):
    return sonar_answering(issues={"total": len(issues), "issues": list(issues)}, legacy=legacy, **kwargs)


def only_the_yaml_spelling() -> None:
    Path(".github").mkdir()
    Path(".github/dependabot.yaml").write_text("")


@pytest.mark.parametrize("arrange", [write_bot_config, lambda: None, only_the_yaml_spelling], ids=["bot config", "no .github", "only dependabot.yaml"])
def test_a_dependabot_rule_finding_counts_like_any_other(monkeypatch, capsys, arrange):
    """SONAR-23: an analysis that raises the rule's finding anyway has it fail the run, with or without the config."""
    arrange()
    code, out, err, _ = run_cli(monkeypatch, capsys, findings_of(bot_issue()))
    analysis, legacy, gate = f"  · analysis      pull request   #7 · {KEY}\n", "  · legacy        0 open         on main\n", "  ✓ quality gate  passed\n"
    assert (code, out, err) == (1, f"{analysis}{COUNTED_LINE}{legacy}{gate}! red · new 1 · legacy 0 · <1s\n", "")


@pytest.mark.parametrize(
    ("environment", "task", "code", "listing", "summary"),
    [
        (PUSH_ENVIRONMENT, FINISHED_FOR_MAIN, 1, COUNTED_LINE.replace("new ", "open"), "\n! red · 1 open on main · <1s\n"),
        (
            FEATURE_ENVIRONMENT, FINISHED_FOR_FEATURE, 0,
            f"  · open          1 finding      report only\n    · Dockerfile:3  open  docker:S8431: {MESSAGE}\n",
            "\n✓ report only · 1 open on feat/a · <1s\n",
        ),
    ],
    ids=["default branch", "another branch"],
)
def test_every_run_kind_counts_the_dependabot_rule_finding(monkeypatch, capsys, environment, task, code, listing, summary):
    """SONAR-27: the default branch and another branch list the finding as an ordinary one."""
    write_bot_config()
    result = run_cli(monkeypatch, capsys, findings_of(bot_issue(), task=task), environment)
    assert (result[0], listing in result[1], result[1].endswith(summary), result[2]) == (code, True, True, "")


@pytest.mark.parametrize("has_bot_config", [True, False], ids=["bot config", "no bot config"])
@pytest.mark.parametrize(("mode", "code", "verdict"), [("fail", 1, "! red"), ("report", 0, "✓ clean")])
def test_the_legacy_number_is_the_default_branchs_whole_total(monkeypatch, capsys, has_bot_config, mode, code, verdict):
    """SONAR-24: the fake answers the same total to every count request, so a rule-filtered count would lower the number."""
    if has_bot_config:
        write_bot_config()
    fake = findings_of(legacy=5)
    result = run_cli(monkeypatch, capsys, fake, config_text=legacy_config(mode))
    assert (result[0], result[1].splitlines()[-1], result[2]) == (code, f"{verdict} · new 0 · legacy 5 · <1s", "")


def test_no_count_request_names_a_rule(monkeypatch, capsys):
    """SONAR-24."""
    write_bot_config()
    fake = findings_of(legacy=5)
    run_cli(monkeypatch, capsys, fake)
    assert fake.urls_to(LEGACY_COUNT)
    queries = [parse_qs(urlparse(url).query) for url, _ in fake.requests]
    assert [query for query in queries if "rules" in query] == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 path, so no permission is refused")
def test_the_findings_door_never_reads_the_bot_config(monkeypatch, capsys):
    """SONAR-26."""
    write_bot_config()
    Path(".github").chmod(0)
    try:
        code, _, _, _ = run_cli(monkeypatch, capsys, findings_of())
    finally:
        Path(".github").chmod(0o755)
    assert code == 0


@pytest.mark.parametrize(
    "answer",
    [{"issues": []}, {"total": "many"}, [], None, {"total": True}, {"total": 3.9}, {"total": -1}, {"total": "3"}],
    ids=["no total", "not a number", "list", "null", "boolean", "fraction", "negative", "numeral string"],
)
def test_an_unreadable_legacy_count_is_exit_2(monkeypatch, capsys, answer):
    """SONAR-29."""
    fake = findings_of()
    fake.answers[LEGACY_COUNT] = [answer]
    code, out, err, _ = run_cli(monkeypatch, capsys, fake, config_text=legacy_config("report"))
    assert (code, out, err.startswith("error: ")) == (2, "", True)


def test_a_sonar_finding_carries_the_open_event():
    assert finding_from_issue(bot_issue()).event is FindingEvent.OPEN


def serve_config_at_merge_commit(gh: FakeGh) -> None:
    gh.files[(sonar_properties.DEPENDABOT_CONFIG, MERGE_SHA)] = "version: 2\n"


@pytest.mark.parametrize("served", [True, False], ids=["file at the merge commit", "not served"])
def test_the_merge_door_names_every_added_finding_where_the_bot_config_exists(served, capsys):
    """SONAR-25."""
    gh = FakeGh()
    if served:
        serve_config_at_merge_commit(gh)
    code, answers = drive(gh, merge_sonar(added=[bot_issue(), bot_issue("python:S1")]))
    refusal = capsys.readouterr().out
    assert code == 1
    assert (sonar_properties.DEPENDABOT_EXCEPTION_RULE in refusal, "python:S1" in refusal) == (True, True)
    changed_nothing(gh)
    assert answers == []


def test_the_merge_door_cannot_judge_when_a_config_read_fails_other_than_not_found(capsys):
    gh = FakeGh()
    gh.files[(CONFIG_PATH, MERGE_SHA)] = GhFailed("gh api failed: HTTP 500")
    code, answers = drive(gh)
    assert code == 2
    assert "not judged" in capsys.readouterr().out
    changed_nothing(gh)
    assert answers == []


def test_the_merge_door_shows_main_whole_and_reads_no_bot_config(capsys):
    """SONAR-24, SONAR-26."""
    gh = FakeGh(merge_lands=True)
    serve_config_at_merge_commit(gh)
    shown_before_asking = []
    code, _ = drive(
        gh, merge_sonar(legacy=12), when_asked=lambda: shown_before_asking.append(capsys.readouterr().out)
    )
    (shown,) = shown_before_asking
    assert code == 0
    assert "  ! sonar         12 legacy findings on main\n" in shown
    assert [call for call in gh.calls if sonar_properties.DEPENDABOT_CONFIG in " ".join(call)] == []


def put_bot_config(root: Path, name: str = "dependabot.yml", content: str = "version: 2\n") -> None:
    (root / ".github").mkdir(exist_ok=True)
    (root / ".github" / name).write_text(content)


def link_bot_config(root: Path, target: str) -> None:
    put_bot_config(root, "real.yml")
    (root / ".github" / "dependabot.yml").symlink_to(target)


def make_bot_config_a_directory(root: Path) -> None:
    (root / ".github" / "dependabot.yml").mkdir(parents=True)


ARRANGEMENTS = [
    pytest.param(put_bot_config, True, id="file"),
    pytest.param(lambda root: put_bot_config(root, content=""), True, id="empty file"),
    pytest.param(lambda root: put_bot_config(root, content="updates: ["), True, id="broken file"),
    pytest.param(make_bot_config_a_directory, True, id="directory"),
    pytest.param(lambda root: link_bot_config(root, "real.yml"), True, id="link to a file"),
    pytest.param(lambda root: link_bot_config(root, "missing.yml"), False, id="dangling link"),
    pytest.param(lambda root: put_bot_config(root, "dependabot.yaml"), False, id="only the yaml spelling"),
    pytest.param(lambda root: None, False, id="no .github"),
]


def written_arguments(step_output: Path) -> list[str]:
    """The values of every `scanner-arguments=` line the door appended."""
    prefix = f"{output.SCANNER_ARGUMENTS_OUTPUT}="
    return [line.removeprefix(prefix) for line in step_output.read_text().splitlines() if line.startswith(prefix)]


def scanner_properties(value: str) -> dict[str, str]:
    """The `-Dkey=value` arguments as the scan action splits them."""
    pairs = (argument.removeprefix("-D").partition("=") for argument in shlex.split(value))
    return {key: setting for key, _, setting in pairs}


@pytest.mark.parametrize(("arrange", "excluded"), ARRANGEMENTS)
def test_properties_hands_the_scan_one_exclusion_exactly_when_the_bot_config_exists(monkeypatch, capsys, build_repo, elsewhere, arrange, excluded):
    """SONAR-17, SONAR-18, SONAR-19, SONAR-20, SONAR-21, SONAR-28."""
    step_output = elsewhere / "output"
    code, out, _ = run_properties(monkeypatch, capsys, build_repo, arrange=arrange, step_output=step_output)
    (value,) = written_arguments(step_output)
    declared = scanner_properties(value)
    assert code == 0
    assert (sonar_properties.DEPENDABOT_EXCEPTION_RULE in out) == excluded
    if not excluded:
        assert declared == {}
        return
    (criterion,) = declared[sonar_properties.IGNORE_CRITERIA_KEY].split(",")
    pattern = declared[f"{sonar_properties.IGNORE_CRITERIA_KEY}.{criterion}.resourceKey"]
    assert declared[f"{sonar_properties.IGNORE_CRITERIA_KEY}.{criterion}.ruleKey"] == sonar_properties.DEPENDABOT_EXCEPTION_RULE
    assert all(PurePath(path).full_match(pattern) for path in ("Dockerfile", "src/probe/Dockerfile", "a/b/c/Dockerfile.dev"))
    assert len(declared) == 3


def test_every_key_properties_writes_is_a_multicriteria_exclusion_key(monkeypatch, capsys, build_repo, elsewhere):
    """SONAR-16."""
    step_output = elsewhere / "output"
    run_properties(monkeypatch, capsys, build_repo, arrange=put_bot_config, step_output=step_output)
    (value,) = written_arguments(step_output)
    assert all(key.startswith(sonar_properties.IGNORE_CRITERIA_KEY) for key in scanner_properties(value))
    assert all(argument.startswith("-Dsonar.issue.ignore.") for argument in shlex.split(value))


def test_properties_keeps_the_lines_the_output_file_already_holds(monkeypatch, capsys, build_repo, elsewhere):
    """SONAR-14."""
    step_output = elsewhere / "output"
    step_output.write_text("earlier=1\n")
    run_properties(monkeypatch, capsys, build_repo, arrange=put_bot_config, step_output=step_output)
    lines = step_output.read_text().splitlines()
    assert (lines[0], len(lines)) == ("earlier=1", 2)
    assert lines[1].startswith(f"{output.SCANNER_ARGUMENTS_OUTPUT}=")


def test_properties_outside_a_workflow_step_prints_and_exits_clean(monkeypatch, capsys, build_repo):
    """SONAR-15."""
    code, out, _ = run_properties(monkeypatch, capsys, build_repo, arrange=put_bot_config)
    assert (code, sonar_properties.DEPENDABOT_EXCEPTION_RULE in out) == (0, True)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 path, so no permission is refused")
def test_properties_with_an_unreachable_bot_config_is_exit_2_and_writes_nothing(monkeypatch, capsys, build_repo, elsewhere):
    """SONAR-22."""
    step_output = elsewhere / "output"
    step_output.write_text("earlier=1\n")

    def lock_github(root: Path) -> None:
        put_bot_config(root)
        (root / ".github").chmod(0)

    try:
        code, _, err = run_properties(monkeypatch, capsys, build_repo, arrange=lock_github, step_output=step_output)
    finally:
        Path(".github").chmod(0o755)
    assert (code, step_output.read_text()) == (2, "earlier=1\n")
    assert ".github/dependabot.yml" in err


@pytest.mark.parametrize(
    "properties",
    [
        "sonar.organization=overnightworks\nsonar.projectKey=overnightworks_quality\nsonar.issue.ignore.multicriteria.e1.ruleKey=*\n",
        "sonar.organization=overnightworks\nsonar.projectKey=someone_else\n",
    ],
    ids=["a property that widens the exclusion", "a foreign project"],
)
def test_a_refused_properties_file_is_exit_2_and_writes_nothing_even_with_a_bot_config(monkeypatch, capsys, build_repo, elsewhere, properties):
    """SONAR-13."""
    step_output = elsewhere / "output"
    step_output.write_text("earlier=1\n")
    code, _, _ = run_properties(monkeypatch, capsys, build_repo, properties, arrange=put_bot_config, step_output=step_output)
    assert (code, step_output.read_text()) == (2, "earlier=1\n")


@pytest.mark.parametrize("arrange", [put_bot_config, lambda root: None], ids=["with a bot config", "without a bot config"])
def test_properties_with_an_unwritable_step_output_is_exit_2_and_claims_no_success(monkeypatch, capsys, build_repo, elsewhere, arrange):
    code, out, err = run_properties(monkeypatch, capsys, build_repo, arrange=arrange, step_output=elsewhere)
    assert (code, out) == (2, "")
    assert output.STEP_OUTPUT_VARIABLE in err
