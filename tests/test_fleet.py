"""`quality scoreboard` (#56): cells name the item's scenario matrix, rules its numbered rules."""

import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest
import yaml

from quality import __version__, cli
from quality.fleet import FLEET_EXAMPLE, FleetError, parse_fleet, read_fleet
from tests.support.clones import function as clone_function
from tests.support.fleet import (
    GITHUB_BASE_VARIABLE,
    GITHUB_REDIRECT,
    MATRIX_HEADING,
    RELEASES,
    TEST_OWNER,
    row,
    write_fleet,
)
from tests.support.git_repositories import GIT_IDENTITY, head_commit, make_repo
from tests.support.source_code import (
    branchy_function,
    long_file,
    long_function,
    minified_bundle,
    typescript_file,
    typescript_function,
)

WORKFLOWS = Path(__file__).parent.parent / ".github" / "workflows"
HEADER = (
    "| Repo | Commit | Languages | Lines prod / test | test:prod | Files ≥ 800 prod / test | Functions ≥ 60 prod / test "
    "| CCN > 15 prod / test | Clone pairs prod / test | Note |"
)
# 3 + 5 * 7 + 2 = 40 tokens: `arguments`, `arg`, `ID`, seven `Assign, Name, ID, Name, ID` and two `Pass`
CLONE = clone_function("copied", "value", "local")


def test_a_public_repo_is_one_row_of_commit_lines_ratio_and_debt(scoreboard, public, remotes):
    """Cell 1, rule 2: commit, physical lines prod / test, test:prod with two decimals and the debt counts."""
    name = public("alpha", {"src/app.py": "a = 1\nb = 2\nc = 3\n", "tests/test_app.py": "x = 1\n" * 5})
    code, output = scoreboard(name)
    assert code == 0, output
    assert row(output, "o/alpha") == [
        name, head_commit(remotes / name)[:7], "Python 8", "3 / 5", "1.67", "0 / 0", "0 / 0", "0 / 0", "0 / 0", ""
    ]


def test_debt_is_counted_from_the_size_rules_thresholds(scoreboard, public):
    """Cell 2, rule 2: 800 file lines, 60 function lines and CCN 16 are debt; 799, 59 and CCN 15 are not."""
    name = public(
        "edges",
        {
            "src/file_799.py": long_file(799),
            "src/file_800.py": long_file(800),
            "src/function_59.py": long_function(59),
            "src/function_60.py": long_function(60),
            "src/ccn_15.py": branchy_function(15),
            "src/ccn_16.py": branchy_function(16),
            "tests/test_big.py": long_file(800),
        },
    )
    code, output = scoreboard(name)
    assert code == 0, output
    assert row(output, "o/edges")[5:8] == ["1 / 1", "1 / 0", "1 / 0"]


@pytest.mark.parametrize(
    ("path", "lines"),
    [
        ("tests/x.py", "0 / 1"),
        ("test/x.py", "0 / 1"),
        ("src/test_a.py", "0 / 1"),
        ("a_test.py", "0 / 1"),
        ("conftest.py", "0 / 1"),
        ("scripts/test_inventory.py", "0 / 1"),
        ("src/testing.py", "1 / 0"),
        ("src/a.test.ts", "0 / 1"),
        ("e2e/login.spec.ts", "0 / 1"),
        ("src/decision.svelte.test.ts", "0 / 1"),
        ("src/__tests__/x.ts", "0 / 1"),
        ("src/a.spec.mjs", "0 / 1"),
        ("tests/Harness.svelte", "0 / 1"),
        ("test/Panel.vue", "0 / 1"),
        ("frontend/e2e/helpers.ts", "1 / 0"),
        ("src/lib/test-utils/x.ts", "1 / 0"),
        ("src/test_x.ts", "1 / 0"),
        ("src/Card.test.svelte", "1 / 0"),
        ("src/__tests__/Card.svelte", "1 / 0"),
    ],
)
def test_the_file_name_decides_production_or_test(scoreboard, public, path, lines):
    """Cell 3, rule 2: production versus test is ratchet.area_of's name rule; #119 E3, cell 8, per language."""
    code, output = scoreboard(public("named", {path: "x = 1\n"}))
    assert code == 0, output
    assert row(output, "o/named")[3] == lines


