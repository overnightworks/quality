import contextlib
import os
import subprocess
from pathlib import Path

import pytest

import quality
from quality import checkout, cli, ratchet, staged
from quality.output import SEPARATOR
from quality.rules import Area
from tests.support.door import CHECK_WORD, DRY_RUN_WORDS, FIX_WORDS, MODULE_DOOR, STAGED_WORDS, door_environment
from tests.support.git_repositories import git
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


def test_the_help_stands_in_its_frame(capsys):
    """HELP-1, HELP-2: the command tree stands in its frame, with verdict and sonar apart under the gate's heading."""
    assert help_of(capsys, []) == (
        "usage: quality COMMAND [-v]\n"
        "\n"
        "Commands:\n"
        "  check [AREA ...] [--staged] [--fix] [--dry-run]\n"
        "      Check this repository against the fleet's rules, with the quality\n"
        "      release its gate pins.\n"
        "  merge OWNER/REPO#N [--dry-run]\n"
        "      Operator only: merge one pull request whose gate is red only for\n"
        "      legacy findings or loosenings he ruled, after he types yes.\n"
        "  report\n"
        "      Measure every fleet repository: size, debt, the gate release it pins.\n"
        "\n"
        "Run by the gate, not by people:\n"
        "  verdict\n"
        "      Decide whether this pull request may merge.\n"
        "  sonar properties\n"
        "      Refuse a properties file, token or coverage report the scan must not\n"
        "      start with.\n"
        "  sonar findings\n"
        "      Wait for this run's analysis, then count its open findings.\n"
        "\n"
        "Options:\n"
        "  --version   the installed version and the release this repository's gate pins\n"
        "  -h, --help  show this help\n"
        "\n"
        "Every command takes -v: the raw scanner output, never a secret.\n"
        "Exit codes: 0 yes, 1 no, 2 could not answer, 130 interrupted.\n"
    )


def test_the_merge_help_stands_in_its_frame(capsys):
    """HELP-4: the merge help stands in a frame of its own."""
    assert help_of(capsys, ["merge"]) == (
        "usage: quality merge OWNER/REPO#N [--dry-run] [-v]\n"
        "\n"
        "Operator only: merge one pull request whose gate is red only for legacy\n"
        "findings or loosenings he ruled, after he types yes.\n"
        "\n"
        "Options:\n"
        "  --dry-run   show the rows, then stop before the question: nothing changes,\n"
        "              exit 0 where the door would merge\n"
        "  -v          show the raw scanner output; the door runs no scanner\n"
        "  -h, --help  show this help\n"
        "\n"
        "Exit codes: 0 merged, 1 refused, 2 could not check or do, 130 interrupted.\n"
    )


def test_the_report_help_stands_in_its_frame(capsys):
    """HELP-4, HELP-11: the report help stands in a frame of its own, and -h prints the same."""
    help_text = help_of(capsys, ["report"])
    assert help_text == (
        "usage: quality report [-v]\n"
        "\n"
        "Measure every fleet repository quality/fleet.toml selects: size, debt, the gate\n"
        "release it pins. A row per repository goes to stderr, the two markdown tables\n"
        "to stdout and to the step summary.\n"
        "\n"
        "Options:\n"
        "  -v          show the raw scanner output; the report runs no scanner\n"
        "  -h, --help  show this help\n"
        "\n"
        "Exit codes: 0 every repository measured, 2 could not answer, 130 interrupted.\n"
    )
    with pytest.raises(SystemExit) as stopped:
        cli.main(["report", "-h"])
    printed = capsys.readouterr()
    assert (stopped.value.code, printed.out, printed.err) == (0, help_text, "")


@pytest.mark.parametrize(
    ("words", "dry_run", "verbose"),
    [
        pytest.param(["acme/widgets#7"], False, False, id="name alone"),
        pytest.param(["acme/widgets#7", "--dry-run"], True, False, id="dry run after"),
        pytest.param(["--dry-run", "acme/widgets#7"], True, False, id="dry run before"),
        pytest.param(["-v", "acme/widgets#7", "--dry-run"], True, True, id="every flag"),
    ],
)
def test_the_merge_invocation_names_the_pull_request_and_how(words, dry_run, verbose):
    arguments = cli.read_invocation(["merge", *words])
    assert (arguments.pull_request, arguments.dry_run, arguments.verbose) == (cli.PullRequestName("acme/widgets", 7), dry_run, verbose)


