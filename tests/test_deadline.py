import sys
import time
from pathlib import Path

import pytest

from quality import deadline, process
from quality.report import CannotJudge


class Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


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