@pytest.mark.parametrize(
    "source", ["a = 1\n\n# note\n", "a = 1\r\n\r\n# note\r\n", "a = 1\r\r# note\r"], ids=["LF", "CRLF", "CR"]
)
def test_blank_and_comment_lines_count_whatever_the_line_ending(scoreboard, public, source):
    """Cell 4, rule 2: physical lines, as the 800-line rule counts them."""
    code, output = scoreboard(public("endings", {"src/a.py": source}))
    assert code == 0, output
    assert row(output, "o/endings")[3] == "3 / 0"


def test_untracked_files_are_not_counted(scoreboard, public, remotes):
    """Cell 5, rule 2: only tracked Python files count."""
    name = public("tracked", {"src/a.py": "a = 1\n", ".gitignore": "ignored.py\n"})
    directory = remotes / name
    (directory / "untracked.py").write_text("u = 1\n" * 10)
    (directory / "ignored.py").write_text("i = 1\n" * 10)
    code, output = scoreboard(name)
    assert code == 0, output
    assert row(output, "o/tracked")[3] == "1 / 0"


@pytest.mark.parametrize(
    ("files", "cells"),
    [
        ({"tests/test_a.py": "x = 1\n"}, ["0 / 1", "—", "0 / 0", "0 / 0", "0 / 0"]),
        ({"README.md": "# no python\n"}, ["0 / 0", "—", "0 / 0", "0 / 0", "0 / 0"]),
    ],
    ids=["tests only", "no Python"],
)
def test_without_production_lines_the_ratio_is_a_dash(scoreboard, public, files, cells):
    """Cells 6 and 7, rule 2: no production lines gives "—"; a repository without measured files is a row of zeros."""
    code, output = scoreboard(public("sparse", files))
    assert code == 0, output
    assert row(output, "o/sparse")[3:8] == cells


LANGUAGES_CELL = 2
PYTHON_100 = {"src/app.py": long_file(100), "tests/test_app.py": long_file(20)}


@pytest.mark.parametrize(
    ("files", "cell"),
    [
        ({"src/a.py": long_file(3)}, "Python 3"),
        (
            {
                **PYTHON_100,
                "frontend/src/lib/api/types.ts": typescript_file(1210),
                "frontend/src/Card.svelte": typescript_file(30),
                "frontend/legacy.js": typescript_file(5),
                "scripts/deploy.sh": "echo hi\n" * 400,
                "frontend/app.css": "a { color: red; }\n" * 400,
                "frontend/index.html": "<p>hi</p>\n" * 400,
            },
            "TypeScript 1,210 · Python 120 · Svelte 30 · JavaScript 5",
        ),
        ({"a.vue": long_file(5), "b.svelte": long_file(5), "c.js": long_file(5)}, "JavaScript 5 · Svelte 5 · Vue 5"),
        ({"README.md": "# nothing measured\n", "run.sh": "echo hi\n"}, "—"),
    ],
    ids=[
        "#119 cell 1 a Python-only repository",
        "#119 cell 2 each language with production and tests together, largest first, no shell, CSS or HTML",
        "#119 cell 2 equal lines are ordered by name",
        "#119 cell 3 no measured file",
    ],
)
def test_the_languages_cell_lists_each_measured_language_largest_first(scoreboard, public, files, cell):
    """#119 E1: production and tests together; a language the table lacks appears nowhere."""
    code, output = scoreboard(public("polyglot", files))
    assert code == 0, output
    assert row(output, "o/polyglot")[LANGUAGES_CELL] == cell


