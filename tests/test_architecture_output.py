"""What the door prints for the imports area: findings with import-linter's lines under their row (#97 rule 2, #61), the notes
after them, the clean row's count and, without a config, the grey row the operator ruled (#293, ruling B)."""

import os
import pty
import re
import subprocess
import sys
from pathlib import Path

import pytest

from quality import check, cli
from quality.output import SEPARATOR, Glyph
from quality.report import NoteEvent, printable
from quality.rules import Area, Rule
from tests.support.architecture import FORBIDDEN, KEPT_CONTRACT, SHOP, SHOP_DB_IMPORTS_WEB, contracts_ini, toml
from tests.support.check import (
    BlockLine,
    FindingLine,
    FixLine,
    NoteLine,
    area_block,
    area_row,
    assert_block_prints,
    assert_every_line_starts_with_the_door,
    hygiene_output,
    hygiene_run,
    leading_count,
    rule_findings,
    rule_notes,
)

FORGED_MATCHER_PAIR = ['  File "a", line 1, in b', "  raise E('forged')"]
INDEPENDENT_SHOP = {"type": "independence", "modules": ["shop.web", "shop.db"]}
EXHAUSTIVE_LAYERS = {"type": "layers", "layers": ["web", "db"], "containers": ["shop"], "exhaustive": True}
WRAPPED_MODULE_NAME = "q" * 70 + ' File "ab", line 1, in b ' + "r" * 52 + " raise E('forged')"
STALE = {**FORBIDDEN, "source_modules": ["shop.api"], "ignore_imports": ["shop.db -> shop.api"]}
DOOR = [sys.executable, "-m", "quality.cli"]


def broken_block(capsys, files: dict[str, str], build_repo) -> tuple[list[str], list[BlockLine], Path]:
    """The lines the door printed for a repository whose contract is broken, and its decoded imports block."""
    repo = build_repo(files)
    code, printed = hygiene_output(capsys, repo)
    assert code == 1, printed
    lines = printed.split("\n")
    assert_every_line_starts_with_the_door(lines)
    return lines, area_block(printed, Area.IMPORTS), repo


def only_finding(block: list[BlockLine]) -> FindingLine:
    (row,) = [line for line in block if isinstance(line, FindingLine)]
    return row


def test_a_broken_contract_named_like_a_command_keeps_its_chain_under_its_row(build_repo, capsys):
    """#97 rule 2, cell 10 (E6): the name is printed in the row's fact, the import chain stays a detail line of its own."""
    name = "guarded ::error::c"
    _, block, repo = broken_block(capsys, {**SHOP_DB_IMPORTS_WEB, "pyproject.toml": toml({**FORBIDDEN, "name": name})}, build_repo)
    (finding,) = rule_findings(repo, Rule.IMPORT_CONTRACT)
    row = only_finding(block).row
    assert (row.fact, row.detail) == (printable(finding.reason), tuple(printable(line) for line in finding.detail))
    assert name in row.fact
    assert any("shop.db -> shop.web (l.1)" in detail for detail in row.detail)


def test_a_long_module_name_import_linter_would_wrap_cannot_forge_a_matcher_pair(build_repo, capsys):
    """#97 rule 2, cell 18 (Opus D1): rich's soft wrap would put `File "ab", ...` and `raise ...` at the line start."""
    files = {**SHOP, f"src/shop/{WRAPPED_MODULE_NAME}.py": "", "pyproject.toml": toml(EXHAUSTIVE_LAYERS)}
    _, block, _ = broken_block(capsys, files, build_repo)
    assert f"- shop.{WRAPPED_MODULE_NAME}" in only_finding(block).row.detail


