"""The verdict's frame: a row for the base, the jobs, the loosenings and the declarations that match nothing, one
closing line with its duration, and `next:` on stderr only after a red of declared loosenings alone."""

import json
import shlex
import subprocess

import pytest

from quality import cli
from quality.config import CONFIG_PATH
from tests.support.git_repositories import GIT_IDENTITY, head_commit, put
from tests.support.verdict import CHECKS_GREEN, declaration, gate_config, jobs_page, without_release_row

pytestmark = pytest.mark.integration

GREEN_JOBS = "  ✓ jobs          success        1 mandatory job · 1 gate job"
NO_LOOSENINGS = "  ✓ loosenings    none"
DECLARED_B = [
    '    ! mandatory job "B" removed · overnightworks/songmaker#1345 of 2026-10-20',
    "        reason: ruled",
]
ONLY_DECLARED = "! red · only declared loosenings · only the operator merges it · <1s"
SET_BY_THE_GATE = "next: quality verdict runs as gate.yml's verdict step, which sets it"


def ticks(*seconds):
    """A clock that reads each of `seconds` in turn."""
    return iter(seconds).__next__


def needs(**results):
    return json.dumps({name: {"result": result} for name, result in results.items()})


@pytest.fixture
def frame(capsys, run_env):
    """`quality verdict` over a fetcher that answers every read with `page`: its exit, both streams and the step summary."""
    _, summary = run_env

    def run(page=CHECKS_GREEN, clock=None):
        clock = clock or ticks(0, 0)
        code = cli.main(["verdict"], fetcher_for=lambda token: lambda url: (page, None), sleep=lambda seconds: None, clock=clock)
        printed = capsys.readouterr()
        return code, without_release_row(printed.out), printed.err, without_release_row(summary.read_text()) if summary.is_file() else None

    return run


@pytest.fixture
def based(pull_request):
    """Commits `base_config` as the pull request's base, writes `head_config` as its head, and returns the base row."""
    repo, commit_base = pull_request

    def arrange(base_config, head_config=None):
        sha = commit_base(None, base_config)
        put(repo / CONFIG_PATH, base_config if head_config is None else head_config)
        return f"  · base          compared       main {sha[:7]} · head 5d1e0b7"

    return arrange


def lines(*rows):
    return "".join(f"{row}\n" for row in rows)


def block(text):
    return f"```\n{text}```\n"


def test_a_green_pull_request_sums_its_jobs_in_one_row(frame, based):
    """VERDICT-25, VERDICT-27, VERDICT-30, VERDICT-32, VERDICT-35."""
    base_row = based(gate_config("checks"))
    out = lines(base_row, GREEN_JOBS, NO_LOOSENINGS, "✓ green · 1 mandatory job · <1s")
    assert frame() == (0, out, "", block(out))


@pytest.mark.parametrize(
    ("entries", "row"),
    [
        pytest.param([("lint", "completed", "failure")], "  ! lint             failure        attempt 1", id="failure"),
        pytest.param([("lint", "completed", "cancelled")], "  ! lint             cancelled      attempt 1", id="cancelled"),
        pytest.param([("lint", "completed", "skipped")], "  ! lint             skipped        attempt 1", id="skipped"),
        pytest.param([("lint", "completed", "timed_out")], "  ! lint             timed out      attempt 1", id="timed out"),
        pytest.param([("lint", "completed", "action_required")], "  ! lint             action required attempt 1",
                     id="action required"),
        pytest.param([("lint", "completed", "startup_failure")], "  ! lint             concluded      startup_failure",
                     id="an unknown conclusion"),
        pytest.param([], "  ! lint             missing        not in the run", id="missing"),
        pytest.param([("lint", "completed", "success"), ("lint", "completed", "failure")],
                     "  ! lint             several entries 2 entries in attempt 1, not all success", id="several entries"),
    ],
)
def test_a_job_that_is_not_green_puts_every_job_on_its_row(frame, pull_request, entries, row):
    """VERDICT-6, VERDICT-7, VERDICT-8, VERDICT-28, VERDICT-29, VERDICT-33."""
    _, commit_base = pull_request
    sha = commit_base(None, gate_config("checks", "lint"))
    out = lines(
        f"  · base             compared       main {sha[:7]} · head 5d1e0b7",
        "  ✓ checks           success",
        row,
        "  ✓ quality / check  success",
        "  ✓ loosenings       none",
        "! red · 1 of 2 mandatory jobs failed · <1s",
    )
    assert frame(jobs_page(("checks", "completed", "success"), *entries)) == (1, out, "", block(out))