def test_the_lines_columns_count_every_language_and_the_function_columns_only_python(scoreboard, public):
    """#119 E2, cells 5 and 6: a 1,210-line .ts is a production file of 800 lines or more; its 1,208-line function
    is neither a function of 60 lines nor a CCN debt."""
    files = {"src/api.ts": typescript_function(1208) + typescript_file(2), "tests/api.spec.ts": typescript_file(10)}
    code, output = scoreboard(public("typed", files))
    assert code == 0, output
    assert row(output, "o/typed")[3:8] == ["1,210 / 10", "0.01", "1 / 0", "0 / 0", "0 / 0"]


def test_a_minified_bundle_counts_its_one_line_and_nothing_is_said_to_be_skipped(scoreboard, public):
    """#119 O4, cell 7: counted by lines like any file; the Note stays empty."""
    code, output = scoreboard(public("bundled", {"dist/index.js": minified_bundle()}))
    assert code == 0, output
    cells = row(output, "o/bundled")
    assert (cells[LANGUAGES_CELL], cells[3], cells[5], cells[-1]) == ("JavaScript 1", "1 / 0", "0 / 0", "")
    assert "skip" not in output


def test_a_file_of_another_language_is_never_not_valid_python(scoreboard, public):
    """#119 cell 23: only an unparsable .py is counted in the Note."""
    files = {"src/broken.py": "def (:\n", "src/api.ts": typescript_file(3), "src/Card.svelte": "<p>{</p>\n"}
    code, output = scoreboard(public("mixed", files))
    assert code == 0, output
    assert row(output, "o/mixed")[-1] == "1 file not valid Python"


def test_an_unparsable_file_is_counted_but_never_named(scoreboard, public):
    """Cell 8, rule 6: its lines count, the row says how many files are not valid Python, the name stays out."""
    code, output = scoreboard(public("broken", {"src/secret_name.py": "def (:\n", "src/ok.py": "a = 1\n"}))
    assert code == 0, output
    assert row(output, "o/broken")[3] == "2 / 0"
    assert row(output, "o/broken")[9] == "1 file not valid Python"
    assert "secret_name" not in output


def test_a_file_name_that_is_not_utf8_is_measured(scoreboard, public):
    """Cell 9: a tracked .py whose name is not UTF-8 does not crash the row."""
    code, output = scoreboard(public("bytes", {"caf\udce9.py": "a = 1\n"}))
    assert code == 0, output
    assert row(output, "o/bytes")[3] == "1 / 0"


def test_a_repo_that_cannot_be_cloned_names_git_exit_and_no_local_path(scoreboard, public, missing, tmp_path):
    """Cell 12, rule 5: the reason carries git's exit code only, never a local absolute path."""
    code, output = scoreboard(missing)
    assert code == 2, output
    assert row(output, "o/gone")[1:3] == ["—", "cannot judge: cannot clone (git exited 128)"]
    assert str(tmp_path) not in output


def test_one_failing_repo_leaves_every_other_row_complete(scoreboard, public, missing):
    """Cell 13, rule 5: the other rows still show in full, and the run exits 2."""
    first = public("first", {"a.py": "a = 1\n"})
    last = public("last", {"tests/test_a.py": "a = 1\n"})
    code, output = scoreboard(first, missing, last)
    assert code == 2, output
    assert [row(output, name)[3] for name in ("o/first", "o/last")] == ["1 / 0", "0 / 1"]
    assert output.index("| o/first |") < output.index("| o/gone |") < output.index("| o/last |")


class AskingForCredentials(BaseHTTPRequestHandler):
    """A git host that answers every request as a private repository does: credentials, please."""

    paths: ClassVar[list[str]] = []
    authorizations: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        type(self).paths.append(self.path)
        if authorization := self.headers.get("Authorization"):
            type(self).authorizations.append(authorization)
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="private"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_: object) -> None:
        """Silent, so the test output carries only the scoreboard."""


