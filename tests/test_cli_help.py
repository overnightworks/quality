import contextlib
import os
import subprocess
from pathlib import Path

import pytest

import quality
from quality import cli, ratchet, report, staged
from quality.output import SEPARATOR
from quality.rules import Area
from tests.support.door import CHECK_WORD, DRY_RUN_WORDS, FIX_WORDS, MODULE_DOOR, STAGED_WORDS, door_environment
from tests.support.terminal import Terminal, without_sgr


def run_door(arguments: list[str], folder: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*MODULE_DOOR, *arguments], cwd=folder, env=door_environment(**extra), capture_output=True, text=True, timeout=60, check=False
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
    assert help_of(capsys, []) == (
        "usage: quality COMMAND\n"
        "\n"
        "Commands:\n"
        "  check [AREA ...] [--staged] [--fix] [--dry-run]\n"
        "      Check this repository against the fleet's rules.\n"
        "  emergency-merge OWNER/REPO PR\n"
        "      Operator only: merge one pull request whose gate is red only for\n"
        "      legacy findings or loosenings he ruled, after he types ja.\n"
        "  scoreboard [PATH]\n"
        "      Measure every fleet repository: size, debt, the gate release it pins.\n"
        "\n"
        "Run by the gate, not by people:\n"
        "  verdict [PATH]\n"
        "      Decide whether this pull request may merge.\n"
        "  sonar properties\n"
        "      Refuse a properties file, token or coverage report the scan must not\n"
        "      start with.\n"
        "  sonar findings\n"
        "      Wait for this run's analysis, then count its open findings.\n"
        "\n"
        "Options:\n"
        "  --version   the installed version\n"
        "  -h, --help  show this help\n"
        "\n"
        "Exit codes: 0 yes, 1 no, 2 could not answer, 130 interrupted.\n"
    )


def test_the_check_help_stands_in_the_pictures_frame(capsys):
    assert help_of(capsys, ["check"]) == (
        "usage: quality check [AREA ...] [--staged] [--fix] [--dry-run] [-v]\n"
        "\n"
        "Check this repository against the fleet's rules.\n"
        "\n"
        "Areas (default: all):\n"
        "  files         what the repository tracks: layout, tests, env files,\n"
        "                compose ports, the Python floor\n"
        "  debt          size and near copies against the debt ceiling\n"
        "                (quality/baseline.toml)\n"
        "  imports       the import contracts\n"
        "  secrets       secrets in the committed history\n"
        "  workflows     GitHub workflows: lint and security\n"
        "  dependencies  known advisories in the lockfiles\n"
        "  code          Python source: security findings and print calls\n"
        "\n"
        "Options:\n"
        "  --staged      secrets in exactly what the next commit records; the commit\n"
        "                hook runs: quality check secrets --staged\n"
        "  --fix         lower the debt ceiling (quality/baseline.toml) where code\n"
        "                shrank, or record it the first time; never raises it\n"
        "  --dry-run     with --fix: show what would change, write nothing\n"
        "  -v            show the raw scanner output, never a secret\n"
        "  -h, --help    show this help\n"
        "\n"
        "Exit codes: 0 clean, 1 findings, 2 could not check, 130 interrupted.\n"
    )


def test_each_area_has_one_entry_in_the_check_help(capsys):
    lines = help_of(capsys, ["check"]).splitlines()
    entries = entries_under(lines, "Areas (default: all):")
    assert [entry.split()[0] for entry in entries if not entry.startswith("   ")] == list(Area)


def test_the_check_entry_names_its_areas_in_the_top_level_help(capsys):
    lines = help_of(capsys, []).splitlines()
    assert "  check [AREA ...] [--staged] [--fix] [--dry-run]" in entries_under(lines, "Commands:")


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
        pytest.param(["check"], id="check"),
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


@pytest.mark.parametrize("words", [[], ["check"], ["sonar", "properties"]], ids=["quality", "check", "sonar properties"])
def test_short_help_equals_long_help(capsys, words):
    long = help_of(capsys, words)
    with pytest.raises(SystemExit) as stopped:
        cli.main([*words, "-h"])
    printed = capsys.readouterr()
    assert (stopped.value.code, printed.out, printed.err) == (0, long, "")


@pytest.mark.integration
@pytest.mark.parametrize("words", [["--help"], ["verdict", "--help"], ["sonar", "properties", "--help"]], ids=["--help", "verdict --help", "sonar properties --help"])
def test_no_door_paints_its_help_where_python_would(tmp_path, words):
    forced = run_door(words, tmp_path, FORCE_COLOR="1", TERM="xterm")
    refused = run_door(words, tmp_path, NO_COLOR="1", TERM="xterm")
    assert (forced.stdout == refused.stdout, "\x1b" in forced.stdout, forced.returncode) == (True, False, 0)