def test_a_gate_job_stands_as_its_check_name(frame, pull_request, monkeypatch):
    """VERDICT-14, VERDICT-28, VERDICT-33."""
    _, commit_base = pull_request
    sha = commit_base(None, gate_config("check"))
    monkeypatch.setenv("GATE_NEEDS", needs(check="success", sonar="failure"))
    out = lines(
        f"  · base             compared       main {sha[:7]} · head 5d1e0b7",
        "  ✓ check            success",
        "  ✓ quality / check  success",
        "  ! quality / sonar  failure",
        "  ✓ loosenings       none",
        "! red · 1 gate job failed · <1s",
    )
    assert frame(jobs_page(("check", "completed", "success"))) == (1, out, "", block(out))


def test_the_base_row_names_the_base_and_the_pull_requests_head(frame, pull_request, monkeypatch):
    """VERDICT-25."""
    repo, commit_base = pull_request
    base = commit_base(None, gate_config("checks"))
    subprocess.run(["git", "-C", str(repo), *GIT_IDENTITY, "commit", "-q", "--allow-empty", "-m", "merge"], check=True)
    merge = head_commit(repo)
    head = "9f3c2a1" + "e" * 33
    monkeypatch.setenv("GATE_HEAD_SHA", head)
    monkeypatch.setenv("GITHUB_SHA", merge)
    _, out, _, _ = frame()
    assert (out.splitlines()[0], merge[:7] in out) == (f"  · base          compared       main {base[:7]} · head 9f3c2a1", False)


def test_a_push_has_no_base_and_no_loosenings_row(frame, monkeypatch):
    """VERDICT-26, VERDICT-27, VERDICT-30, VERDICT-32."""
    monkeypatch.setenv("GATE_PULL_REQUEST", "")
    monkeypatch.setenv("GATE_HEAD_SHA", "")
    out = lines(
        "  · base          none           a push or merge group: no loosening judged",
        GREEN_JOBS,
        "✓ green · 1 mandatory job · <1s",
    )
    assert frame() == (0, out, "", block(out))


def test_an_only_declared_red_names_the_door_on_stderr(frame, based, monkeypatch):
    """VERDICT-30, VERDICT-62, VERDICT-63."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/widgets")
    base_row = based(gate_config("checks", "B"), gate_config("checks", declared=(declaration('mandatory job "B" removed'),)))
    out = lines(base_row, GREEN_JOBS, "  ! loosenings    1 declared", *DECLARED_B, ONLY_DECLARED)
    err = "next: quality merge acme/widgets#7\n"
    assert frame() == (1, out, err, block(out + err))


def test_the_next_command_parses_back_to_the_pull_request_the_run_names(frame, based, monkeypatch):
    """VERDICT-63."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/gadgets")
    monkeypatch.setenv("GATE_PULL_REQUEST", "12")
    based(gate_config("checks", "B"), gate_config("checks", declared=(declaration('mandatory job "B" removed'),)))
    _, _, err, _ = frame()
    program, *words = shlex.split(err.removeprefix("next: "))
    named = cli.read_invocation(words).pull_request
    assert (program, named.repository, named.number) == ("quality", "acme/gadgets", 12)


