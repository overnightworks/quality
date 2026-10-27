import json
from datetime import UTC, datetime
from urllib.parse import quote

import pytest

from quality import cli
from quality.config import CONFIG_PATH
from quality.emergency import GhFailed
from quality.ratchet import BASELINE_PATH
from quality.sonar import SonarHttpError, basic_authorization
from tests.support.advisories import allowance_config
from tests.support.emergency import (
    ALL_METHODS_ALLOWED,
    GATE_CONFIG,
    LEGACY_RED,
    MERGE_SHA,
    ONE_JOB,
    REPO,
    SHA,
    SHOWN,
    SONAR_GREEN,
    FakeGh,
    changed_nothing,
    declaring,
    drive,
    sonar,
)
from tests.support.git_repositories import git_pull
from tests.support.hygiene import assert_every_line_starts_with_the_door

REPORT_CONFIG = GATE_CONFIG.replace("fail", "report")
LINEAR_HISTORY = {"type": "required_linear_history"}
UNRELATED_RULE = {"type": "creation"}
NO_USABLE_METHOD = "emergency-merge: not merged - the repository and its branch rules allow no merge method the door uses (a merge commit or a squash)"
FIXED_NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
BASELINE_82 = '[function_lines."src/quality/cli.py"]\n"main" = 82\n'
BASELINE_90 = BASELINE_82.replace("82", "90")
TWO_JOBS = '[gate]\nmandatory_jobs = ["checks", "B"]\n'
B_REMOVED = 'mandatory job "B" removed'
ALLOWED_SHOWN = "declared loosening (overnightworks/agent-presentator#371 of 2026-10-21): advisory allowance adds "
ADDS_QWEN = "advisory allowance adds GHSA-462x-pgg6-55fp in speech/providers/qwen/uv.lock until 2027-01-31"


def pull_request_rule(*methods):
    return {"type": "pull_request", "parameters": {"allowed_merge_methods": list(methods)}}


def settings_allowing(*methods):
    return {f"allow_{name}": method in methods for method, name in (("merge", "merge_commit"), ("squash", "squash_merge"), ("rebase", "rebase_merge"))}


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
    output = capsys.readouterr().out
    assert "not confirmed, nothing was changed" in output
    assert ("\n\nemergency-merge: not confirmed" in output) is isinstance(typed, BaseException)


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
        ({"checks": {"quality / sonar": "success", "quality / verdict": None}}, {}, "still running: quality / verdict"),
        ({"checks": {"quality / sonar": "success", "quality / verdict": "failure"}}, {}, "finds nothing: re-run the verdict"),
        ({"checks": {"quality / sonar": "timed_out", "quality / verdict": "failure"}}, {}, "quality / sonar is timed_out: the door needs it green or failed"),
        ({"checks": {"quality / sonar": "cancelled", "quality / verdict": "failure"}}, {}, "quality / sonar is cancelled"),
        ({"checks": {"quality / verdict": "failure"}}, {}, "quality / sonar did not run: the door needs it green or failed"),
        ({"checks": {"quality / sonar": "failure"}}, {}, "quality / verdict must have failed"),
        ({"checks": SONAR_GREEN, "base_config": TWO_JOBS, "head_config": ONE_JOB}, {}, f"loosened (undeclared): {B_REMOVED}"),
        (
            {"checks": SONAR_GREEN, "base_config": TWO_JOBS, "head_config": ONE_JOB + declaring(B_REMOVED) + declaring("x")},
            {}, "declared loosening matches nothing: x",
        ),
        (
            {"checks": {**SONAR_GREEN, "B": "failure"}, "base_config": TWO_JOBS, "head_config": ONE_JOB + declaring(B_REMOVED)},
            {}, "B (failure)",
        ),
        (
            {"checks": SONAR_GREEN, "base_config": TWO_JOBS, "head_config": ONE_JOB + declaring(B_REMOVED), "merge_config": ONE_JOB},
            {}, f"loosened (undeclared): {B_REMOVED}",
        ),
        (
            {"checks": SONAR_GREEN, "base_config": ONE_JOB, "head_config": ONE_JOB + declaring(ADDS_QWEN) + allowance_config(reason="no image input")},
            {}, f"declared loosening matches nothing: {ADDS_QWEN}",
        ),
        ({"head_config": GATE_CONFIG.replace("fail", "report")}, {}, "loosened (undeclared): legacy fail"),
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
        "cell 18 nothing red", "cell 18 verdict running", "cell 17 sonar green, nothing re-derived", "cell 19 sonar timed out",
        "cell 19 sonar cancelled", "cell 28 no sonar run", "verdict green", "cell 15 undeclared removal",
        "cell 16 a stale declaration", "cell 3 the removed job still runs red", "cell 22 only the head sha declares",
        "#116 cell 24 a gate.loosened entry for an allowance",
        "loosened legacy", "report on both sides", "head fail but merge commit report", "report and Sonar unreachable", "no config at either sha", "report mode and a new finding", "removed mandatory job",
        "closed", "no legacy", "legacy unknown", "stale analysis",
    ],
)
def test_refuses_unless_the_door_is_the_only_way(gh_kwargs, sonar_kwargs, message, capsys):
    """#37 rule 2 and #103 rule 4 (E7): each refusal of the door."""
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
    [
        ({}, False, REPO, 1), ({}, True, "not-a-repo", 1), ({}, True, "acme/widg\u0435ts", 1), ({"admin": False}, True, REPO, 2),
        ({"fail_on": ["pull"]}, True, REPO, 2),
    ],
    ids=["no terminal", "bad name", "#103 rule 1 lookalike name", "missing admin permission", "gh down"],
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
    """#37 rule 4 and exit 2, #103 rule 5c: Ctrl-C during the merge restores the gate and says, on a line of its own,
    that it cannot tell."""
    gh = FakeGh(interrupt_on=["merge"])
    assert drive(gh)[0] == 2
    assert gh.gate_values == ["false", "true"]
    assert "\n\nemergency-merge: merge state unknown" in capsys.readouterr().out
    assert "merge state unknown" in gh.comments[0]