def test_the_check_help_stands_in_its_frame(capsys):
    """HELP-3: the check help explains each area in one entry."""
    assert help_of(capsys, ["check"]) == (
        "usage: quality check [AREA ...] [--staged] [--fix] [--dry-run] [-v]\n"
        "\n"
        "Check this repository against the fleet's rules, with the quality release\n"
        "its gate pins.\n"
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
    """HELP-3: the check help has one entry per area, in the areas' order."""
    lines = help_of(capsys, ["check"]).splitlines()
    entries = entries_under(lines, "Areas (default: all):")
    assert [entry.split()[0] for entry in entries if not entry.startswith("   ")] == list(Area)


def test_the_check_entry_names_its_areas_in_the_top_level_help(capsys):
    """HELP-1: the top-level help lists check with its areas."""
    lines = help_of(capsys, []).splitlines()
    assert "  check [AREA ...] [--staged] [--fix] [--dry-run]" in entries_under(lines, "Commands:")


def test_the_verdict_help_stands_in_its_frame(capsys):
    """HELP-4: the verdict help stands in a frame of its own."""
    assert help_of(capsys, ["verdict"]) == (
        "usage: quality verdict [-v]\n"
        "\n"
        "Run by the gate, not by people: decide whether this pull request may merge,\n"
        "from the jobs of the running workflow and the loosenings of this checkout\n"
        "against the pull request's base.\n"
        "\n"
        "Options:\n"
        "  -v          show the raw scanner output; the verdict runs no scanner\n"
        "  -h, --help  show this help\n"
        "\n"
        "Exit codes: 0 green, 1 red, 2 could not judge, 130 interrupted.\n"
    )


@pytest.mark.parametrize(
    ("words", "frame"),
    [
        pytest.param(
            ["sonar"],
            "usage: quality sonar COMMAND [-v]\n"
            "\n"
            "Run by the gate, not by people: the two steps of gate.yml's sonar job.\n"
            "\n"
            "Commands:\n"
            "  properties\n"
            "      Refuse a properties file, token or coverage report the scan must not\n"
            "      start with, then hand the scan its arguments.\n"
            "  findings\n"
            "      Wait for this run's analysis, then judge its open findings and its\n"
            "      quality gate.\n"
            "\n"
            "Options:\n"
            "  -h, --help  show this help\n"
            "\n"
            "Exit codes: 0 clean, 1 findings, 2 could not judge, 130 interrupted.\n",
            id="sonar",
        ),
        pytest.param(
            ["sonar", "properties"],
            "usage: quality sonar properties [-v]\n"
            "\n"
            "Run by the gate before the scan: refuse a sonar-project.properties, token,\n"
            "coverage report or tracked .scannerwork/ the scan must not start with, then\n"
            "hand the scan its arguments in the step output. With [gate] sonar = false in\n"
            "quality/config.toml it refuses a properties file or a passed token instead.\n"
            "\n"
            "Options:\n"
            "  -v          show the raw scanner output; the step runs no scanner\n"
            "  -h, --help  show this help\n"
            "\n"
            "Exit codes: 0 clean, 2 could not judge, 130 interrupted.\n",
            id="sonar properties",
        ),
        pytest.param(
            ["sonar", "findings"],
            "usage: quality sonar findings [-v]\n"
            "\n"
            "Run by the gate after the scan: wait for this run's own analysis\n"
            "(.scannerwork/report-task.txt), then judge its open findings, the default\n"
            "branch's legacy count and, on a pull request, SonarCloud's quality gate. It\n"
            "needs quality/config.toml with [gate] mandatory_jobs.\n"
            "\n"
            "Options:\n"
            "  -v          show the raw scanner output; the step runs no scanner\n"
            "  -h, --help  show this help\n"
            "\n"
            "Exit codes: 0 clean or report only, 1 findings, 2 could not judge,\n"
            "130 interrupted.\n",
            id="sonar findings",
        ),
    ],
)
def test_the_sonar_helps_stand_in_their_frames(capsys, words, frame):
    """HELP-4, HELP-11: each sonar help stands in a frame of its own, and -h prints the same."""
    assert help_of(capsys, words) == frame
    with pytest.raises(SystemExit) as stopped:
        cli.main([*words, "-h"])
    printed = capsys.readouterr()
    assert (stopped.value.code, printed.out, printed.err) == (0, frame, "")


def test_every_command_path_stands_in_the_help_once_in_the_parsers_order_and_every_entry_is_one(capsys):
    """HELP-1: every command path stands in the help once, in the parser's order."""
    lines = help_of(capsys, []).splitlines()
    shown = [path_of(entry) for heading in headings(lines)[:2] for entry in entries_under(lines, heading)]
    assert shown == leaf_paths(cli.build_parser())


def test_verdict_and_sonar_stand_apart_under_run_by_the_gate(capsys):
    """HELP-2: verdict and sonar stand apart from the commands people run."""
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
        pytest.param(["merge"], id="merge"),
        pytest.param(["report"], id="report"),
    ],
)
def test_each_doors_help_goes_to_stdout_alone_and_exits_0(capsys, words):
    """HELP-8: the help of every command goes to stdout alone and exits 0."""
    first = help_of(capsys, words).splitlines()[0]
    expected = ["quality", *words] if words else ["quality", "COMMAND"]
    assert first.split()[1:][: len(expected)] == expected