def test_an_undeclared_loosening_is_red_without_next(frame, based):
    """VERDICT-30, VERDICT-33."""
    base_row = based(gate_config("checks", "B"), gate_config("checks"))
    out = lines(
        base_row,
        "  ✓ jobs          success        2 mandatory jobs · 1 gate job",
        "  ! loosenings    1 undeclared",
        '    ! mandatory job "B" removed',
        "! red · 1 undeclared loosening · <1s",
    )
    assert frame(jobs_page(("checks", "completed", "success"), ("B", "completed", "success"))) == (1, out, "", block(out))


def test_undeclared_and_declared_loosenings_share_one_row(frame, based):
    """VERDICT-30, VERDICT-33."""
    head = gate_config("checks", declared=(declaration("legacy fail → report"),))
    base_row = based(gate_config("checks", "B", legacy="fail"), head)
    out = lines(
        base_row,
        "  ✓ jobs          success        2 mandatory jobs · 1 gate job",
        "  ! loosenings    1 undeclared   1 declared",
        '    ! mandatory job "B" removed',
        "    ! legacy fail → report · overnightworks/songmaker#1345 of 2026-10-20",
        "        reason: ruled",
        "! red · 1 undeclared loosening · 1 declared loosening · <1s",
    )
    assert frame(jobs_page(("checks", "completed", "success"), ("B", "completed", "success"))) == (1, out, "", block(out))


def test_a_declaration_that_matches_nothing_is_its_row(frame, based):
    """VERDICT-31, VERDICT-33."""
    base_row = based(gate_config("checks"), gate_config("checks", declared=(declaration("nothing such"),)))
    out = lines(
        base_row,
        GREEN_JOBS,
        NO_LOOSENINGS,
        "  ! declarations  1 unmatched",
        "    ! nothing such · overnightworks/songmaker#1345 of 2026-10-20",
        "        reason: ruled",
        "! red · 1 declaration unmatched · <1s",
    )
    assert frame() == (1, out, "", block(out))


def test_a_refusal_after_judged_reads_shows_their_rows_before_the_pair(frame, pull_request):
    """READ-4, VERDICT-1, VERDICT-21, VERDICT-35."""
    _, commit_base = pull_request
    sha = commit_base(None, gate_config("checks", "lint"))
    out = lines(
        '  · run              13 reads       GitHub first listed "checks" (attempt 1) as in_progress',
        f"  · base             compared       main {sha[:7]} · head 5d1e0b7",
        "  ! checks           running        attempt 1 · in_progress",
        "  ! lint             failure        attempt 1",
        "  ✓ quality / check  success",
        "  ✓ loosenings       none",
    )
    err = lines(
        'error: GitHub still lists "checks" (attempt 1) as in_progress after 13 reads · the gate starts after every '
        "mandatory job, so this answer is stale",
        "next:",
        "    re-run this run's failed jobs",
        "    a mandatory job missing from the caller job's needs: add it there",
    )
    page = jobs_page(("checks", "in_progress", None), ("lint", "completed", "failure"))
    assert frame(page) == (2, out, err, block(out + err))


def test_rows_share_the_widest_subject(frame, pull_request):
    """VERDICT-22."""
    _, commit_base = pull_request
    long = "Acceptance evidence (integration)"
    sha = commit_base(None, gate_config("checks", long))
    out = lines(
        f"  · base                               compared       main {sha[:7]} · head 5d1e0b7",
        "  ✓ checks                             success",
        "  ! Acceptance evidence (integration)  failure        attempt 1",
        "  ✓ quality / check                    success",
        "  ✓ loosenings                         none",
        "! red · 1 of 2 mandatory jobs failed · <1s",
    )
    page = jobs_page(("checks", "completed", "success"), (long, "completed", "failure"))
    assert frame(page) == (1, out, "", block(out))


def test_a_job_name_with_a_line_feed_prints_as_one_escaped_row_in_log_and_summary(frame, run_env):
    """VERDICT-54."""
    repo, _ = run_env
    put(repo / CONFIG_PATH, gate_config("checks", "a\n::error::b"))
    out = lines(
        "  · base             none           a push or merge group: no loosening judged",
        "  ✓ checks           success",
        "  ! a\\x0a::error::b  missing        not in the run",
        "  ✓ quality / check  success",
        "! red · 1 of 2 mandatory jobs failed · <1s",
    )
    assert frame() == (1, out, "", block(out))