@pytest.mark.integration
@pytest.mark.parametrize(("words", "columns"), [(["--help"], 40), (["verdict", "--help"], 200)], ids=["quality --help", "verdict --help"])
def test_a_help_at_a_terminal_carries_no_escape_and_stands_whole(tmp_path, window, words, columns):
    terminal = window(columns)
    done = subprocess.run(
        [*MODULE_DOOR, *words], cwd=tmp_path, env=door_environment(TERM="xterm"), stdout=terminal.stream, stderr=terminal.stream, timeout=60, check=False
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
        done = subprocess.run([*MODULE_DOOR, "--help"], cwd=tmp_path, env=door_environment(), stdout=write_end, stderr=subprocess.PIPE, text=True, timeout=60, check=False)
    finally:
        os.close(write_end)
    assert (done.returncode, done.stderr) == (0, "")


@pytest.mark.integration
def test_a_closed_stdout_takes_no_help_and_exit_is_0(tmp_path):
    done = subprocess.run(
        ["sh", "-c", 'exec "$@" >&-', "sh", *MODULE_DOOR, "--help"],
        cwd=tmp_path, env=door_environment(), stderr=subprocess.PIPE, text=True, timeout=60, check=False,
    )
    assert (done.returncode, done.stderr) == (0, "")


@pytest.mark.parametrize(
    ("argv", "group", "fact", "help_owner"),
    [
        pytest.param([], (), "no command{sep}commands: {commands}", "quality", id="no command"),
        pytest.param(["nonsense"], (), "no command nonsense{sep}commands: {commands}", "quality", id="unknown command"),
        pytest.param(["sonar"], ("sonar",), "no command{sep}commands: {commands}", "quality sonar", id="no sonar command"),
        pytest.param(["sonar", "nonsense"], ("sonar",), "no command nonsense{sep}commands: {commands}", "quality sonar", id="unknown sonar command"),
        pytest.param(["check", "a", "b"], (), "no area a{sep}areas: {areas}", "quality check", id="the first of two words"),
        pytest.param(["check", "."], (), "no area .{sep}areas: {areas}", "quality check", id="a path of the old form"),
        pytest.param(["check", "src"], (), "no area src{sep}areas: {areas}", "quality check", id="a folder name"),
        pytest.param(["check", "-v", "."], (), "no area .{sep}areas: {areas}", "quality check", id="a path after the flag"),
        pytest.param(["check", "files", "docs"], (), "no area docs{sep}areas: {areas}", "quality check", id="a wrong area after a right one"),
        pytest.param(["check", "docs", "--nonsense"], (), "no area docs{sep}areas: {areas}", "quality check", id="a wrong area before a stray option"),
        pytest.param(["check", "files", "--nonsense"], (), "unknown argument{sep}--nonsense", "quality check", id="a stray option after an area"),
        pytest.param(["check", "--nonsense"], (), "unknown argument{sep}--nonsense", "quality check", id="a stray option of check"),
        pytest.param(["check", "--verbose"], (), "unknown argument{sep}--verbose", "quality check", id="the long form of the flag"),
        pytest.param(["-v", "check"], (), "unknown argument{sep}-v", "quality check", id="the flag before the command"),
        pytest.param(["verdict", "--nonsense"], (), "unknown argument{sep}--nonsense", "quality verdict", id="stray option"),
        pytest.param(["scan-staged"], (), "no command scan-staged{sep}commands: {commands}", "quality", id="the word that left"),
        pytest.param(["scan-staged", "--engines", "x"], (), "no command scan-staged{sep}commands: {commands}", "quality", id="the word that left, with its option"),
        pytest.param(["check", "--stag"], (), "unknown argument{sep}--stag", "quality check", id="check --stag"),
        pytest.param(["check", "secrets", "--staged", "--nonsense"], (), "unknown argument{sep}--nonsense", "quality check", id="a stray option beside the staged flag"),
        pytest.param(["check", "docs", "--staged"], (), "no area docs{sep}areas: {areas}", "quality check", id="a wrong area beside the staged flag"),
        pytest.param(["sonar", "properties", "x"], (), "unknown argument{sep}x", "quality sonar", id="stray argument of sonar"),
        pytest.param(["--nonsense", "scoreboard"], (), "unknown argument{sep}--nonsense", "quality scoreboard", id="stray option before a command"),
    ],
)
def test_a_wrong_invocation_is_the_pair_on_stderr_and_exit_2(capsys, argv, group, fact, help_owner):
    wording = fact.format(sep=SEPARATOR, commands=commands_of(*group), areas=", ".join(Area))
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
    plain = f"error: no command nonsense{SEPARATOR}commands: {commands_of()}\nnext: quality --help\n"
    assert (without_sgr(written.getvalue()), "\x1b[" in written.getvalue()) == (plain, painted)


def test_the_version_prints_the_installed_version_alone(capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--version"])
    printed = capsys.readouterr()
    assert (stopped.value.code, printed.out, printed.err) == (0, f"{quality.__version__}\n", "")


def test_the_unknown_area_pair_stands_as_the_picture_draws_it(capsys):
    code = cli.main(["check", "docs"], environment={})
    printed = capsys.readouterr()
    assert (code, printed.out, printed.err) == (
        2,
        "",
        "error: no area docs · areas: files, debt, imports, secrets, workflows, dependencies, code\nnext: quality check --help\n",
    )


def test_a_wrong_area_stands_escaped_in_the_pair(capsys):
    word = f"{chr(0x1B)}[31m{chr(0x202E)}letters"
    cli.main(["check", word], environment={})
    err = capsys.readouterr().err
    assert (err.splitlines()[0].startswith("error: no area \\x1b[31m\\u202eletters"), "\x1b" in err, "\u202e" in err) == (True, False, False)


@pytest.mark.parametrize(
    "words",
    [
        pytest.param(["-v", "debt", "secrets"], id="first"),
        pytest.param(["debt", "-v", "secrets"], id="between"),
        pytest.param(["debt", "secrets", "-v"], id="last"),
        pytest.param(["-v", "--", "debt", "secrets"], id="after --"),
    ],
)
def test_options_may_stand_between_the_named_areas(words):
    arguments = cli.read_invocation(["check", *words])
    assert (arguments.areas, arguments.verbose) == ([Area.DEBT, Area.SECRETS], True)


def test_no_area_named_leaves_the_invocation_empty():
    assert cli.read_invocation(["check"]).areas == []


@pytest.mark.parametrize(
    "words",
    [
        pytest.param(["--staged"], id="no area"),
        pytest.param(["debt", "--staged"], id="debt"),
        pytest.param(["--staged", "debt"], id="debt first"),
        pytest.param(["secrets", "debt", "--staged"], id="two areas"),
        pytest.param(["files", "secrets", "--staged"], id="secrets last"),
    ],
)
def test_staged_outside_secrets_is_the_pair_and_runs_nothing(capsys, words):
    code = cli.main(["check", *words], environment={})
    printed = capsys.readouterr()
    assert (code, printed.out, printed.err) == (2, "", "error: --staged checks only secrets · nothing checked\nnext: quality check secrets --staged\n")


@pytest.mark.parametrize(
    ("words", "fact", "step"),
    [
        pytest.param(["--fix", "--staged"], "--fix does not take --staged", "quality check --fix", id="staged"),
        pytest.param(["--fix", "--dry-run", "--staged"], "--fix does not take --staged", "quality check --fix --dry-run", id="staged, dry run"),
        pytest.param(["--fix", "secrets"], "--fix changes only the debt ceiling", "quality check debt --fix", id="an area other than debt"),
        pytest.param(
            ["--fix", "--dry-run", "secrets"], "--fix changes only the debt ceiling", "quality check debt --fix --dry-run", id="another area, dry run"
        ),
        pytest.param(["--fix", "files", "secrets"], "--fix changes only the debt ceiling", "quality check debt --fix", id="two other areas"),
    ],
)
def test_a_fix_beside_what_it_cannot_change_is_the_pair_and_runs_nothing(capsys, words, fact, step):
    code = cli.main(["check", *words], environment={})
    printed = capsys.readouterr()
    assert (code, printed.out, printed.err) == (2, "", f"error: {fact} · nothing checked\nnext: {step}\n")


@pytest.mark.parametrize(
    ("words", "pair"),
    [
        pytest.param(["baseline", "write", "."], "error: no command baseline · commands: {commands}\nnext: quality --help\n", id="the word that left"),
    ],
)
def test_the_words_that_are_not_the_fix_are_refused(capsys, words, pair):
    code = cli.main(words, environment={})
    printed = capsys.readouterr()
    assert (code, printed.out, printed.err) == (2, "", pair.format(commands=commands_of()))


@pytest.mark.parametrize(
    "words",
    [
        pytest.param(["--dry-run"], id="alone"),
        pytest.param(["debt", "--dry-run"], id="beside debt"),
        pytest.param(["secrets", "--staged", "--dry-run"], id="beside secrets --staged"),
        pytest.param(["--dry-run", "--staged"], id="before --staged"),
    ],
)
def test_a_dry_run_without_the_fix_is_the_pair_and_runs_nothing(capsys, words):
    code = cli.main(["check", *words], environment={})
    printed = capsys.readouterr()
    assert (code, printed.out, printed.err) == (2, "", "error: --dry-run needs --fix · nothing checked\nnext: quality check --fix --dry-run\n")


def test_the_dry_run_words_are_the_commands_words():
    assert cli.DRY_RUN_COMMAND.replace(CHECK_WORD, " ".join(DRY_RUN_WORDS[:2])) == f"quality {' '.join(DRY_RUN_WORDS)}"


def test_the_fix_words_are_the_commands_words():
    assert ratchet.FIX_COMMAND.replace(CHECK_WORD, " ".join(FIX_WORDS[:2])) == f"quality {' '.join(FIX_WORDS)}"


def test_the_staged_words_are_the_commands_words():
    assert staged.COMMAND == f"quality {' '.join(STAGED_WORDS)}"
