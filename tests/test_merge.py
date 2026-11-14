import json
from datetime import UTC, datetime
from urllib.parse import quote

import pytest

from quality import architecture, cli
from quality.baseline_file import BASELINE_PATH
from quality.config import CONFIG_PATH
from quality.sonar import SonarHttpError, basic_authorization
from tests.support.advisories import allowance_config
from tests.support.check import assert_every_line_starts_with_the_door
from tests.support.door import RELEASE_ROW
from tests.support.git_repositories import git_pull
from tests.support.merge import (
    ALL_METHODS_ALLOWED,
    B_REMOVED,
    BASE_SHA,
    GATE_CONFIG,
    LEGACY_RED,
    MERGE_SHA,
    ONE_JOB,
    REPO,
    SHA,
    SONAR_GREEN,
    TWO_JOBS,
    FakeGh,
    base_row,
    changed_nothing,
    closing_line,
    declaring,
    door_ports,
    drive,
    landing_row,
    merge_request,
    not_judged_pair,
    prompt,
    pull_row,
    rows_before_the_prompt,
    sonar,
)

REPORT_CONFIG = GATE_CONFIG.replace("fail", "report")
LINEAR_HISTORY = {"type": "required_linear_history"}
UNRELATED_RULE = {"type": "creation"}
NO_USABLE_METHOD = "the repository and its branch rules allow no merge method the door uses (a merge commit or a squash)"
FIXED_NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
BASELINE_82 = '[function_lines."src/quality/cli.py"]\n"main" = 82\n'
BASELINE_90 = BASELINE_82.replace("82", "90")
ADDS_QWEN = "advisory allowance adds GHSA-462x-pgg6-55fp in speech/providers/qwen/uv.lock until 2027-01-31"


def pull_request_rule(*methods):
    return {"type": "pull_request", "parameters": {"allowed_merge_methods": list(methods)}}


def settings_allowing(*methods):
    return {f"allow_{name}": method in methods for method, name in (("merge", "merge_commit"), ("squash", "squash_merge"), ("rebase", "rebase_merge"))}


def checks_row(red="quality / sonar and quality / verdict are"):
    return f"  ✓ checks        ok             only {red} red\n"


def loosenings_none():
    return "  ✓ loosenings    none\n"


def legacy_row():
    return "  ! sonar         4 legacy findings on main\n"


def merge_row(event="merge commit"):
    return f"  · merge         {event}   allowed on main\n"


def declared_row(count):
    return f"  ! loosenings    {count} declared\n"


def qwen_reason():
    return "no image input"


def qwen_lines():
    return f"    ! {ADDS_QWEN} · {presentator_ruling()}\n        reason: {qwen_reason()}\n"


def songmaker_ruling():
    return "overnightworks/songmaker#1345 of 2026-10-20"


def presentator_ruling():
    return "overnightworks/agent-presentator#371 of 2026-10-21"


def removed_b_lines():
    return f"    ! {B_REMOVED} · {songmaker_ruling()}\n        reason: ruled\n"


def removed_b():
    return (B_REMOVED, songmaker_ruling(), "ruled")


def malformed_rules():
    return "the branch rules of main are malformed"


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
    """MERGE-79: an unset or empty token means the transport is asked for no Authorization at all."""
    if unset:
        monkeypatch.delenv("SONAR_TOKEN", raising=False)
    else:
        monkeypatch.setenv("SONAR_TOKEN", "")
    transport = recording(sonar())
    assert drive(FakeGh(), transport)[0] == 0
    assert transport.sent
    assert set(transport.sent) == {None}


def test_a_set_token_still_sends_basic_authorization(monkeypatch, capsys):
    """MERGE-77, MERGE-78: a set token is sent as Basic authorization and never shown in a row."""
    monkeypatch.setenv("SONAR_TOKEN", "s3cret-token")
    transport = recording(sonar())
    assert drive(FakeGh(), transport)[0] == 0
    assert set(transport.sent) == {basic_authorization("s3cret-token")}
    output = capsys.readouterr().out
    assert "s3cret-token" not in output
    assert basic_authorization("s3cret-token").split()[1] not in output


