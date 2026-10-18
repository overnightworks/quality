import json
from datetime import UTC, datetime

import pytest

from quality import cli
from quality.config import CONFIG_PATH
from quality.emergency import GhFailed
from quality.hygiene import CannotJudge
from quality.ratchet import BASELINE_PATH
from quality.sonar import SonarHttpError, basic_authorization

REPO = "acme/widgets"
SHA = "a" * 40
BASE_SHA = "b" * 40
MERGE_SHA = "d" * 40
GATE_CONFIG = '[gate]\nmandatory_jobs = ["checks"]\nlegacy = "fail"\n'
REPORT_CONFIG = GATE_CONFIG.replace("fail", "report")
LEGACY_RED = {"quality / sonar": "failure", "quality / verdict": "failure"}
FIXED_NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
BASELINE_82 = '[function_lines."src/quality/cli.py"]\n"main" = 82\n'
BASELINE_90 = BASELINE_82.replace("82", "90")


class FakeGh:
    """Answers the gh calls the command makes and records every one; `fail_on` names a call to break."""

    def __init__(
        self, admin=True, checks=None, statuses=None, state="open", fail_on=(), interrupt_on=(), merge_lands=False,
        head_config=GATE_CONFIG, base_config=GATE_CONFIG, merge_config=None, merge_sha=MERGE_SHA,
        base_baseline=None, merge_baseline=None,
    ):
        self.admin, self.state, self.fail_on, self.merge_lands = admin, state, set(fail_on), merge_lands
        self.interrupt_on = list(interrupt_on)
        self.merge_sha = merge_sha
        self.files = {
            (CONFIG_PATH, SHA): head_config,
            (CONFIG_PATH, BASE_SHA): base_config,
            (CONFIG_PATH, MERGE_SHA): head_config if merge_config is None else merge_config,
            (BASELINE_PATH, BASE_SHA): base_baseline,
            (BASELINE_PATH, MERGE_SHA): merge_baseline,
        }
        self.checks = {"checks": "success", **(LEGACY_RED if checks is None else checks)}
        self.statuses = statuses or {}
        self.merged = False
        self.calls: list[tuple[str, ...]] = []
        self.sequence: list[str] = []
        self.gate_values: list[str] = []
        self.comments: list[str] = []

    def __call__(self, arguments, stdin=None):
        self.calls.append(tuple(arguments))
        step = self.step_of(arguments)
        if step == "merge" and self.merge_lands:
            self.merged = True
        if step in self.interrupt_on:
            raise KeyboardInterrupt
        if step in self.fail_on:
            raise GhFailed(f"{step} broke")
        return self.answer(step, arguments, stdin)

    @staticmethod
    def step_of(arguments):
        text = " ".join(arguments)
        for marker, step in (
            ("check-runs", "checks"), ("/status", "statuses"), ("contents/", "config"), ("/pulls/", "pull"),
            ("properties/values", "gate"), ("pr merge", "merge"), ("/comments", "comment"), ("--jq .login", "login"),
        ):
            if marker in text:
                return step
        return "repository"

    @staticmethod
    def file_of(arguments):
        """The (path, sha) a contents call asks for; a file the fake does not hold answers 404."""
        path, _, sha = arguments[-1].partition("/contents/")[2].partition("?ref=")
        return path, sha

    def answer(self, step, arguments, stdin):
        if step == "pull":
            return json.dumps({"state": self.state, "merged": self.merged, "head": {"sha": SHA}, "base": {"sha": BASE_SHA}, "merge_commit_sha": self.merge_sha})
        if step == "checks":
            return "\n".join(
                json.dumps({"name": name, "status": "in_progress" if result is None else "completed", "conclusion": result})
                for name, result in self.checks.items()
            )
        if step == "statuses":
            return "\n".join(json.dumps({"context": context, "state": state}) for context, state in self.statuses.items())
        if step == "config":
            content = self.files.get(self.file_of(arguments))
            if content is None:
                raise GhFailed("gh api failed: HTTP 404")
            return content
        if step == "repository":
            return json.dumps({"default_branch": "main", "permissions": {"admin": self.admin}})
        if step == "login":
            return "felix\n"
        if step == "merge":
            self.merged = True
            self.sequence.append("merge")
        if step == "gate":
            self.gate_values.append(json.loads(stdin)["properties"][0]["value"])
            self.sequence.append(f"gate {self.gate_values[-1]}")
        if step == "comment":
            self.comments.append(next(a for a in arguments if a.startswith("body=")))
            self.sequence.append("comment")
        return ""

    @property
    def merge_calls(self):
        return [call for call in self.calls if call[:2] == ("pr", "merge")]


