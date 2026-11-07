import json
from datetime import UTC, datetime
from urllib.parse import quote

import pytest

from quality import cli
from quality.ratchet import BASELINE_PATH
from quality.sonar import SonarHttpError, basic_authorization
from tests.support.advisories import allowance_config
from tests.support.check import assert_every_line_starts_with_the_door
from tests.support.git_repositories import git_pull
from tests.support.merge import (
    ALL_METHODS_ALLOWED,
    B_REMOVED,
    GATE_CONFIG,
    LEGACY_RED,
    MERGE_SHA,
    ONE_JOB,
    REPO,
    SHA,
    SONAR_GREEN,
    TWO_JOBS,
    FakeGh,
    changed_nothing,
    closing_line,
    declaring,
    drive,
    landing_row,
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


def test_logs_who_and_the_legacy_count_on_the_pull_request():
    """#37 rule 5: the comment names who, the legacy count and the sequence."""
    gh = FakeGh()
    drive(gh)
    assert "@felix" in gh.comments[0]
    assert "4 legacy" in gh.comments[0]
    assert "off → merge → on" in gh.comments[0]


def test_shows_repository_head_and_legacy_count_before_asking(capsys):
    """#37 rule 3: it shows, then asks."""
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
            {"checks": SONAR_GREEN, "base_config": ONE_JOB, "head_config": ONE_JOB + declaring(ADDS_QWEN) + allowance_config(reason=qwen_reason())},
            {}, f"declared loosening matches nothing: {ADDS_QWEN}",
        ),
        ({"head_config": GATE_CONFIG.replace("fail", "report")}, {}, "loosened (undeclared): legacy fail"),
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
    assert "not judged" in capsys.readouterr().out
    changed_nothing(gh)


def test_cannot_judge_without_the_merge_commit_the_jobs_ran_on(capsys):
    """#37 rule 2: the config the jobs ran on is unknown, so exit 2 and nothing changed."""
    gh = FakeGh(merge_sha=None)
    code, answers = drive(gh, sonar())
    assert code == 2
    assert "not judged" in capsys.readouterr().out
    changed_nothing(gh)
    assert answers == []