@pytest.mark.parametrize("status", [401, 403, 404])
def test_a_refused_anonymous_read_is_cannot_judge_naming_the_token(status, monkeypatch, capsys):
    """MERGE-80: an anonymous refusal names the missing SONAR_TOKEN and changes nothing."""
    monkeypatch.setenv("SONAR_TOKEN", "")
    gh = FakeGh()
    assert drive(gh, refusing_anonymous_read(status))[0] == 2
    out = capsys.readouterr().out
    assert f"did not resolve acme_widgets in pullRequest=7 anonymously (HTTP {status}); a private project needs SONAR_TOKEN" in out
    changed_nothing(gh)


def test_the_token_never_appears_when_sonar_refuses_it(monkeypatch, capsys):
    """MERGE-78, MERGE-81: a rejected token is said so, and a refused read with a token does not echo the token or its
    base64 form."""
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


def test_logs_who_and_the_legacy_count_on_the_pull_request():
    """MERGE-46, MERGE-48, MERGE-50: the comment names who, the legacy count and the sequence."""
    gh = FakeGh()
    drive(gh)
    assert "@felix" in gh.comments[0]
    assert "4 legacy" in gh.comments[0]
    assert "off → merge → on" in gh.comments[0]


def test_shows_repository_head_and_legacy_count_before_asking(capsys):
    """MERGE-1, MERGE-3, MERGE-5, MERGE-6: the rows come in order with the landing last, then the question."""
    shown = []
    drive(FakeGh(), when_asked=lambda: shown.append(capsys.readouterr()))
    assert (shown[0].out, shown[0].err) == (
        rows_before_the_prompt(),
        prompt(),
    )


@pytest.mark.parametrize(
    ("gh_kwargs", "sonar_kwargs", "message"),
    [
        ({}, {"new": 1}, "adds findings"),
        ({"checks": {**LEGACY_RED, "tests": "failure"}}, {}, "tests (failure)"),
        ({"checks": {**LEGACY_RED, "tests": "skipped"}}, {}, "tests (skipped)"),
        ({"checks": {**LEGACY_RED, "tests": None}}, {}, "running        tests"),
        ({"statuses": {"external-ci": "failure"}}, {}, "external-ci (failure)"),
        ({"statuses": {"external-ci": "pending"}}, {}, "running        external-ci"),
        ({"checks": {"quality / sonar": "success", "quality / verdict": "success"}}, {}, "no check is red"),
        ({"checks": {"quality / sonar": "success", "quality / verdict": None}}, {}, "running        quality / verdict"),
        ({"checks": {"quality / sonar": "success", "quality / verdict": "failure"}}, {}, "finds nothing: re-run the verdict"),
        ({"checks": {"quality / sonar": "timed_out", "quality / verdict": "failure"}}, {}, "quality / sonar is timed_out: the door needs it green or failed"),
        ({"checks": {"quality / sonar": "cancelled", "quality / verdict": "failure"}}, {}, "quality / sonar is cancelled"),
        ({"checks": {"quality / verdict": "failure"}}, {}, "quality / sonar did not run: the door needs it green or failed"),
        ({"checks": {"quality / sonar": "failure"}}, {}, "quality / verdict must have failed"),
        ({"checks": SONAR_GREEN, "base_config": TWO_JOBS, "head_config": ONE_JOB}, {}, f"    ! {B_REMOVED}"),
        (
            {"checks": SONAR_GREEN, "base_config": TWO_JOBS, "head_config": ONE_JOB + declaring(B_REMOVED) + declaring("x")},
            {}, "    ! x · overnightworks/songmaker#1345 of 2026-10-20\n",
        ),
        (
            {"checks": {**SONAR_GREEN, "B": "failure"}, "base_config": TWO_JOBS, "head_config": ONE_JOB + declaring(B_REMOVED)},
            {}, "B (failure)",
        ),
        (
            {"checks": SONAR_GREEN, "base_config": TWO_JOBS, "head_config": ONE_JOB + declaring(B_REMOVED), "merge_config": ONE_JOB},
            {}, f"    ! {B_REMOVED}",
        ),
        (
            {"checks": SONAR_GREEN, "base_config": ONE_JOB, "head_config": ONE_JOB + declaring(ADDS_QWEN) + allowance_config(reason=qwen_reason())},
            {}, f"    ! {ADDS_QWEN} · overnightworks/songmaker#1345 of 2026-10-20\n",
        ),
        ({"head_config": GATE_CONFIG.replace("fail", "report")}, {}, "    ! legacy fail"),
        ({"head_config": REPORT_CONFIG, "base_config": REPORT_CONFIG}, {}, "legacy = report at the pull request's current merge commit"),
        ({"head_config": GATE_CONFIG, "base_config": REPORT_CONFIG, "merge_config": REPORT_CONFIG}, {}, "legacy = report at the pull request's current merge commit"),
        ({"head_config": REPORT_CONFIG, "base_config": REPORT_CONFIG}, {"unreachable": True}, "legacy = report"),
        ({"head_config": None, "base_config": None}, {}, "legacy = report at the pull request's current merge commit"),
        ({"head_config": REPORT_CONFIG, "base_config": REPORT_CONFIG}, {"new": 1}, "adds findings"),
        ({"head_config": '[gate]\nmandatory_jobs = ["other"]\nlegacy = "fail"\n'}, {}, 'mandatory job "checks" removed'),
        ({"state": "closed"}, {}, "closed"),
        ({}, {"legacy": 0}, "not legacy findings"),
        ({}, {"main_analysed": False}, "legacy unknown"),
        ({}, {"analysed": "c" * 40}, f"Sonar analysed {'c' * 40}, head is {SHA}"),
    ],
    ids=[
        "new finding", "other red check", "other skipped check", "pending check", "failing status", "pending status",
        "nothing red", "verdict running", "sonar green, nothing re-derived", "sonar timed out",
        "sonar cancelled", "no sonar run", "verdict green", "undeclared removal",
        "a stale declaration", "the removed job still runs red", "only the head sha declares",
        "a gate.loosened entry for an allowance",
        "loosened legacy", "report on both sides", "head fail but merge commit report", "report and Sonar unreachable", "no config at either sha", "report mode and a new finding", "removed mandatory job",
        "closed", "no legacy", "legacy unknown", "stale analysis",
    ],
)
def test_refuses_unless_the_door_is_the_only_way(gh_kwargs, sonar_kwargs, message, capsys):
    """MERGE-14, MERGE-15, MERGE-56, MERGE-59, MERGE-60, MERGE-61, MERGE-62, MERGE-72, MERGE-73, MERGE-74, MERGE-75,
    MERGE-76, MERGE-82, MERGE-84, MERGE-85, MERGE-87: each refusal of the door names its reason, changes nothing and
    asks no question."""
    gh = FakeGh(**gh_kwargs)
    code, answers = drive(gh, sonar(**sonar_kwargs))
    assert code == 1
    assert message in capsys.readouterr().out
    changed_nothing(gh)
    assert answers == []


