import json
from datetime import UTC, datetime

import pytest

from quality import cli
from quality.emergency import GhFailed

REPO = "acme/widgets"
SHA = "a" * 40
LEGACY_RED = {"quality / sonar": "failure", "quality / verdict": "failure"}
FIXED_NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


class FakeGh:
    """Answers the gh calls the command makes and records every one; `fail_on` names a call to break."""

    def __init__(self, scopes="repo, admin:org", checks=None, state="open", fail_on=(), interrupt_on=None):
        self.scopes, self.state, self.fail_on, self.interrupt_on = scopes, state, set(fail_on), interrupt_on
        self.checks = {"checks": "success", **(LEGACY_RED if checks is None else checks)}
        self.calls: list[tuple[str, ...]] = []
        self.gate_values: list[str] = []
        self.comments: list[str] = []

    def __call__(self, arguments, stdin=None):
        self.calls.append(tuple(arguments))
        step = self.step_of(arguments)
        if step == self.interrupt_on:
            raise KeyboardInterrupt
        if step in self.fail_on:
            raise GhFailed(f"{step} broke")
        return self.answer(step, arguments, stdin)

    @staticmethod
    def step_of(arguments):
        text = " ".join(arguments)
        for marker, step in (
            ("--include user", "scope"), ("check-runs", "checks"), ("/pulls/", "pull"), ("properties/values", "gate"),
            ("pr merge", "merge"), ("/comments", "comment"), ("--jq .login", "login"),
        ):
            if marker in text:
                return step
        return "repository"

    def answer(self, step, arguments, stdin):
        if step == "scope":
            return f"HTTP/2 200\nX-Oauth-Scopes: {self.scopes}\n\n{{}}"
        if step == "pull":
            return json.dumps({"state": self.state, "head": {"sha": SHA}})
        if step == "checks":
            return "\n".join(
                json.dumps({"name": name, "status": "in_progress" if result is None else "completed", "conclusion": result})
                for name, result in self.checks.items()
            )
        if step == "repository":
            return json.dumps({"default_branch": "main"})
        if step == "login":
            return "felix\n"
        if step == "gate":
            self.gate_values.append(json.loads(stdin)["properties"][0]["value"])
        if step == "comment":
            self.comments.append(next(a for a in arguments if a.startswith("body=")))
        return ""

    @property
    def merge_calls(self):
        return [call for call in self.calls if call[:2] == ("pr", "merge")]


def sonar(new=0, legacy=4, main_analysed=True):
    def transport(url, authorization):
        if "project_branches" in url:
            return {"branches": [{"name": "main", "isMain": True, **({"analysisDate": "x"} if main_analysed else {})}]}
        if "measures" in url:
            return {}
        if "pullRequest=" in url:
            issues = [{"component": "k:a.py", "line": 3, "rule": "s1", "message": "bad"}][:new]
            return {"total": new, "issues": issues}
        return {"total": legacy, "issues": []}

    return transport


def drive(gh, transport=None, typed="ja", terminal=True, repo=REPO):
    answers = []

    def ask(prompt):
        answers.append(prompt)
        return typed

    code = cli.main(
        ["emergency-merge", repo, "7"], gh=gh, transport=transport or sonar(), ask=ask, is_terminal=lambda: terminal
    )
    return code, answers


def test_merges_between_gate_off_and_on_and_logs(capsys):
    gh = FakeGh()
    code, _ = drive(gh)
    out = capsys.readouterr().out
    assert code == 0
    assert gh.gate_values == ["false", "true"]
    assert gh.merge_calls == [("pr", "merge", "7", "--repo", REPO, "--merge", "--match-head-commit", SHA)]
    assert gh.comments and "@felix" in gh.comments[0] and "4 legacy" in gh.comments[0]
    assert "off → merge → on" in gh.comments[0]
    assert f"head {SHA}: 4 legacy" in out and "aco release <n> --merged 7" in out


@pytest.mark.parametrize("typed", ["", "yes", "JA", "n"])
def test_anything_but_ja_changes_nothing(typed):
    gh = FakeGh()
    code, answers = drive(gh, typed=typed)
    assert (code, gh.gate_values, gh.merge_calls, len(answers)) == (1, [], [], 1)


@pytest.mark.parametrize(
    ("gh", "transport", "message"),
    [
        (FakeGh(), sonar(new=1), "adds findings"),
        (FakeGh(checks={**LEGACY_RED, "tests": "failure"}), None, "tests (failure)"),
        (FakeGh(checks={**LEGACY_RED, "tests": None}), None, "still running: tests"),
        (FakeGh(checks={"quality / sonar": "success", "quality / verdict": "success"}), None, "no check is red"),
        (FakeGh(state="closed"), None, "pull request is closed"),
        (FakeGh(), sonar(legacy=0), "not legacy findings"),
        (FakeGh(), sonar(main_analysed=False), "legacy unknown"),
    ],
    ids=["new finding", "other red check", "pending check", "nothing red", "closed", "no legacy", "legacy unknown"],
)
def test_refuses_unless_the_door_is_the_only_way(gh, transport, message, capsys):
    code, answers = drive(gh, transport)
    assert code == 1 and message in capsys.readouterr().out
    assert (gh.gate_values, gh.merge_calls, answers) == ([], [], [])


def test_names_the_finding_a_pull_request_adds(capsys):
    drive(FakeGh(), sonar(new=1))
    assert "a.py:3" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("kwargs", "terminal", "repo", "code"),
    [({}, False, REPO, 1), ({}, True, "not-a-repo", 1), ({"scopes": "repo"}, True, REPO, 2), ({"fail_on": ["pull"]}, True, REPO, 2)],
    ids=["no terminal", "bad name", "missing admin:org", "gh down"],
)
def test_changes_nothing_when_it_cannot_start(kwargs, terminal, repo, code):
    gh = FakeGh(**kwargs)
    assert drive(gh, terminal=terminal, repo=repo)[0] == code
    assert (gh.gate_values, gh.merge_calls) == ([], [])


def test_names_the_missing_right(capsys):
    drive(FakeGh(scopes="repo"))
    assert "admin:org" in capsys.readouterr().out


def test_gate_is_back_on_after_a_failed_merge(capsys):
    gh = FakeGh(fail_on=["merge"])
    code, _ = drive(gh)
    assert (code, gh.gate_values) == (1, ["false", "true"])
    assert "not merged" in capsys.readouterr().out and "failed" in gh.comments[0]


def test_gate_is_back_on_after_ctrl_c():
    gh = FakeGh(interrupt_on="merge")
    code, _ = drive(gh)
    assert (code, gh.gate_values) == (1, ["false", "true"])


def test_gate_is_back_on_when_switching_off_broke_halfway():
    gh = FakeGh()
    original = gh.__call__
    attempts = []

    def flaky(arguments, stdin=None):
        if "properties/values" in " ".join(arguments) and not attempts:
            attempts.append(1)
            raise GhFailed("first switch broke")
        return original(arguments, stdin)

    code, _ = drive(flaky)
    assert code == 1 and gh.gate_values == ["true"] and gh.merge_calls == []


def test_exit_2_and_loud_line_when_the_gate_cannot_be_restored(capsys):
    gh = FakeGh()
    original = gh.__call__

    def cannot_restore(arguments, stdin=None):
        if stdin and json.loads(stdin)["properties"][0]["value"] == "true":
            raise GhFailed("denied")
        return original(arguments, stdin)

    code, _ = drive(cannot_restore)
    assert code == 2
    assert f"quality_gate MAY STILL BE OFF for {REPO}" in capsys.readouterr().out