@pytest.fixture
def private_host(monkeypatch) -> Iterator[None]:
    """`https://github.com/` is served by a local host that asks every caller for credentials."""
    AskingForCredentials.paths = []
    AskingForCredentials.authorizations = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), AskingForCredentials)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(GITHUB_BASE_VARIABLE, f"http://127.0.0.1:{server.server_port}/")
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_no_configured_credential_reaches_a_host_that_asks_for_one(scoreboard, public, private_host, tmp_path, monkeypatch):
    """Cell 14, rules 3 and 4, and the only URL asked for: a helper, an askpass and an Authorization header in system,
    global or environment config are never used, the repository stays unmeasured and the host sees only that
    repository's paths."""
    home = tmp_path / "home"
    home.mkdir()
    asked = tmp_path / "asked"
    askpass = home / "askpass"
    askpass.write_text(f"#!/bin/sh\ntouch {asked}-askpass\necho secret\n")
    askpass.chmod(0o755)
    (home / ".gitconfig").write_text(
        f"[credential]\n\thelper = \"!f() {{ touch {asked}-helper; echo username=u; echo password=p; }}; f\"\n"
        "[http]\n\textraHeader = Authorization: Basic global\n"
    )
    (home / "system.gitconfig").write_text("[http]\n\textraHeader = Authorization: Basic system\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(home / "system.gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "http.extraHeader")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "Authorization: Basic environment")
    monkeypatch.setenv("GIT_ASKPASS", str(askpass))
    code, output = scoreboard("overnightworks/edge")
    assert code == 2, output
    assert row(output, "overnightworks/edge")[1:3] == ["—", "cannot judge: cannot clone (git exited 128)"]
    assert AskingForCredentials.paths
    assert all(path.startswith("/overnightworks/edge/") for path in AskingForCredentials.paths)
    assert AskingForCredentials.authorizations == []
    assert sorted(path.name for path in tmp_path.iterdir() if path.name.startswith("asked")) == []


def test_only_the_default_branch_tip_is_measured(scoreboard, public, remotes):
    """Cell 15, rule 3: a shallow clone of the default branch; another branch's files do not count."""
    name = public("branches", {"a.py": "a = 1\n"})
    directory = remotes / name
    tip = head_commit(directory)
    git = ["git", "-C", str(directory), *GIT_IDENTITY]
    subprocess.run([*git, "checkout", "-q", "-b", "other"], check=True)
    Path(directory, "b.py").write_text("b = 1\n" * 7)
    subprocess.run([*git, "add", "b.py"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "other"], check=True)
    subprocess.run([*git, "checkout", "-q", "-"], check=True)
    code, output = scoreboard(name)
    assert code == 0, output
    assert row(output, "o/branches")[1:4] == [tip[:7], "Python 1", "1 / 0"]


def git_that_signals(directory: Path, signal_name: str) -> Path:
    """A directory whose `git` serves github.com from the remotes like the `public` fixture, runs the real one, but first
    sends `signal_name` to its caller on `rev-parse`.

    The scoreboard runs `rev-parse` once the clone exists, so the signal arrives while the clone is on disk.
    """
    directory.mkdir()
    fake = directory / "git"
    fake.write_text(
        f'#!/bin/sh\n{GITHUB_REDIRECT}\ncase " $* " in *" rev-parse "*) kill -{signal_name} $PPID;; esac\nexec {shlex.quote(shutil.which("git"))} "$@"\n'
    )
    fake.chmod(0o755)
    return directory


def scoreboard_signalled(fleet: Path, scratch: Path, fake_git: Path) -> int:
    """The exit status of `quality scoreboard` on `fleet` in its own process, its temporary directories made in `scratch`."""
    environment = {**os.environ, "PATH": f"{fake_git}{os.pathsep}{os.environ['PATH']}", "TMPDIR": str(scratch)}
    program = f"import sys; from quality import cli; sys.exit(cli.main(sys.argv[1:], release_tags=lambda: {RELEASES!r}))"
    return subprocess.run(
        [sys.executable, "-c", program, "scoreboard", str(fleet)],
        env=environment, capture_output=True, check=False, timeout=60,
    ).returncode