def test_cannot_judge_when_sonar_is_unreachable_under_legacy_fail(capsys):
    """MERGE-16: a SonarCloud that cannot be reached under legacy fail is not judged: exit 2, nothing changed."""
    gh = FakeGh()
    code, _ = drive(gh, sonar(unreachable=True))
    assert code == 2
    assert "not judged" in capsys.readouterr().out
    changed_nothing(gh)


def test_cannot_judge_without_the_merge_commit_the_jobs_ran_on(capsys):
    """The release and the config the jobs ran on are unknown, so exit 2 and nothing changed."""
    gh = FakeGh(merge_sha=None)
    code, answers = drive(gh, sonar())
    assert code == 2
    assert capsys.readouterr().out == ""
    changed_nothing(gh)
    assert answers == []


def test_names_the_finding_a_pull_request_adds(capsys):
    """MERGE-75: a pull request that adds a finding is refused, naming it."""
    drive(FakeGh(), sonar(new=1))
    assert "a.py:3" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("gh_kwargs", "terminal", "repo", "code"),
    [
        ({}, False, REPO, 1), ({}, True, "not-a-repo", 2), ({}, True, "acme/widg\u0435ts", 2), ({"admin": False}, True, REPO, 2),
        ({"fail_on": ["pull"]}, True, REPO, 2),
    ],
    ids=["no terminal", "bad name", "a lookalike name", "missing admin permission", "gh down"],
)
def test_changes_nothing_when_it_cannot_start(gh_kwargs, terminal, repo, code):
    """MERGE-16, MERGE-21, MERGE-54: the door changes nothing when it has no terminal, no admin right or no working gh,
    or is given a name of another form."""
    gh = FakeGh(**gh_kwargs)
    assert drive(gh, terminal=terminal, repo=repo)[0] == code
    changed_nothing(gh)


