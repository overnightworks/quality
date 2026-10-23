import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from test_emergency import FakeGh, changed_nothing, drive
from test_emergency import sonar as emergency_sonar
from test_sonar import (
    FEATURE_ENVIRONMENT,
    FINISHED_FOR_FEATURE,
    FINISHED_FOR_MAIN,
    KEY,
    LEGACY_COUNT,
    PUSH_ENVIRONMENT,
    issue,
    issues_page,
    legacy_config,
    rule_count_endpoint,
    run_cli,
    sonar_answering,
)

from quality import sonar

MESSAGE = "Use either the version tag or the digest for the image instead of both"
SET_ASIDE_LINE = (
    f"sonar Dockerfile:3 - docker:S8431: {MESSAGE} (does not count: .github/dependabot.yml exists, "
    "Sonar's own exception, which its pull-request analysis misses)\n"
)
FIX = "Fix it in the code; an accepted or false-positive mark in SonarCloud still counts"
COUNTED_LINE = f"sonar Dockerfile:3 - docker:S8431: {MESSAGE}\n  Fix: {FIX}\n"
NEW_ZERO = f"sonar: new 0 · legacy 0 ({KEY}, main)\n"
UNREADABLE = "sonar: cannot judge - cannot reach .github/dependabot.yml: [Errno 13] Permission denied\n"


def bot_issue(rule: str = "docker:S8431", path: str = "Dockerfile") -> dict:
    return {"rule": rule, "component": f"{KEY}:{path}", "line": 3, "textRange": {"startLine": 3}, "message": MESSAGE}


def write_bot_config(content: str = "version: 2\n") -> None:
    Path(".github").mkdir(exist_ok=True)
    Path(".github/dependabot.yml").write_text(content)


@pytest.fixture(autouse=True)
def checkout(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "quality").mkdir()


def findings_of(*issues: dict, legacy: int = 0, **kwargs):
    return sonar_answering(issues={"total": len(issues), "issues": list(issues)}, legacy=legacy, **kwargs)


def test_a_bot_config_sets_the_finding_aside_and_the_pull_request_is_green(monkeypatch, capsys):
    """#98 rule 1, cell 1: the finding prints as not counting, after the counted ones, and the run is clean"""
    write_bot_config()
    code, out, err, _ = run_cli(monkeypatch, capsys, findings_of(bot_issue()))
    assert (code, out, err) == (0, SET_ASIDE_LINE + NEW_ZERO, "")


@pytest.mark.parametrize(
    ("has_bot_config", "default_branch_total", "mode", "code", "legacy"),
    [
        (True, 3, "fail", 0, 0),
        (True, 5, "fail", 1, 2),
        (False, 3, "fail", 1, 3),
        (True, 5, "report", 0, 2),
    ],
    ids=["only set-aside findings", "others remain", "no bot config stays raw", "report mode"],
)
def test_a_legacy_count_sets_aside_what_the_default_branch_run_sets_aside(
    monkeypatch, capsys, has_bot_config, default_branch_total, mode, code, legacy
):
    """#166 cells 1-4: re-ruling E3 of #98, the legacy number is the default branch's total less its set-aside rule"""
    if has_bot_config:
        write_bot_config()
    fake = findings_of(legacy=default_branch_total, legacy_by_rule={"docker:S8431": 3})
    result = run_cli(monkeypatch, capsys, fake, config_text=legacy_config(mode))
    assert (result[0], result[2] or result[1]) == (code, f"sonar: new 0 · legacy {legacy} ({KEY}, main)\n")


def test_the_set_aside_rule_is_counted_on_the_default_branch_with_the_same_statuses(monkeypatch, capsys):
    """#166 cell 5"""
    write_bot_config()
    fake = findings_of(legacy=5, legacy_by_rule={"docker:S8431": 3})
    run_cli(monkeypatch, capsys, fake)
    total, set_aside = (parse_qs(urlparse(url).query) for url in fake.urls_to(LEGACY_COUNT) + fake.urls_to(rule_count_endpoint("docker:S8431")))
    assert set_aside == {**total, "rules": ["docker:S8431"]}
    assert (set_aside["branch"], set_aside["issueStatuses"]) == (["main"], total["issueStatuses"])


