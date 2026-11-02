"""Under `quality check -v`, each area shows each scanner's exit and its stderr, once gitleaks has cleared the text."""

import contextlib
import io
import json
import os
import shlex
import unicodedata
from pathlib import Path

import pytest

from quality import scanners
from quality.report import NoteEvent
from quality.rules import Area
from tests.support.advisories import reporting, uv_lock
from tests.support.check import DetailLine, FindingLine, NoteLine, area_block, area_fact, decode_block, scanner_rows
from tests.support.door import CHECK_WORD, door_in
from tests.support.python_lint import fake_ruff, ruff_entry
from tests.support.scanners import (
    CLEAN_SCANS,
    OUTPUTS,
    fake_gitleaks,
    fake_scanner,
    fake_zizmor,
    flagging,
    lacking,
    vetting,
)
from tests.support.secrets import FAKE_KEY, allow_config
from tests.support.terminal import screen, without_sgr


def streams(repo: Path, capsys, *flags: str, **keywords) -> tuple[int, str, str]:
    """The exit, stdout and stderr of `quality check <flags>` in `repo`."""
    code = door_in(repo, CHECK_WORD, *flags, clock=lambda: 0.0, **keywords)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def shouting(lines: list[str]) -> str:
    """Shell that writes `lines` to stderr."""
    return f"printf '%s\\n' {shlex.join(lines)} >&2"


def writing(name: str, line: str, exit_code: int = 0) -> str:
    """The body of scanner `name` that answers clean, then writes `line` to stderr and exits `exit_code`."""
    if name == scanners.OSV_SCANNER:
        return reporting(OUTPUTS / "osv-scanner-clean.json", exit_code, line)
    return f"{CLEAN_SCANS.get(name, CLEAN_SCANS[scanners.ACTIONLINT])}\n{shouting([line])}; exit {exit_code}"


def printing(report: Path, exit_code: int) -> str:
    return f"cat '{report}'; exit {exit_code}"


def installing(served_scanners: Path, elsewhere: Path, bodies: dict[str, str]) -> dict:
    """The door's keywords for scanners that run `bodies` (name to shell); the others answer clean."""
    nothing_said = CLEAN_SCANS[scanners.ACTIONLINT]
    for name in bodies.keys() - {scanners.ZIZMOR, scanners.RUFF}:
        fake_scanner(served_scanners, name, bodies[name])
    zizmor = fake_zizmor(elsewhere / "zizmor", bodies.get(scanners.ZIZMOR, nothing_said))
    ruff = fake_ruff(elsewhere, bodies.get(scanners.RUFF, nothing_said))
    return {"locate_zizmor": zizmor, "locate_ruff": lambda: str(ruff)}


def scanning_repository(build_repo) -> Path:
    return build_repo({".github/workflows/w.yml": "on: push\n", "uv.lock": uv_lock(), "src/a.py": "x = 1\n"})


def without_scanner_rows(printed: str) -> str:
    """`printed` without its exit rows and the marked lines directly below them."""
    kept, dropping = [], False
    for line in decode_block(printed.splitlines()):
        row = isinstance(line, NoteLine) and line.row.event is NoteEvent.EXITED
        dropping = row or (dropping and isinstance(line, DetailLine))
        if not dropping:
            kept.append(line.text)
    return "\n".join(kept) + "\n"


def test_each_scanner_stands_under_its_area_with_its_exit_and_its_stderr(build_repo, served_scanners, elsewhere, capsys):
    said = {name: f"{name} wrote this" for name in scanners.VERSION_ARGUMENTS}
    keywords = installing(served_scanners, elsewhere, {name: writing(name, line) for name, line in said.items()})
    _, out, _ = streams(scanning_repository(build_repo), capsys, "-v", **keywords)
    assert {area: scanner_rows(out, area) for area in (Area.SECRETS, Area.WORKFLOWS, Area.DEPENDENCIES, Area.CODE)} == {
        Area.SECRETS: [(scanners.GITLEAKS, "0", (said[scanners.GITLEAKS],))],
        Area.WORKFLOWS: [(scanners.ACTIONLINT, "0", (said[scanners.ACTIONLINT],)), (scanners.ZIZMOR, "0", (said[scanners.ZIZMOR],))],
        Area.DEPENDENCIES: [(scanners.OSV_SCANNER, "0", (said[scanners.OSV_SCANNER],))],
        Area.CODE: [(scanners.RUFF, "0", (said[scanners.RUFF],))],
    }


