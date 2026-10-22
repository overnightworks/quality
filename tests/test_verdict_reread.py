import http.client
import json
import urllib.error

import pytest
from conftest import put
from test_sonar import reply
from test_verdict import GREEN_GATE, declaration, fetch_jobs, gate_config, job

from quality import cli, verdict
from quality.config import CONFIG_PATH, LegacyMode
from quality.hygiene import CannotJudge
from quality.verdict import GateSettings, Side, judge


def running(state="in_progress", name="checks", attempt=1, leg=1):
    return job(name, attempt, state, None, leg)


def page(*jobs, total=None, link=None):
    listed = [
        {"id": entry.id, "name": entry.name, "run_attempt": entry.run_attempt, "status": entry.status,
         "conclusion": entry.conclusion}
        for entry in jobs
    ]
    return json.dumps({"total_count": len(listed) if total is None else total, "jobs": listed}), link


def bad_gateway():
    return verdict.GitHubServerError(502, "Bad Gateway", "u")


def http_error(status, reason):
    return urllib.error.HTTPError("u", status, reason, {}, None)


def failed_reads():
    """Each way a jobs read can fail, the type it becomes and its line."""
    return [
        pytest.param(http_error(502, "Bad Gateway"), verdict.GitHubServerError,
                     "GitHub answered HTTP 502 Bad Gateway for u", id="502"),
        pytest.param(http_error(500, "Internal Server Error"), verdict.GitHubServerError,
                     "GitHub answered HTTP 500 Internal Server Error for u", id="500"),
        pytest.param(http_error(403, "Forbidden"), CannotJudge,
                     "cannot read u: HTTP Error 403: Forbidden (does the caller grant actions: read?)", id="403"),
        pytest.param(http_error(404, "Not Found"), CannotJudge,
                     "cannot read u: HTTP Error 404: Not Found (does the caller grant actions: read?)", id="404"),
        pytest.param(http_error(401, "Unauthorized"), CannotJudge,
                     "cannot read u: HTTP Error 401: Unauthorized", id="401"),
        pytest.param(http_error(302, "Found"), CannotJudge, "cannot read u: HTTP Error 302: Found", id="302"),
        pytest.param(http.client.IncompleteRead(b"abcd", 96), CannotJudge,
                     "cannot read u: IncompleteRead(4 bytes read, 96 more expected)", id="truncated"),
        pytest.param(TimeoutError(), CannotJudge,
                     "GitHub did not answer u within 30s; re-run the verdict", id="read timeout"),
        pytest.param(urllib.error.URLError(TimeoutError()), CannotJudge,
                     "GitHub did not answer u within 30s; re-run the verdict", id="connect timeout"),
        pytest.param(urllib.error.URLError(ConnectionRefusedError(111, "refused")), CannotJudge,
                     "could not reach GitHub at u: <urlopen error [Errno 111] refused>; re-run the verdict",
                     id="refused"),
    ]


@pytest.mark.parametrize(("error", "kind", "line"), failed_reads())
def test_a_failed_jobs_read_names_its_cause(error, kind, line):
    """#85 rule 3, cells 13-16 (E5, E6): a 5xx is GitHub's, only 403 and 404 carry the actions hint, a timeout says
    re-run."""
    classified = verdict.unreadable_jobs("u", error)
    assert (type(classified), str(classified)) == (kind, line)


def test_a_502_jobs_page_is_named_as_githubs(stub_server):
    """#85 rule 3, cell 13 (E5): the real fetcher names GitHub's 502 as GitHub's answer, a type the re-read retries."""
    origin, _ = stub_server(reply(502, b""))
    with pytest.raises(CannotJudge) as failed:
        fetch_jobs(origin)
    assert (type(failed.value), str(failed.value)) == (
        verdict.GitHubServerError,
        f"GitHub answered HTTP 502 Bad Gateway for {origin}/jobs",
    )


def test_an_unsettled_job_prints_no_failure_and_is_never_green():
    """#85 rule 1 (E1): a judged job GitHub lists as running is unsettled, adds no failure line, and is never green."""
    result = judge(Side(GateSettings(("checks",), LegacyMode.REPORT), None), None, [running()], GREEN_GATE, 1, {})
    assert (result.unsettled, result.blocking, result.green) == (
        (verdict.Unsettled("checks", 1, "in_progress"),),
        (),
        False,
    )