def sonar(new=0, legacy=4, main_analysed=True, analysed=SHA, unreachable=False):
    def transport(url, authorization):
        if unreachable:
            raise CannotJudge("SonarCloud is down")
        if "project_branches" in url:
            return {"branches": [{"name": "main", "isMain": True, **({"analysisDate": "x"} if main_analysed else {})}]}
        if "project_pull_requests" in url:
            return {"pullRequests": [{"key": "6", "commit": {"sha": "0" * 40}}, {"key": "7", "commit": {"sha": analysed}}]}
        if "measures" in url:
            return {}
        if "pullRequest=" in url:
            issues = [{"component": "k:a.py", "line": 3, "rule": "s1", "message": "bad"}][:new]
            return {"total": new, "issues": issues}
        return {"total": legacy, "issues": []}

    return transport


def recording(transport):
    sent = []

    def record(url, authorization):
        sent.append(authorization)
        return transport(url, authorization)

    record.sent = sent
    return record


def refusing_anonymous_read(status):
    def transport(url, authorization):
        if authorization is None:
            raise SonarHttpError(status, url)
        return sonar()(url, authorization)

    return transport


def drive(gh, transport=None, typed="ja", terminal=True, repo=REPO):
    answers = []

    def ask(prompt):
        answers.append(prompt)
        if isinstance(typed, BaseException):
            raise typed
        return typed

    code = cli.main(
        ["emergency-merge", repo, "7"], gh=gh, transport=transport or sonar(), ask=ask, is_terminal=lambda: terminal
    )
    return code, answers


def changed_nothing(gh):
    assert gh.gate_values == []
    assert gh.merge_calls == []


@pytest.mark.parametrize("unset", [True, False], ids=["unset", "empty"])
def test_an_empty_token_reads_sonar_anonymously(unset, monkeypatch):
    """#73 rule 1: no token means the transport is asked for no Authorization at all."""
    if unset:
        monkeypatch.delenv("SONAR_TOKEN", raising=False)
    else:
        monkeypatch.setenv("SONAR_TOKEN", "")
    transport = recording(sonar())
    assert drive(FakeGh(), transport)[0] == 0
    assert transport.sent
    assert set(transport.sent) == {None}


def test_a_set_token_still_sends_basic_authorization(monkeypatch, capsys):
    """#73 rule 5: a set token is sent as Basic auth and never printed."""
    monkeypatch.setenv("SONAR_TOKEN", "s3cret-token")
    transport = recording(sonar())
    assert drive(FakeGh(), transport)[0] == 0
    assert set(transport.sent) == {basic_authorization("s3cret-token")}
    output = capsys.readouterr().out
    assert "s3cret-token" not in output
    assert basic_authorization("s3cret-token").split()[1] not in output


@pytest.mark.parametrize("status", [401, 403, 404])
def test_a_refused_anonymous_read_is_cannot_judge_naming_the_token(status, monkeypatch, capsys):
    """#73 rule 2: an anonymous refusal names the missing SONAR_TOKEN and changes nothing."""
    monkeypatch.setenv("SONAR_TOKEN", "")
    gh = FakeGh()
    assert drive(gh, refusing_anonymous_read(status))[0] == 2
    out = capsys.readouterr().out
    assert f"did not resolve acme_widgets in pullRequest=7 anonymously (HTTP {status}); a private project needs SONAR_TOKEN" in out
    changed_nothing(gh)


def test_the_token_never_appears_when_sonar_refuses_it(monkeypatch, capsys):
    """#73 rule 2, rule 3: a rejected token is said so, and a refused credentialed read does not echo the token or its base64 form."""
    monkeypatch.setenv("SONAR_TOKEN", "s3cret-token")

    def rejecting(url, authorization):
        raise SonarHttpError(401, url)

    gh = FakeGh()
    assert drive(gh, rejecting)[0] == 2
    output = capsys.readouterr().out
    assert "rejected the token" in output
    assert "s3cret-token" not in output
    assert basic_authorization("s3cret-token").split()[1] not in output
    changed_nothing(gh)


def test_gate_goes_off_then_merge_then_on_then_log(capsys):
    """#37 rule 4, #73 rule 4: off, merge, on, in this order, and the closing line names the release that works."""
    gh = FakeGh()
    assert drive(gh)[0] == 0
    assert gh.sequence == ["gate false", "merge", "gate true", "comment"]
    assert gh.merge_calls == [("pr", "merge", "7", "--repo", REPO, "--merge", "--match-head-commit", SHA)]
    assert 'aco release <n> --abandoned "landed as PR #7 by emergency-merge"' in capsys.readouterr().out