def test_ctrl_c_during_a_merge_that_landed_is_reported_merged():
    """#37 rule 4: GitHub's own answer settles an interrupted merge."""
    gh = FakeGh(interrupt_on=["merge"], merge_lands=True)
    assert drive(gh)[0] == 0


def test_gate_is_back_on_when_switching_off_broke_halfway():
    """#37 rule 4, #103 rule 5b: a half-done switch off is switched on again, nothing is merged, and the log says so."""
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
    assert "quality_gate not switched off → no merge → on" in gh.comments[0]


@pytest.mark.parametrize("restore_failure", [GhFailed("denied"), KeyboardInterrupt()], ids=["gh failed", "Ctrl-C"])
def test_exit_2_loud_line_and_log_when_the_gate_cannot_be_restored(restore_failure, capsys):
    """#37 rule 4, #103 rule 5c: a restore that fails is exit 2 with one loud line, on a line of its own after Ctrl-C,
    and the PR still gets its log."""
    gh = FakeGh()
    original = gh.__call__

    def cannot_restore(arguments, stdin=None):
        if stdin and json.loads(stdin)["properties"][0]["value"] == "true":
            raise restore_failure
        return original(arguments, stdin)

    assert drive(cannot_restore)[0] == 2
    output = capsys.readouterr().out
    assert f"quality_gate MAY STILL BE OFF for {REPO}" in output
    assert ("\n\nemergency-merge: quality_gate MAY STILL BE OFF" in output) is isinstance(restore_failure, KeyboardInterrupt)
    assert "NOT restored" in gh.comments[0]


@pytest.mark.parametrize(
    ("gh_kwargs", "read_back_failure", "expected"),
    [
        ({"merge_lands": True}, None, 'aco release <n> --abandoned "landed as PR #7 by emergency-merge"'),
        ({"fail_on": ["merge"]}, GhFailed("pull broke"), ") - check #7 on GitHub"),
        ({"fail_on": ["merge"]}, KeyboardInterrupt(), ") - check #7 on GitHub"),
        ({"fail_on": ["merge"]}, None, "not merged"),
        ({"merge_lands": True, "fail_on": ["comment"]}, None, 'aco release <n> --abandoned "landed as PR #7 by emergency-merge"'),
        ({"fail_on": ["merge", "comment"]}, None, "not merged"),
    ],
    ids=["merged", "unknown", "unknown, Ctrl-C", "not merged", "merged, comment fails", "not merged, comment fails"],
)
def test_names_the_merge_outcome_when_the_gate_cannot_be_restored(gh_kwargs, read_back_failure, expected, capsys):
    """#37 rule 4, #103 rule 5d: the operator learns both manual checks, the gate and the pull request, in one line,
    before the door sends GitHub another request, and that line is the door's last, even after a failed log comment."""
    gh = FakeGh(**gh_kwargs)
    original = gh.__call__
    printed, warned_before_request = [], []

    def cannot_restore(arguments, stdin=None):
        printed.append(capsys.readouterr().out)
        step = FakeGh.step_of(arguments)
        if step == "comment" or (gh.merge_calls and step == "pull"):
            warned_before_request.append("MAY STILL BE OFF" in "".join(printed))
        if stdin and json.loads(stdin)["properties"][0]["value"] == "true":
            raise GhFailed("denied")
        if read_back_failure and gh.merge_calls and step == "pull":
            raise read_back_failure
        return original(arguments, stdin)

    assert drive(cannot_restore)[0] == 2
    output = "".join(printed) + capsys.readouterr().out
    assert set(warned_before_request) == {True}
    last_line = output.splitlines()[-1]
    assert "MAY STILL BE OFF" in last_line
    assert expected in last_line