def test_a_run_without_v_prints_no_scanner_row(build_repo, served_scanners, elsewhere, capsys):
    said = "this stays unprinted"
    keywords = installing(served_scanners, elsewhere, {name: writing(name, said) for name in scanners.VERSION_ARGUMENTS if name != scanners.GITLEAKS})
    _, out, err = streams(scanning_repository(build_repo), capsys, **keywords)
    assert [line for line in decode_block(out.splitlines()) if isinstance(line, NoteLine) and line.row.event is NoteEvent.EXITED] == []
    assert said not in out + err


@pytest.mark.parametrize(
    ("files", "refusing", "code"),
    [
        pytest.param({"README.md": "x\n"}, False, 0, id="clean"),
        pytest.param({".env": "KEY=1\n"}, False, 1, id="finding"),
        pytest.param({".github/workflows/w.yml": "on: push\n"}, True, 2, id="refused"),
    ],
)
def test_v_adds_only_its_lines_and_changes_no_exit(build_repo, served_scanners, elsewhere, capsys, files, refusing, code):
    keywords = installing(served_scanners, elsewhere, {scanners.ACTIONLINT: writing(scanners.ACTIONLINT, "it refuses", 3)} if refusing else {})
    repo = build_repo(files)
    plain = streams(repo, capsys, **keywords)
    verbose = streams(repo, capsys, "-v", **keywords)
    assert (plain[0], verbose[0], verbose[2]) == (code, code, plain[2])
    assert without_scanner_rows(verbose[1]) == plain[1]


def test_a_report_never_stands_under_v(build_repo, served_scanners, elsewhere, edited_report, capsys):
    repo = build_repo({".github/workflows/a.yml": "on: push\n", ".github/workflows/w.yml": "on: push\n", "src/a.py": "x = 1\n"})

    def planted_in_the_source(entries: list) -> None:
        for place in entries[0]["locations"]:
            place["concrete"]["feature"] = FAKE_KEY

    ruff_report = json.dumps([{**ruff_entry(filename=os.path.realpath(repo / "src" / "a.py")), "fix": {"content": FAKE_KEY}}])
    bodies = {
        scanners.ACTIONLINT: printing(edited_report("actionlint-finding.json", lambda entries: entries[0].update(snippet=FAKE_KEY)), 1),
        scanners.ZIZMOR: printing(edited_report("zizmor-finding.json", planted_in_the_source), 12),
        scanners.RUFF: f"echo {shlex.quote(ruff_report)}; exit 1",
    }
    code, out, err = streams(repo, capsys, "-v", **installing(served_scanners, elsewhere, bodies))
    assert code == 1
    assert FAKE_KEY not in out + err
    assert {area: scanner_rows(out, area) for area in (Area.WORKFLOWS, Area.CODE)} == {
        Area.WORKFLOWS: [(scanners.ACTIONLINT, "1", ()), (scanners.ZIZMOR, "12", ())],
        Area.CODE: [(scanners.RUFF, "1", ())],
    }


@pytest.mark.parametrize(
    ("name", "area"),
    [(scanners.ACTIONLINT, Area.WORKFLOWS), (scanners.ZIZMOR, Area.WORKFLOWS), (scanners.OSV_SCANNER, Area.DEPENDENCIES), (scanners.RUFF, Area.CODE)],
)
def test_a_successful_scans_warning_is_vetted_too(build_repo, served_scanners, elsewhere, capsys, summary, name, area):
    fake_gitleaks(served_scanners, on_scan=vetting(flagging(FAKE_KEY)))
    keywords = installing(served_scanners, elsewhere, {name: writing(name, f"aws_access_key_id = {FAKE_KEY}")})
    repo = scanning_repository(build_repo)
    plain_code = streams(repo, capsys, **keywords)[0]
    code, out, err = streams(repo, capsys, "-v", **keywords)
    assert code == plain_code
    assert [row for row in scanner_rows(out, area) if row[0] == name] == [(name, "0 · 1 stderr line withheld · not cleared by gitleaks", ())]
    assert FAKE_KEY not in out + err
    assert not summary.exists()