def test_logs_who_and_the_legacy_count_on_the_pull_request():
    """#37 rule 5: the comment names who, the legacy count and the sequence."""
    gh = FakeGh()
    drive(gh)
    assert "@felix" in gh.comments[0]
    assert "4 legacy" in gh.comments[0]
    assert "off → merge → on" in gh.comments[0]


def test_shows_repository_head_and_legacy_count_before_asking(capsys):
    """#37 rule 3: it shows, then asks."""
    drive(FakeGh())
    assert f"{REPO} #7 head {SHA}: 4 legacy" in capsys.readouterr().out


@pytest.mark.parametrize("typed", ["", "yes", "JA", "n", KeyboardInterrupt(), EOFError()], ids=repr)
def test_anything_but_ja_changes_nothing(typed, capsys):
    """#37 rule 3: anything but the typed word changes nothing, Ctrl-C and Ctrl-D included."""
    gh = FakeGh()
    code, _ = drive(gh, typed=typed)
    assert code == 1
    changed_nothing(gh)
    assert "not confirmed, nothing was changed" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("gh_kwargs", "sonar_kwargs", "message"),
    [
        ({}, {"new": 1}, "adds findings"),
        ({"checks": {**LEGACY_RED, "tests": "failure"}}, {}, "tests (failure)"),
        ({"checks": {**LEGACY_RED, "tests": "skipped"}}, {}, "tests (skipped)"),
        ({"checks": {**LEGACY_RED, "tests": None}}, {}, "still running: tests"),
        ({"statuses": {"external-ci": "failure"}}, {}, "external-ci (failure)"),
        ({"statuses": {"external-ci": "pending"}}, {}, "still running: external-ci"),
        ({"checks": {"quality / sonar": "success", "quality / verdict": "success"}}, {}, "no check is red"),
        ({"checks": {"quality / sonar": "success", "quality / verdict": "failure"}}, {}, "quality / sonar must have failed"),
        ({"checks": {"quality / sonar": "timed_out", "quality / verdict": "failure"}}, {}, "quality / sonar must have failed"),
        ({"checks": {"quality / sonar": "failure"}}, {}, "quality / verdict must have failed"),
        ({"head_config": GATE_CONFIG.replace("fail", "report")}, {}, "loosened: legacy fail"),
        ({"head_config": REPORT_CONFIG, "base_config": REPORT_CONFIG}, {}, "legacy = report at the pull request's current merge commit"),
        ({"head_config": GATE_CONFIG, "base_config": REPORT_CONFIG, "merge_config": REPORT_CONFIG}, {}, "legacy = report at the pull request's current merge commit"),
        ({"head_config": REPORT_CONFIG, "base_config": REPORT_CONFIG}, {"unreachable": True}, "legacy = report"),
        ({"head_config": None, "base_config": None}, {}, "legacy = report at the pull request's current merge commit"),
        ({"head_config": REPORT_CONFIG, "base_config": REPORT_CONFIG}, {"new": 1}, "adds findings"),
        ({"head_config": '[gate]\nmandatory_jobs = ["other"]\nlegacy = "fail"\n'}, {}, 'mandatory job "checks" removed'),
        ({"state": "closed"}, {}, "pull request is closed"),
        ({}, {"legacy": 0}, "not legacy findings"),
        ({}, {"main_analysed": False}, "legacy unknown"),
        ({}, {"analysed": "c" * 40}, f"Sonar analysed {'c' * 40}, head is {SHA}"),
    ],
    ids=[
        "new finding", "other red check", "other skipped check", "pending check", "failing status", "pending status",
        "nothing red", "sonar green", "sonar timed out", "verdict green", "loosened legacy", "report on both sides", "report and Sonar unreachable", "head fail but merge commit report", "no config at either sha", "report mode and a new finding", "removed mandatory job",
        "closed", "no legacy", "legacy unknown", "stale analysis",
    ],
)
def test_refuses_unless_the_door_is_the_only_way(gh_kwargs, sonar_kwargs, message, capsys):
    """#37 rule 2: each refusal of the door."""
    gh = FakeGh(**gh_kwargs)
    code, answers = drive(gh, sonar(**sonar_kwargs))
    assert code == 1
    assert message in capsys.readouterr().out
    changed_nothing(gh)
    assert answers == []