def test_ctrl_c_while_reading_back_the_merge_state_is_exit_2_with_the_gate_on(capsys):
    """#37 rule 4, #103 rule 5c: an interruption after the restore is still an outcome line, on a line of its own, and
    exit 2, not a traceback."""
    gh = FakeGh(fail_on=["merge"])
    original = gh.__call__

    def interrupted_reading_back(arguments, stdin=None):
        if gh.merge_calls and FakeGh.step_of(arguments) == "pull":
            raise KeyboardInterrupt
        return original(arguments, stdin)

    assert drive(interrupted_reading_back)[0] == 2
    assert gh.gate_values == ["false", "true"]
    assert "\n\nemergency-merge: merge state unknown" in capsys.readouterr().out


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
    if restore_fails:
        last_line = output.splitlines()[-1]
        assert "MAY STILL BE OFF" in last_line
        assert "merged." in last_line


@pytest.mark.integration
@pytest.mark.parametrize(
    ("base_baseline", "merge_baseline", "code", "message"),
    [
        (BASELINE_82, BASELINE_90, 1, "loosened (undeclared): size baseline raises function_lines src/quality/cli.py main 82 -> 90"),
        (BASELINE_82, BASELINE_82, 0, "merged."),
        (BASELINE_82, None, 1, "loosened (undeclared): quality/baseline.toml removed"),
        (None, None, 0, "merged."),
    ],
    ids=["cell 20 raised", "cell 21 equal", "cell 22 removed at the merge commit", "cell 23 absent at both"],
)
def test_the_door_refuses_a_baseline_loosening_next_to_legacy_red(base_baseline, merge_baseline, code, message, capsys, tmp_path):
    """#48 rule 6, cells 20-23: the door judges the baseline at the merge commit against base, as the verdict does."""
    remote, base_sha, merge_sha = git_pull(tmp_path, base_baseline, merge_baseline)
    gh = FakeGh(base_baseline=base_baseline, merge_baseline=merge_baseline, base_sha=base_sha, merge_sha=merge_sha, remote=remote)
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


@pytest.mark.parametrize(
    ("base_config", "merge_config", "declared"),
    [
        (TWO_JOBS, ONE_JOB + declaring(B_REMOVED), [f"{SHOWN}{B_REMOVED} - reason: ruled"]),
        (GATE_CONFIG, REPORT_CONFIG + declaring("legacy fail → report"),
         [f"{SHOWN}legacy fail → report - reason: ruled"]),
        (ONE_JOB, ONE_JOB + allowance_config(reason="no image input"),
         [f"{ALLOWED_SHOWN}GHSA-462x-pgg6-55fp in speech/providers/qwen/uv.lock until 2027-01-31 - reason: no image input"]),
        (TWO_JOBS, ONE_JOB + declaring(B_REMOVED) + allowance_config(reason="no image input"),
         [f"{SHOWN}{B_REMOVED} - reason: ruled",
          f"{ALLOWED_SHOWN}GHSA-462x-pgg6-55fp in speech/providers/qwen/uv.lock until 2027-01-31 - reason: no image input"]),
    ],
    ids=[
        "cell 14 songmaker's removed job", "cell 27 a declared legacy fail → report", "#116 cell 23 an allowance",
        "#116 cell 28 an allowance next to a declared removal",
    ],
)
def test_a_declared_loosening_merges_through_the_door_without_sonar(
    capsys, monkeypatch, base_config, merge_config, declared
):
    """#103 rule 4 (E6, E7), cells 14 and 27, and #116 R2, E7: with Sonar green and only declared loosenings at the
    merge commit, an allowance's own among them, the door shows each declaration, then asks, merges with the head
    pinned, and logs it, with no SonarCloud request."""
    monkeypatch.delenv("SONAR_TOKEN", raising=False)
    gh, transport, shown = FakeGh(checks=SONAR_GREEN, base_config=base_config, merge_config=merge_config), recording(sonar()), []

    def ask(prompt):
        shown.append(capsys.readouterr().out)
        return "ja"

    code = cli.main(["emergency-merge", REPO, "7"], gh=gh, transport=transport, ask=ask, is_terminal=lambda: True)
    assert (code, transport.sent, gh.sequence) == (0, [], ["gate false", "merge", "gate true", "comment"])
    assert shown[0].splitlines() == [f"{REPO} #7 head {SHA}: {len(declared)} declared loosening(s)", *declared, "merges with a merge commit"]
    assert gh.merge_calls == [("pr", "merge", "7", "--repo", REPO, "--merge", "--match-head-commit", SHA)]
    assert f"{'; '.join(declared)}; merges with a merge commit; quality_gate off → merge → on." in gh.comments[0]