def test_names_the_missing_right(capsys):
    """MERGE-55: the missing right is named."""
    drive(FakeGh(admin=False))
    assert f"no admin permission on {REPO}" in capsys.readouterr().out


@pytest.mark.integration
@pytest.mark.parametrize(
    ("base_baseline", "merge_baseline", "code", "message"),
    [
        (BASELINE_82, BASELINE_90, 1, "    ! size baseline raises function_lines src/quality/cli.py main 82 -> 90"),
        (BASELINE_82, BASELINE_82, 0, closing_line()),
        (BASELINE_82, None, 1, "    ! quality/baseline.toml removed"),
        (None, None, 0, closing_line()),
    ],
    ids=[
        "a raised baseline", "an equal baseline", "a baseline removed at the merge commit",
        "no baseline on either side",
    ],
)
def test_the_door_refuses_a_baseline_loosening_next_to_legacy_red(base_baseline, merge_baseline, code, message, capsys, tmp_path):
    """MERGE-15, MERGE-82, MERGE-83, MERGE-84: the door judges the baseline at the merge commit against base as the
    verdict does, with Sonar taken as green: a raised or a removed baseline is refused, an equal or an absent one
    merges."""
    remote, base_sha, merge_sha = git_pull(tmp_path, base_baseline, merge_baseline)
    gh = FakeGh(base_baseline=base_baseline, merge_baseline=merge_baseline, base_sha=base_sha, merge_sha=merge_sha, remote=remote)
    assert drive(gh)[0] == code
    assert message in capsys.readouterr().out
    assert (gh.merge_calls == []) == (code == 1)


def test_the_verdict_reads_the_base_side_at_the_compared_tip_not_at_the_pull_requests_base_sha(capsys):
    """MERGE-106: the config, the baseline and the import-linter configs of the base side are read at the tip the
    comparison named, never at the older sha the pull request still names."""
    older = "c" * 40
    gh = FakeGh(pull_base_sha=older, checks=SONAR_GREEN, base_config=TWO_JOBS, head_config=ONE_JOB + declaring(B_REMOVED))
    assert drive(gh)[0] == 0
    reads = {call[-1].partition("/contents/")[2].partition("?ref=")[0] for call in gh.calls if call[-1].endswith(f"?ref={BASE_SHA}")}
    assert reads == {CONFIG_PATH, BASELINE_PATH, *architecture.CONFIG_FILES}
    assert not any(older in argument for call in gh.calls for argument in call)


def test_a_malformed_baseline_at_the_merge_commit_cannot_be_judged(capsys):
    """MERGE-16, MERGE-82: a baseline the door cannot read at the merge commit is not judged: exit 2, the file and the
    commit named, nothing changed."""
    gh = FakeGh(base_baseline=BASELINE_82, merge_baseline="[ccn")
    assert drive(gh)[0] == 2
    assert f"{BASELINE_PATH} at {MERGE_SHA}" in capsys.readouterr().out
    changed_nothing(gh)


@pytest.mark.parametrize(
    ("base_config", "merge_config", "declared", "rows"),
    [
        (TWO_JOBS, ONE_JOB + declaring(B_REMOVED), (removed_b(),), declared_row(1) + removed_b_lines()),
        (
            GATE_CONFIG, REPORT_CONFIG + declaring("legacy fail → report"),
            (("legacy fail → report", songmaker_ruling(), "ruled"),),
            declared_row(1) + f"    ! legacy fail → report · {songmaker_ruling()}\n        reason: ruled\n",
        ),
        (
            ONE_JOB, ONE_JOB + allowance_config(reason=qwen_reason()),
            ((ADDS_QWEN, presentator_ruling(), qwen_reason()),),
            declared_row(1) + qwen_lines(),
        ),
        (
            TWO_JOBS, ONE_JOB + declaring(B_REMOVED) + allowance_config(reason=qwen_reason()),
            (removed_b(), (ADDS_QWEN, presentator_ruling(), qwen_reason())),
            declared_row(2) + removed_b_lines()
            + qwen_lines(),
        ),
    ],
    ids=[
        "a declared removed job", "a declared legacy fail → report", "an allowance",
        "an allowance next to a declared removal",
    ],
)
def test_a_declared_loosening_merges_through_the_door_without_sonar(
    capsys, monkeypatch, base_config, merge_config, declared, rows
):
    """MERGE-2, MERGE-33, MERGE-49, MERGE-50, MERGE-88: with Sonar green and only declared loosenings at the merge
    commit, an allowance's own among them, the door shows each declaration whole with its reason below,
    then asks, merges with the head pinned, and logs it, with no SonarCloud request."""
    monkeypatch.delenv("SONAR_TOKEN", raising=False)
    gh, transport, shown = FakeGh(checks=SONAR_GREEN, base_config=base_config, merge_config=merge_config, body="Work-Item: #9"), recording(sonar()), []

    def read_line():
        shown.append(capsys.readouterr())
        return "yes\n"

    code = cli.main(["merge", f"{REPO}#7"], gh=gh, transport=transport, terminal=cli.Terminal(read_line, lambda: True), ports=door_ports())
    assert (code, transport.sent, gh.sequence) == (0, [], ["gate false", "merge", "gate true", "comment"])
    assert (shown[0].out, shown[0].err) == (
        RELEASE_ROW + pull_row() + base_row() + checks_row("quality / verdict is") + rows + merge_row() + landing_row("#9"),
        prompt(),
    )
    assert gh.merge_calls == [merge_request()[0]]
    assert "merges with a merge commit; quality_gate off → merge → on." in gh.comments[0]
    assert all(f"declared loosening ({ruling}): {what} - reason: {reason}" in gh.comments[0] for what, ruling, reason in declared)


