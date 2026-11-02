import contextlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

import quality
from quality import cli, output, report
from quality.output import SEPARATOR
from tests.support.terminal import Terminal

PLAIN_ENVIRONMENT_DROPS = ("GITHUB_ACTIONS", "NO_COLOR", "PYTHON_COLORS", "FORCE_COLOR")


def door_environment(**extra: str) -> dict[str, str]:
    """The process environment without the variables that change how the door paints or stops commands."""
    kept = {key: value for key, value in os.environ.items() if key not in PLAIN_ENVIRONMENT_DROPS}
    return kept | extra


def door_argv(*arguments: str) -> list[str]:
    return [sys.executable, "-m", "quality.cli", *arguments]


def run_door(arguments: list[str], folder: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        door_argv(*arguments), cwd=folder, env=door_environment(**extra), capture_output=True, text=True, timeout=60, check=False
    )


def help_of(capsys, words: list[str]) -> str:
    """The stdout of `quality <words> --help`, which must end in a clean exit with nothing on stderr."""
    with pytest.raises(SystemExit) as stopped:
        cli.main([*words, "--help"])
    printed = capsys.readouterr()
    assert (stopped.value.code, printed.err) == (0, "")
    return printed.out


def leaf_paths(parser: cli.DoorParser, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    if not parser.commands:
        return [prefix]
    return [path for word, child in parser.commands.items() for path in leaf_paths(child, (*prefix, word))]


def headings(lines: list[str]) -> list[str]:
    return [line for line in lines if line.endswith(":") and not line.startswith(" ")]


def entries_under(lines: list[str], heading: str) -> list[str]:
    """The entries (lines indented by exactly two spaces) between `heading` and the next heading."""
    start = lines.index(heading) + 1
    stop = next((number for number in range(start, len(lines)) if headings([lines[number]])), len(lines))
    return [line for line in lines[start:stop] if line.startswith("  ") and not line.startswith("   ")]


def path_of(entry: str) -> tuple[str, ...]:
    words: list[str] = []
    for word in entry.split():
        if not word.islower():
            break
        words.append(word)
    return tuple(words)


def commands_of(*group: str) -> str:
    parser = cli.build_parser()
    for word in group:
        parser = parser.commands[word]
    return ", ".join(parser.commands)


def test_the_help_stands_in_the_pictures_frame(capsys):
    lines = help_of(capsys, []).splitlines()
    assert (lines[0], headings(lines), lines[-1], max(len(line) for line in lines) <= 80) == (
        "usage: quality COMMAND",
        ["Commands:", "Run by the gate, not by people:", "Options:"],
        "Exit codes: 0 yes, 1 no, 2 could not answer, 130 interrupted.",
        True,
    )


def test_every_command_path_stands_in_the_help_once_and_every_entry_is_one(capsys):
    lines = help_of(capsys, []).splitlines()
    shown = [path_of(entry) for heading in headings(lines)[:2] for entry in entries_under(lines, heading)]
    assert sorted(shown) == sorted(leaf_paths(cli.build_parser()))


def test_verdict_and_sonar_stand_apart_under_run_by_the_gate(capsys):
    lines = help_of(capsys, []).splitlines()
    by_people = {path_of(entry)[0] for entry in entries_under(lines, "Commands:")}
    by_the_gate = {path_of(entry)[0] for entry in entries_under(lines, "Run by the gate, not by people:")}
    assert (by_the_gate, by_the_gate & by_people) == ({"verdict", "sonar"}, set())


@pytest.mark.parametrize(
    "words",
    [
        pytest.param([], id="quality"),
        pytest.param(["hygiene"], id="hygiene"),
        pytest.param(["baseline"], id="baseline"),
        pytest.param(["baseline", "write"], id="baseline write"),
        pytest.param(["scan-staged"], id="scan-staged"),
        pytest.param(["verdict"], id="verdict"),
        pytest.param(["sonar"], id="sonar"),
        pytest.param(["sonar", "properties"], id="sonar properties"),
        pytest.param(["sonar", "findings"], id="sonar findings"),
        pytest.param(["emergency-merge"], id="emergency-merge"),
        pytest.param(["scoreboard"], id="scoreboard"),
    ],
)
def test_each_doors_help_goes_to_stdout_alone_and_exits_0(capsys, words):
    first = help_of(capsys, words).splitlines()[0]
    expected = ["quality", *words] if words else ["quality", "COMMAND"]
    assert first.split()[1:][: len(expected)] == expected


@pytest.mark.parametrize("words", [[], ["hygiene"], ["sonar", "properties"]], ids=["quality", "hygiene", "sonar properties"])
def test_short_help_equals_long_help(capsys, words):
    long = help_of(capsys, words)
    with pytest.raises(SystemExit):
        cli.main([*words, "-h"])
    assert capsys.readouterr().out == long


@pytest.mark.integration
@pytest.mark.parametrize("words", [["--help"], ["hygiene", "--help"], ["sonar", "properties", "--help"]], ids=["--help", "hygiene --help", "sonar properties --help"])
def test_no_door_paints_its_help_where_python_would(tmp_path, words):
    forced = run_door(words, tmp_path, FORCE_COLOR="1", TERM="xterm")
    refused = run_door(words, tmp_path, NO_COLOR="1", TERM="xterm")
    assert (forced.stdout == refused.stdout, "\x1b" in forced.stdout, forced.returncode) == (True, False, 0)


@pytest.mark.integration
@pytest.mark.parametrize(("words", "columns"), [(["--help"], 40), (["hygiene", "--help"], 200)], ids=["quality --help", "hygiene --help"])
def test_a_help_at_a_terminal_carries_no_escape_and_stands_whole(tmp_path, window, words, columns):
    terminal = window(columns)
    done = subprocess.run(
        door_argv(*words), cwd=tmp_path, env=door_environment(TERM="xterm"), stdout=terminal.stream, stderr=terminal.stream, timeout=60, check=False
    )
    printed = terminal.read()
    assert (done.returncode, "\x1b" in printed) == (0, False)
    if words == ["--help"]:
        assert printed == run_door(words, tmp_path).stdout


@pytest.mark.integration
def test_a_reader_that_left_before_the_help_keeps_exit_0_and_stderr_empty(tmp_path):
    read_end, write_end = os.pipe()
    os.close(read_end)
    try:
        done = subprocess.run(door_argv("--help"), cwd=tmp_path, env=door_environment(), stdout=write_end, stderr=subprocess.PIPE, text=True, timeout=60, check=False)
    finally:
        os.close(write_end)
    assert (done.returncode, done.stderr) == (0, "")


@pytest.mark.integration
def test_a_closed_stdout_takes_no_help_and_exit_is_0(tmp_path):
    done = subprocess.run(
        ["sh", "-c", 'exec "$@" >&-', "sh", *door_argv("--help")],
        cwd=tmp_path, env=door_environment(), stderr=subprocess.PIPE, text=True, timeout=60, check=False,
    )
    assert (done.returncode, done.stderr) == (0, "")


@pytest.mark.parametrize(
    ("argv", "group", "fact", "help_owner"),
    [
        pytest.param([], (), "no command{sep}commands: {commands}", "quality", id="no command"),
        pytest.param(["nonsense"], (), "no command nonsense{sep}commands: {commands}", "quality", id="unknown command"),
        pytest.param(["sonar"], ("sonar",), "no command{sep}commands: {commands}", "quality sonar", id="no sonar command"),
        pytest.param(["baseline", "nonsense"], ("baseline",), "no command nonsense{sep}commands: {commands}", "quality baseline", id="unknown baseline command"),
        pytest.param(["hygiene", "a", "b"], (), "unknown argument{sep}b", "quality hygiene", id="stray argument"),
        pytest.param(["scan-staged", "--nonsense"], (), "unknown argument{sep}--nonsense", "quality scan-staged", id="stray option"),
        pytest.param(["baseline", "write", "a", "b"], (), "unknown argument{sep}b", "quality baseline", id="stray argument of a group's command"),
        pytest.param(["sonar", "properties", "x"], (), "unknown argument{sep}x", "quality sonar", id="stray argument of sonar"),
        pytest.param(["--nonsense", "scoreboard"], (), "unknown argument{sep}--nonsense", "quality scoreboard", id="stray option before a command"),
    ],
)
def test_a_wrong_invocation_is_the_pair_on_stderr_and_exit_2(capsys, argv, group, fact, help_owner):
    wording = fact.format(sep=SEPARATOR, commands=commands_of(*group))
    code = cli.main(argv, environment={})
    printed = capsys.readouterr()
    assert (code, printed.out, printed.err) == (2, "", f"error: {wording}\nnext: {help_owner} --help\n")


@pytest.mark.parametrize(
    ("argv", "help_owner"),
    [
        pytest.param(["--nonsense"], "quality", id="missing command behind an option"),
        pytest.param(["--nonsense", "nonsense"], "quality", id="unknown command behind an option"),
        pytest.param(["sonar", "--nonsense"], "quality sonar", id="missing sonar command"),
        pytest.param(["emergency-merge"], "quality emergency-merge", id="missing arguments"),
        pytest.param(["emergency-merge", "o/r", "x"], "quality emergency-merge", id="pull request is no number"),
    ],
)
def test_what_only_the_parser_can_word_stands_in_the_pair(capsys, argv, help_owner):
    """The sentence after `error:` is the standard library's and changes with Python, so only its place is pinned."""
    code = cli.main(argv, environment={})
    printed = capsys.readouterr()
    lines = printed.err.splitlines()
    assert (code, printed.out, len(lines), lines[0].startswith("error: "), lines[-1]) == (2, "", 2, True, f"next: {help_owner} --help")


def test_a_wrong_word_stands_escaped_in_the_pair(capsys):
    word = f"{chr(0x1B)}[31m{chr(0x202E)}letters"
    code = cli.main([word], environment={})
    err = capsys.readouterr().err
    expected = f"error: no command {report.printable(word)}{SEPARATOR}commands: {commands_of()}\nnext: quality --help\n"
    assert (code, err, "\x1b" in err) == (2, expected, False)


@pytest.mark.parametrize(
    ("environment", "painted"),
    [
        pytest.param({"TERM": "xterm"}, True, id="terminal"),
        pytest.param({"TERM": "xterm", "NO_COLOR": "1"}, False, id="NO_COLOR"),
        pytest.param({"TERM": "dumb"}, False, id="dumb"),
    ],
)
def test_the_pair_is_painted_by_the_doors_environment(environment, painted):
    written = Terminal()
    with contextlib.redirect_stderr(written):
        cli.main(["nonsense"], environment=environment)
    expected = Terminal()
    output.write_pair(
        f"no command nonsense{SEPARATOR}commands: {commands_of()}", (report.Command("quality --help"),), output.open_channel(expected, environment)
    )
    assert (written.getvalue(), "\x1b[" in written.getvalue()) == (expected.getvalue(), painted)


def test_the_version_prints_the_installed_version_alone(capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--version"])
    printed = capsys.readouterr()
    assert (stopped.value.code, printed.out, printed.err) == (0, f"{quality.__version__}\n", "")
