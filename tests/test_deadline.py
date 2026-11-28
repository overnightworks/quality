import sys
import time
from pathlib import Path

import pytest

from quality import deadline, process
from quality.report import CannotJudge
from tests.support.clock import Clock


def test_the_time_left_falls_with_the_clock():
    """SCANNER-19: the deadline counts down on the injected clock."""
    clock = Clock(100.0)
    run_deadline = deadline.RunDeadline.starting(clock)
    clock.now += 40.0
    assert run_deadline.left() == deadline.RUN_DEADLINE_SECONDS - 40.0


@pytest.mark.integration
def test_a_process_failure_before_the_end_passes_unchanged(tmp_path):
    """SCANNER-20: a process failure that is no timeout stays the scanner's own failure."""
    run_deadline = deadline.RunDeadline.starting(Clock())
    with pytest.raises(process.ProcessError, match="cannot run") as failure:
        run_deadline.run([str(tmp_path / "missing-scanner")])
    assert not isinstance(failure.value, CannotJudge)


@pytest.mark.integration
def test_a_process_failure_at_the_end_becomes_the_cut_wording(tmp_path):
    """SCANNER-20, REFUSAL-22: a scanner that fails once the time is up cannot judge, with no step."""
    run_ended = tmp_path / "run-ended"
    scanner = tmp_path / "zizmor"
    scanner.write_text(f"#!/bin/sh\ntouch '{run_ended}'\nprintf '\\377'\n")
    scanner.chmod(0o755)

    def clock() -> float:
        return float(deadline.RUN_DEADLINE_SECONDS) if run_ended.exists() else 0.0

    run_deadline = deadline.RunDeadline.starting(clock)
    with pytest.raises(CannotJudge) as refusal:
        run_deadline.run([str(tmp_path / "zizmor")])
    assert not isinstance(refusal.value, process.ProcessError)
    assert refusal.value.reason.fact == (
        f"zizmor did not finish within the run's {deadline.RUN_DEADLINE_SECONDS} seconds"
    )
    assert refusal.value.reason.next_steps == ()


HANGING = [sys.executable, "-c", "import time; time.sleep(30)"]


@pytest.mark.integration
def test_a_timeout_smaller_than_the_time_left_is_the_processs_own_failure():
    """SCANNER-19: a command that outlives its own bound, with time left in the run, fails as a process failure."""
    run_deadline = deadline.RunDeadline.starting(Clock())
    with pytest.raises(process.ProcessError, match="did not finish within 0.2 seconds") as failure:
        run_deadline.run(HANGING, timeout=0.2)
    assert not isinstance(failure.value, CannotJudge)


@pytest.mark.integration
def test_a_timeout_larger_than_the_time_left_ends_at_the_end_of_the_run_with_the_cut_wording():
    """SCANNER-19, SCANNER-20, REFUSAL-22: the command's bound is the smaller of its own and the time left, so a
    command still running when the run ends is cut."""
    run_deadline = deadline.RunDeadline(time.monotonic() + 0.3, time.monotonic)
    started = time.monotonic()
    with pytest.raises(CannotJudge) as refusal:
        run_deadline.run(HANGING, timeout=30)
    assert time.monotonic() - started < 10
    assert not isinstance(refusal.value, process.ProcessError)
    assert refusal.value.reason.fact == (
        f"{Path(sys.executable).name} did not finish within the run's {deadline.RUN_DEADLINE_SECONDS} seconds"
    )


@pytest.mark.parametrize("starting", ["run", "run_in_own_group"])
def test_no_time_left_starts_nothing(tmp_path, starting):
    """SCANNER-20, REFUSAL-22: with the time spent no command starts."""
    marker = tmp_path / "started"
    command = [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]
    clock = Clock()
    run_deadline = deadline.RunDeadline.starting(clock)
    clock.now = deadline.RUN_DEADLINE_SECONDS
    start = {
        "run": lambda: run_deadline.run(command),
        "run_in_own_group": lambda: run_deadline.run_in_own_group(command, {}),
    }[starting]
    with pytest.raises(CannotJudge) as refusal:
        start()
    assert not marker.exists()
    assert refusal.value.reason.fact == f"the run's {deadline.RUN_DEADLINE_SECONDS} seconds are spent"
    assert refusal.value.reason.next_steps == ()


class Retrying:
    """A deadline of 5 seconds on a clock that only `sleep` moves, and an attempt that fails with `failures` in turn
    before it returns."""

    def __init__(self, *failures: Exception) -> None:
        self.clock = Clock()
        self.deadline = deadline.Deadline(5.0, self.clock)
        self.failures = iter(failures)
        self.slept: list[float] = []
        self.attempts = 0

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.clock.now += seconds

    def attempt(self) -> str:
        self.attempts += 1
        if failure := next(self.failures, None):
            raise failure
        return "done"

    def retried(self, delays: tuple[float, ...], again=lambda failure: True) -> str:
        return self.deadline.retried(self.attempt, again, iter(delays), self.sleep)


def test_a_failure_that_is_accepted_is_tried_again_after_each_delay_cut_to_the_time_left():
    """ADVISORY-9: each wait is the next delay or the time left, whichever is smaller."""
    retrying = Retrying(OSError("one"), OSError("two"))
    assert (retrying.retried((3, 9)), retrying.slept) == ("done", [3, 2])


def test_a_failure_that_is_not_accepted_is_raised_at_once():
    """ADVISORY-9: a failure that `again` refuses is not waited for."""
    retrying = Retrying(OSError("permanent"))
    with pytest.raises(OSError, match="permanent"):
        retrying.retried((3, 9), again=lambda failure: False)
    assert (retrying.attempts, retrying.slept) == (1, [])


def test_the_failure_after_the_last_delay_is_raised():
    """ADVISORY-9: the delays are the attempts that remain."""
    retrying = Retrying(OSError("first"), OSError("last"))
    with pytest.raises(OSError, match="last"):
        retrying.retried((1,))
    assert (retrying.attempts, retrying.slept) == (2, [1])


def test_a_failure_with_no_time_left_is_raised_without_a_wait():
    """ADVISORY-9: a spent deadline waits for nothing, not even a zero-second wait."""
    retrying = Retrying(OSError("late"))
    retrying.clock.now = retrying.deadline.ends_at
    with pytest.raises(OSError, match="late"):
        retrying.retried((3,))
    assert (retrying.attempts, retrying.slept) == (1, [])
