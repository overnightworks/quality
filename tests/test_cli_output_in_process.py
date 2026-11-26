import io
import os
import pty
import signal
import subprocess
import sys
from typing import NoReturn

import pytest

from quality import cli, config, pin, report
from tests.support.check import (
    CASE_TWO,
    ROOT_ALLOWS_ENV,
    STOP_LINE,
    check_result,
    frame_run,
    stop_token,
)
from tests.support.door import CHECK_WORD, MODULE_DOOR, door_environment
from tests.support.processes import process_is_gone
from tests.support.scanners import fake_scanner
from tests.support.terminal import read_pseudo_terminal, without_sgr


def run_in_process(environment: dict[str, str], exit_code: int = 3) -> int:
    with pytest.raises(SystemExit) as exited:
        cli.run(door=lambda: exit_code, environment=environment)
    return exited.value.code


def test_run_stops_the_commands_on_both_streams_and_keeps_the_door_exit(capsys):
    """MASK-14: in Actions run stops the commands on both streams and keeps the door's exit."""
    assert run_in_process({"GITHUB_ACTIONS": "true"}) == 3
    captured = capsys.readouterr()
    assert stop_token(captured.out) == stop_token(captured.err)
    assert len(captured.out.splitlines()) == len(captured.err.splitlines()) == 1


def test_run_prints_nothing_outside_actions_and_keeps_the_door_exit(capsys):
    """MASK-14: outside Actions run prints nothing and keeps the door's exit."""
    assert run_in_process({}) == 3
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


def test_run_skips_a_closed_stream_in_process(capsys, monkeypatch):
    """MASK-14: a stream that is None is skipped."""
    monkeypatch.setattr(sys, "stderr", None)
    assert run_in_process({"GITHUB_ACTIONS": "true"}) == 3
    stdout = capsys.readouterr().out
    stop_token(stdout)
    assert len(stdout.splitlines()) == 1


class SharedLogStream(io.StringIO):
    def __init__(self, name: str, log: list[tuple[str, str]]):
        super().__init__()
        self.name, self.log = name, log

    def write(self, text: str) -> int:
        self.log.append((self.name, text))
        return len(text)

    def flush(self) -> None:
        self.log.append((self.name, "flush"))

    def reconfigure(self, **_options) -> None:
        pass


def test_run_writes_and_flushes_the_stop_on_stderr_then_stdout_before_the_door_is_entered(monkeypatch):
    """MASK-14: the stop goes to stderr, then to stdout, each flushed before the door is entered."""
    log: list[tuple[str, str]] = []
    monkeypatch.setattr(sys, "stderr", SharedLogStream("stderr", log))
    monkeypatch.setattr(sys, "stdout", SharedLogStream("stdout", log))

    def door() -> int:
        log.append(("door", "entered"))
        return 0

    with pytest.raises(SystemExit):
        cli.run(door=door, environment={"GITHUB_ACTIONS": "true"})
    events = [event for event in log if event[1] != "\n"]
    stderr_stop, stdout_stop = events[0][1], events[2][1]
    assert events == [
        ("stderr", stderr_stop),
        ("stderr", "flush"),
        ("stdout", stdout_stop),
        ("stdout", "flush"),
        ("door", "entered"),
    ]
    assert STOP_LINE.fullmatch(stderr_stop)
    assert stdout_stop == stderr_stop


def raising_interrupt() -> int:
    raise KeyboardInterrupt


@pytest.mark.parametrize("interrupted_door", [raising_interrupt, lambda: 128 + signal.SIGINT], ids=["a Ctrl-C", "an exit of 130"])
def test_an_interrupt_in_the_door_ends_the_run_as_interrupted_through_the_injected_sender(capsys, interrupted_door):
    """CLOSING-31, CLOSING-32, MERGE-113: an interrupt, or a merge run over several names that ends interrupted, ends the
    run by SIGINT sent to its own process, with exit 130 where that signal does not end it, and prints nothing."""
    sent = []

    saved = signal.getsignal(signal.SIGINT)
    try:
        with pytest.raises((SystemExit, KeyboardInterrupt)) as exited:
            cli.run(door=interrupted_door, environment={}, send_signal=lambda pid, number: sent.append((pid, number)))
    finally:
        signal.signal(signal.SIGINT, saved)
    assert sent == [(os.getpid(), signal.SIGINT)]
    assert exited.value.code == 128 + signal.SIGINT == 130
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