@pytest.mark.parametrize(
    ("name", "area", "exit_code"),
    [
        (scanners.ACTIONLINT, Area.WORKFLOWS, 3),
        (scanners.ZIZMOR, Area.WORKFLOWS, 3),
        (scanners.OSV_SCANNER, Area.DEPENDENCIES, 127),
        (scanners.RUFF, Area.CODE, 2),
    ],
)
def test_a_value_above_the_last_five_lines_withholds_every_line(build_repo, served_scanners, elsewhere, capsys, summary, name, area, exit_code):
    fake_gitleaks(served_scanners, on_scan=vetting(flagging(FAKE_KEY)))
    lines = [f"aws_access_key_id = {FAKE_KEY}", *(f"line {number}" for number in range(2, 9))]
    keywords = installing(served_scanners, elsewhere, {name: writing(name, "\n".join(lines), exit_code)})
    _, out, err = streams(scanning_repository(build_repo), capsys, "-v", **keywords)
    assert [line for line in lines if line in out + err] == []
    assert [row for row in scanner_rows(out, area) if row[0] == name] == [(name, f"{exit_code} · 8 stderr lines withheld · not cleared by gitleaks", ())]
    assert "8 stderr lines withheld" in area_fact(out, area)
    assert not summary.exists()


@pytest.mark.parametrize(
    ("on_vet", "gitleaks_lacking", "code"),
    [
        pytest.param('echo "[]" > "$report"; exit 3', False, 0, id="exit 3"),
        pytest.param("exit 0", False, 0, id="no report"),
        pytest.param(None, True, 2, id="gitleaks missing"),
    ],
)
def test_a_vet_that_cannot_answer_withholds_the_lines_under_v(build_repo, served_scanners, use_mise, elsewhere, capsys, on_vet, gitleaks_lacking, code):
    if gitleaks_lacking:
        use_mise(lacking(served_scanners, scanners.GITLEAKS))
    else:
        fake_gitleaks(served_scanners, on_scan=vetting(on_vet))
    keywords = installing(served_scanners, elsewhere, {scanners.ACTIONLINT: writing(scanners.ACTIONLINT, "boom")})
    repo = build_repo({".github/workflows/w.yml": "on: push\n"})
    plain_code = streams(repo, capsys, **keywords)[0]
    exit_code, out, err = streams(repo, capsys, "-v", **keywords)
    assert (exit_code, plain_code) == (code, code)
    assert scanner_rows(out, Area.WORKFLOWS)[0] == (scanners.ACTIONLINT, "0 · 1 stderr line withheld · not cleared by gitleaks", ())
    assert "boom" not in out + err


@pytest.mark.parametrize("opening", [r"\033[31m", r"\302\204"], ids=["ESC", "C1"])
def test_a_terminal_code_is_withheld_under_v(build_repo, served_scanners, elsewhere, capsys, summary, opening):
    fake_gitleaks(served_scanners, on_scan=vetting(flagging(FAKE_KEY)))
    keywords = installing(served_scanners, elsewhere, {scanners.ACTIONLINT: f"{CLEAN_SCANS[scanners.ACTIONLINT]}\nprintf '{opening}red\\nplain\\n' >&2"})
    code, out, err = streams(build_repo({".github/workflows/w.yml": "on: push\n"}), capsys, "-v", **keywords)
    assert code == 0
    assert scanner_rows(out, Area.WORKFLOWS)[0] == (scanners.ACTIONLINT, "0 · 2 stderr lines withheld · not cleared by gitleaks", ())
    assert [character for character in out + err if unicodedata.category(character) == "Cc" and character != "\n"] == []
    assert not (served_scanners / "gitleaks.vetted").exists()
    assert not summary.exists()


def test_v_leaves_a_scanner_areas_findings_and_notes_as_they_are(build_repo, served_scanners, elsewhere, capsys):
    fake_gitleaks(served_scanners, on_scan=f'cp {OUTPUTS / "gitleaks-finding.json"} "$report"; exit {scanners.GITLEAKS_LEAKS_FOUND}')
    keywords = installing(served_scanners, elsewhere, {scanners.ACTIONLINT: printing(OUTPUTS / "actionlint-finding.json", scanners.ACTIONLINT_FINDINGS)})
    repo = build_repo(
        {
            ".github/workflows/w.yml": "on: push\n",
            "quality/config.toml": allow_config(("aws-access-token", "deploy/config.txt", "PLACEHOLDER-SECRET-VALUE", "recorded test value")),
        }
    )
    plain = streams(repo, capsys, **keywords)
    verbose = streams(repo, capsys, "-v", **keywords)
    shown = [line for area in (Area.SECRETS, Area.WORKFLOWS) for line in area_block(plain[1], area)]
    assert [isinstance(line, FindingLine) for line in shown].count(True) == 1
    assert [line.row.event for line in shown if isinstance(line, NoteLine)] == [NoteEvent.ALLOWED]
    assert (verbose[0], without_scanner_rows(verbose[1])) == (plain[0], plain[1])