def test_cannot_judge_when_sonar_is_unreachable_under_legacy_fail(capsys):
    """#37 rule 2."""
    gh = FakeGh()
    code, _ = drive(gh, sonar(unreachable=True))
    assert code == 2
    assert "cannot judge" in capsys.readouterr().out
    changed_nothing(gh)


def test_cannot_judge_without_the_merge_commit_the_jobs_ran_on(capsys):
    """#37 rule 2: the config the jobs ran on is unknown, so exit 2 and nothing changed."""
    gh = FakeGh(merge_sha=None)
    code, answers = drive(gh, sonar())
    assert code == 2
    assert "cannot judge" in capsys.readouterr().out
    changed_nothing(gh)
    assert answers == []


def test_names_the_finding_a_pull_request_adds(capsys):
    """#37 rule 2: a pull request that adds a finding is refused, naming it."""
    drive(FakeGh(), sonar(new=1))
    assert "a.py:3" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("gh_kwargs", "terminal", "repo", "code"),
    [({}, False, REPO, 1), ({}, True, "not-a-repo", 1), ({"admin": False}, True, REPO, 2), ({"fail_on": ["pull"]}, True, REPO, 2)],
    ids=["no terminal", "bad name", "missing admin permission", "gh down"],
)
def test_changes_nothing_when_it_cannot_start(gh_kwargs, terminal, repo, code):
    """#37 rule 1: operator only; the right and gh checked first, nothing changed."""
    gh = FakeGh(**gh_kwargs)
    assert drive(gh, terminal=terminal, repo=repo)[0] == code
    changed_nothing(gh)


def test_names_the_missing_right(capsys):
    """#37 rule 1: the missing right is named."""
    drive(FakeGh(admin=False))
    assert f"no admin permission on {REPO}" in capsys.readouterr().out


def test_gate_is_back_on_after_a_failed_merge(capsys):
    """#37 rule 4: always back on after a merge that GitHub did not do."""
    gh = FakeGh(fail_on=["merge"])
    assert drive(gh)[0] == 1
    assert gh.gate_values == ["false", "true"]
    assert "not merged" in capsys.readouterr().out
    assert "merge failed" in gh.comments[0]


def test_a_merge_that_landed_despite_a_lost_answer_counts_as_merged():
    """#37 rule 4: the outcome is what GitHub holds, not what the lost answer implied."""
    gh = FakeGh(fail_on=["merge"], merge_lands=True)
    assert drive(gh)[0] == 0
    assert gh.gate_values == ["false", "true"]


def test_gate_is_back_on_after_ctrl_c_and_the_merge_state_is_unknown(capsys):
    """#37 rule 4 and exit 2: Ctrl-C during the merge restores the gate and says it cannot tell."""
    gh = FakeGh(interrupt_on=["merge"])
    assert drive(gh)[0] == 2
    assert gh.gate_values == ["false", "true"]
    assert "merge state unknown" in capsys.readouterr().out
    assert "merge state unknown" in gh.comments[0]


def test_ctrl_c_during_a_merge_that_landed_is_reported_merged():
    """#37 rule 4: GitHub's own answer settles an interrupted merge."""
    gh = FakeGh(interrupt_on=["merge"], merge_lands=True)
    assert drive(gh)[0] == 0


def test_gate_is_back_on_when_switching_off_broke_halfway():
    """#37 rule 4: a half-done switch off is switched on again, and nothing is merged."""
    gh = FakeGh()
    original = gh.__call__

    def flaky(arguments, stdin=None):
        if "properties/values" in " ".join(arguments) and not gh.gate_values:
            gh.gate_values.append("false")
            raise GhFailed("first switch broke")
        return original(arguments, stdin)

    assert drive(flaky)[0] == 1
    assert gh.gate_values == ["false", "true"]
    assert gh.merge_calls == []


@pytest.mark.parametrize("restore_failure", [GhFailed("denied"), KeyboardInterrupt()], ids=["gh failed", "Ctrl-C"])
def test_exit_2_loud_line_and_log_when_the_gate_cannot_be_restored(restore_failure, capsys):
    """#37 rule 4: a restore that fails is exit 2 with one loud line, and the PR still gets its log."""
    gh = FakeGh()
    original = gh.__call__

    def cannot_restore(arguments, stdin=None):
        if stdin and json.loads(stdin)["properties"][0]["value"] == "true":
            raise restore_failure
        return original(arguments, stdin)

    assert drive(cannot_restore)[0] == 2
    assert f"quality_gate MAY STILL BE OFF for {REPO}" in capsys.readouterr().out
    assert "NOT restored" in gh.comments[0]


