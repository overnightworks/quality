"""The live line the door shows on a terminal's stderr while a scanner runs, and the streams that never get it."""

import contextlib
import io
import os
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

import pytest

from quality import check, cli, scanners
from tests.support.advisories import uv_lock
from tests.support.door import check_door, staged_door
from tests.support.git_repositories import stage
from tests.support.python_lint import fake_ruff
from tests.support.scanners import CLEAN_SCANS, OUTPUTS, fake_gitleaks, fake_scanner, fake_zizmor, lacking
from tests.support.terminal import PseudoTerminal, Terminal, run_on, screen, without_sgr


@pytest.fixture
def xterm() -> dict[str, str]:
    return {"TERM": "xterm"}


def showing(name: str, answer: str) -> str:
    """A scanner body that writes its own name to the terminal under test before it answers."""
    return f'printf {name} > "$LIVE_TERMINAL"\n{answer}'


def staged_door_on(repo: Path, stdout: TextIO, stderr: TextIO, environment: dict[str, str]) -> int:
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        return staged_door(repo, environment=environment, clock=lambda: 0.0)


def test_each_scanner_runs_under_its_own_live_line(build_repo, served_scanners, window, tmp_path, monkeypatch, xterm):
    repo = build_repo({".github/workflows/w.yml": "on: push\n", "uv.lock": uv_lock(), "src/a.py": "x = 1\n"})
    terminal = window(80)
    terminal.stream.reconfigure(line_buffering=False)
    monkeypatch.setenv("LIVE_TERMINAL", os.ttyname(terminal.stream.fileno()))
    for name, answer in CLEAN_SCANS.items():
        fake_scanner(served_scanners, name, showing(name, answer))
    locate_zizmor = fake_zizmor(tmp_path / "zizmor", showing(scanners.ZIZMOR, "echo '[]'"))
    locate_ruff = str(fake_ruff(tmp_path, showing(scanners.RUFF, "echo '[]'")))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(terminal.stream):
        check_door(repo, locate_ruff=lambda: locate_ruff, locate_zizmor=locate_zizmor, clock=lambda: 0.0, environment=xterm)
    assert without_sgr(terminal.read()).split("\r\x1b[K") == [
        "  ⠋ secrets       gitleaks\rgitleaks",
        "  ⠋ workflows     actionlint\ractionlint",
        "  ⠋ workflows     zizmor\rzizmor",
        "  ⠋ dependencies  osv-scanner\rosv-scanner",
        "  ⠋ code          ruff\rruff",
        "",
    ]


@pytest.mark.parametrize(
    ("files", "scanner_body"),
    [
        pytest.param({"README.md": "x\n"}, None, id="clean"),
        pytest.param({"compose.yaml": "services: [\n"}, None, id="refused by a rule"),
        pytest.param({"README.md": "x\n"}, "exit 3", id="refused by its scanner"),
    ],
)
def test_each_row_stands_where_its_live_line_stood_on_one_terminal(build_repo, served_scanners, window, files, scanner_body, xterm):
    repo = build_repo(files)
    if scanner_body:
        fake_scanner(served_scanners, scanners.GITLEAKS, scanner_body)
    out, err = io.StringIO(), io.StringIO()
    plain_code = run_on(repo, out, err, xterm)
    terminal = window(200)
    code = run_on(repo, terminal.stream, terminal.stream, xterm)
    assert (screen(terminal.read()), code) == (out.getvalue() + err.getvalue(), plain_code)


@pytest.mark.parametrize(
    ("files", "code"),
    [
        pytest.param({"README.md": "x\n"}, 0, id="clean"),
        pytest.param({"src/.env": "A=1\n"}, 1, id="finding"),
        pytest.param({"compose.yaml": "services: [\n"}, 2, id="refused"),
    ],
)
def test_stdout_is_byte_equal_with_and_without_a_terminal_on_stderr(build_repo, window, files, code, xterm):
    repo = build_repo(files)
    terminal = window(80)
    shown = io.StringIO()
    shown_code = run_on(repo, shown, terminal.stream, xterm)
    plain, plain_err = io.StringIO(), io.StringIO()
    plain_code = run_on(repo, plain, plain_err, xterm)
    assert (plain_code, shown_code, shown.getvalue(), screen(terminal.read())) == (code, code, plain.getvalue(), plain_err.getvalue())