def test_the_door_names_each_of_79_allowances_within_one_comment(capsys):
    """#116 cell 29: presentator#372's 79 allowances are 79 door lines, and the log comment stays within GitHub's
    65,536 characters."""
    allowances = "".join(
        allowance_config(f"GHSA-{number:04d}-pgg6-55fp", f"speech/providers/provider-{number}/uv.lock", reason="x" * 60)
        for number in range(79)
    )
    gh = FakeGh(checks=SONAR_GREEN, base_config=ONE_JOB, merge_config=ONE_JOB + allowances)
    assert drive(gh)[0] == 0
    shown = [line for line in capsys.readouterr().out.splitlines() if line.startswith(ALLOWED_SHOWN)]
    assert (len(shown), len(gh.comments[0]) < 65_536) == (79, True)


def test_legacy_findings_and_a_declared_loosening_merge_in_one_run(capsys):
    """#103 rule 4 (E8, ruled 21.10.2026), cell 21: a red Sonar on legacy findings next to a declared loosening merges
    under every legacy condition, showing both."""
    gh = FakeGh(base_config=GATE_CONFIG.replace('["checks"]', '["checks", "B"]'), head_config=GATE_CONFIG + declaring(B_REMOVED))
    assert drive(gh)[0] == 0
    output = capsys.readouterr().out
    assert f"{REPO} #7 head {SHA}: 4 legacy finding(s), 1 declared loosening(s)\n{SHOWN}{B_REMOVED} - reason: ruled" in output
    assert f"4 legacy finding(s); {SHOWN}{B_REMOVED}" in gh.comments[0]


def test_a_red_sonar_run_next_to_a_green_one_takes_the_legacy_path(capsys):
    """#103 rule 4, cell 28: Sonar is green only when every run is green, so one red run is judged for legacy findings."""
    assert drive(FakeGh(extra_runs=[("quality / sonar", "success")]))[0] == 0
    assert f"{REPO} #7 head {SHA}: 4 legacy finding(s)\n" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("base_config", "merge_config", "code", "expected"),
    [
        (TWO_JOBS, ONE_JOB + declaring(B_REMOVED, reason="ok\x1b[8m versteckt"), 0, f"{SHOWN}{B_REMOVED} - reason: ok\\x1b[8m versteckt"),
        (TWO_JOBS, ONE_JOB + declaring(B_REMOVED, reason="ok\rforged"), 2, "needs a non-empty one-line reason"),
        (TWO_JOBS, ONE_JOB + declaring(B_REMOVED, reason="ok\nforged"), 2, "needs a non-empty one-line reason"),
        (ONE_JOB, '[gate]\nmandatory_jobs = ["checks", "a\\n::error::b"]\n', 1, 'mandatory job "a\\x0a::error::b" is missing'),
        (ONE_JOB, ONE_JOB + allowance_config("GHSA-\x1b[8m", "a\tb/uv.lock", reason="ok\x1b[8m versteckt"), 0,
         f"{ALLOWED_SHOWN}GHSA-\\x1b[8m in a\\x09b/uv.lock until 2027-01-31 - reason: ok\\x1b[8m versteckt"),
    ],
    ids=[
        "ESC in the reason", "carriage return in the reason", "line feed in the reason", "line feed in a refused job",
        "#116 cell 25 control characters in an allowance's id, lock and reason",
    ],
)
def test_every_line_the_door_prints_is_escaped(capsys, base_config, merge_config, code, expected):
    """#103 rule 4 (E9), cell 23: no repository text in a declaration or a refusal can hide, split or forge a line."""
    gh = FakeGh(checks=SONAR_GREEN, base_config=base_config, merge_config=merge_config)
    assert drive(gh)[0] == code
    lines = capsys.readouterr().out.splitlines()
    assert [line for line in lines if expected in line] != []
    assert_every_line_starts_with_the_door(lines)


