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


def test_a_process_failure_before_the_end_passes_unchanged(monkeypatch):
    """SCANNER-20: a process failure that is no timeout stays the scanner's own failure."""
    monkeypatch.setattr(deadline.process, "run", lambda *_a, **_k: (_ for _ in ()).throw(process.ProcessError("cannot run x")))
    run_deadline = deadline.RunDeadline.starting(Clock())
    with pytest.raises(process.ProcessError, match="cannot run x"):
        run_deadline.run(["/bin/x"])


def test_a_process_failure_at_the_end_becomes_the_cut_wording(monkeypatch):
    """SCANNER-20, REFUSAL-22: a scanner that fails once the time is up cannot judge, with no step."""
    clock = Clock()
    run_deadline = deadline.RunDeadline.starting(clock)

    def timing_out(*_arguments: object, **_keywords: object) -> None:
        clock.now = deadline.RUN_DEADLINE_SECONDS
        raise process.ProcessError("x did not finish")

    monkeypatch.setattr(deadline.process, "run", timing_out)
    with pytest.raises(CannotJudge) as refusal:
        run_deadline.run(["/opt/tools/zizmor"])
    assert not isinstance(refusal.value, process.ProcessError)
    assert refusal.value.reason.fact == f"zizmor did not finish within the run's {deadline.RUN_DEADLINE_SECONDS} seconds"
    assert refusal.value.reason.next_steps == ()


@pytest.mark.parametrize("starting", ["run", "run_in_own_group"])
def test_no_time_left_starts_nothing(monkeypatch, starting):
    """SCANNER-20, REFUSAL-22: with the time spent no command starts."""
    monkeypatch.setattr(deadline.process, "run", lambda *_a, **_k: pytest.fail("a command started"))
    monkeypatch.setattr(deadline.process, "run_bounded_in_own_group", lambda *_a, **_k: pytest.fail("a command started"))
    clock = Clock()
    run_deadline = deadline.RunDeadline.starting(clock)
    clock.now = deadline.RUN_DEADLINE_SECONDS
    start = {"run": lambda: run_deadline.run(["/bin/x"]), "run_in_own_group": lambda: run_deadline.run_in_own_group(["/bin/x"], {})}[starting]
    with pytest.raises(CannotJudge) as refusal:
        start()
    assert refusal.value.reason.fact == f"the run's {deadline.RUN_DEADLINE_SECONDS} seconds are spent"
    assert refusal.value.reason.next_steps == ()