@pytest.mark.parametrize("outcome", ["measured", "cannot judge"])
def test_no_temporary_clone_outlives_the_run(scoreboard, public, missing, scratch, outcome):
    """Cell 16, rule 3: the temporary directory goes after success and after cannot judge."""
    scoreboard(missing if outcome == "cannot judge" else public("cleaned", {"a.py": "a = 1\n"}))
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize("stop", [signal.SIGINT, signal.SIGTERM], ids=["Ctrl-C", "SIGTERM"])
def test_no_temporary_clone_outlives_a_stopped_run(public, scratch, tmp_path, stop):
    """Cell 16, rule 3: the temporary directory goes when Ctrl-C, a cancelled job or `timeout` stops the run."""
    name = public("cleaned", {"a.py": "a = 1\n"})
    fleet = write_fleet(tmp_path / "fleet", [name])
    status = scoreboard_signalled(fleet, scratch, git_that_signals(tmp_path / "bin", stop.name.removeprefix("SIG")))
    assert status in {-stop, 128 + stop}
    assert list(scratch.iterdir()) == []


def test_the_heading_names_date_version_and_languages(scoreboard, public):
    """Cell 17, rule 6: the date of the run, the quality version and what only Python has."""
    before = datetime.now(UTC).date()
    _, output = scoreboard(public("dated", {}))
    after = datetime.now(UTC).date()
    heading, blank, header, separator, *_ = output.splitlines()
    match = re.fullmatch(rf"\*\*Fleet scoreboard, (\S+), quality {re.escape(__version__)} — functions and CCN: Python only\*\*", heading)
    assert match, heading
    assert match.group(1) in {before.isoformat(), after.isoformat()}
    assert (blank, header, separator) == ("", HEADER, "|---" * 10 + "|")


def test_the_table_goes_to_stdout_and_identically_to_the_step_summary(scoreboard, public, tmp_path, monkeypatch):
    """Cell 18, rule 6: with GITHUB_STEP_SUMMARY the same text is appended there."""
    summary = tmp_path / "summary.md"
    summary.write_text("earlier step\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    _, output = scoreboard(public("summed", {"a.py": "a = 1\n"}))
    assert MATRIX_HEADING in output
    assert summary.read_text() == "earlier step\n" + output


def test_without_a_step_summary_nothing_is_written(scoreboard, public, tmp_path, monkeypatch):
    """Cell 18, rule 6: without GITHUB_STEP_SUMMARY only stdout carries the table."""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    before = sorted(tmp_path.rglob("*"))
    _, output = scoreboard(public("quiet", {}))
    assert "| o/quiet |" in output
    assert [path for path in sorted(tmp_path.rglob("*")) if path not in before and not {"remotes", "fleet"} & set(path.parts)] == []


def test_a_url_given_as_the_path_is_a_missing_fleet_file_like_any_other_path(capsys):
    """#63 rule 1: a URL is read as a PATH, so no URL reaches git."""
    assert cli.main(["scoreboard", "https://github.com/o/r"]) == 2
    assert "quality/fleet.toml is missing" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [["--private", "https://github.com/o/p"], ["checkout", "https://github.com/o/r"]],
    ids=["--private", "second positional"],
)
def test_a_fleet_given_on_the_command_line_is_a_usage_error(capsys, arguments):
    """#63 rule 1: `--private` and a second positional are argparse usage errors."""
    with pytest.raises(SystemExit) as stopped:
        cli.main(["scoreboard", *arguments])
    assert stopped.value.code == 2
    assert "scoreboard" in capsys.readouterr().err


def test_the_fleet_file_is_read_from_the_working_directory_by_default(capsys, public, tmp_path, monkeypatch):
    """#63 rule 1: `quality scoreboard` without PATH reads ./quality/fleet.toml."""
    monkeypatch.chdir(write_fleet(tmp_path / "checkout", [public("here", {"a.py": "a = 1\n"})]))
    assert cli.main(["scoreboard"], release_tags=lambda: RELEASES) == 0
    assert row(capsys.readouterr().out, "o/here")[3] == "1 / 0"