@pytest.mark.parametrize(
    ("gh_kwargs", "sentence"),
    [({}, "merges with a merge commit"), ({"rules": [[pull_request_rule("squash")]]}, "merges with a squash")],
    ids=["a merge commit", "a squash"],
)
def test_the_operator_reads_the_method_before_ja_and_the_log_comment_names_it(gh_kwargs, sentence, capsys):
    """#146: the shown text ends with the method line, and the log comment carries it."""
    gh = FakeGh(**gh_kwargs)
    drive(gh)
    assert sentence in capsys.readouterr().out.splitlines()
    assert f"; {sentence}; " in gh.comments[0]


def merge_call(flag):
    return ("pr", "merge", "7", "--repo", REPO, flag, "--match-head-commit", SHA)


@pytest.mark.parametrize(
    ("gh_kwargs", "flag"),
    [
        ({}, "--merge"),
        ({"rules": [[pull_request_rule("squash")]]}, "--squash"),
        ({"rules": [[LINEAR_HISTORY]]}, "--squash"),
        ({"settings": settings_allowing("squash", "rebase")}, "--squash"),
        ({"rules": [[pull_request_rule("merge", "squash"), pull_request_rule("squash", "rebase")]]}, "--squash"),
        ({"rules": [[UNRELATED_RULE]]}, "--merge"),
        ({"rules": [[]]}, "--merge"),
        ({"rules": [[{"type": "pull_request", "parameters": {}}]]}, "--merge"),
        ({"rules": [[{"type": "pull_request"}]]}, "--merge"),
        ({"rules": [[UNRELATED_RULE, LINEAR_HISTORY]]}, "--squash"),
        ({"rules": [[pull_request_rule("merge", "squash")], [LINEAR_HISTORY]]}, "--squash"),
        ({"base_ref": "release/1", "rules": [[pull_request_rule("squash")]]}, "--squash"),
        ({"base_ref": "rel#x", "rules": [[pull_request_rule("squash")]]}, "--squash"),
        ({"settings": settings_allowing("merge"), "rules": [[pull_request_rule("merge", "rebase")]]}, "--merge"),
    ],
    ids=[
        "cell 1 both sources allow a merge commit", "cell 2 a rule allows only squash", "cell 3 linear history",
        "cell 4 settings forbid merge commits", "cell 5 two rules intersect to squash", "cell 8 an unrelated rule only",
        "cell 8 no rules", "cell 9 allowed_merge_methods absent", "cell 9 no parameters", "cell 9 rules without a parameters key", "cell 10 two pages both count",
        "cell 12 a non-default base branch", "cell 12 a base branch with a hash", "cell 13 a merge commit where only it and rebase are allowed",
    ],
)
def test_the_door_merges_with_a_method_the_settings_and_every_rule_allow(gh_kwargs, flag):
    """#146 rules 1 and 3: the method is the first of merge commit, squash that both sources allow, and the merge pins the head."""
    gh = FakeGh(**gh_kwargs)
    assert drive(gh)[0] == 0
    assert gh.merge_calls == [merge_call(flag)]
    rules_read = next(call for call in gh.calls if "/rules/branches/" in " ".join(call))
    assert rules_read == ("api", "--paginate", "--slurp", f"repos/{REPO}/rules/branches/{quote(gh.base_ref, safe='/')}?per_page=100")