def test_the_door_names_each_of_79_allowances_within_one_comment(capsys):
    """The 79 allowances of one pull request are 79 lines of the door, and the log comment stays within GitHub's
    65,536 characters."""
    allowances = "".join(
        allowance_config(f"GHSA-{number:04d}-pgg6-55fp", f"speech/providers/provider-{number}/uv.lock", reason="x" * 60)
        for number in range(79)
    )
    gh = FakeGh(checks=SONAR_GREEN, base_config=ONE_JOB, merge_config=ONE_JOB + allowances)
    assert drive(gh)[0] == 0
    shown = [line for line in capsys.readouterr().out.splitlines() if line.startswith("    ! advisory allowance adds ")]
    assert (len(shown), len(gh.comments[0]) < 65_536) == (79, True)


def test_legacy_findings_and_a_declared_loosening_merge_in_one_run(capsys):
    """MERGE-48, MERGE-49, MERGE-71: a red Sonar on legacy findings next to a declared loosening merges under every
    legacy condition, showing both, and the log comment names the legacy count and the declaration."""
    gh = FakeGh(base_config=GATE_CONFIG.replace('["checks"]', '["checks", "B"]'), head_config=GATE_CONFIG + declaring(B_REMOVED))
    shown = []
    assert drive(gh, when_asked=lambda: shown.append(capsys.readouterr()))[0] == 0
    assert (shown[0].out, shown[0].err) == (
        RELEASE_ROW + pull_row() + base_row() + checks_row() + declared_row(1) + removed_b_lines() + legacy_row() + merge_row()
        + landing_row(),
        prompt(),
    )
    assert f'4 legacy finding(s); declared loosening ({songmaker_ruling()}): {B_REMOVED}' in gh.comments[0]


def test_a_red_sonar_run_next_to_a_green_one_takes_the_legacy_path(capsys):
    """MERGE-62: Sonar is green only when every run is green, so one red run is judged for legacy findings."""
    assert drive(FakeGh(extra_runs=[("quality / sonar", "success")]))[0] == 0
    assert legacy_row() in capsys.readouterr().out