def test_rows_sort_by_lower_cased_name_in_both_tables_whatever_the_file_order(scoreboard, public):
    """#270 ruling 3: the file's order is not the table's order."""
    later = public("Zeta", {"a.py": "a = 1\n"}, owner="b")
    earlier = public("alpha", {"a.py": "a = 1\n"}, owner="C")
    first = public("alpha", {"a.py": "a = 1\n"}, owner="a")
    _, output = scoreboard(later, earlier, first)
    for table in (output.partition(MATRIX_HEADING)[0], output.partition(MATRIX_HEADING)[2]):
        assert table.index("| a/alpha |") < table.index("| b/Zeta |") < table.index("| C/alpha |")


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        (None, "is missing"),
        ('repositories = ["o/a"', "is not valid TOML"),
        (f"repositories = {'9' * 5000}", "is not valid TOML"),
        (f"repositories = {'[' * 5000}{']' * 5000}", "is not valid TOML"),
        ('public = ["o/a"]\n', "unknown key(s) ['public']"),
        ('repositories = ["o/a"]\nprivate = []\n', "unknown key(s) ['private']"),
        ('repositories = ["o/a"]\nextra = 1\n', "unknown key(s) ['extra']"),
        ("", "needs repositories as a list of strings"),
        ('repositories = "o/a"\n', "needs repositories as a list of strings"),
        ("repositories = [1]\n", "needs repositories as a list of strings"),
        ("repositories = []\n", "lists no repository"),
    ],
    ids=["missing file", "invalid TOML", "oversized integer", "nested too deep", "public", "private", "unknown key",
         "no repositories", "not a list", "not a string", "empty list"],
)
@pytest.mark.usefixtures("public")
def test_a_fleet_file_that_cannot_be_used_cannot_be_judged(capsys, tmp_path, content, problem):
    """#63 rule 2: exit 2 before any clone, naming quality/fleet.toml, the problem and the right shape, and no table."""
    (tmp_path / "quality").mkdir()
    if content is not None:
        (tmp_path / "quality" / "fleet.toml").write_text(content)
    assert cli.main(["scoreboard", str(tmp_path)]) == 2
    captured = capsys.readouterr()
    assert "quality/fleet.toml" in captured.err
    assert problem in captured.err
    assert FLEET_EXAMPLE in captured.err
    assert captured.out == ""


@pytest.mark.parametrize("entry", ["overnightworks/quality", "O/My.repo_x-1", "a/b", "a-b/...x", "o/.hidden"])
def test_an_owner_slash_name_is_a_fleet_entry(entry):
    """#270 target: GitHub's owner and repository name syntax."""
    assert parse_fleet(f'repositories = ["{entry}"]').repositories == (entry,)


@pytest.mark.parametrize(
    "entry",
    [
        "lonelyword", "a/b/c", "/nameless", "ownerless/", "https://user:hunter2@github.com/o/r", "git@github.com:o/r",
        "ssh://git@github.com/o/r", "--upload-pack=x/y", "o/..", "o/.", "-owner/name", "o/name\\n", "o/na me",
    ],
    ids=["no slash", "two slashes", "empty owner", "empty name", "URL with a password", "scp-like", "ssh", "option",
         "dots only", "single dot", "leading hyphen", "trailing line break", "space"],
)
def test_an_entry_that_is_no_owner_slash_name_is_named_by_its_place_never_its_text(entry):
    """#270 target: a pasted URL with a password is reported as `repositories[1]`."""
    with pytest.raises(FleetError) as refused:
        parse_fleet(f'repositories = ["o/fine", "{entry}"]')
    assert "repositories[1] is no owner/name" in str(refused.value)
    assert entry not in str(refused.value)
    assert "hunter2" not in str(refused.value)