def test_the_closing_line_counts_what_applies(frame, based, monkeypatch):
    """VERDICT-33."""
    monkeypatch.setenv("GATE_NEEDS", needs(check="success", sonar="failure"))
    head = gate_config("checks", declared=(declaration("legacy fail → report"), declaration("nothing such")))
    based(gate_config("checks", "B", legacy="fail"), head)
    _, out, _, _ = frame(jobs_page(("checks", "completed", "failure"), ("B", "completed", "success")))
    assert out.splitlines()[-1] == (
        "! red · 1 of 2 mandatory jobs failed · 1 gate job failed · 1 undeclared loosening · 1 declaration unmatched"
        " · 1 declared loosening · <1s"
    )


def test_the_duration_runs_from_the_door_start(frame):
    """VERDICT-32, VERDICT-34."""
    _, out, _, _ = frame(clock=ticks(5, 9))
    assert out.splitlines()[-1] == "✓ green · 1 mandatory job · 4s"


@pytest.mark.parametrize(
    ("name", "value", "fact"),
    [
        pytest.param("GATE_PULL_REQUEST", "0", "GATE_PULL_REQUEST is not a pull request number", id="number 0"),
        pytest.param("GATE_PULL_REQUEST", "seven", "GATE_PULL_REQUEST is not a pull request number", id="number as a word"),
        pytest.param("GATE_HEAD_SHA", "5d1e0b7", "GATE_HEAD_SHA is not a commit sha", id="head sha too short"),
        pytest.param("GATE_HEAD_SHA", "5D1E0B7" * 5 + "5D1E0", "GATE_HEAD_SHA is not a commit sha", id="head sha in capitals"),
    ],
)
def test_a_malformed_pull_request_variable_is_its_pair(frame, run_env, monkeypatch, name, value, fact):
    """VERDICT-44, VERDICT-45."""
    repo, _ = run_env
    token = "hunter2-token"
    monkeypatch.setenv("GITHUB_TOKEN", token)
    monkeypatch.setenv("GATE_BASE_SHA", head_commit(repo))
    monkeypatch.setenv(name, value)
    pair = lines(f"error: {fact}", SET_BY_THE_GATE)
    code, out, err, summary = frame()
    assert ((code, out, err, summary), token in out + err + summary) == ((2, "", pair, block(pair)), False)


@pytest.fixture
def raw_frame(capsys, run_env):
    """`quality verdict`'s exit and whole stdout and stderr, its release row included."""

    def run():
        code = cli.main(["verdict"], fetcher_for=lambda token: lambda url: (CHECKS_GREEN, None), sleep=lambda seconds: None, clock=ticks(0, 0))
        printed = capsys.readouterr()
        return code, printed.out, printed.err

    return run


@pytest.mark.parametrize(
    ("version", "row"),
    [
        pytest.param("0.10.0", "  · release       v0.10.0        this gate run", id="at a release"),
        pytest.param("0.10.1.dev3+g5d1e0b7", "  · release       this checkout  0.10.1.dev3+g5d1e0b7 · quality judges itself", id="in quality's own run"),
    ],
)
def test_the_first_row_names_the_release_of_the_gate_run(raw_frame, based, monkeypatch, version, row):
    """VERDICT-23, VERDICT-24."""
    monkeypatch.setattr("quality.verdict.__version__", version)
    based(gate_config("checks"))
    assert raw_frame()[1].splitlines()[0] == row


def test_a_refusal_frame_opens_with_the_release_row(raw_frame, monkeypatch):
    """VERDICT-23."""
    monkeypatch.setattr("quality.verdict.__version__", "0.10.0")
    monkeypatch.delenv("GATE_NEEDS")
    code, out, err = raw_frame()
    assert (code, out, err.startswith("error: ")) == (2, "  · release       v0.10.0        this gate run\n", True)
