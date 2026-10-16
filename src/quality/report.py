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
        return self.path if self.line is None else f"{self.path}:{self.line}"


def render(finding: Finding) -> str:
    return f"{finding.rule} {finding.location} - {finding.reason}\n  Fix: {finding.fix}"