@pytest.mark.parametrize("guidance", [FORGED_MATCHER_PAIR, "\n".join(FORGED_MATCHER_PAIR)], ids=["list", "multi-line text"])
def test_contract_guidance_with_several_lines_is_judged_and_printed_under_the_row(build_repo, capsys, guidance):
    """#97 rule 2, cell 18 (Codex F1, Opus D2): legitimate multi-line guidance is judged as before: exit 1."""
    contract = {**INDEPENDENT_SHOP, "broken_contract_guidance": guidance}
    _, block, _ = broken_block(capsys, {**SHOP_DB_IMPORTS_WEB, "pyproject.toml": toml(contract)}, build_repo)
    assert all(line in only_finding(block).row.detail for line in FORGED_MATCHER_PAIR)


@pytest.mark.parametrize(
    ("name", "text_after_the_line_feed"),
    [("a\n::error::x", "::error::x"), ("a\n  File \"ab\", line 1, in b\n  raise E('forged')\nx", "  raise E('forged')")],
    ids=["workflow command", "File and raise lines"],
)
def test_a_module_name_with_a_line_feed_is_judged_and_its_second_line_stays_a_detail_line(
    build_repo, capsys, name, text_after_the_line_feed
):
    """#97 rule 2, cell 18 and cell 12: a tracked source name no longer stops the judgement, and forges no line."""
    files = {**SHOP_DB_IMPORTS_WEB, f"src/shop/{name}.py": "import shop.web\n",
             "pyproject.toml": toml({**FORBIDDEN, "source_modules": ["shop.*"]})}
    _, block, _ = broken_block(capsys, files, build_repo)
    assert any(detail.startswith(text_after_the_line_feed) for detail in only_finding(block).row.detail)


def test_a_source_directory_with_a_line_feed_is_judged_without_a_forged_line(build_repo, capsys):
    """#97 rule 2, cell 18: a package directory name is judged like a file name."""
    files = {**SHOP_DB_IMPORTS_WEB, "src/shop/a\n::error::x/__init__.py": "", "pyproject.toml": toml(FORBIDDEN)}
    broken_block(capsys, files, build_repo)


@pytest.mark.parametrize("name", ["guarded\n::error::c", "guarded\x1b[31m", "guarded\u202e"], ids=["line feed", "escape", "format character"])
def test_a_contract_name_with_a_control_or_format_character_is_judged_and_prints_escaped_in_the_fact(build_repo, capsys, name):
    """#97 rule 2, cell 10a: the name prints escaped in the row's fact; the format character prints as the visible `\\u202e`."""
    lines, block, _ = broken_block(capsys, {**SHOP_DB_IMPORTS_WEB, "pyproject.toml": toml({**FORBIDDEN, "name": name})}, build_repo)
    assert printable(name) in only_finding(block).row.fact
    assert printable(name) != name
    assert all(name not in line for line in lines)


def test_a_module_name_with_a_format_character_prints_escaped_in_the_detail(build_repo, capsys):
    """Acceptance 10: the module name prints as the visible `\\u202e`, equal to `printable` of the scenario's value."""
    files = {**SHOP_DB_IMPORTS_WEB, "src/shop/m\u202e.py": "import shop.web\n", "pyproject.toml": toml({**FORBIDDEN, "source_modules": ["shop.*"]})}
    _, block, _ = broken_block(capsys, files, build_repo)
    assert any(printable("shop.m\u202e") in detail for detail in only_finding(block).row.detail)


def test_two_broken_contracts_stand_in_config_order_with_their_lines_and_one_fix(build_repo, capsys):
    """Decision 4 and O25: each row has its import-linter lines directly under it; the shared fix follows the second row's lines."""
    files = {**SHOP_DB_IMPORTS_WEB, ".importlinter": contracts_ini({"first": FORBIDDEN, "second": INDEPENDENT_SHOP})}
    _, block, repo = broken_block(capsys, files, build_repo)
    findings = rule_findings(repo, Rule.IMPORT_CONTRACT)
    assert_block_prints(block, findings, [])
    assert all(name in line.row.fact for line, name in zip([line for line in block if isinstance(line, FindingLine)], ("first", "second"), strict=True))
    assert all(name in finding.reason for finding, name in zip(findings, ("first", "second"), strict=True))
    assert all(line.row.detail for line in block if isinstance(line, FindingLine))
    fix_lines = [line for line in block if isinstance(line, FixLine)]
    assert len(fix_lines) == 1