@pytest.mark.usefixtures("public")
def test_every_entry_problem_of_a_fleet_file_is_named_in_one_exit_2_message(capsys, tmp_path):
    """#270 target: two malformed entries and a repeat across case are reported together, before any clone."""
    fleet = write_fleet(tmp_path / "fleet", ["o/a", "nope", "O/A", "https://user:hunter2@github.com/o/r"])
    assert cli.main(["scoreboard", str(fleet)]) == 2
    captured = capsys.readouterr()
    for problem in ("repositories[1] is no owner/name", "repositories[2] repeats repositories[0]", "repositories[3] is no owner/name"):
        assert problem in captured.err
    assert "hunter2" not in captured.out + captured.err
    assert captured.out == ""


def test_the_fleet_lives_once_in_the_fleet_file():
    """#270 rule 5 and #63 rule 5: the real fleet file parses into owner/names and the workflow carries no URL."""
    fleet = read_fleet(Path(__file__).parent.parent)
    assert fleet.repositories
    assert "github.com/overnightworks" not in (WORKFLOWS / "scoreboard.yml").read_text()


def test_help_lists_the_scoreboard(capsys):
    """Cell 20: `quality --help` names the command."""
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "scoreboard" in capsys.readouterr().out


def workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def pinned_actions(document: dict) -> set[str]:
    return {step["uses"] for job in document["jobs"].values() for step in job.get("steps", []) if "uses" in step}


def test_the_workflow_reads_only_and_pins_what_ci_pins():
    """Cell 23, rule 7: contents: read, no secrets, no persisted credentials, ci.yml's SHAs, a timeout, concurrency."""
    scoreboard_workflow = workflow("scoreboard.yml")
    text = (WORKFLOWS / "scoreboard.yml").read_text()
    (job,) = scoreboard_workflow["jobs"].values()
    triggers = scoreboard_workflow[True]  # YAML 1.1 reads the key `on` as True
    assert set(triggers) == {"workflow_dispatch", "schedule"}
    assert triggers["schedule"] == [{"cron": "17 5 * * 1"}]
    assert scoreboard_workflow["permissions"] == {"contents": "read"}
    assert "secrets." not in text
    assert job["timeout-minutes"] == 15
    assert "concurrency" in scoreboard_workflow
    checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] is False
    used = pinned_actions(scoreboard_workflow)
    assert used <= pinned_actions(workflow("ci.yml"))
    assert {action.split("@")[0] for action in used} == {"actions/checkout", "astral-sh/setup-uv"}


@pytest.mark.parametrize(
    ("files", "pairs"),
    [
        ({"src/one.py": CLONE, "src/two.py": CLONE}, "1 / 0"),
        ({"tests/test_one.py": CLONE, "tests/test_two.py": CLONE}, "0 / 1"),
        ({"src/one.py": CLONE, "tests/test_two.py": CLONE}, "1 / 0"),
    ],
    ids=["two production files", "two test files", "a test copying production"],
)
def test_the_clone_pairs_cell_counts_a_pair_as_test_only_when_both_sides_are_tests(scoreboard, public, files, pairs):
    """#117 E7, rule 3: a function copied between two files is one pair, prod / test."""
    code, output = scoreboard(public("twins", files))
    assert code == 0, output
    assert row(output, "o/twins")[8] == pairs


@pytest.mark.integration
def test_a_public_repo_with_an_unreadable_tracked_file_cannot_be_measured(scoreboard, public, remotes, tmp_path):
    """#227 finding 10: a tracked symlink to a folder is refused as a read, and the row names the file, never the clone."""
    directory = remotes / TEST_OWNER / "linked"
    directory.mkdir(parents=True)
    os.symlink("docs", directory / "x.py")
    code, output = scoreboard(f"{TEST_OWNER}/{make_repo(directory, {'docs/a.md': 'text\n'}).name}")
    assert code == 2, output
    assert row(output, "o/linked")[1:3] == ["—", "cannot judge: cannot measure (x.py is not a file)"]
    assert str(tmp_path) not in output