@pytest.mark.integration
@pytest.mark.parametrize("under_shell", [True, False], ids=["under a shell", "direct"])
def test_ctrl_c_during_a_scan_ends_the_run_as_interrupted_without_a_traceback(clean_repo, served_scanners, tmp_path, under_shell):
    """CLOSING-31, CLOSING-32: an interrupt during a scan dies by SIGINT, which a shell reads as 130, prints no
    traceback, leaves no scanner running and removes the scratch directory."""
    scanner_pid = tmp_path / "scanner.pid"
    scratch_parent = tmp_path / "scratch"
    scratch_parent.mkdir()
    fake_scanner(
        served_scanners, "gitleaks",
        on_scan='echo $$ > "$SCANNER_PID"; head -c 1048576 /dev/zero; kill -INT $PPID; exec sleep 60',
    )
    door = [*MODULE_DOOR, CHECK_WORD]
    command = ["sh", "-c", '"$@"; echo "exit $?" >&2', "sh", *door] if under_shell else door
    environment = os.environ | {"SCANNER_PID": str(scanner_pid), "TMPDIR": str(scratch_parent)}
    interrupted = subprocess.run(command, cwd=clean_repo, env=environment, capture_output=True, text=True, timeout=60, check=False)
    if under_shell:
        assert interrupted.stderr.rstrip().endswith("exit 130")
    else:
        assert interrupted.returncode == -signal.SIGINT
    assert "Traceback" not in interrupted.stdout + interrupted.stderr
    assert "KeyboardInterrupt" not in interrupted.stdout + interrupted.stderr
    assert process_is_gone(int(scanner_pid.read_text()))
    assert list(scratch_parent.iterdir()) == []


@pytest.mark.integration
def test_a_folder_outside_any_repository_prints_only_the_location_pair(tmp_path, capsys, own_home):
    """CLOSING-17, CLOSING-18, CLOSING-19, CLOSING-21: a folder outside any repository prints only the location pair and
    exits 2."""
    code, out, err = frame_run(capsys, tmp_path, judging=pin.judge_here)
    assert (code, out, err) == (2, [], [f"error: not inside a git work tree · {tmp_path}", "next: run quality check inside a repository"])


@pytest.mark.integration
@pytest.mark.parametrize(
    ("home_spelling", "folder_of", "tail_of"),
    [
        pytest.param("{home}", lambda home: home / "Downloads", lambda folder: f"~/{folder.name}", id="under the home folder"),
        pytest.param("{home}", lambda home: home, lambda folder: "~", id="the home folder itself"),
        pytest.param("{home}", lambda home: home.with_name(f"{home.name}2"), str, id="outside it"),
        pytest.param("{home}/.", lambda home: home / "Downloads", lambda folder: f"~/{folder.name}", id="an untidy home"),
    ],
)
def test_the_folder_is_spoken_with_the_home_folder_as_a_tilde(own_home, monkeypatch, capsys, home_spelling, folder_of, tail_of):
    """CLOSING-18, CLOSING-25: the folder is spoken with the home folder as `~`, and as its absolute path outside it."""
    folder = folder_of(own_home)
    folder.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", home_spelling.format(home=own_home))
    code, out, err = frame_run(capsys, folder, judging=pin.judge_here)
    assert (code, out, err) == (2, [], [f"error: not inside a git work tree · {tail_of(folder)}", "next: run quality check inside a repository"])


def unknown_user(uid: int) -> NoReturn:
    raise KeyError(uid)


@pytest.mark.integration
def test_an_unknown_home_folder_speaks_the_absolute_folder_and_never_a_traceback(tmp_path, monkeypatch, capsys):
    """CLOSING-25, CLOSING-26: where no home folder is known the folder stands as its absolute path, and no traceback
    follows."""
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setattr("pwd.getpwuid", unknown_user)
    code, out, err = frame_run(capsys, tmp_path, judging=pin.judge_here)
    assert (code, out, err) == (2, [], [f"error: not inside a git work tree · {tmp_path}", "next: run quality check inside a repository"])


@pytest.mark.integration
def test_a_folder_outside_any_repository_is_outside_in_every_locale(tmp_path, fake_git, monkeypatch, capsys, own_home):
    """CLOSING-20: git is asked in the C locale, whatever the machine speaks: the test's git words its refusal in German
    unless it is asked there, so the test needs no installed catalogue."""
    fake_git(
        'if [ "$LC_ALL" = C ]; then echo "fatal: not a git repository (or any of the parent directories): .git" >&2; '
        'else echo "fatal: Kein Git-Repository (oder irgendein Elternverzeichnis): .git" >&2; fi; exit 128'
    )
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    code, out, err = frame_run(capsys, tmp_path, judging=pin.judge_here)
    assert (code, out, err[0]) == (2, [], f"error: not inside a git work tree · {tmp_path}")