@pytest.mark.parametrize("words", [[], ["check"], ["sonar", "properties"]], ids=["quality", "check", "sonar properties"])
def test_short_help_equals_long_help(capsys, words):
    """HELP-11: -h prints what --help prints."""
    long = help_of(capsys, words)
    with pytest.raises(SystemExit) as stopped:
        cli.main([*words, "-h"])
    printed = capsys.readouterr()
    assert (stopped.value.code, printed.out, printed.err) == (0, long, "")


@pytest.mark.integration
@pytest.mark.parametrize("words", [["--help"], ["verdict", "--help"], ["sonar", "properties", "--help"]], ids=["--help", "verdict --help", "sonar properties --help"])
def test_no_door_paints_its_help_where_python_would(tmp_path, words):
    """HELP-9: the help carries no escape sequence, whatever the colour variables say."""
    forced = run_door(words, tmp_path, FORCE_COLOR="1", TERM="xterm")
    refused = run_door(words, tmp_path, NO_COLOR="1", TERM="xterm")
    assert (forced.stdout == refused.stdout, "\x1b" in forced.stdout, forced.returncode) == (True, False, 0)


@pytest.mark.integration
@pytest.mark.parametrize(("words", "columns"), [(["--help"], 40), (["verdict", "--help"], 200)], ids=["quality --help", "verdict --help"])
def test_a_help_at_a_terminal_carries_no_escape_and_stands_whole(tmp_path, window, words, columns):
    """HELP-9, HELP-10: at a terminal the help carries no escape sequence and stands as it does in a pipe."""
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
    """HELP-8: a reader that left before the help keeps exit 0 and an empty stderr."""
    read_end, write_end = os.pipe()
    os.close(read_end)
    try:
        done = subprocess.run([*MODULE_DOOR, "--help"], cwd=tmp_path, env=door_environment(), stdout=write_end, stderr=subprocess.PIPE, text=True, timeout=60, check=False)
    finally:
        os.close(write_end)
    assert (done.returncode, done.stderr) == (0, "")


