"""The verdict's door: the variables gate.yml's verdict step sets, every refusal as the `error:`/`next:` pair, and the
step summary as one code block of the lines the run printed."""

import json
import sys

import pytest

from quality import cli
from quality.config import CONFIG_PATH
from quality.ratchet import BASELINE_PATH
from tests.support.git_repositories import head_commit
from tests.support.verdict import CHECKS_GREEN, jobs_page

pytestmark = pytest.mark.integration

SET_BY_THE_GATE = "next: quality verdict runs as gate.yml's verdict step, which sets it"
REREAD_NOTE = 'verdict: read the run\'s jobs 13 times - GitHub first listed "checks" (attempt 1) as in_progress\n'
STALE_PAIR = (
    'error: GitHub still lists "checks" (attempt 1) as in_progress after 13 reads · the gate starts after every '
    "mandatory job, so this answer is stale\n"
    "next:\n"
    "    re-run this run's failed jobs\n"
    "    a mandatory job missing from the caller job's needs: add it there\n"
)


@pytest.fixture
def door(capsys, run_env):
    """`quality verdict <words>` over a fetcher that answers every read with `page`: its exit, both streams and the
    step summary as written (None where it was not)."""
    _, summary = run_env

    def run(*words: str, page: str = CHECKS_GREEN) -> tuple[int, str, str, str | None]:
        code = cli.main(["verdict", *words], fetcher_for=lambda token: lambda url: (page, None), sleep=lambda seconds: None)
        printed = capsys.readouterr()
        return code, printed.out, printed.err, summary.read_text() if summary.is_file() else None

    return run


def refused(pair: str) -> tuple[int, str, str, str]:
    """A refusal before any judged line: nothing on stdout, the pair on stderr and, in a code block, in the summary."""
    return 2, "", pair, f"```\n{pair}```\n"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        *(pytest.param(name, None, id=name) for name in (
            "GATE_NEEDS", "GITHUB_RUN_ATTEMPT", "GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_TOKEN", "GITHUB_BASE_REF",
        )),
        pytest.param("GITHUB_TOKEN", "", id="an empty token"),
    ],
)
def test_a_variable_the_gate_sets_that_is_missing_is_its_pair(monkeypatch, run_env, door, name, value):
    repo, _ = run_env
    monkeypatch.setenv("GATE_BASE_SHA", head_commit(repo))
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    assert door() == refused(f"error: {name} is not set\n{SET_BY_THE_GATE}\n")


@pytest.mark.parametrize(
    ("name", "value", "fact"),
    [
        pytest.param("GITHUB_REPOSITORY", "widgets", "GITHUB_REPOSITORY is not an owner/name", id="repository without an owner"),
        pytest.param("GITHUB_REPOSITORY", "\u0430cme/widgets", "GITHUB_REPOSITORY is not an owner/name", id="repository with a lookalike letter"),
        pytest.param("GITHUB_REPOSITORY", "acme/wid gets", "GITHUB_REPOSITORY is not an owner/name", id="repository with a space"),
        pytest.param("GITHUB_RUN_ID", "first", "GITHUB_RUN_ID is not a run id", id="run id as a word"),
        pytest.param("GITHUB_RUN_ID", "0", "GITHUB_RUN_ID is not a run id", id="run id 0"),
        pytest.param("GITHUB_RUN_ATTEMPT", "first", "GITHUB_RUN_ATTEMPT is not a run attempt", id="attempt as a word"),
        pytest.param("GITHUB_RUN_ATTEMPT", "1" * 5000, "GITHUB_RUN_ATTEMPT is not a run attempt", id="attempt past the integer digit limit"),
        pytest.param("GITHUB_RUN_ATTEMPT", "0", "GITHUB_RUN_ATTEMPT is not a run attempt", id="attempt 0"),
        pytest.param("GATE_NEEDS", "[1]", "GATE_NEEDS is not the needs of gate.yml's verdict job", id="needs as a list"),
        pytest.param("GATE_NEEDS", '{"check": "success"}', "GATE_NEEDS is not the needs of gate.yml's verdict job", id="a need as text"),
        pytest.param("GATE_NEEDS", '{"check": {}}', "GATE_NEEDS is not the needs of gate.yml's verdict job", id="a need without result"),
        pytest.param("GATE_NEEDS", "{", "GATE_NEEDS is not the needs of gate.yml's verdict job", id="needs not JSON"),
        pytest.param("GATE_BASE_SHA", "4e1a9c2", "GATE_BASE_SHA is not a commit sha", id="base sha too short"),
        pytest.param("GATE_BASE_SHA", "4E1A9C2" * 5 + "4E1A9", "GATE_BASE_SHA is not a commit sha", id="base sha in capitals"),
        pytest.param("GITHUB_BASE_REF", "a b", "GITHUB_BASE_REF is not a branch name", id="base ref with a space"),
    ],
)
def test_a_malformed_variable_is_its_pair_and_never_its_value(monkeypatch, run_env, door, name, value, fact):
    repo, _ = run_env
    token = "hunter2-token"
    monkeypatch.setenv("GITHUB_TOKEN", token)
    monkeypatch.setenv("GATE_BASE_SHA", head_commit(repo))
    monkeypatch.setenv(name, value)
    result = door()
    assert result == refused(f"error: {fact}\n{SET_BY_THE_GATE}\n")
    assert token not in "".join(result[1:])