@pytest.mark.parametrize(
    ("base_config", "merge_config", "code", "expected"),
    [
        (TWO_JOBS, ONE_JOB + declaring(B_REMOVED, reason="ok\x1b[8m versteckt"), 0, "        reason: ok\\x1b[8m versteckt"),
        (TWO_JOBS, ONE_JOB + declaring(B_REMOVED, reason="ok\u202edetsurt"), 0, "        reason: ok\\u202edetsurt"),
        (TWO_JOBS, ONE_JOB + declaring(B_REMOVED, reason="ok\rforged"), 2, "needs a non-empty one-line reason"),
        (TWO_JOBS, ONE_JOB + declaring(B_REMOVED, reason="ok\nforged"), 2, "needs a non-empty one-line reason"),
        (ONE_JOB, '[gate]\nmandatory_jobs = ["checks", "a\\n::error::b"]\n', 1, "    ! a\\x0a::error::b missing · not in the run"),
        (ONE_JOB, ONE_JOB + allowance_config("GHSA-\x1b[8m", "a\tb/uv.lock", reason="ok\x1b[8m versteckt"), 0,
         f"    ! advisory allowance adds GHSA-\\x1b[8m in a\\x09b/uv.lock until 2027-01-31 · {presentator_ruling()}"),
    ],
    ids=[
        "ESC in the reason", "bidi override in the reason", "carriage return in the reason", "line feed in the reason", "line feed in a refused job",
        "control characters in an allowance's id, lock and reason",
    ],
)
def test_every_line_the_door_prints_is_escaped(capsys, base_config, merge_config, code, expected):
    """MERGE-22, MERGE-86: no repository text in a declaration or a refusal can hide, split or forge a line, and a
    refused job's name prints escaped."""
    gh = FakeGh(checks=SONAR_GREEN, base_config=base_config, merge_config=merge_config)
    assert drive(gh)[0] == code
    streams = capsys.readouterr()
    assert [line for line in streams.out.splitlines() if expected in line] != []
    assert_every_line_starts_with_the_door([*streams.out.splitlines(), *streams.err.splitlines()])
    assert "\u202e" not in "".join(gh.comments)


@pytest.mark.parametrize(
    ("gh_kwargs", "event", "sentence"),
    [({}, "merge commit", "merges with a merge commit"), ({"rules": [[pull_request_rule("squash")]]}, "squash      ", "merges with a squash")],
    ids=["a merge commit", "a squash"],
)
def test_the_operator_reads_the_method_before_the_question_and_the_log_comment_names_it(gh_kwargs, event, sentence, capsys):
    """MERGE-4, MERGE-33: the method row says what the door lands with on the base branch, and the log comment
    carries the method."""
    gh, shown = FakeGh(**gh_kwargs), []
    drive(gh, when_asked=lambda: shown.append(capsys.readouterr()))
    assert (shown[0].out, shown[0].err) == (
        RELEASE_ROW + pull_row() + base_row() + checks_row() + loosenings_none() + legacy_row() + merge_row(event) + landing_row(),
        prompt(),
    )
    assert f"; {sentence}; " in gh.comments[0]