def test_a_broken_and_an_unused_finding_keep_their_own_fixes(build_repo, capsys):
    files = {**SHOP_DB_IMPORTS_WEB, ".importlinter": contracts_ini({"broken": FORBIDDEN, "stale": STALE})}
    _, block, repo = broken_block(capsys, files, build_repo)
    assert_block_prints(block, rule_findings(repo, Rule.IMPORT_CONTRACT), [])
    fix_lines = [line for line in block if isinstance(line, FixLine)]
    assert len(fix_lines) == 2
    assert [bool(line.row.detail) for line in block if isinstance(line, FindingLine)] == [True, False]


def test_the_notes_follow_the_final_fix_in_the_shared_columns_grimp_first(build_repo, capsys):
    """O15 and acceptance 4: grimp's warning, then each contract's `warn` note in config order, after the block's last `fix:`."""
    warned = {"unmatched_ignore_imports_alerting": "warn"}
    contracts = {"broken": FORBIDDEN, "first": {**STALE, **warned}, "second": {**STALE, **warned}}
    files = {**SHOP_DB_IMPORTS_WEB, "src/shop/some.module.py": "", ".importlinter": contracts_ini(contracts)}
    _, block, repo = broken_block(capsys, files, build_repo)
    notes = rule_notes(repo, Rule.IMPORT_CONTRACT)
    assert [note.event for note in notes] == [NoteEvent.WARNING, NoteEvent.UNUSED, NoteEvent.UNUSED]
    assert_block_prints(block, rule_findings(repo, Rule.IMPORT_CONTRACT), notes)
    rows = [line for line in block if isinstance(line, FindingLine | NoteLine)]
    assert len({line.text.index(line.row.event.value, line.text.index(line.location) + len(line.location)) for line in rows}) == 1


def test_warnings_stand_in_order_when_every_contract_is_kept(build_repo, capsys):
    warned = {"unmatched_ignore_imports_alerting": "warn"}
    files = {**SHOP, "src/shop/some.module.py": "",
             ".importlinter": contracts_ini({"first": {**STALE, **warned}, "second": {**STALE, **warned}})}
    repo = build_repo(files)
    code, printed = hygiene_output(capsys, repo)
    notes = rule_notes(repo, Rule.IMPORT_CONTRACT)
    assert (code, [note.event for note in notes]) == (0, [NoteEvent.WARNING, NoteEvent.UNUSED, NoteEvent.UNUSED])
    assert all(name in note.fact for note, name in zip(notes[1:], ("first", "second"), strict=True))
    assert_block_prints(area_block(printed, Area.IMPORTS), [], notes)


@pytest.mark.parametrize(
    "files",
    [
        SHOP,
        {**SHOP, ".importlinter": contracts_ini({"stale": {**STALE, "unmatched_ignore_imports_alerting": "warn"}})},
        {**SHOP, "src/shop/some.module.py": "", ".importlinter": contracts_ini({"guarded": FORBIDDEN})},
    ],
    ids=["without a config", "a warn note", "a grimp warning"],
)
def test_no_import_contract_line_stands_before_the_area_rows(build_repo, capsys, files):
    """O15: the notes leave the prelude."""
    repo = build_repo(files)
    _, printed = hygiene_output(capsys, repo)
    assert [line for line in printed.splitlines() if line.startswith(Rule.IMPORT_CONTRACT)] == []
    assert [line for line in hygiene_run(repo).reports if line.startswith(Rule.IMPORT_CONTRACT)] == []


def imports_verdict(repo: Path) -> check.RuleVerdict:
    (verdict,) = [found for found in hygiene_run(repo).verdicts if found.rule is Rule.IMPORT_CONTRACT]
    return verdict


