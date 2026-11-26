from pathlib import Path

import pytest

from quality.config import GateSettings, LegacyMode
from quality.ratchet import NO_RENAMES
from quality.report import CannotJudge
from quality.sides import Side
from quality.verdict import JobEvent, JobKind, JobOutcome, judge, read_jobs
from tests.support.verdict import (
    GREEN_GATE,
    job,
)

FIXTURES = Path(__file__).parent / "fixtures"


def problems(jobs, mandatory=("checks",), base=(), gate=GREEN_GATE, attempt=1, legacy=LegacyMode.REPORT, base_legacy=LegacyMode.REPORT):
    head, base_side = Side(GateSettings(mandatory, legacy), None), Side(GateSettings(base, base_legacy), None)
    result = judge(head, base_side, jobs, gate, attempt, NO_RENAMES)
    return (*result.undeclared, *(outcome for outcome in result.jobs if not outcome.green))


def failed(event, fact="", name="checks", kind=JobKind.MANDATORY):
    return JobOutcome(name, kind, event, fact)


@pytest.mark.parametrize(
    ("jobs", "attempt", "expected"),
    [
        ([job()], 1, ()),
        ([], 1, (failed(JobEvent.MISSING, "not in the run"),)),
        ([job(conclusion="failure")], 1, (failed(JobEvent.FAILURE, "attempt 1"),)),
        ([job(conclusion="skipped")], 1, (failed(JobEvent.SKIPPED, "attempt 1"),)),
        ([job(conclusion="cancelled")], 1, (failed(JobEvent.CANCELLED, "attempt 1"),)),
        ([job(), job(attempt=2, conclusion="failure")], 2, (failed(JobEvent.FAILURE, "attempt 2"),)),
        ([job(conclusion="failure"), job(attempt=2)], 2, ()),
        ([job()], 2, ()),
        ([job(), job(attempt=3, conclusion="failure")], 2, ()),
        ([job(), job(conclusion="failure")], 1, (failed(JobEvent.SEVERAL_ENTRIES, "2 entries in attempt 1, not all success"),)),
        ([job(), job()], 1, ()),
        ([job(name="other", conclusion="failure"), job()], 1, ()),
        ([job(name="checks (3.12)")], 1, (failed(JobEvent.MISSING, "not in the run"),)),
    ],
    ids=[
        "success",
        "missing",
        "failure",
        "skipped",
        "cancelled",
        "newer-failure-beats-older-success",
        "newer-success-beats-older-failure",
        "attempt-below-current-counts",
        "attempt-above-current-ignored",
        "duplicate-entries-one-failing",
        "duplicate-entries-all-success",
        "other-job-failing-ignored",
        "name-must-match-exactly",
    ],
)
def test_judges_the_effective_result_of_each_mandatory_job(jobs, attempt, expected):
    """VERDICT-2, VERDICT-3, VERDICT-5, VERDICT-6, VERDICT-7, VERDICT-8: the newest attempt up to the current one
    decides each mandatory job; several entries, a missing job and a failed, skipped or cancelled one are red; other
    jobs and other names do not count."""
    assert problems(jobs, attempt=attempt) == expected


def test_a_gate_job_that_did_not_succeed_is_red():
    """VERDICT-14: a gate job that did not succeed makes the verdict red, with no mandatory entry."""
    assert problems([job()], gate={"check": "failure"}) == (failed(JobEvent.FAILURE, name="check", kind=JobKind.GATE),)


def test_a_job_dropped_against_the_base_is_loosened_and_red():
    """VERDICT-13: a mandatory job dropped against the base is loosened and red."""
    found = problems([job("tests-lite")], mandatory=("tests-lite",), base=("tests",))
    assert 'mandatory job "tests" removed' in found
    assert failed(JobEvent.MISSING, "not in the run", "tests") in found


def test_a_base_job_kept_by_the_head_is_green():
    """VERDICT-13: a base job the head keeps is not loosened."""
    assert problems([job()], mandatory=("checks",), base=("checks",)) == ()