@pytest.mark.integration
def test_a_closed_stdout_takes_no_help_and_exit_is_0(tmp_path):
    """HELP-8: a closed stdout takes no help, and the exit is 0 with an empty stderr."""
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
        pytest.param(
            ["check", "."],
            (),
            "no area .{sep}areas: {areas}",
            "quality check",
            id="a path where an area goes",
        ),
        pytest.param(["check", "src"], (), "no area src{sep}areas: {areas}", "quality check", id="a folder name"),
        pytest.param(["check", "-v", "."], (), "no area .{sep}areas: {areas}", "quality check", id="a path after the flag"),
        pytest.param(["check", "files", "docs"], (), "no area docs{sep}areas: {areas}", "quality check", id="a wrong area after a right one"),
        pytest.param(["check", "docs", "--nonsense"], (), "no area docs{sep}areas: {areas}", "quality check", id="a wrong area before a stray option"),
        pytest.param(["check", "files", "--nonsense"], (), "unknown argument{sep}--nonsense", "quality check", id="a stray option after an area"),
        pytest.param(["check", "--nonsense"], (), "unknown argument{sep}--nonsense", "quality check", id="a stray option of check"),
        pytest.param(["check", "--verbose"], (), "unknown argument{sep}--verbose", "quality check", id="the long form of the flag"),
        pytest.param(["-v", "check"], (), "unknown argument{sep}-v", "quality check", id="the flag before the command"),
        pytest.param(["verdict", "--nonsense"], (), "unknown argument{sep}--nonsense", "quality verdict", id="stray option"),
        pytest.param(["verdict", "."], (), "unknown argument{sep}.", "quality verdict", id="a path to verdict"),
        pytest.param(["verdict", "--verbose"], (), "unknown argument{sep}--verbose", "quality verdict", id="the long form of the verdict flag"),
        pytest.param(["verdict", "--he"], (), "unknown argument{sep}--he", "quality verdict", id="an abbreviated help of the verdict"),
        pytest.param(
            ["scan-staged"],
            (),
            "no command scan-staged{sep}commands: {commands}",
            "quality",
            id="the word scan-staged",
        ),
        pytest.param(
            ["scan-staged", "--engines", "x"],
            (),
            "no command scan-staged{sep}commands: {commands}",
            "quality",
            id="the word scan-staged with an option",
        ),
        pytest.param(["check", "--stag"], (), "unknown argument{sep}--stag", "quality check", id="check --stag"),
        pytest.param(["check", "secrets", "--staged", "--nonsense"], (), "unknown argument{sep}--nonsense", "quality check", id="a stray option beside the staged flag"),
        pytest.param(["check", "docs", "--staged"], (), "no area docs{sep}areas: {areas}", "quality check", id="a wrong area beside the staged flag"),
        pytest.param(["sonar", "properties", "x"], (), "unknown argument{sep}x", "quality sonar properties", id="stray argument of sonar"),
        pytest.param(["sonar", "properties", "--verbose"], (), "unknown argument{sep}--verbose", "quality sonar properties", id="a stray option of sonar properties"),
        pytest.param(["sonar", "findings", "--he"], (), "unknown argument{sep}--he", "quality sonar findings", id="an abbreviated help of sonar findings"),
        pytest.param(["sonar", "findings", "x"], (), "unknown argument{sep}x", "quality sonar findings", id="a stray argument of sonar findings"),
        pytest.param(["--nonsense", "report"], (), "unknown argument{sep}--nonsense", "quality report", id="stray option before a command"),
        pytest.param(
            ["scoreboard"],
            (),
            "no command scoreboard{sep}commands: {commands}",
            "quality",
            id="the word scoreboard",
        ),
        pytest.param(
            ["scoreboard", "."],
            (),
            "no command scoreboard{sep}commands: {commands}",
            "quality",
            id="the word scoreboard with a path",
        ),
        pytest.param(["report", "--verbose"], (), "unknown argument{sep}--verbose", "quality report", id="a stray option of the report"),
        pytest.param(["report", "--he"], (), "unknown argument{sep}--he", "quality report", id="an abbreviated help of the report"),
        pytest.param(["merge"], (), "no pull request{sep}a pull request reads OWNER/REPO#N", "quality merge", id="no pull request"),
        pytest.param(
            ["merge", "acme/widgets", "7"],
            (),
            "no pull request acme/widgets{sep}a pull request reads OWNER/REPO#N",
            "quality merge",
            id="owner and number as two words",
        ),
        pytest.param(["merge", "acme/widgets"], (), "no pull request acme/widgets{sep}a pull request reads OWNER/REPO#N", "quality merge", id="no number"),
        pytest.param(["merge", "acme/widgets#0"], (), "no pull request acme/widgets#0{sep}a pull request reads OWNER/REPO#N", "quality merge", id="number 0"),
        pytest.param(["merge", "acme/widgets#7x"], (), "no pull request acme/widgets#7x{sep}a pull request reads OWNER/REPO#N", "quality merge", id="letters after the number"),
        pytest.param(["merge", "widgets#7"], (), "no pull request widgets#7{sep}a pull request reads OWNER/REPO#N", "quality merge", id="no owner"),
        pytest.param(
            ["merge", "acme/widg\u0435ts#7"], (), "no pull request acme/widg\u0435ts#7{sep}a pull request reads OWNER/REPO#N", "quality merge", id="a lookalike name"
        ),
        pytest.param(["merge", "acme/widgets#7", "acme/widgets#8"], (), "unknown argument{sep}acme/widgets#8", "quality merge", id="two pull requests"),
        pytest.param(["merge", "acme/widgets#7", "--dry"], (), "unknown argument{sep}--dry", "quality merge", id="an abbreviated dry run"),
        pytest.param(["merge", "acme/widgets#7", "--verbose"], (), "unknown argument{sep}--verbose", "quality merge", id="the long form of the merge flag"),
        pytest.param(
            ["emergency-merge", "acme/widgets", "7"],
            (),
            "no command emergency-merge{sep}commands: {commands}",
            "quality",
            id="the word emergency-merge",
        ),
    ],
)
def test_a_wrong_invocation_is_the_pair_on_stderr_and_exit_2(capsys, argv, group, fact, help_owner):
    """HELP-12, HELP-13, HELP-14, HELP-15, HELP-16, HELP-18, HELP-19, HELP-20, HELP-24: each wrong invocation is the
    pair in its own words on stderr with exit 2, and nothing is printed on stdout."""
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
    ],
)
def test_what_only_the_parser_can_word_stands_in_the_pair(capsys, argv, help_owner):
    """HELP-12, HELP-21, HELP-24: the sentence after `error:` is the standard library's and changes with Python, so only
    its place is pinned."""
    code = cli.main(argv, environment={})
    printed = capsys.readouterr()
    lines = printed.err.splitlines()
    assert (code, printed.out, len(lines), lines[0].startswith("error: "), lines[-1]) == (2, "", 2, True, f"next: {help_owner} --help")