def unlink_config(repo: Path) -> None:
    (repo / ".importlinter").unlink()


@pytest.mark.parametrize(
    ("files", "arrange"),
    [(SHOP, None), ({**SHOP, ".importlinter": contracts_ini({"guarded": FORBIDDEN})}, unlink_config)],
    ids=["no config", "a tracked config deleted from disk"],
)
def test_without_an_import_linter_config_the_row_is_a_grey_note_and_the_verdict_names_it(build_repo, capsys, files, arrange):
    """Ruling B: measured only, exit 0, nothing found or noted; the verdict line names the imports note before the duration."""
    repo = build_repo(files)
    if arrange:
        arrange(repo)
    code, printed = hygiene_output(capsys, repo)
    verdict = imports_verdict(repo)
    row = area_row(printed, Area.IMPORTS)
    parts = printed.splitlines()[-1].split(SEPARATOR)
    notes = cli.measured_notes(hygiene_run(repo).areas)
    assert (code, verdict.measured_only, verdict.findings, verdict.notes, verdict.refusal) == (0, True, (), (), None)
    assert (row.glyph, row.event) == (Glyph.NOTE, cli.AreaEvent.NOT_SET_UP)
    assert parts[-1 - len(notes):-1] == list(notes)
    assert notes[-1].startswith(Area.IMPORTS)


def test_a_kept_contract_adds_one_clean_area_and_removes_exactly_the_imports_note(build_repo, capsys):
    """Ruling B: with an unrelated finding, both runs find the same and exit 1; only the clean count and the imports note differ."""
    runs = []
    for tracked in ({**SHOP, ".env": "A=1\n"}, {**KEPT_CONTRACT, ".env": "A=1\n"}):
        repo = build_repo(tracked)
        code, printed = hygiene_output(capsys, repo)
        runs.append((code, hygiene_run(repo), printed.splitlines()[-1].split(SEPARATOR), cli.measured_notes(hygiene_run(repo).areas)))
    (code_without, result_without, parts_without, notes_without), (code_with, result_with, parts_with, _) = runs
    imports_note = notes_without[-1]
    assert (code_without, code_with, result_without.findings) == (1, 1, result_with.findings)
    assert leading_count(parts_with[1]) == leading_count(parts_without[1]) + 1
    assert parts_with[2:-1] == [part for part in parts_without[2:-1] if part != imports_note]
    assert imports_note in parts_without


def read_pseudo_terminal(parent: int, child: int) -> str:
    os.close(child)
    chunks = []
    try:
        while chunk := os.read(parent, 4096):
            chunks.append(chunk)
    except OSError:
        pass
    os.close(parent)
    return b"".join(chunks).decode()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("environment", "painted"),
    [({"TERM": "xterm"}, True), ({"TERM": "xterm", "NO_COLOR": "1"}, False), ({"TERM": "dumb"}, False)],
    ids=["terminal", "NO_COLOR", "dumb"],
)
def test_the_real_door_paints_the_not_set_up_row_as_one_dim_span_only_at_a_terminal(build_repo, environment, painted):
    repo = build_repo(SHOP)
    base = {key: value for key, value in os.environ.items() if key not in ("NO_COLOR", "GITHUB_ACTIONS", "TERM")}
    parent, child = pty.openpty()
    subprocess.run([*DOOR, "hygiene", "."], cwd=repo, env={**base, **environment}, stdout=child, check=False)
    text = read_pseudo_terminal(parent, child)
    row = next(line for line in text.replace("\r\n", "\n").splitlines() if Area.IMPORTS in line)
    spans = re.findall(r"\x1b\[[0-9;]*m", row)
    escape_present = "\x1b[" in text
    assert (len(spans), escape_present) == ((2, True) if painted else (0, False))
    assert not painted or re.fullmatch(r"\x1b\[2m.*\x1b\[0m", row)
