from dataclasses import dataclass

from quality.config import Allowance


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str
    line: int | None
    reason: str
    fix: str
    allowance: Allowance | None = None

    @property
    def location(self) -> str:
        path = printable(self.path)
        return path if self.line is None else f"{path}:{self.line}"


def printable(path: str) -> str:
    """The one rendering of a path: a name that is not valid UTF-8 prints backslash-escaped, never as a crash."""
    return path.encode("utf-8", "backslashreplace").decode("utf-8")


def render(finding: Finding) -> str:
    return f"{finding.rule} {finding.location} - {finding.reason}\n  Fix: {finding.fix}"
