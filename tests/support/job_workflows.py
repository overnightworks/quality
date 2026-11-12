"""Workflow files whose jobs a test gives a `timeout-minutes`, for the rule that judges them and the fix that lowers them."""

ON_PULL_REQUEST = "on: pull_request\n"
STEPS = "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n"


def job(name: str = "j", limit: str | None = "10") -> str:
    """A job whose id stands on its own line and whose `timeout-minutes`, when given, stands on the next."""
    return f"  {name}:\n" + ("" if limit is None else f"    timeout-minutes: {limit}\n") + STEPS


def workflow(on: str = ON_PULL_REQUEST, jobs: str | None = None, top: str = "") -> str:
    """A workflow whose `on:` starts on line 2 and, with no `top`, whose first job id is on line `first_job_line(on)`."""
    return f"name: w\n{on}permissions: {{}}\n{top}jobs:\n{job(limit=None) if jobs is None else jobs}"


def first_job_line(on: str = ON_PULL_REQUEST) -> int:
    return 4 + on.count("\n")
