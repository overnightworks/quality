import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
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


def _indentation(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def finding_rows(printed: str, location: str) -> list[FindingRow]:
    """Every finding row at `location`, in printed order."""
    return [row for _, row in printed_finding_rows(printed, [location])]


def printed_finding_rows(printed: str, locations: list[str]) -> list[tuple[str, FindingRow]]:
    """Every finding row at any of `locations`, each with its location, in the order the output prints them. A row is
    found by its glyph and an event of `FindingEvent`, a fix line by the label of `output`; the padding between the
    columns is never rebuilt."""
    lines = printed.splitlines()
    rows = []
    for number, line in enumerate(lines):
        prefix = next((f"{Glyph.NEEDS_YOU} {location} " for location in locations if line.lstrip().startswith(f"{Glyph.NEEDS_YOU} {location} ")), None)
        if prefix is None:
            continue
        remainder = line.lstrip()[len(prefix):].lstrip(" ")
        events = [event for event in FindingEvent if remainder.startswith(f"{event.value} ")]
        if not events:
            continue
        event = max(events, key=lambda member: len(member.value))
        fix, fix_indent, fix_follows = None, None, False
        for later, following in enumerate(lines[number + 1:], start=number + 1):
            if _indentation(following) < _indentation(line):
                break
            if following.lstrip().startswith(f"{output.FIX_LABEL} "):
                fix, fix_indent, fix_follows = following.lstrip()[len(output.FIX_LABEL) + 1:], _indentation(following), later == number + 1
                break
        rows.append((prefix[len(Glyph.NEEDS_YOU) + 1:-1], FindingRow(_indentation(line), event, remainder[len(event.value):].strip(), fix, fix_indent, fix_follows)))
    return rows


def area_fact(printed: str, area: str) -> str:
    """The fact of `area`'s row, found by the area word and an `AreaEvent`; the padding between the columns is never rebuilt."""
    for line in printed.splitlines():
        words = line.split()
        if len(words) > 2 and words[1] == area:
            remainder = line.split(area, 1)[1].lstrip(" ")
            events = [event for event in cli.AreaEvent if remainder.startswith(f"{event.value} ")]
            if events:
                return remainder[len(max(events, key=lambda member: len(member.value)).value):].strip()
    raise AssertionError(f"no row of {area} in {printed}")


def leading_count(fact: str) -> int:
    """The integer an extent opens with."""
    return int(fact.split()[0])


def extent_counts(fact: str) -> list[int]:
    """Every integer of an extent, in the order the fact names them."""
    return [int(number) for number in re.findall(r"\d+", fact)]


def area_finding_rows(printed: str, area: str) -> list[tuple[str, FindingRow]]:
    """Every finding row standing under `area`'s row, each with its location, in printed order."""
    lines = printed.splitlines()
    start = next(number for number, line in enumerate(lines) if line.split()[1:2] == [area])
    locations = []
    for line in lines[start + 1:]:
        if not line.startswith(f"{output.FINDING_INDENT}{Glyph.NEEDS_YOU} "):
            if line.startswith(output.FINDING_INDENT):
                continue
            break
        locations.append(line.split()[1])
    return printed_finding_rows(printed, list(dict.fromkeys(locations)))


@dataclass(frozen=True)
class NoteRow:
    """One note row as printed: its indentation, event and fact."""

    indent: int
    event: NoteEvent
    fact: str


def note_rows(printed: str, location: str) -> list[NoteRow]:
    """Every note row at `location`, in printed order. A row is found by its glyph and an event of `NoteEvent`; the
    padding between the columns is never rebuilt."""
    rows = []
    for line in printed.splitlines():
        prefix = f"{Glyph.NOTE} {location} "
        if not line.lstrip().startswith(prefix):
            continue
        remainder = line.lstrip()[len(prefix):].lstrip(" ")
        events = [event for event in NoteEvent if remainder.startswith(f"{event.value} ")]
        if events:
            event = max(events, key=lambda member: len(member.value))
            rows.append(NoteRow(_indentation(line), event, remainder[len(event.value):].strip()))
    return rows


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


def refusals(repo: Path, locate_ruff: Callable[[], str] = installed_ruff, locate_zizmor: Callable[[], str] | None = None) -> dict[Rule, str]:
    """Each rule that cannot judge `repo`, with its reason, as the rule runner reports it."""
    verdicts = hygiene_run(repo, locate_ruff, locate_zizmor).verdicts
    return {verdict.rule: str(verdict.refusal) for verdict in verdicts if verdict.refusal is not None}


def copy_block(example: tuple[str, ...]) -> str:
    """The lone `next:` step that shows `example`, as the run's stderr holds it: the label, then each line four spaces deep."""
    return "\n".join([f"next: {check.CONFIG_EXAMPLE_LABEL}:", *(f"    {line}" for line in example)]) + "\n"


def heard(capsys: pytest.CaptureFixture[str]) -> str:
    """Everything the door printed since the last read, stdout then stderr."""
    captured = capsys.readouterr()
    return captured.out + captured.err