def test_a_wrong_word_stands_escaped_in_the_pair(capsys):
    """MASK-6, MASK-7: a wrong first word prints escaped in the pair, ESC and a format character included."""
    word = f"{chr(0x1B)}[31m{chr(0x202E)}letters"
    code = cli.main([word], environment={})
    err = capsys.readouterr().err
    expected = f"error: no command \\x1b[31m\\u202eletters{SEPARATOR}commands: {commands_of()}\nnext: quality --help\n"
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


def gate_workflow(commit: str, comment: str) -> dict[str, str]:
    uses = f"{checkout.GATE_REPOSITORY}/.github/workflows/gate.yml@{commit}{comment}"
    return {".github/workflows/ci.yml": f"on: push\njobs:\n  gate:\n    uses: {uses}\n"}


def outside_a_work_tree(build_repo, elsewhere: Path) -> Path:
    return elsewhere


def no_gate_call(build_repo, elsewhere: Path) -> Path:
    return build_repo({"README.md": "shop\n"})


def quality_s_checkout(build_repo, elsewhere: Path) -> Path:
    repo = build_repo({".github/workflows/ci.yml": "on: push\njobs:\n  gate:\n    uses: ./.github/workflows/gate.yml\n"})
    git(repo, "remote", "add", "origin", f"https://github.com/{checkout.GATE_REPOSITORY}.git")
    return repo


def one_pin(build_repo, elsewhere: Path) -> Path:
    return build_repo(gate_workflow("5e" * 20, " # v0.10.0"))


def a_pin_without_its_release(build_repo, elsewhere: Path) -> Path:
    return build_repo(gate_workflow("5e" * 20, ""))


@pytest.mark.integration
@pytest.mark.parametrize(
    ("arrange", "words"),
    [
        pytest.param(outside_a_work_tree, "not inside a repository", id="outside a work tree"),
        pytest.param(no_gate_call, "no workflow here calls quality's gate.yml", id="no gate call"),
        pytest.param(quality_s_checkout, "this checkout of quality judges itself", id="quality's checkout"),
        pytest.param(one_pin, "this repository's gate pins v0.10.0", id="one pin"),
        pytest.param(a_pin_without_its_release, "the gate pin in .github/workflows/ci.yml is not @<commit sha> # v<tag>", id="a pin without its release"),
    ],
)
def test_the_version_line_names_the_installed_version_and_what_the_gate_pins(build_repo, elsewhere, monkeypatch, capsys, arrange, words):
    """HELP-5, HELP-6, HELP-7: the version line names the installed version, then the pin or why none is known."""
    monkeypatch.chdir(arrange(build_repo, elsewhere))
    code = cli.main(["--version"])
    printed = capsys.readouterr()
    assert (code, printed.out, printed.err) == (0, f"quality {quality.__version__} · {words}\n", "")


def test_the_version_line_escapes_a_workflow_name(build_repo, monkeypatch, capsys):
    """HELP-5, HELP-6, MASK-7: an unreadable workflow's name stands escaped in the reason the pin is unknown."""
    monkeypatch.chdir(build_repo({f".github/workflows/ci{chr(0x202E)}.yml": "jobs: [\n"}))
    code = cli.main(["--version"])
    printed = capsys.readouterr()
    fact = "cannot read .github/workflows/ci\\u202e.yml, so the gate pin is unknown"
    assert (code, printed.out, printed.err) == (0, f"quality {quality.__version__} · {fact}\n", "")