def test_a_rule_count_above_the_total_is_exit_2(monkeypatch, capsys):
    """#166 cell 6: a contradictory answer is never a negative or clamped number"""
    write_bot_config()
    code, out, err, _ = run_cli(monkeypatch, capsys, findings_of(legacy=2, legacy_by_rule={"docker:S8431": 3}))
    assert (code, out) == (2, "")
    assert err.startswith("sonar: cannot judge - ") and "docker:S8431" in err


@pytest.mark.parametrize("answer", [{"issues": []}, {"total": "many"}, [], None], ids=["no total", "not a number", "list", "null"])
def test_an_unreadable_rule_count_is_exit_2(monkeypatch, capsys, answer):
    """#166 cell 7: never a silent zero"""
    write_bot_config()
    fake = findings_of(legacy=5)
    fake.answers[rule_count_endpoint("docker:S8431")] = [answer]
    code, out, err, _ = run_cli(monkeypatch, capsys, fake)
    assert (code, out, err.startswith("sonar: cannot judge - ")) == (2, "", True)


def test_without_the_bot_config_no_rule_is_counted(monkeypatch, capsys):
    """#166 cell 9"""
    fake = findings_of(legacy=3)
    run_cli(monkeypatch, capsys, fake)
    assert [url for url, _ in fake.requests if "rules=" in url] == []


def only_the_yaml_spelling() -> None:
    Path(".github").mkdir()
    Path(".github/dependabot.yaml").write_text("")


@pytest.mark.parametrize("arrange", [lambda: None, only_the_yaml_spelling], ids=["no .github", "only dependabot.yaml"])
def test_without_the_bot_config_the_finding_counts_as_before(monkeypatch, capsys, arrange):
    """#98 rule 1, cell 2: no .github/dependabot.yml, so the finding fails the run unchanged"""
    arrange()
    code, out, err, _ = run_cli(monkeypatch, capsys, findings_of(bot_issue()))
    assert code == 1
    assert out == COUNTED_LINE
    assert err == f"sonar: new 1 · legacy 0 ({KEY}, main)\n"


@pytest.mark.parametrize("content", ["", "updates: [", "version: 2\n"], ids=["empty", "broken", "valid"])
def test_the_bot_config_is_never_read(monkeypatch, capsys, content):
    """#98 rule 2, cell 3: an empty or broken file counts as existing"""
    write_bot_config(content)
    code, out, _, _ = run_cli(monkeypatch, capsys, findings_of(bot_issue()))
    assert (code, out) == (0, SET_ASIDE_LINE + NEW_ZERO)


def test_only_the_dependabot_rule_is_set_aside(monkeypatch, capsys):
    """#98 rules 1 and 3, cell 4: docker:S6596 on the same line still counts"""
    write_bot_config()
    code, out, err, _ = run_cli(monkeypatch, capsys, findings_of(bot_issue(), bot_issue("docker:S6596")))
    assert code == 1
    assert out.index("docker:S6596") < out.index("docker:S8431")
    assert out.endswith(SET_ASIDE_LINE)
    assert err == f"sonar: new 1 · legacy 0 ({KEY}, main)\n"


def test_the_file_path_of_the_finding_is_no_input(monkeypatch, capsys):
    """#98 rule 1, cell 5: a Dockerfile Dependabot does not maintain is set aside too"""
    write_bot_config()
    code, out, _, _ = run_cli(monkeypatch, capsys, findings_of(bot_issue(path="deploy/Dockerfile.dev")))
    assert (code, out.startswith("sonar deploy/Dockerfile.dev:3 - docker:S8431")) == (0, True)


@pytest.mark.parametrize(
    ("environment", "task", "summary"),
    [
        (PUSH_ENVIRONMENT, FINISHED_FOR_MAIN, f"sonar: clean for '{KEY}' (branch=main): 0 open finding(s)\n"),
        (FEATURE_ENVIRONMENT, FINISHED_FOR_FEATURE, f"sonar: clean for '{KEY}' (branch=feat/a): 0 open finding(s)\n"),
    ],
    ids=["default branch", "another branch"],
)
def test_every_run_kind_sets_the_finding_aside(monkeypatch, capsys, environment, task, summary):
    """#98 E3, cell 6: the default branch and another branch judge it like a pull request"""
    write_bot_config()
    code, out, _, _ = run_cli(monkeypatch, capsys, findings_of(bot_issue(), task=task), environment)
    assert (code, out) == (0, SET_ASIDE_LINE + summary)