def test_a_base_branch_in_a_step_prints_escaped(monkeypatch, pull_request, door):
    repo, commit_base = pull_request
    commit_base(None, '[gate]\nmandatory_jobs = ["checks"]\nsonar = "false"\n')
    (repo / CONFIG_PATH).write_text('[gate]\nmandatory_jobs = ["checks"]\n')
    monkeypatch.setenv("GITHUB_BASE_REF", "ma\u202ein")
    _, out, err, _ = door()
    step = "    fix it on ma\\u202ein first, then update the pull request's branch with ma\\u202ein"
    assert err.splitlines()[-1] == step
    assert "\u202e" not in out + err


def test_a_head_baseline_that_cannot_be_read_names_its_restore_step_apart(run_env, door):
    repo, _ = run_env
    (repo / BASELINE_PATH).write_text("[ccn")
    assert door() == refused(
        f"error: {BASELINE_PATH} at the head: {BASELINE_PATH} is not valid TOML (Expected ']' at the end of a table "
        "declaration (at end of document))\n"
        f"next: restore {BASELINE_PATH} from git, or repair it by hand\n"
    )


@pytest.mark.parametrize(
    ("config", "page", "judged"),
    [
        pytest.param(
            '[gate]\nmandatory_jobs = ["checks"]\n', jobs_page(("checks", "in_progress", None)), REREAD_NOTE, id="only running",
        ),
        pytest.param(
            '[gate]\nmandatory_jobs = ["checks", "lint"]\n',
            jobs_page(("checks", "in_progress", None), ("lint", "completed", "failure")),
            REREAD_NOTE + 'mandatory job "lint" (attempt 1) concluded failure\n',
            id="running beside a failure",
        ),
    ],
)
def test_a_refusal_after_judged_reads_keeps_their_lines_on_stdout(run_env, door, config, page, judged):
    repo, _ = run_env
    (repo / CONFIG_PATH).write_text(config)
    assert door(page=page) == (2, judged, STALE_PAIR, f"```\n{judged}{STALE_PAIR}```\n")


@pytest.mark.parametrize(
    ("job", "earlier", "summary"),
    [
        pytest.param(
            "<!-- a", None, '```\nmandatory job "<!-- a" is missing from the run\nverdict: red\n```\n', id="a comment",
        ),
        pytest.param(
            "a```b", None, '````\nmandatory job "a```b" is missing from the run\nverdict: red\n````\n', id="a backtick run",
        ),
        pytest.param(None, "earlier\n", "earlier\n```\nverdict: green\n```\n", id="an earlier summary ending its line"),
        pytest.param(None, "earlier", "earlier\n```\nverdict: green\n```\n", id="an earlier summary without a final line feed"),
    ],
)
def test_the_step_summary_is_one_code_block_no_line_can_close(run_env, door, job, earlier, summary):
    repo, written = run_env
    if job is not None:
        (repo / CONFIG_PATH).write_text(f"[gate]\nmandatory_jobs = {json.dumps(['checks', job])}\n")
    if earlier is not None:
        written.write_text(earlier)
    assert door()[3] == summary


def test_an_unwritable_step_summary_adds_its_pair_after_the_run(monkeypatch, run_env, door, tmp_path):
    unwritable = tmp_path / "a-directory"
    unwritable.mkdir()
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(unwritable))
    assert door() == (
        2,
        "verdict: green\n",
        (
            f"error: GITHUB_STEP_SUMMARY is not writable · [Errno 21] Is a directory: '{unwritable}'\n"
            "next: fix what the error names, then re-run this run's failed jobs\n"
        ),
        None,
    )


def test_the_verdict_takes_v_and_prints_the_same(run_env, door):
    _, summary = run_env
    plain = door()
    summary.unlink()
    assert door("-v") == plain


def test_a_closed_stderr_moves_no_refusal_to_stdout(monkeypatch, door):
    monkeypatch.delenv("GATE_NEEDS")
    monkeypatch.setattr(sys, "stderr", None)
    assert door()[:2] == (2, "")