def test_names_the_finding_a_pull_request_adds(capsys):
    """#37 rule 2: a pull request that adds a finding is refused, naming it."""
    drive(FakeGh(), sonar(new=1))
    assert "a.py:3" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("gh_kwargs", "terminal", "repo", "code"),
    [
        ({}, False, REPO, 1), ({}, True, "not-a-repo", 2), ({}, True, "acme/widg\u0435ts", 2), ({"admin": False}, True, REPO, 2),
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


@pytest.mark.integration
@pytest.mark.parametrize(
    ("base_baseline", "merge_baseline", "code", "message"),
    [
        (BASELINE_82, BASELINE_90, 1, "loosened (undeclared): size baseline raises function_lines src/quality/cli.py main 82 -> 90"),
        (BASELINE_82, BASELINE_82, 0, closing_line()),
        (BASELINE_82, None, 1, "loosened (undeclared): quality/baseline.toml removed"),
        (None, None, 0, closing_line()),
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


def test_a_malformed_baseline_at_the_merge_commit_cannot_be_judged(capsys):
    """#48 rule 4: the door names the commit whose baseline it cannot read, and changes nothing."""
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
        "cell 14 songmaker's removed job", "cell 27 a declared legacy fail → report", "#116 cell 23 an allowance",
        "#116 cell 28 an allowance next to a declared removal",
    ],
)
def test_a_declared_loosening_merges_through_the_door_without_sonar(
    capsys, monkeypatch, base_config, merge_config, declared, rows
):
    """#103 rule 4 (E6, E7), cells 14 and 27, and #116 R2, E7: with Sonar green and only declared loosenings at the
    merge commit, an allowance's own among them, the door shows each declaration whole with its reason below, then
    asks, merges with the head pinned, and logs it, with no SonarCloud request."""
    monkeypatch.delenv("SONAR_TOKEN", raising=False)
    gh, transport, shown = FakeGh(checks=SONAR_GREEN, base_config=base_config, merge_config=merge_config, body="Work-Item: #9"), recording(sonar()), []

    def read_line():
        shown.append(capsys.readouterr())
        return "yes\n"

    code = cli.main(["merge", f"{REPO}#7"], gh=gh, transport=transport, terminal=cli.Terminal(read_line, lambda: True))
    assert (code, transport.sent, gh.sequence) == (0, [], ["gate false", "merge", "gate true", "comment"])
    assert (shown[0].out, shown[0].err) == (
        pull_row() + checks_row("quality / verdict is") + rows + merge_row() + landing_row("#9"),
        prompt(),
    )
    assert gh.merge_calls == [("pr", "merge", "7", "--repo", REPO, "--merge", "--match-head-commit", SHA, "--body-file", "-")]
    assert "merges with a merge commit; quality_gate off → merge → on." in gh.comments[0]
    assert all(f"declared loosening ({ruling}): {what} - reason: {reason}" in gh.comments[0] for what, ruling, reason in declared)


def test_the_door_names_each_of_79_allowances_within_one_comment(capsys):
    """#116 cell 29: presentator#372's 79 allowances are 79 door lines, and the log comment stays within GitHub's
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
    """#103 rule 4 (E8, ruled 21.10.2026), cell 21: a red Sonar on legacy findings next to a declared loosening merges
    under every legacy condition, showing both."""
    gh = FakeGh(base_config=GATE_CONFIG.replace('["checks"]', '["checks", "B"]'), head_config=GATE_CONFIG + declaring(B_REMOVED))
    shown = []
    assert drive(gh, when_asked=lambda: shown.append(capsys.readouterr()))[0] == 0
    assert (shown[0].out, shown[0].err) == (
        pull_row() + checks_row() + declared_row(1) + removed_b_lines() + legacy_row() + merge_row()
        + landing_row(),
        prompt(),
    )
    assert f'4 legacy finding(s); declared loosening ({songmaker_ruling()}): {B_REMOVED}' in gh.comments[0]


def test_a_red_sonar_run_next_to_a_green_one_takes_the_legacy_path(capsys):
    """#103 rule 4, cell 28: Sonar is green only when every run is green, so one red run is judged for legacy findings."""
    assert drive(FakeGh(extra_runs=[("quality / sonar", "success")]))[0] == 0
    assert legacy_row() in capsys.readouterr().out


@pytest.mark.parametrize(
    ("base_config", "merge_config", "code", "expected"),
    [
        (TWO_JOBS, ONE_JOB + declaring(B_REMOVED, reason="ok\x1b[8m versteckt"), 0, "        reason: ok\\x1b[8m versteckt"),
        (TWO_JOBS, ONE_JOB + declaring(B_REMOVED, reason="ok\u202edetsurt"), 0, "        reason: ok\\u202edetsurt"),
        (TWO_JOBS, ONE_JOB + declaring(B_REMOVED, reason="ok\rforged"), 2, "needs a non-empty one-line reason"),
        (TWO_JOBS, ONE_JOB + declaring(B_REMOVED, reason="ok\nforged"), 2, "needs a non-empty one-line reason"),
        (ONE_JOB, '[gate]\nmandatory_jobs = ["checks", "a\\n::error::b"]\n', 1, 'mandatory job "a\\x0a::error::b" is missing'),
        (ONE_JOB, ONE_JOB + allowance_config("GHSA-\x1b[8m", "a\tb/uv.lock", reason="ok\x1b[8m versteckt"), 0,
         f"    ! advisory allowance adds GHSA-\\x1b[8m in a\\x09b/uv.lock until 2027-01-31 · {presentator_ruling()}"),
    ],
    ids=[
        "ESC in the reason", "bidi override in the reason", "carriage return in the reason", "line feed in the reason", "line feed in a refused job",
        "#116 cell 25 control characters in an allowance's id, lock and reason",
    ],
)
def test_every_line_the_door_prints_is_escaped(capsys, base_config, merge_config, code, expected):
    """#103 rule 4 (E9), cell 23: no repository text in a declaration or a refusal can hide, split or forge a line."""
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
    """The method row says what the door lands with on the base branch, and the log comment carries the method."""
    gh, shown = FakeGh(**gh_kwargs), []
    drive(gh, when_asked=lambda: shown.append(capsys.readouterr()))
    assert (shown[0].out, shown[0].err) == (
        pull_row() + checks_row() + loosenings_none() + legacy_row() + merge_row(event) + landing_row(),
        prompt(),
    )
    assert f"; {sentence}; " in gh.comments[0]


def merge_call(flag):
    return ("pr", "merge", "7", "--repo", REPO, flag, "--match-head-commit", SHA, "--body-file", "-")


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
    streams = capsys.readouterr()
    assert (code, streams.out, streams.err, answers) == (
        2,
        pull_row() + checks_row() + loosenings_none() + legacy_row() + f"  ✗ merge         not judged     {line}\n",
        not_judged_pair(),
        [],
    )
    changed_nothing(gh)


def test_a_method_github_refuses_at_merge_time_leaves_the_gate_on_and_the_pull_request_unmerged(capsys):
    """#146 cell 14: the selected squash is refused, so the gate is switched on again and the outcome is not merged."""
    gh = FakeGh(rules=[[pull_request_rule("squash")]], fail_on=["merge"])
    assert drive(gh)[0] == 2
    assert (gh.merge_calls, gh.gate_values) == ([merge_call("--squash")], ["false", "true"])
    assert "not merged" in capsys.readouterr().out