def test_set_aside_findings_lower_the_count_across_pages(monkeypatch, capsys):
    """#98 rule 3, cell 7: 150 issues over two pages with one set aside count 149, and both pages are read"""
    write_bot_config()
    first = {"total": 150, "issues": [bot_issue()] + [issue(number) for number in range(2, 101)]}
    second = {"total": 150, "issues": [issue(number) for number in range(101, 151)]}
    fake = sonar_answering()
    fake.answers["issues/search"] = [first, second]
    code, _, err, _ = run_cli(monkeypatch, capsys, fake)
    assert (code, err) == (1, f"sonar: new 149 · legacy 0 ({KEY}, main)\n")
    assert len(fake.urls_to("issues/search")) == 2


def test_a_lone_set_aside_finding_ends_paging_after_one_page(monkeypatch, capsys):
    """#98 rule 3, cell 8: paging stops on the raw number fetched"""
    write_bot_config()
    fake = findings_of(bot_issue())
    code, out, _, _ = run_cli(monkeypatch, capsys, fake)
    assert (code, out) == (0, SET_ASIDE_LINE + NEW_ZERO)
    assert len(fake.urls_to("issues/search")) == 1


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 path, so no permission is refused")
def test_an_unreachable_bot_config_is_exit_2_before_any_request(monkeypatch, capsys):
    """#98 rule 2, E5, cell 9: .github without search permission names the file and sends nothing"""
    write_bot_config()
    Path(".github").chmod(0)
    fake = findings_of(bot_issue())
    try:
        code, out, err, _ = run_cli(monkeypatch, capsys, fake)
    finally:
        Path(".github").chmod(0o755)
    assert (code, out, err) == (2, "", UNREADABLE)
    assert fake.requests == []


@pytest.mark.parametrize(
    ("arrange", "set_aside"),
    [
        (lambda: Path(".github/dependabot.yml").symlink_to("missing.yml"), False),
        (lambda: Path(".github/dependabot.yml").symlink_to("real.yml"), True),
        (lambda: Path(".github/dependabot.yml").mkdir(), True),
    ],
    ids=["link to nowhere is absent", "link to a file counts", "directory counts"],
)
def test_a_link_or_directory_counts_exactly_when_it_leads_to_something(monkeypatch, capsys, arrange, set_aside):
    """#98 N1, cell 9: presence is what repository.is_on_disk answers"""
    Path(".github").mkdir()
    Path(".github/real.yml").write_text("version: 2\n")
    arrange()
    code, out, _, _ = run_cli(monkeypatch, capsys, findings_of(bot_issue()))
    assert (code, "does not count" in out) == (0 if set_aside else 1, set_aside)


def test_without_the_exception_nothing_is_set_aside(monkeypatch, capsys):
    """#98 cell 11: no S8431 anywhere, so the output is today's"""
    write_bot_config()
    code, out, err, _ = run_cli(monkeypatch, capsys, sonar_answering(issues=issues_page(1, 1)))
    assert code == 1
    assert out == f"sonar src/a1.py:1 - python:S1: m\n  Fix: {FIX}\n"
    assert err == f"sonar: new 1 · legacy 0 ({KEY}, main)\n"


@pytest.mark.parametrize(("exists", "rules"), [(True, {"docker:S8431"}), (False, set())], ids=["file exists", "no file"])
def test_the_decider_answers_by_existence_alone(exists, rules):
    """#98 rule 1: the pure decider reads nothing"""
    assert sonar.uncounted_rules(exists) == rules


def test_the_emergency_door_still_counts_the_dependabot_rule(capsys):
    """#98 cell 10: the door passes no rule set, so docker:S8431 stays an added finding next to .github/dependabot.yml"""
    write_bot_config()
    answering = emergency_sonar(new=1)

    def transport(url, authorization):
        answer = answering(url, authorization)
        if "pullRequest=" in url and "issues" in answer:
            return {"total": 1, "issues": [bot_issue()]}
        return answer

    gh = FakeGh()
    code, answers = drive(gh, transport)
    assert code == 1
    assert capsys.readouterr().out == f"emergency-merge: refused - the pull request adds findings:\n{COUNTED_LINE}"
    changed_nothing(gh)
    assert answers == []
