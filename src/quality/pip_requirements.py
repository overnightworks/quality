import re
from dataclasses import dataclass

NAME = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
EXTRAS = r"(?:\[[A-Za-z0-9._,\s-]*\])?"
VERSION = r"[0-9][0-9A-Za-z.!+_]*"
HASHES = r"(?P<hashes>(?:\s+--hash=[a-z0-9]+:[0-9a-f]+)*)"
MARKER = r"(?:\s*;\s*\S.*?)?"
PINNED = re.compile(rf"(?P<name>{NAME}){EXTRAS}\s*==\s*(?P<version>{VERSION}){MARKER}{HASHES}")
WHEEL = re.compile(
    rf"(?P<name>{NAME}){EXTRAS}\s+@\s+https://[^\s?#%]*/(?P<distribution>[^-/\s?#%]+)-(?P<version>{VERSION})-[^/\s?#%]+\.whl{MARKER}{HASHES}"
)
COMMENT = re.compile(r"(^|\s)#.*")


@dataclass(frozen=True)
class Requirement:
    """One package version a requirements file pins; `line` is where its logical line starts, counted from 1."""

    name: str
    version: str
    line: int
    is_wheel: bool


class RequirementsError(ValueError):
    def __init__(self, line: int) -> None:
        super().__init__(
            f"line {line} is neither `name==version` (with extras, a marker and --hash) nor "
            "`name @ https://.../name-version-....whl` with --hash"
        )


def normalised_name(name: str) -> str:
    """PEP 503: the name as PyPI compares it."""
    return re.sub(r"[-_.]+", "-", name).lower()


def read(text: str) -> tuple[Requirement, ...]:
    """The pins of a requirements file in exactly two forms; any other logical line raises `RequirementsError` with its number."""
    return tuple(_requirement(line, number) for number, line in _logical_lines(text) if line)


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Each logical line with the number of its first physical line: comments dropped, `\\` continuations joined."""
    lines: list[tuple[int, str]] = []
    parts: list[str] = []
    start = 1
    for number, physical in enumerate(text.splitlines() + [""], start=1):
        content = COMMENT.sub("", physical).strip()
        if not parts:
            start = number
        parts.append(content.removesuffix("\\").strip())
        if not content.endswith("\\"):
            lines.append((start, " ".join(part for part in parts if part)))
            parts = []
    return lines


def _requirement(line: str, number: int) -> Requirement:
    if pinned := PINNED.fullmatch(line):
        return Requirement(normalised_name(pinned["name"]), pinned["version"], number, is_wheel=False)
    wheel = WHEEL.fullmatch(line)
    if wheel and wheel["hashes"] and normalised_name(wheel["distribution"]) == normalised_name(wheel["name"]):
        return Requirement(normalised_name(wheel["name"]), wheel["version"], number, is_wheel=True)
    raise RequirementsError(number)