def test_a_raw_line_stands_escaped_behind_the_marker(build_repo, served_scanners, elsewhere, capsys):
    raw = (
        r"::warning::forged\nred\342\200\256\nbefore\342\200\250after\342\200\251last\013part\na\034b"
        r"\nfirst\nsecond\n\none\rtwo\n"
    )
    keywords = installing(served_scanners, elsewhere, {scanners.ACTIONLINT: f"{CLEAN_SCANS[scanners.ACTIONLINT]}\nprintf '{raw}' >&2"})
    _, out, _ = streams(build_repo({".github/workflows/w.yml": "on: push\n"}), capsys, "-v", **keywords)
    assert [line for line in out.splitlines() if line.startswith("      |")] == [
        "      | ::warning::forged",
        "      | red\\u202e",
        "      | before\\u2028after\\u2029last\\x0bpart",
        "      | a\\x1cb",
        "      | first",
        "      | second",
        "      |",
        "      | one",
        "      | two",
    ]
    assert [name for name in ("RIGHT-TO-LEFT OVERRIDE", "LINE SEPARATOR", "PARAGRAPH SEPARATOR") if unicodedata.lookup(name) in out] == []


def test_at_a_terminal_a_raw_line_is_dim_and_whole(build_repo, served_scanners, elsewhere, window):
    line = "x" * 70
    keywords = installing(served_scanners, elsewhere, {scanners.ACTIONLINT: writing(scanners.ACTIONLINT, line)})
    terminal = window(40)
    with contextlib.redirect_stdout(terminal.stream), contextlib.redirect_stderr(io.StringIO()):
        door_in(build_repo({".github/workflows/w.yml": "on: push\n"}), CHECK_WORD, "-v", clock=lambda: 0.0, environment={"TERM": "xterm"}, **keywords)
    raw = terminal.read()
    assert f"\x1b[2m      | {line}\x1b[0m" in raw
    assert f"\x1b[2m    · {scanners.ACTIONLINT}  {NoteEvent.EXITED.value}  0\x1b[0m" in raw
    assert line in without_sgr(raw)


def test_a_scanners_rows_stand_after_the_areas_findings_and_notes(build_repo, served_scanners, capsys):
    fake_gitleaks(served_scanners, on_scan=f'cp {OUTPUTS / "gitleaks-finding.json"} "$report"; exit 7')
    _, out, _ = streams(build_repo({"README.md": "x\n"}), capsys, "-v")
    block = area_block(out, Area.SECRETS)
    assert (isinstance(block[0], FindingLine), isinstance(block[-1], NoteLine), block[-1].row.event, block[-1].row.fact) == (True, True, NoteEvent.EXITED, "7")


def test_osv_scanners_lines_name_paths_as_the_refusal_does(build_repo, served_scanners, elsewhere, capsys):
    repo = build_repo({"uv.lock": uv_lock()})
    lock = os.path.realpath(repo / "uv.lock")
    body = f'echo "read {lock} in $PWD" >&2\n{reporting(OUTPUTS / "osv-scanner-clean.json", 0)}'
    fake_gitleaks(served_scanners, on_scan=vetting('echo "[]" > "$report"; exit 0'))
    _, out, _ = streams(repo, capsys, "-v", **installing(served_scanners, elsewhere, {scanners.OSV_SCANNER: body}))
    assert [(scanner, fact, details[0]) for scanner, fact, details in scanner_rows(out, Area.DEPENDENCIES)] == [
        (scanners.OSV_SCANNER, "0", "read uv.lock in <scratch>")
    ]
    assert str(repo) not in out
    assert "read uv.lock in <scratch>\n" in (served_scanners / "gitleaks.vetted").read_text()


def test_a_scanner_that_never_ran_has_no_row(build_repo, capsys):
    _, out, _ = streams(build_repo({"README.md": "x\n"}), capsys, "-v")
    exits = [line.location for line in decode_block(out.splitlines()) if isinstance(line, NoteLine) and line.row.event is NoteEvent.EXITED]
    assert exits == [scanners.GITLEAKS]


def test_the_raw_lines_stand_below_the_row_the_live_line_gave_way_to(build_repo, window):
    terminal = window(80)
    with contextlib.redirect_stdout(terminal.stream), contextlib.redirect_stderr(terminal.stream):
        door_in(build_repo({"README.md": "x\n"}), CHECK_WORD, "-v", clock=lambda: 0.0, environment={"TERM": "xterm"})
    lines = screen(terminal.read()).splitlines()
    assert "⠋" not in "".join(lines)
    row = next(number for number, line in enumerate(lines) if line.split()[1:2] == [Area.SECRETS])
    assert row < next(number for number, line in enumerate(lines) if line.strip().startswith(f"· {scanners.GITLEAKS}"))