@pytest.mark.integration
@pytest.mark.parametrize(("environment", "coloured"), [({"TERM": "xterm"}, True), ({"TERM": "xterm", "NO_COLOR": "1"}, False)], ids=["terminal", "NO_COLOR"])
def test_the_real_door_colours_a_pseudo_terminal_only_without_no_color(clean_repo, environment, coloured):
    """COLOUR-1, COLOUR-6: the real door paints a pseudo-terminal, and prints it plain under `NO_COLOR`."""
    parent, child = pty.openpty()
    base = {key: value for key, value in os.environ.items() if key not in ("NO_COLOR", "GITHUB_ACTIONS")}
    done = subprocess.run([*MODULE_DOOR, CHECK_WORD], cwd=clean_repo, env={**base, **environment}, stdout=child, stderr=subprocess.PIPE, check=False)
    text = read_pseudo_terminal(parent, child)
    assert (done.returncode, "\x1b[" in text, "✓" in text) == (0, coloured, True), text


@pytest.mark.integration
@pytest.mark.parametrize("environment", [{"TERM": "xterm"}, {"TERM": "xterm", "NO_COLOR": "1"}, {"TERM": "dumb"}, None], ids=["terminal", "NO_COLOR", "dumb", "pipe"])
def test_the_real_door_paints_the_files_block_only_at_a_terminal_and_prints_the_same_words(build_repo, environment):
    """COLOUR-1, COLOUR-6: SGR only in the first form; stripped of it, the lines equal the piped ones."""
    repo = build_repo({**CASE_TWO, **ROOT_ALLOWS_ENV})
    base = door_environment()
    piped = subprocess.run([*MODULE_DOOR, CHECK_WORD], cwd=repo, env=base, capture_output=True, text=True, check=False)
    parent, child = pty.openpty() if environment else (None, subprocess.PIPE)
    done = subprocess.run([*MODULE_DOOR, CHECK_WORD], cwd=repo, env={**base, **(environment or {})}, stdout=child, stderr=subprocess.PIPE, check=False, text=True)
    text = read_pseudo_terminal(parent, child) if environment else done.stdout
    words = [without_sgr(line) for line in text.replace("\r\n", "\n").splitlines()]
    assert ("\x1b[" in text) is (environment == {"TERM": "xterm"}), text
    assert [line for line in words if not line.startswith(("!", "✓")) or " · " not in line] == [line for line in piped.stdout.splitlines() if not line.startswith(("!", "✓")) or " · " not in line]
    assert (piped.stderr, done.stderr) == ("", "")


REFUSED_CONFIG = {"quality/config.toml": '[gate]\nmandatory_jobs = ["checks"]\n[[size.generated]]\npath = "x.ts"\njob = "other"\n'
                  'item = "#1"\ndate = 2026-10-30\nreason = "r"\n'}


@pytest.mark.integration
@pytest.mark.parametrize(
    ("environment", "coloured"),
    [({"TERM": "xterm"}, True), ({"TERM": "xterm", "NO_COLOR": "1"}, False), ({"TERM": "dumb"}, False), (None, False)],
    ids=["terminal", "NO_COLOR", "dumb", "pipe"],
)
def test_the_real_door_prints_every_config_example_line_whole_in_each_stderr_form(build_repo, environment, coloured):
    """COLOUR-1, COLOUR-6, REFUSAL-15, REFUSAL-16, REFUSAL-17, REFUSAL-24: after removing quality's SGR, the label and each
    example line stand whole on their own lines, one level deeper, in all four forms; colour only at a terminal."""
    repo = build_repo(REFUSED_CONFIG)
    base = door_environment()
    parent, child = pty.openpty() if environment else (None, subprocess.PIPE)
    done = subprocess.run([*MODULE_DOOR, CHECK_WORD], cwd=repo, env={**base, **(environment or {})}, stdout=subprocess.DEVNULL,
                          stderr=child, check=False, text=environment is None)
    if environment:
        text = read_pseudo_terminal(parent, child)
    else:
        text = done.stderr
    stripped = without_sgr(text)
    example = [f"    {line}" for line in config.GENERATED_FILE_EXAMPLE]
    expected = [f"error: {cli.refusal_error(check_result(repo).areas)}", f"next: {report.CONFIG_EXAMPLE_LABEL}:", *example]
    assert done.returncode == 2
    assert ("\x1b[" in text) is coloured, text
    assert stripped.replace("\r\n", "\n").splitlines() == expected