def terminal_at(columns: int) -> Callable[..., tuple[TextIO, Callable[[], str]]]:
    def open_stream(window: Callable[[int], PseudoTerminal], _tmp_path: Path, _stack: contextlib.ExitStack) -> tuple[TextIO, Callable[[], str]]:
        terminal = window(columns)
        return terminal.stream, terminal.read

    return open_stream


def in_memory(kind: type[io.StringIO]) -> Callable[..., tuple[TextIO, Callable[[], str]]]:
    def open_stream(_window: Callable[[int], PseudoTerminal], _tmp_path: Path, _stack: contextlib.ExitStack) -> tuple[TextIO, Callable[[], str]]:
        stream = kind()
        return stream, stream.getvalue

    return open_stream


def a_file(_window: Callable[[int], PseudoTerminal], tmp_path: Path, stack: contextlib.ExitStack) -> tuple[TextIO, Callable[[], str]]:
    path = tmp_path / "stderr"
    stream = stack.enter_context(path.open("w", encoding="utf-8"))

    def written() -> str:
        stream.flush()
        return path.read_text(encoding="utf-8")

    return stream, written


@pytest.mark.parametrize(
    ("environment", "open_stream"),
    [
        pytest.param({"NO_COLOR": "1"}, terminal_at(80), id="NO_COLOR"),
        pytest.param({"TERM": "dumb"}, terminal_at(80), id="TERM=dumb"),
        pytest.param({}, terminal_at(0), id="no window"),
        pytest.param({}, in_memory(Terminal), id="no descriptor"),
        pytest.param({}, a_file, id="a file"),
        pytest.param({}, in_memory(io.StringIO), id="a string"),
    ],
)
def test_a_stream_that_may_not_show_the_line_gets_no_byte(build_repo, window, tmp_path, environment, open_stream, xterm):
    repo = build_repo({"README.md": "x\n"})
    with contextlib.ExitStack() as stack:
        stream, written = open_stream(window, tmp_path, stack)
        run_on(repo, io.StringIO(), stream, xterm | environment)
        assert written() == ""


def test_an_exception_in_a_check_takes_the_line_down(build_repo, window, monkeypatch, xterm):
    class Broken(Exception):
        pass

    def raising(*_arguments: object) -> None:
        raise Broken

    repo = build_repo({"README.md": "x\n"})
    monkeypatch.setattr(check, "check_secrets", raising)
    terminal = window(80)
    stdout = io.StringIO()
    with pytest.raises(Broken):
        run_on(repo, stdout, terminal.stream, xterm)
    assert screen(terminal.read()) == ""


@pytest.mark.parametrize(
    ("outcome", "marker"),
    [pytest.param("clean", "gitleaks", id="clean"), pytest.param("finding", "gitleaks", id="finding"), pytest.param("refused", "", id="refused")],
)
def test_the_staged_door_shows_its_scanner_and_takes_the_line_down_before_its_answer(
    build_repo, served_scanners, use_mise, window, monkeypatch, xterm, outcome, marker
):
    repo = build_repo({"README.md": "x\n"})
    stage(repo, {"staged.txt": "x\n"})
    if outcome == "finding":
        fake_gitleaks(served_scanners, on_scan=showing(scanners.GITLEAKS, f'cp "{OUTPUTS / "gitleaks-finding.json"}" "$report"; exit 7'))
    else:
        fake_scanner(served_scanners, scanners.GITLEAKS, showing(scanners.GITLEAKS, CLEAN_SCANS[scanners.GITLEAKS]))
    if outcome == "refused":
        use_mise(lacking(served_scanners, scanners.GITLEAKS))
    monkeypatch.setenv("LIVE_TERMINAL", os.devnull)
    plain_out, plain_err = io.StringIO(), io.StringIO()
    plain_code = staged_door_on(repo, plain_out, plain_err, xterm)
    terminal = window(200)
    terminal.stream.reconfigure(line_buffering=False)
    monkeypatch.setenv("LIVE_TERMINAL", os.ttyname(terminal.stream.fileno()))
    code = staged_door_on(repo, terminal.stream, terminal.stream, xterm)
    answer = plain_out.getvalue() + plain_err.getvalue()
    expected = {"clean": cli.EXIT_CLEAN, "finding": cli.EXIT_FINDINGS, "refused": cli.EXIT_CANNOT_JUDGE}[outcome]
    assert (plain_code, code) == (expected, expected)
    assert without_sgr(terminal.read()) == f"  ⠋ secrets       gitleaks\r{marker}\r\x1b[K{answer}"