@pytest.mark.parametrize(
    ("gh_kwargs", "method"),
    [
        ({}, "merge"),
        ({"rules": [[pull_request_rule("squash")]]}, "squash"),
        ({"rules": [[LINEAR_HISTORY]]}, "squash"),
        ({"settings": settings_allowing("squash", "rebase")}, "squash"),
        ({"rules": [[pull_request_rule("merge", "squash"), pull_request_rule("squash", "rebase")]]}, "squash"),
        ({"rules": [[UNRELATED_RULE]]}, "merge"),
        ({"rules": [[]]}, "merge"),
        ({"rules": [[{"type": "pull_request", "parameters": {}}]]}, "merge"),
        ({"rules": [[{"type": "pull_request"}]]}, "merge"),
        ({"rules": [[UNRELATED_RULE, LINEAR_HISTORY]]}, "squash"),
        ({"rules": [[pull_request_rule("merge", "squash")], [LINEAR_HISTORY]]}, "squash"),
        ({"base_ref": "release/1", "rules": [[pull_request_rule("squash")]]}, "squash"),
        ({"base_ref": "rel#x", "rules": [[pull_request_rule("squash")]]}, "squash"),
        ({"settings": settings_allowing("merge"), "rules": [[pull_request_rule("merge", "rebase")]]}, "merge"),
    ],
    ids=[
        "both sources allow a merge commit", "a rule allows only squash", "linear history",
        "settings forbid merge commits", "two rules intersect to squash", "an unrelated rule only", "no rules",
        "allowed_merge_methods absent", "no parameters", "rules without a parameters key", "two pages both count",
        "a non-default base branch", "a base branch with a hash", "a merge commit where only it and rebase are allowed",
    ],
)
def test_the_door_merges_with_a_method_the_settings_and_every_rule_allow(gh_kwargs, method):
    """MERGE-30, MERGE-31: the door merges with the first of a merge commit and a squash that the settings and every
    rule allow, and pins the head."""
    gh = FakeGh(**gh_kwargs)
    assert drive(gh)[0] == 0
    assert (gh.merge_calls, [body["merge_method"] for body in gh.merge_bodies]) == ([merge_request()[0]], [method])
    compare_read = next(call for call in gh.calls if "/compare/" in " ".join(call))
    assert compare_read == ("api", f"repos/{REPO}/compare/refs/heads/{quote(gh.base_ref, safe='/')}...{SHA}?per_page=1")
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
        ({"settings": {}}, "the repository settings withhold or malform which merge methods they allow"),
        ({"settings": {**ALL_METHODS_ALLOWED, "allow_squash_merge": "yes"}}, "the repository settings withhold or malform which merge methods they allow"),
        ({"rules": [["not a rule"]]}, malformed_rules()),
        ({"rules": [[{"type": "pull_request", "parameters": "x"}]]}, malformed_rules()),
        ({"rules": [[{"type": "pull_request", "parameters": {"allowed_merge_methods": "squash"}}]]}, malformed_rules()),
        ({"rules": [[pull_request_rule("fast-forward")]]}, malformed_rules()),
        ({"rules": [[{"parameters": {"allowed_merge_methods": ["merge"]}}]]}, malformed_rules()),
        ({"rules": [[{"type": 5}]]}, malformed_rules()),
        ({"rules": [[{"type": "pull_request", "parameters": None}]]}, malformed_rules()),
        ({"rules": [[{"type": "required_linear_history", "parameters": None}]]}, malformed_rules()),
        ({"rules": [[{"type": "deletion", "parameters": "x"}]]}, malformed_rules()),
        ({"rules": [[{"type": "pull_request", "parameters": {"allowed_merge_methods": None}}]]}, malformed_rules()),
        ({"rules_raw": "not json"}, malformed_rules()),
        ({"rules_raw": json.dumps({"type": "creation"})}, malformed_rules()),
        ({"rules_raw": json.dumps([{"type": "creation"}])}, malformed_rules()),
        ({"rules_raw": json.dumps([['{"type":"creation"}']])}, malformed_rules()),
        ({"rules_raw": json.dumps([[5]])}, malformed_rules()),
        ({"rules_error": "gh api failed: HTTP 403: Resource not accessible"}, "cannot read the branch rules of main: gh api failed: HTTP 403: Resource not accessible"),
        (
            {"rules_error": "gh api failed: HTTP 403: Upgrade to GitHub Pro or Team to enable this feature."},
            "cannot read the branch rules of main: gh api failed: HTTP 403: Upgrade to GitHub Pro or Team to enable this feature.",
        ),
    ],
    ids=[
        "sources do not intersect", "settings allow only rebase", "a rule allows only rebase",
        "allowed_merge_methods empty", "settings allow nothing", "settings withheld", "settings malformed",
        "a rule is no object", "parameters malformed", "methods malformed", "an unknown method",
        "a rule without a type", "a rule type that is no string", "pull_request parameters null",
        "linear history parameters null", "an unrelated rule with parameters that are no object", "methods null",
        "a line that is no JSON", "the answer is an object", "a page is an object", "a rule is a JSON string",
        "a rule is a number", "generic 403", "plan 403",
    ],
)
def test_the_door_refuses_before_the_gate_when_no_method_is_usable(gh_kwargs, line, capsys):
    """MERGE-16, MERGE-32: a repository that allows no merge commit or squash, or whose rules cannot be read, is exit 2
    with a named line, and neither the gate nor the merge is touched."""
    gh = FakeGh(**gh_kwargs)
    code, answers = drive(gh)
    streams = capsys.readouterr()
    assert (code, streams.out, streams.err, answers) == (
        2,
        RELEASE_ROW + pull_row() + base_row() + checks_row() + loosenings_none() + legacy_row() + f"  ✗ merge         not judged     {line}\n",
        not_judged_pair(),
        [],
    )
    changed_nothing(gh)


def test_a_method_github_refuses_at_merge_time_leaves_the_gate_on_and_the_pull_request_unmerged(capsys):
    """MERGE-29, MERGE-31, MERGE-34: a squash that GitHub refuses at merge time leaves the gate switched on again and
    the pull request unmerged."""
    gh = FakeGh(rules=[[pull_request_rule("squash")]], fail_on=["merge"])
    assert drive(gh)[0] == 2
    assert (gh.merge_calls, gh.gate_values) == ([merge_request(method="squash")[0]], ["false", "true"])
    assert "not merged" in capsys.readouterr().out