@pytest.mark.parametrize(
    ("gh_kwargs", "read_back_fails", "expected"),
    [
        ({"merge_lands": True}, False, 'aco release <n> --abandoned "landed as PR #7 by emergency-merge"'),
        ({"fail_on": ["merge"]}, True, ") - check #7 on GitHub"),
        ({"fail_on": ["merge"]}, False, "not merged"),
    ],
    ids=["merged", "unknown", "not merged"],
)
def test_names_the_merge_outcome_when_the_gate_cannot_be_restored(gh_kwargs, read_back_fails, expected, capsys):
    """#37 rule 4: the operator learns both manual checks, the gate and the pull request."""
    gh = FakeGh(**gh_kwargs)
    original = gh.__call__

    def cannot_restore(arguments, stdin=None):
        if stdin and json.loads(stdin)["properties"][0]["value"] == "true":
            raise GhFailed("denied")
        if read_back_fails and gh.merge_calls and FakeGh.step_of(arguments) == "pull":
            raise GhFailed("pull broke")
        return original(arguments, stdin)

    assert drive(cannot_restore)[0] == 2
    output = capsys.readouterr().out
    assert "MAY STILL BE OFF" in output
    assert expected in output


def test_ctrl_c_while_reading_back_the_merge_state_is_exit_2_with_the_gate_on(capsys):
    """#37 rule 4: an interruption after the restore is still an outcome line and exit 2, not a traceback."""
    gh = FakeGh(fail_on=["merge"])
    original = gh.__call__

    def interrupted_reading_back(arguments, stdin=None):
        if gh.merge_calls and FakeGh.step_of(arguments) == "pull":
            raise KeyboardInterrupt
        return original(arguments, stdin)

    assert drive(interrupted_reading_back)[0] == 2
    assert gh.gate_values == ["false", "true"]
    assert "merge state unknown" in capsys.readouterr().out


@pytest.mark.parametrize("restore_fails", [False, True], ids=["gate on", "gate not restored"])
def test_ctrl_c_while_posting_the_log_comment_is_reported(restore_fails, capsys):
    """#37 rule 4: a Ctrl-C on the log comment keeps the exit code and, when the gate is off, the loud line."""
    gh = FakeGh(interrupt_on=["comment"])
    original = gh.__call__

    def cannot_restore(arguments, stdin=None):
        if restore_fails and stdin and json.loads(stdin)["properties"][0]["value"] == "true":
            raise GhFailed("denied")
        return original(arguments, stdin)

    assert drive(cannot_restore)[0] == (2 if restore_fails else 0)
    output = capsys.readouterr().out
    assert "log comment on #7 was not posted" in output
    assert ("MAY STILL BE OFF" in output) is restore_fails


@pytest.mark.parametrize(
    ("base_baseline", "merge_baseline", "code", "message"),
    [
        (BASELINE_82, BASELINE_90, 1, "loosened: size baseline raises function_lines src/quality/cli.py main 82 -> 90"),
        (BASELINE_82, BASELINE_82, 0, "merged."),
        (BASELINE_82, None, 1, "loosened: quality/baseline.toml removed"),
        (None, None, 0, "merged."),
    ],
    ids=["cell 20 raised", "cell 21 equal", "cell 22 removed at the merge commit", "cell 23 absent at both"],
)
def test_the_door_refuses_a_baseline_loosening_next_to_legacy_red(base_baseline, merge_baseline, code, message, capsys):
    """#48 rule 6, cells 20-23: the door judges the baseline at the merge commit against base, as the verdict does."""
    gh = FakeGh(base_baseline=base_baseline, merge_baseline=merge_baseline)
    assert drive(gh)[0] == code
    assert message in capsys.readouterr().out
    assert (gh.merge_calls == []) == (code == 1)


def test_a_malformed_config_at_the_merge_commit_names_the_commit(capsys):
    """#48 rule 4: the door names the side of a malformed config.toml, as the verdict does, and changes nothing."""
    gh = FakeGh(merge_config="[gate")
    assert drive(gh)[0] == 2
    assert f"{CONFIG_PATH} at {MERGE_SHA}: {CONFIG_PATH} is not valid TOML" in capsys.readouterr().out
    changed_nothing(gh)


def test_a_malformed_baseline_at_the_merge_commit_cannot_be_judged(capsys):
    """#48 rule 4: the door names the commit whose baseline it cannot read, and changes nothing."""
    gh = FakeGh(base_baseline=BASELINE_82, merge_baseline="[ccn")
    assert drive(gh)[0] == 2
    assert f"{BASELINE_PATH} at {MERGE_SHA}" in capsys.readouterr().out
    changed_nothing(gh)