@pytest.mark.parametrize(
    ("gh_kwargs", "line"),
    [
        ({"settings": settings_allowing("merge"), "rules": [[pull_request_rule("squash")]]}, NO_USABLE_METHOD),
        ({"settings": settings_allowing("rebase")}, NO_USABLE_METHOD),
        ({"rules": [[pull_request_rule("rebase")]]}, NO_USABLE_METHOD),
        ({"rules": [[pull_request_rule()]]}, NO_USABLE_METHOD),
        ({"settings": settings_allowing()}, NO_USABLE_METHOD),
        ({"settings": {}}, "emergency-merge: cannot judge - the repository settings withhold or malform"),
        ({"settings": {**ALL_METHODS_ALLOWED, "allow_squash_merge": "yes"}}, "emergency-merge: cannot judge - the repository settings withhold or malform"),
        ({"rules": [["not a rule"]]}, "emergency-merge: cannot judge - the branch rules of main are malformed"),
        ({"rules": [[{"type": "pull_request", "parameters": "x"}]]}, "emergency-merge: cannot judge - the branch rules of main are malformed"),
        ({"rules": [[{"type": "pull_request", "parameters": {"allowed_merge_methods": "squash"}}]]}, "emergency-merge: cannot judge - the branch rules of main are malformed"),
        ({"rules": [[pull_request_rule("fast-forward")]]}, "emergency-merge: cannot judge - the branch rules of main are malformed"),
        ({"rules": [[{"parameters": {"allowed_merge_methods": ["merge"]}}]]}, "emergency-merge: cannot judge - the branch rules of main are malformed"),
        ({"rules": [[{"type": 5}]]}, "emergency-merge: cannot judge - the branch rules of main are malformed"),
        ({"rules": [[{"type": "pull_request", "parameters": None}]]}, "emergency-merge: cannot judge - the branch rules of main are malformed"),
        ({"rules": [[{"type": "required_linear_history", "parameters": None}]]}, "emergency-merge: cannot judge - the branch rules of main are malformed"),
        ({"rules": [[{"type": "deletion", "parameters": "x"}]]}, "emergency-merge: cannot judge - the branch rules of main are malformed"),
        ({"rules": [[{"type": "pull_request", "parameters": {"allowed_merge_methods": None}}]]}, "emergency-merge: cannot judge - the branch rules of main are malformed"),
        ({"rules_raw": "not json"}, "emergency-merge: cannot judge - the branch rules of main are malformed"),
        ({"rules_raw": json.dumps({"type": "creation"})}, "emergency-merge: cannot judge - the branch rules of main are malformed"),
        ({"rules_raw": json.dumps([{"type": "creation"}])}, "emergency-merge: cannot judge - the branch rules of main are malformed"),
        ({"rules_raw": json.dumps([['{"type":"creation"}']])}, "emergency-merge: cannot judge - the branch rules of main are malformed"),
        ({"rules_raw": json.dumps([[5]])}, "emergency-merge: cannot judge - the branch rules of main are malformed"),
        ({"rules_error": "gh api failed: HTTP 403: Resource not accessible"}, "emergency-merge: cannot judge - cannot read the branch rules of main"),
        (
            {"rules_error": "gh api failed: HTTP 403: Upgrade to GitHub Pro or Team to enable this feature."},
            "emergency-merge: cannot judge - cannot read the branch rules of main",
        ),
    ],
    ids=[
        "cell 6 sources do not intersect", "cell 7 settings allow only rebase", "cell 7 a rule allows only rebase",
        "cell 9 allowed_merge_methods empty", "cell 6 settings allow nothing", "cell 11 settings withheld",
        "cell 11 settings malformed", "cell 11 a rule is no object", "cell 11 parameters malformed",
        "cell 11 methods malformed", "cell 11 an unknown method", "cell 11 a rule without a type",
        "cell 11 a rule type that is no string", "cell 11 pull_request parameters null",
        "cell 11 linear history parameters null", "cell 11 an unrelated rule with parameters that are no object", "cell 11 methods null", "cell 11 a line that is no JSON",
        "cell 11 the answer is an object", "cell 11 a page is an object", "cell 11 a rule is a JSON string", "cell 11 a rule is a number",
        "cell 11 generic 403", "cell 11 plan 403",
    ],
)
def test_the_door_refuses_before_the_gate_when_no_method_is_usable(gh_kwargs, line, capsys):
    """#146 rules 2 and 4: a repository that allows no merge commit or squash, or whose rules cannot be read, is exit 2 with a
    named line, and neither the gate nor the merge is touched."""
    gh = FakeGh(**gh_kwargs)
    code, answers = drive(gh)
    assert code == 2
    assert [shown for shown in capsys.readouterr().out.splitlines() if shown.startswith(line)] != []
    changed_nothing(gh)
    assert answers == []


def test_a_method_github_refuses_at_merge_time_leaves_the_gate_on_and_the_pull_request_unmerged(capsys):
    """#146 cell 14: the selected squash is refused, so the gate is switched on again and the outcome is not merged."""
    gh = FakeGh(rules=[[pull_request_rule("squash")]], fail_on=["merge"])
    assert drive(gh)[0] == 1
    assert (gh.merge_calls, gh.gate_values) == ([merge_call("--squash")], ["false", "true"])
    assert "not merged" in capsys.readouterr().out
