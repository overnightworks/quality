import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from quality import process
from quality.report import CannotJudge

RUN_DEADLINE_SECONDS = 540


class RunDeadlineSpent(CannotJudge):
    """The run's time is spent or ran out in a scanner; it brings no step, because nothing a person
    installs changes it."""

    @classmethod
    def not_started(cls) -> RunDeadlineSpent:
        return cls(f"the run's {RUN_DEADLINE_SECONDS} seconds are spent")

    @classmethod
    def cut(cls, subject: str) -> RunDeadlineSpent:
        return cls(f"{subject} did not finish within the run's {RUN_DEADLINE_SECONDS} seconds")


@dataclass(frozen=True)
class RunDeadline:
    """The one time budget of a `check` run, counted on the clock the door injected."""

    ends_at: float
    monotonic: Callable[[], float]

    @classmethod
    def starting(cls, monotonic: Callable[[], float]) -> RunDeadline:
        return cls(monotonic() + RUN_DEADLINE_SECONDS, monotonic)

    def left(self) -> float:
        return self.ends_at - self.monotonic()

    def ensure_time_left(self) -> None:
        if self.left() <= 0:
            raise RunDeadlineSpent.not_started()

    def run(
        self, command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess:
        """`process.run` bounded by the time left or, when `timeout` is smaller, by `timeout`."""
        def start(left: float) -> subprocess.CompletedProcess:
            bound = left if timeout is None else min(timeout, left)
            return process.run(command, cwd=cwd, env=env, timeout=bound)

        return self._within_time(command, start)

    def run_in_own_group(self, command: list[str], env: Mapping[str, str] | None) -> subprocess.CompletedProcess:
        """For gitleaks alone: its `git` children die with it, which needs `process.scratch_directory` around
        the call."""
        return self._within_time(command, lambda left: process.run_bounded_in_own_group(command, left, env))

    def _within_time(
        self, command: list[str], start: Callable[[float], subprocess.CompletedProcess]
    ) -> subprocess.CompletedProcess:
        self.ensure_time_left()
        try:
            return start(self.left())
        except process.ProcessError:
            if self.left() <= 0:
                raise RunDeadlineSpent.cut(Path(command[0]).name) from None
            raise
