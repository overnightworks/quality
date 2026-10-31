import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import requires
from pathlib import Path

import pytest

from quality import DISTRIBUTION, check, cli, env_file, output
from quality.output import Glyph
from quality.ratchet import BASELINE_PATH, load_baseline
from quality.report import Finding, FindingEvent, Note, NoteEvent, printable
from quality.rules import Rule

installed_ruff = cli.INSTALLED_RUFF

CLEAN_VERDICT = "✓ clean · "


def is_clean(output: str) -> bool:
    """The run ended in the clean verdict line, which the door prints only on exit 0 with no finding."""
    return any(line.startswith(CLEAN_VERDICT) for line in output.splitlines())


def refused(output: str, area: str) -> bool:
    """The run named `area` as one it could not check."""
    return any(line.startswith(f"  ✗ {area} ") for line in output.splitlines())


def loosen_requirement(site: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Puts temporary metadata for quality first on the search path, its real requirements with `name`'s `==` turned into `>=`."""
    lines = [line.replace("==", ">=") if line.startswith(f"{name}==") else line for line in requires(DISTRIBUTION) or ()]
    dist_info = site / f"{DISTRIBUTION.replace('-', '_')}-0.dist-info"
    dist_info.mkdir(parents=True)
    requirements = "".join(f"Requires-Dist: {line}\n" for line in lines)
    (dist_info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {DISTRIBUTION}\nVersion: 0\n{requirements}")
    monkeypatch.syspath_prepend(str(site))


def assert_every_line_starts_with_the_door(lines: list[str]) -> None:
    """#97 rule 2, cell 18 and #228 rule 7: no line repository text could forge, and no raw control, format or line- or paragraph-separator
    character (categories Cc, Cf, Zl, Zp, listed here by hand from the rule) between the lines."""
    for line in lines:
        assert not line.lstrip().startswith(("::", "File ", "raise ")), line
        assert all(unicodedata.category(character) not in ("Cc", "Cf", "Zl", "Zp") for character in line), line


@dataclass(frozen=True)
class FindingRow:
    """One finding row as printed: its indentation, event and fact, the fix of the `fix:` run it belongs to (the next
    `fix:` line below it), that line's indentation, and whether that line follows it directly."""

    indent: int
    event: FindingEvent
    fact: str
    fix: str | None
    fix_indent: int | None
    fix_follows: bool
    detail: tuple[str, ...] = ()


def _indentation(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


@dataclass(frozen=True)
class BlockLine:
    """One printed line of a decoded span, at its place in the printed output."""

    position: int
    text: str


@dataclass(frozen=True)
class FindingLine(BlockLine):
    location: str
    row: FindingRow


@dataclass(frozen=True)
class DetailLine(BlockLine):
    """A line of a finding's detail: everything after the marker's one separator space, leading whitespace included."""

    detail: str


@dataclass(frozen=True)
class FixLine(BlockLine):
    """A `fix:` line."""


@dataclass(frozen=True)
class NoteLine(BlockLine):
    location: str
    row: NoteRow


@dataclass(frozen=True)
class OtherLine(BlockLine):
    """Any line that is none of these: until the other areas move to rows, an event-less finding's text."""


def _event_of[Event: StrEnum](remainder: str, events: type[Event]) -> Event | None:
    """The member whose word opens `remainder` followed by a space; the longest wins."""
    matching = [event for event in events if remainder.startswith(f"{event.value} ")]
    return max(matching, key=lambda member: len(member.value)) if matching else None


def _location_and_remainder(stripped: str, locations: Sequence[str]) -> tuple[str, str]:
    """The location a row names and what follows it: a known location first, else the first word."""
    after_glyph = stripped.split(" ", 1)[1] if " " in stripped else ""
    location = next((known for known in locations if after_glyph.startswith(f"{known} ")), after_glyph.split(" ", 1)[0])
    return location, after_glyph[len(location):].lstrip(" ")


def _detail_of(line: str) -> str | None:
    """What a detail line holds after the marker, or None for a line that is none."""
    marker = f"{output.FIX_INDENT}{output.DETAIL_MARKER}"
    if not line.startswith(marker) or line[len(marker):len(marker) + 1] not in ("", " "):
        return None
    return line[len(marker) + 1:]


def _details_below(lines: Sequence[str], number: int) -> tuple[str, ...]:
    """The detail lines standing contiguously directly under the row at `number`."""
    details = []
    for following in lines[number + 1:]:
        if (detail := _detail_of(following)) is None:
            break
        details.append(detail)
    return tuple(details)


def _fix_below(lines: Sequence[str], number: int) -> tuple[str | None, int | None, bool]:
    """The fix of the `fix:` run a row belongs to: the next `fix:` line below it that is not shallower, its indentation,
    and whether it follows the row and its own detail lines directly."""
    own = number + len(_details_below(lines, number))
    for later, following in enumerate(lines[number + 1:], start=number + 1):
        if _indentation(following) < _indentation(lines[number]):
            break
        if following.lstrip().startswith(f"{output.FIX_LABEL} "):
            return following.lstrip()[len(output.FIX_LABEL) + 1:], _indentation(following), later == own + 1
    return None, None, False


def decode_block(lines: Sequence[str], locations: Sequence[str] = (), first_position: int = 0) -> list[BlockLine]:
    """Every line of a span in printed order: a finding row (glyph `!`, a location, a `FindingEvent`, a fact), a `fix:`
    line, a note row (glyph `·`, a location, a `NoteEvent`, a fact) or any other line. A row is found by its glyph and
    its event; the padding between the columns is never rebuilt."""
    decoded: list[BlockLine] = []
    for number, line in enumerate(lines):
        stripped = line.lstrip()
        position = first_position + number
        if stripped.startswith(f"{Glyph.NEEDS_YOU} ") and (found := _finding_line(lines, number, locations, position)):
            decoded.append(found)
        elif (detail := _detail_of(line)) is not None:
            decoded.append(DetailLine(position, line, detail))
        elif stripped.startswith(f"{output.FIX_LABEL} "):
            decoded.append(FixLine(position, line))
        elif stripped.startswith(f"{Glyph.NOTE} ") and (note := _note_line(line, locations, position)):
            decoded.append(note)
        else:
            decoded.append(OtherLine(position, line))
    return decoded


def _finding_line(lines: Sequence[str], number: int, locations: Sequence[str], position: int) -> FindingLine | None:
    location, remainder = _location_and_remainder(lines[number].lstrip(), locations)
    event = _event_of(remainder, FindingEvent)
    if event is None:
        return None
    fix, fix_indent, fix_follows = _fix_below(lines, number)
    detail = _details_below(lines, number)
    row = FindingRow(_indentation(lines[number]), event, remainder[len(event.value):].strip(), fix, fix_indent, fix_follows, detail)
    return FindingLine(position, lines[number], location, row)


def _note_line(line: str, locations: Sequence[str], position: int) -> NoteLine | None:
    location, remainder = _location_and_remainder(line.lstrip(), locations)
    event = _event_of(remainder, NoteEvent)
    if event is None:
        return None
    return NoteLine(position, line, location, NoteRow(_indentation(line), event, remainder[len(event.value):].strip()))


def finding_rows(printed: str, location: str) -> list[FindingRow]:
    """Every finding row at `location`, in printed order."""
    return [row for _, row in printed_finding_rows(printed, [location])]


def printed_finding_rows(printed: str, locations: list[str]) -> list[tuple[str, FindingRow]]:
    """Every finding row at any of `locations`, each with its location, in the order the output prints them."""
    return [(line.location, line.row) for line in decode_block(printed.splitlines(), locations) if isinstance(line, FindingLine) and line.location in locations]


@dataclass(frozen=True)
class AreaRow:
    """One area row as printed: its glyph, `AreaEvent` and fact."""

    glyph: str
    event: cli.AreaEvent
    fact: str


def area_row(printed: str, area: str) -> AreaRow:
    """`area`'s row, found by the area word and an `AreaEvent`; the padding between the columns is never rebuilt."""
    for line in printed.splitlines():
        words = line.split()
        if len(words) > 2 and words[1] == area:
            remainder = line.split(area, 1)[1].lstrip(" ")
            event = _event_of(remainder, cli.AreaEvent)
            if event is not None:
                return AreaRow(words[0], event, remainder[len(event.value):].strip())
    raise AssertionError(f"no row of {area} in {printed}")


def area_fact(printed: str, area: str) -> str:
    """The fact of `area`'s row."""
    return area_row(printed, area).fact


def leading_count(fact: str) -> int:
    """The integer an extent opens with."""
    return int(fact.split()[0])


def extent_counts(fact: str) -> list[int]:
    """Every integer of an extent, in the order the fact names them."""
    return [int(number) for number in re.findall(r"\d+", fact)]


def area_block(printed: str, area: str) -> list[BlockLine]:
    """Every line under `area`'s row, in printed order with its position: from the row to the next area row, the verdict
    line or `error:`, that is while the lines stay indented under the row."""
    lines = printed.splitlines()
    start = next(number for number, line in enumerate(lines) if line.split()[1:2] == [area])
    end = next((number for number in range(start + 1, len(lines)) if not lines[number].startswith(output.FINDING_INDENT)), len(lines))
    return decode_block(lines[start + 1:end], first_position=start + 1)


def area_finding_rows(printed: str, area: str) -> list[tuple[str, FindingRow]]:
    """Every finding row standing under `area`'s row, each with its location, in printed order."""
    return [(line.location, line.row) for line in area_block(printed, area) if isinstance(line, FindingLine)]


def assert_block_order(block: Sequence[BlockLine]) -> None:
    """Decision 4 and O25: a finding's detail lines stand contiguously directly under its row, each run's `fix:` follows
    the last detail line of the run's last row and equals that row's fix, and every note follows the block's final `fix:`."""
    for index, line in enumerate(block):
        before = block[index - 1] if index else None
        if isinstance(line, DetailLine):
            assert isinstance(before, FindingLine | DetailLine), line
        if isinstance(line, FixLine):
            assert isinstance(before, FindingLine | DetailLine), line
            owner = next(earlier for earlier in reversed(block[:index]) if isinstance(earlier, FindingLine))
            assert line.text.strip() == f"{output.FIX_LABEL} {owner.row.fix}", line
        if isinstance(line, FindingLine) and index:
            assert not any(isinstance(earlier, NoteLine) for earlier in block[:index]), line
    last_fix = max((line.position for line in block if isinstance(line, FixLine)), default=-1)
    assert all(line.position > last_fix for line in block if isinstance(line, NoteLine))


def assert_block_prints(block: Sequence[BlockLine], findings: Sequence[Finding], notes: Sequence[Note]) -> None:
    """The decoded block equals what the rule returned: each row its finding with its escaped detail, each note its note,
    in order, and the block keeps the order of `assert_block_order`."""
    rows = [line for line in block if isinstance(line, FindingLine)]
    assert len(rows) == len(findings)
    for line, found in zip(rows, findings, strict=True):
        assert (line.location, line.row.detail) == (found.location, tuple(printable(text) for text in found.detail))
        assert_row_prints_finding(line.row, found)
    printed_notes = [(line.location, line.row.event, line.row.fact) for line in block if isinstance(line, NoteLine)]
    assert printed_notes == [(note.location, note.event, printable(note.fact)) for note in notes]
    assert_block_order(block)


@dataclass(frozen=True)
class NoteRow:
    """One note row as printed: its indentation, event and fact."""

    indent: int
    event: NoteEvent
    fact: str


def note_rows(printed: str, location: str) -> list[NoteRow]:
    """Every note row at `location`, in printed order."""
    return [line.row for line in decode_block(printed.splitlines(), [location]) if isinstance(line, NoteLine) and line.location == location]


def rule_notes(repo: Path, rule: Rule) -> list[Note]:
    """The notes `rule` returns over `repo` before the door prints them."""
    return [note for verdict in hygiene_run(repo).verdicts if verdict.rule is rule for note in verdict.notes]


def recorded_entries(repo: Path) -> int:
    """How many entries a first `quality baseline write` records for `repo`."""
    assert cli.main(["baseline", "write", str(repo)]) == 0
    return len(load_baseline((repo / BASELINE_PATH).read_text(encoding="utf-8")).size)


def rows_of(printed: str, finding: Finding) -> list[FindingRow]:
    """The rows at `finding.location` whose event is the finding's, in printed order: another rule's row at the same location is not it."""
    return [row for row in finding_rows(printed, finding.location) if row.event is finding.event]


def rule_findings(repo: Path, rule: Rule) -> list[Finding]:
    """The findings `rule` returns over `repo` before the door prints them."""
    return [finding for verdict in hygiene_run(repo).verdicts if verdict.rule is rule for finding in verdict.findings]


def assert_row_prints_finding(row: FindingRow, finding: Finding) -> None:
    """The decoded row carries the finding's event, fact and fix as the owners escape them."""
    assert (row.event, row.fact, row.fix) == (finding.event, printable(finding.reason), printable(finding.fix))


def assert_finding_vocabulary(finding: Finding) -> None:
    """Decision 3 and 12: neither fact nor fix ends in a full stop, and no word of the row is a rule id."""
    assert not finding.reason.endswith(".")
    assert not finding.fix.endswith(".")
    assert {rule.value for rule in Rule}.isdisjoint(f"{finding.event} {finding.reason} {finding.fix}".split())


def env_row_stands(printed: str) -> bool:
    """The `.env` finding of the env-file rule stands as a row of the output, with the rule's event and fact."""
    return any(row.event is FindingEvent.TRACKED and row.fact == printable(env_file.ENV_FACT) for row in finding_rows(printed, ".env"))


def hygiene_output(
    capsys: pytest.CaptureFixture[str], path: Path, locate_ruff: Callable[[], str] = installed_ruff
) -> tuple[int, str]:
    """The exit code and the whole output of `quality hygiene` over `path`, through the real door."""
    code = cli.main(["hygiene", str(path)], locate_ruff=locate_ruff, clock=lambda: 0.0)
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def compose(*port_lines: str) -> dict[str, str]:
    body = "".join(f"      {line}\n" for line in port_lines)
    return {"compose.yaml": f"services:\n  web:\n    image: x\n    ports:\n{body}"}


def refusal_row(area: str) -> str:
    """What an area row that could not check says before its reason (docs/output.md, case 4)."""
    return f"  ✗ {area:<12}  {'not checked':<14} "


def hygiene_run(repo: Path, locate_ruff: Callable[[], str] = installed_ruff, locate_zizmor: Callable[[], str] | None = None) -> check.HygieneResult:
    """What the rule runner judged over `repo`, before the door prints it."""
    return check.run_hygiene(repo, locate_ruff, locate_zizmor or cli.INSTALLED_ZIZMOR)


def refusals_of(result: check.HygieneResult) -> dict[Rule, str]:
    """Each rule that cannot judge, with its reason, as the rule runner reports it."""
    return {verdict.rule: str(verdict.refusal) for verdict in result.verdicts if verdict.refusal is not None}


def refusals(repo: Path, locate_ruff: Callable[[], str] = installed_ruff, locate_zizmor: Callable[[], str] | None = None) -> dict[Rule, str]:
    """Each rule that cannot judge `repo`, with its reason, as the rule runner reports it."""
    return refusals_of(hygiene_run(repo, locate_ruff, locate_zizmor))


def copy_block(example: tuple[str, ...]) -> str:
    """The lone `next:` step that shows `example`, as the run's stderr holds it: the label, then each line four spaces deep."""
    return "\n".join([f"next: {check.CONFIG_EXAMPLE_LABEL}:", *(f"    {line}" for line in example)]) + "\n"


def heard(capsys: pytest.CaptureFixture[str]) -> str:
    """Everything the door printed since the last read, stdout then stderr."""
    captured = capsys.readouterr()
    return captured.out + captured.err