def test_the_version_line_in_a_folder_that_was_removed_names_the_folder_git_cannot_read(tmp_path, monkeypatch, capsys):
    """HELP-5, HELP-6: a working directory that was removed is why the pin is unknown."""
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()
    code = cli.main(["--version"])
    printed = capsys.readouterr()
    prefix = f"quality {quality.__version__} · . is not a readable git repository: the working directory cannot be read"
    assert (code, printed.out.startswith(prefix), printed.out.count("\n"), printed.err) == (0, True, 1, ""), printed.out


@pytest.mark.integration
def test_a_closed_stdout_takes_no_version_line_and_exit_is_0(tmp_path):
    """HELP-6: a closed stdout takes no version line, and the exit is 0 with an empty stderr."""
    done = subprocess.run(
        ["sh", "-c", 'exec "$@" >&-', "sh", *MODULE_DOOR, "--version"],
        cwd=tmp_path, env=door_environment(), stderr=subprocess.PIPE, text=True, timeout=60, check=False,
    )
    assert (done.returncode, done.stderr) == (0, "")


def test_an_unknown_area_pair_names_the_areas(capsys):
    """HELP-16, HELP-24: an unknown area is the pair that names the areas and the help of quality check."""
    code = cli.main(["check", "docs"], environment={})
    printed = capsys.readouterr()
    assert (code, printed.out, printed.err) == (
        2,
        "",
        "error: no area docs · areas: files, debt, imports, secrets, workflows, dependencies, code\nnext: quality check --help\n",
    )


def test_a_wrong_area_stands_escaped_in_the_pair(capsys):
    """MASK-6, MASK-7: a wrong area prints escaped in the pair, ESC and a format character included."""
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
    """HELP-17: an option stands before, between or after the named areas, or before --."""
    arguments = cli.read_invocation(["check", *words])
    assert (arguments.areas, arguments.verbose) == ([Area.DEBT, Area.SECRETS], True)


def test_no_area_named_leaves_the_invocation_empty():
    """CHECK-3: no area named leaves the invocation's areas empty."""
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
    """HELP-12, HELP-23: --staged beside any area but secrets, or beside none, is the pair, and nothing runs."""
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
    """HELP-12, HELP-25, HELP-26, HELP-27: --fix beside --staged or an area but debt is the pair that names the run to
    make instead, and a dry run stays a dry run in it."""
    code = cli.main(["check", *words], environment={})
    printed = capsys.readouterr()
    assert (code, printed.out, printed.err) == (2, "", f"error: {fact} · nothing checked\nnext: {step}\n")


@pytest.mark.parametrize(
    ("words", "pair"),
    [
        pytest.param(
            ["baseline", "write", "."],
            "error: no command baseline · commands: {commands}\nnext: quality --help\n",
            id="the word baseline",
        ),
    ],
)
def test_the_words_that_are_not_the_fix_are_refused(capsys, words, pair):
    """HELP-13: baseline is no command, so its words are refused as any unknown command."""
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
    """HELP-12, HELP-22: --dry-run without --fix is the pair, and nothing runs."""
    code = cli.main(["check", *words], environment={})
    printed = capsys.readouterr()
    assert (code, printed.out, printed.err) == (2, "", "error: --dry-run needs --fix · nothing checked\nnext: quality check --fix --dry-run\n")


def test_the_dry_run_words_are_the_commands_words():
    """HELP-22: the dry run's command in the pair is the command's own words."""
    assert cli.DRY_RUN_COMMAND.replace(CHECK_WORD, " ".join(DRY_RUN_WORDS[:2])) == f"quality {' '.join(DRY_RUN_WORDS)}"


def test_the_fix_words_are_the_commands_words():
    """HELP-25: the fix's command in the pair is the command's own words."""
    assert ratchet.FIX_COMMAND.replace(CHECK_WORD, " ".join(FIX_WORDS[:2])) == f"quality {' '.join(FIX_WORDS)}"


def test_the_staged_words_are_the_commands_words():
    """HELP-23: the staged scan's command in the pair is the command's own words."""
    assert staged.COMMAND == f"quality {' '.join(STAGED_WORDS)}"