def fetch_from_fixtures(*names):
    pages = [(FIXTURES / name).read_text(encoding="utf-8") for name in names]
    links = [f'<https://api.example/next{i}>; rel="next"' for i in range(len(pages) - 1)] + [None]
    served = iter(zip(pages, links, strict=True))
    return lambda url: next(served)


def test_reads_the_mandatory_job_that_only_sits_on_page_two():
    """VERDICT-11: jobs on later pages are read from recorded responses."""
    jobs = read_jobs("o/r", "1", fetch_from_fixtures("jobs-page1.json", "jobs-page2.json"))
    assert problems(jobs, mandatory=("quality / verdict",)) == ()
    assert problems(jobs, mandatory=("checks",)) == (failed(JobEvent.MISSING, "not in the run"),)


def test_a_missing_second_page_cannot_be_judged():
    """VERDICT-12: jobs read disagreeing with total_count cannot be judged."""
    fetch = fetch_from_fixtures("jobs-page1.json")
    with pytest.raises(CannotJudge, match="read 2 jobs but the run has 3"):
        read_jobs("o/r", "1", fetch)


def test_more_pages_than_the_cap_cannot_be_judged():
    """VERDICT-12: past the page cap the run cannot be judged."""
    page = (FIXTURES / "jobs-page1.json").read_text(encoding="utf-8")
    with pytest.raises(CannotJudge, match="more than 10 pages"):
        read_jobs("o/r", "1", lambda url: (page, '<https://api.example/next>; rel="next"'))


@pytest.mark.parametrize(
    ("base_legacy", "legacy", "expected"),
    [
        (LegacyMode.FAIL, LegacyMode.REPORT, ("legacy fail → report",)),
        (LegacyMode.FAIL, LegacyMode.FAIL, ()),
        (LegacyMode.REPORT, LegacyMode.FAIL, ()),
        (LegacyMode.REPORT, LegacyMode.REPORT, ()),
    ],
    ids=["fail to report", "fail kept", "report to fail", "report kept"],
)
def test_switching_legacy_back_from_fail_is_a_loosening(base_legacy, legacy, expected):
    """VERDICT-51: a head config that is not "fail" under a base config that is shows as loosened and is red"""
    head, base = Side(GateSettings(("checks",), legacy), None), Side(GateSettings(("checks",), base_legacy), None)
    result = judge(head, base, [job()], GREEN_GATE, 1, NO_RENAMES)
    assert (result.undeclared, result.green) == (expected, not expected)


@pytest.mark.parametrize(
    ("base_sonar", "sonar", "expected"),
    [
        (True, False, ("sonar true → false",)),
        (True, True, ()),
        (False, True, ()),
        (False, False, ()),
    ],
    ids=["on to off", "on kept", "off to on", "off kept"],
)
def test_turning_sonar_off_is_a_loosening(base_sonar, sonar, expected):
    """VERDICT-18: A head config that turns SonarCloud off under a base config that runs it shows as loosened and is
    red."""
    head = Side(GateSettings(("checks",), LegacyMode.REPORT, sonar=sonar), None)
    base = Side(GateSettings(("checks",), LegacyMode.REPORT, sonar=base_sonar), None)
    result = judge(head, base, [job()], GREEN_GATE, 1, NO_RENAMES)
    assert (result.undeclared, result.green) == (expected, not expected)


@pytest.mark.parametrize(
    "body",
    ["not json", "[]", '{"total_count": 1, "jobs": [1]}', '{"jobs": []}'],
    ids=["not-json", "not-an-object", "job-not-an-object", "no-total-count"],
)
def test_a_page_of_the_wrong_shape_cannot_be_judged(body):
    """VERDICT-46: decoding and shape errors of a page are cannot judge, not a traceback."""
    with pytest.raises(CannotJudge, match="expected jobs page"):
        read_jobs("o/r", "1", lambda url: (body, None))
