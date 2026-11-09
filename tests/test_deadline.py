import sys

import pytest

from quality import deadline, process
from quality.report import CannotJudge


class Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class ClockPastTheEndAfter:
    """Reads 0 for the first `reads` calls, then the deadline's length."""

    def __init__(self, reads: int) -> None:
        self.reads_left = reads

    def __call__(self) -> float:
        self.reads_left -= 1
        return 0.0 if self.reads_left >= 0 else float(deadline.RUN_DEADLINE_SECONDS)


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
    run_deadline = deadline.RunDeadline.starting(ClockPastTheEndAfter(reads=3))
    with pytest.raises(CannotJudge) as refusal:
        run_deadline.run([str(tmp_path / "zizmor")])
    assert not isinstance(refusal.value, process.ProcessError)
    assert refusal.value.reason.fact == f"zizmor did not finish within the run's {deadline.RUN_DEADLINE_SECONDS} seconds"
    assert refusal.value.reason.next_steps == ()


@pytest.mark.parametrize("starting", ["run", "run_in_own_group"])
def test_no_time_left_starts_nothing(tmp_path, starting):
    """SCANNER-20, REFUSAL-22: with the time spent no command starts."""
    marker = tmp_path / "started"
    command = [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]
    clock = Clock()
    run_deadline = deadline.RunDeadline.starting(clock)
    clock.now = deadline.RUN_DEADLINE_SECONDS
    start = {"run": lambda: run_deadline.run(command), "run_in_own_group": lambda: run_deadline.run_in_own_group(command, {})}[starting]
    with pytest.raises(CannotJudge) as refusal:
        start()
    assert not marker.exists()
    assert refusal.value.reason.fact == f"the run's {deadline.RUN_DEADLINE_SECONDS} seconds are spent"
    assert refusal.value.reason.next_steps == ()