@pytest.fixture
def reread(capsys, monkeypatch, run_env, pull_request):
    """#85 rule 6: the verdict's door over a scripted fetcher and a recording sleep. Each page read takes the next
    answer: a page, an error the fetcher raises, or a fetcher that reads the page itself."""
    (repo, summary), (_, commit_base) = run_env, pull_request

    def run(*answers, needs=(), attempt=1, base=None, head=None):
        if base:
            commit_base(None, base)
        put(repo / CONFIG_PATH, gate_config("checks") if head is None else head)
        gate_needs = {name: {"result": result} for name, result in {**GREEN_GATE, **dict(needs)}.items()}
        monkeypatch.setenv("GATE_NEEDS", json.dumps(gate_needs))
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", str(attempt))
        script, slept = iter(answers), []

        def fetch(url):
            answer = next(script)
            if isinstance(answer, Exception):
                raise answer
            return answer(url) if callable(answer) else answer

        code = cli.main(["verdict", str(repo)], fetcher_for=lambda token: fetch, sleep=slept.append)
        out, err = capsys.readouterr()
        assert (next(script, None), (out if code == 2 else err), summary.read_text()) == (None, "", out + err)
        return code, (out + err).splitlines(), sum(slept)

    return run


@pytest.mark.parametrize(
    ("answers", "options", "code", "expected", "slept"),
    [
        pytest.param((page(job()),), {}, 0, ["verdict: green"], 0, id="cell 1"),
        pytest.param(
            (page(running()), page(job())),
            {},
            0,
            ['verdict: read the run\'s jobs 2 times - GitHub first listed "checks" (attempt 1) as in_progress',
             "verdict: green"],
            10,
            id="cell 2",
        ),
        pytest.param(
            (page(running("queued")), page(running()), page(job())),
            {},
            0,
            ['verdict: read the run\'s jobs 3 times - GitHub first listed "checks" (attempt 1) as queued',
             "verdict: green"],
            20,
            id="cell 3",
        ),
        pytest.param(
            (page(running()), page(job(conclusion="failure"))),
            {},
            1,
            ['verdict: read the run\'s jobs 2 times - GitHub first listed "checks" (attempt 1) as in_progress',
             'mandatory job "checks" (attempt 1) concluded failure',
             "verdict: red"],
            10,
            id="cell 4",
        ),
        *(
            pytest.param(
                (page(running(state)), page(job())),
                {},
                0,
                [f'verdict: read the run\'s jobs 2 times - GitHub first listed "checks" (attempt 1) as {state}',
                 "verdict: green"],
                10,
                id=f"cell 6 {state}",
            )
            for state in ("waiting", "pending", "requested")
        ),
        pytest.param((page(running(name="quality / verdict"), job()),), {}, 0, ["verdict: green"], 0, id="cell 7"),
        pytest.param(
            (page(job(), running(name="B")),),
            {"base": gate_config("checks", "B"),
             "head": gate_config("checks", declared=(declaration('mandatory job "B" removed'),))},
            1,
            ['loosened (declared, overnightworks/songmaker#1345 of 2026-10-20): mandatory job "B" removed',
             ("verdict: red - only declared loosenings: only the operator's quality emergency-merge lands this pull "
             "request")],
            0,
            id="cell 8",
        ),
        pytest.param(
            (page(job(), running("queued", "B")), page(job(), job("B"))),
            {"base": gate_config("checks", "B"), "head": gate_config("checks", "B")},
            0,
            ['verdict: read the run\'s jobs 2 times - GitHub first listed "B" (attempt 1) as queued', "verdict: green"],
            10,
            id="cell 9",
        ),
        pytest.param(
            (page(job(), running(attempt=2)), page(job(), job(attempt=2), running(attempt=3))),
            {"attempt": 2},
            0,
            ['verdict: read the run\'s jobs 2 times - GitHub first listed "checks" (attempt 2) as in_progress',
             "verdict: green"],
            10,
            id="cell 10",
        ),
        pytest.param(
            (page(job(), running(leg=2)), page(job(), job(leg=2))),
            {},
            0,
            ['verdict: read the run\'s jobs 2 times - GitHub first listed "checks" (attempt 1) as in_progress',
             "verdict: green"],
            10,
            id="cell 11",
        ),
        pytest.param(
            (bad_gateway(), page(job())),
            {},
            0,
            ["verdict: read the run's jobs 2 times - GitHub first answered HTTP 502 Bad Gateway", "verdict: green"],
            10,
            id="cell 12",
        ),
        pytest.param(
            (page(running()), page(job())),
            {"needs": {"sonar": "failure"}},
            1,
            ['verdict: read the run\'s jobs 2 times - GitHub first listed "checks" (attempt 1) as in_progress',
             'gate job "sonar" result failure',
             "verdict: red"],
            10,
            id="cell 18",
        ),
        pytest.param(
            (page(job(), running(attempt=2)), page(job()), page(job(), job(attempt=2, conclusion="failure"))),
            {"attempt": 2},
            1,
            ['verdict: read the run\'s jobs 3 times - GitHub first listed "checks" (attempt 2) as in_progress',
             'mandatory job "checks" (attempt 2) concluded failure',
             "verdict: red"],
            20,
            id="cell 20",
        ),
        pytest.param(
            (
                page(job("other"), total=2, link='<n>; rel="next"'),
                bad_gateway(),
                page(job("other"), total=2, link='<n>; rel="next"'),
                page(job(), total=2),
            ),
            {},
            0,
            ["verdict: read the run's jobs 2 times - GitHub first answered HTTP 502 Bad Gateway", "verdict: green"],
            10,
            id="cell 25",
        ),
        pytest.param(
            (page(job(), job(conclusion="failure", leg=2), running(name="X")), page(job(), job("X"))),
            {"head": gate_config("checks", "X")},
            1,
            ['verdict: read the run\'s jobs 2 times - GitHub first listed "X" (attempt 1) as in_progress',
             'mandatory job "checks" (attempt 1) has 2 entries, not all success',
             "verdict: red"],
            10,
            id="cell 26",
        ),
        pytest.param(
            (page(job(conclusion="failure"), running(leg=2)), page(job(leg=2))),
            {},
            1,
            ['verdict: read the run\'s jobs 2 times - GitHub first listed "checks" (attempt 1) as in_progress',
             'mandatory job "checks" (attempt 1) has 2 entries, not all success',
             "verdict: red"],
            10,
            id="cell 27",
        ),
        pytest.param(
            (page(job(conclusion="failure"), running(name="X")), page(job(leg=2), job("X"))),
            {"head": gate_config("checks", "X")},
            1,
            ['verdict: read the run\'s jobs 2 times - GitHub first listed "X" (attempt 1) as in_progress',
             'mandatory job "checks" (attempt 1) has 2 entries, not all success',
             "verdict: red"],
            10,
            id="cell 29",
        ),
        pytest.param(
            (page(running()),) * 13,
            {},
            2,
            ['verdict: read the run\'s jobs 13 times - GitHub first listed "checks" (attempt 1) as in_progress',
             ('verdict: cannot judge - GitHub still lists "checks" (attempt 1) as in_progress after 13 reads; the gate '
             "starts after every mandatory job, so this answer is stale: re-run the verdict (a mandatory job missing "
             "from the caller's needs: must join it)")],
            120,
            id="cell 5",
        ),
        pytest.param(
            (bad_gateway(),) * 13,
            {},
            2,
            ["verdict: read the run's jobs 13 times - GitHub first answered HTTP 502 Bad Gateway",
             ("verdict: cannot judge - GitHub answered HTTP 502 Bad Gateway for u after 13 reads; this is GitHub's "
             "fault, not the caller's: re-run the verdict")],
            120,
            id="cell 13",
        ),
        pytest.param(
            (page(running()),) * 13,
            {"needs": {"sonar": "failure"}},
            2,
            ['verdict: read the run\'s jobs 13 times - GitHub first listed "checks" (attempt 1) as in_progress',
             'gate job "sonar" result failure',
             ('verdict: cannot judge - GitHub still lists "checks" (attempt 1) as in_progress after 13 reads; the gate '
             "starts after every mandatory job, so this answer is stale: re-run the verdict (a mandatory job missing "
             "from the caller's needs: must join it)")],
            120,
            id="cell 21",
        ),
        pytest.param(
            (page(running()), *(bad_gateway(),) * 12),
            {},
            2,
            ['verdict: read the run\'s jobs 13 times - GitHub first listed "checks" (attempt 1) as in_progress',
             ("verdict: cannot judge - GitHub answered HTTP 502 Bad Gateway for u after 13 reads; this is GitHub's "
             "fault, not the caller's: re-run the verdict")],
            120,
            id="cell 22",
        ),
        pytest.param(
            (page(running()), *(bad_gateway(),) * 12),
            {"needs": {"sonar": "failure"}},
            2,
            ['verdict: read the run\'s jobs 13 times - GitHub first listed "checks" (attempt 1) as in_progress',
             'gate job "sonar" result failure',
             ("verdict: cannot judge - GitHub answered HTTP 502 Bad Gateway for u after 13 reads; this is GitHub's "
             "fault, not the caller's: re-run the verdict")],
            120,
            id="cell 22 with sonar red",
        ),
        pytest.param(
            (page(running(attempt=2)), *(page(),) * 12),
            {"attempt": 2},
            2,
            ['verdict: read the run\'s jobs 13 times - GitHub first listed "checks" (attempt 2) as in_progress',
             ('verdict: cannot judge - GitHub still lists "checks" (attempt 2) as in_progress after 13 reads; the gate '
             "starts after every mandatory job, so this answer is stale: re-run the verdict (a mandatory job missing "
             "from the caller's needs: must join it)")],
            120,
            id="cell 20 run out",
        ),
        pytest.param(
            (page(job(), job(conclusion="failure", leg=2), running(name="X")), *(page(job()),) * 12),
            {"head": gate_config("checks", "X")},
            2,
            ['verdict: read the run\'s jobs 13 times - GitHub first listed "X" (attempt 1) as in_progress',
             'mandatory job "checks" (attempt 1) has 2 entries, not all success',
             ('verdict: cannot judge - GitHub still lists "X" (attempt 1) as in_progress after 13 reads; the gate '
             "starts after every mandatory job, so this answer is stale: re-run the verdict (a mandatory job missing "
             "from the caller's needs: must join it)")],
            120,
            id="cell 26 run out",
        ),
        pytest.param(
            (
                page(running()),
                CannotJudge("cannot read u: HTTP Error 403: Forbidden (does the caller grant actions: read?)"),
            ),
            {},
            2,
            ['verdict: read the run\'s jobs 2 times - GitHub first listed "checks" (attempt 1) as in_progress',
             "verdict: cannot judge - cannot read u: HTTP Error 403: Forbidden (does the caller grant actions: read?)"],
            10,
            id="cell 23",
        ),
        pytest.param(
            (page(running()), ('{"jobs": []}', None)),
            {},
            2,
            ['verdict: read the run\'s jobs 2 times - GitHub first listed "checks" (attempt 1) as in_progress',
             ("verdict: cannot judge - https://api.github.com/repos/o/r/actions/runs/1/jobs?filter=all&per_page=100 "
             "did not return the expected jobs page: KeyError('total_count')")],
            10,
            id="cell 17",
        ),
        pytest.param(
            (page(running()), page(job(), total=2)),
            {},
            2,
            ['verdict: read the run\'s jobs 2 times - GitHub first listed "checks" (attempt 1) as in_progress',
             "verdict: cannot judge - read 1 jobs but the run has 2"],
            10,
            id="cell 24",
        ),
    ],
)
def test_the_verdict_judges_the_first_settled_read_and_never_lets_a_red_reason_go(
    reread, answers, options, code, expected, slept
):
    """#85 rules 1, 2 and 4 (E1-E5): a stale answer or a 5xx is re-read every 10 s, at most 12 times; the first settled
    read decides after E4's note; out of reads, or on any other failure, exit 2 keeps the last judged read's blocking
    lines; a red read is never green and an unsettled job never prints a failure line."""
    assert reread(*answers, **options) == (code, expected, slept)


@pytest.mark.parametrize(
    ("error", "line"),
    [
        pytest.param(error, line, id=read.id)
        for read in failed_reads()
        for error, kind, line in [read.values]
        if kind is CannotJudge
    ],
)
def test_a_failed_read_that_is_not_githubs_server_error_ends_the_verdict_at_once(reread, error, line):
    """#85 cells 14-16 (E6): only a server error is read again; any other failure exits 2 at once."""
    assert reread(verdict.unreadable_jobs("u", error)) == (2, [f"verdict: cannot judge - {line}"], 0)


def test_a_body_that_is_not_utf_8_after_a_stale_read_keeps_the_note(reread, stub_server):
    """#85 rule 3, cell 28: the real fetcher names a body that is not UTF-8 as unreadable, after E4's note."""
    origin, _ = stub_server(reply(200, b"\xff"))
    read_page = verdict.github_fetcher("t")
    assert reread(page(running()), lambda url: read_page(origin + "/jobs")) == (
        2,
        ['verdict: read the run\'s jobs 2 times - GitHub first listed "checks" (attempt 1) as in_progress',
         (f"verdict: cannot judge - cannot read {origin}/jobs: 'utf-8' codec can't decode byte 0xff in position 0: "
         "invalid start byte")],
        10,
    )
