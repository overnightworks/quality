"""`quality scoreboard` (#56): cells name the item's scenario matrix, rules its numbered rules."""

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest
import yaml
from conftest import (
    GIT_IDENTITY,
    branchy_function,
    long_file,
    long_function,
    make_repo,
    minified_bundle,
    typescript_file,
    typescript_function,
)
from test_clones import function as clone_function

from quality import __version__, cli
from quality.scoreboard import read_fleet, require_anonymous

WORKFLOWS = Path(__file__).parent.parent / ".github" / "workflows"
HEADER = (
    "| Repo | Commit | Languages | Lines prod / test | test:prod | Files ≥ 800 prod / test | Functions ≥ 60 prod / test "
    "| CCN > 15 prod / test | Clone pairs prod / test | Note |"
)
# 3 + 5 * 7 + 2 = 40 tokens: `arguments`, `arg`, `ID`, seven `Assign, Name, ID, Name, ID` and two `Pass`
CLONE = clone_function("copied", "value", "local")
PublicRepo = Callable[[str, dict[str, str]], str]


def write_fleet(directory: Path, public: Sequence[str] = (), private: Sequence[str] = ()) -> Path:
    """A repository whose quality/fleet.toml lists these URLs; json.dumps writes a TOML array of basic strings."""
    (directory / "quality").mkdir(parents=True)
    (directory / "quality" / "fleet.toml").write_text(f"public = {json.dumps(public)}\nprivate = {json.dumps(private)}\n")
    return directory


@pytest.fixture
def scoreboard(capsys, tmp_path) -> Callable[..., tuple[int, str]]:
    """Runs `quality scoreboard` on a fleet file listing the given URLs (#63 rule 1)."""

    def run(*public: str, private: Sequence[str] = ()) -> tuple[int, str]:
        code = cli.main(["scoreboard", str(write_fleet(tmp_path / "fleet", public, private))])
        return code, capsys.readouterr().out

    return run


def row(output: str, name: str) -> list[str]:
    """The cells of the table row of repository `name`."""
    (line,) = [line for line in output.splitlines() if line.startswith(f"| {name} |")]
    return [cell.strip() for cell in line.strip("|").split("|")]


def head_of(url: str) -> str:
    directory = url.removeprefix("file://")
    return subprocess.run(
        ["git", "-C", directory, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def scratch(tmp_path, monkeypatch) -> Path:
    """The directory every temporary clone is made in, so a test can see what is left behind."""
    directory = tmp_path / "scratch"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    return directory


@pytest.fixture
def public(tmp_path, scratch) -> PublicRepo:
    """A repository served as a file:// URL, which the scoreboard clones as it clones a public one (rule 3)."""

    def build(name: str, files: dict[str, str]) -> str:
        directory = tmp_path / "remotes" / name
        directory.mkdir(parents=True)
        return make_repo(directory, files).as_uri()

    return build


@pytest.fixture
def missing(tmp_path) -> str:
    return (tmp_path / "remotes" / "gone").as_uri()


def test_a_public_repo_is_one_row_of_commit_lines_ratio_and_debt(scoreboard, public):
    """Cell 1, rule 2: commit, physical lines prod / test, test:prod with two decimals and the debt counts."""
    url = public("alpha", {"src/app.py": "a = 1\nb = 2\nc = 3\n", "tests/test_app.py": "x = 1\n" * 5})
    code, output = scoreboard(url)
    assert code == 0, output
    assert row(output, "alpha") == [
        "alpha", head_of(url)[:7], "Python 8", "3 / 5", "1.67", "0 / 0", "0 / 0", "0 / 0", "0 / 0", ""
    ]


def test_debt_is_counted_from_the_size_rules_thresholds(scoreboard, public):
    """Cell 2, rule 2: 800 file lines, 60 function lines and CCN 16 are debt; 799, 59 and CCN 15 are not."""
    url = public(
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
    code, output = scoreboard(url)
    assert code == 0, output
    assert row(output, "edges")[5:8] == ["1 / 1", "1 / 0", "1 / 0"]


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
    assert row(output, "named")[3] == lines


@pytest.mark.parametrize(
    "source", ["a = 1\n\n# note\n", "a = 1\r\n\r\n# note\r\n", "a = 1\r\r# note\r"], ids=["LF", "CRLF", "CR"]
)
def test_blank_and_comment_lines_count_whatever_the_line_ending(scoreboard, public, source):
    """Cell 4, rule 2: physical lines, as the 800-line rule counts them."""
    code, output = scoreboard(public("endings", {"src/a.py": source}))
    assert code == 0, output
    assert row(output, "endings")[3] == "3 / 0"


def test_untracked_files_are_not_counted(scoreboard, public):
    """Cell 5, rule 2: only tracked Python files count."""
    url = public("tracked", {"src/a.py": "a = 1\n", ".gitignore": "ignored.py\n"})
    directory = Path(url.removeprefix("file://"))
    (directory / "untracked.py").write_text("u = 1\n" * 10)
    (directory / "ignored.py").write_text("i = 1\n" * 10)
    code, output = scoreboard(url)
    assert code == 0, output
    assert row(output, "tracked")[3] == "1 / 0"


@pytest.mark.parametrize(
    ("files", "cells"),
    [
        ({"tests/test_a.py": "x = 1\n"}, ["0 / 1", "—", "0 / 0", "0 / 0", "0 / 0"]),
        ({"README.md": "# no python\n"}, ["0 / 0", "—", "0 / 0", "0 / 0", "0 / 0"]),
    ],
    ids=["tests only", "no Python"],
)
def test_without_production_lines_the_ratio_is_a_dash(scoreboard, public, files, cells):
    """Cells 6 and 7, rule 2: no production lines gives "—"; a repository without Python is a row of zeros."""
    code, output = scoreboard(public("sparse", files))
    assert code == 0, output
    assert row(output, "sparse")[3:8] == cells


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
    assert row(output, "polyglot")[LANGUAGES_CELL] == cell


def test_the_lines_columns_count_every_language_and_the_function_columns_only_python(scoreboard, public):
    """#119 E2, cells 5 and 6: a 1,210-line .ts is a production file of 800 lines or more; its 1,208-line function
    is neither a function of 60 lines nor a CCN debt."""
    files = {"src/api.ts": typescript_function(1208) + typescript_file(2), "tests/api.spec.ts": typescript_file(10)}
    code, output = scoreboard(public("typed", files))
    assert code == 0, output
    assert row(output, "typed")[3:8] == ["1,210 / 10", "0.01", "1 / 0", "0 / 0", "0 / 0"]


def test_a_minified_bundle_counts_its_one_line_and_nothing_is_said_to_be_skipped(scoreboard, public):
    """#119 O4, cell 7: counted by lines like any file; the Note stays empty."""
    code, output = scoreboard(public("bundled", {"dist/index.js": minified_bundle()}))
    assert code == 0, output
    cells = row(output, "bundled")
    assert (cells[LANGUAGES_CELL], cells[3], cells[5], cells[-1]) == ("JavaScript 1", "1 / 0", "0 / 0", "")
    assert "skip" not in output


def test_a_file_of_another_language_is_never_not_valid_python(scoreboard, public):
    """#119 cell 23: only an unparsable .py is counted in the Note."""
    files = {"src/broken.py": "def (:\n", "src/api.ts": typescript_file(3), "src/Card.svelte": "<p>{</p>\n"}
    code, output = scoreboard(public("mixed", files))
    assert code == 0, output
    assert row(output, "mixed")[-1] == "1 file not valid Python"


def test_an_unparsable_file_is_counted_but_never_named(scoreboard, public):
    """Cell 8, rule 6: its lines count, the row says how many files are not valid Python, the name stays out."""
    code, output = scoreboard(public("broken", {"src/secret_name.py": "def (:\n", "src/ok.py": "a = 1\n"}))
    assert code == 0, output
    assert row(output, "broken")[3] == "2 / 0"
    assert row(output, "broken")[9] == "1 file not valid Python"
    assert "secret_name" not in output


def test_a_file_name_that_is_not_utf8_is_measured(scoreboard, public):
    """Cell 9: a tracked .py whose name is not UTF-8 does not crash the row."""
    code, output = scoreboard(public("bytes", {"caf\udce9.py": "a = 1\n"}))
    assert code == 0, output
    assert row(output, "bytes")[3] == "1 / 0"


def test_an_unreadable_private_repo_is_not_measured(scoreboard, public, missing):
    """Cell 10, rule 4: a declared private repo that anonymous git cannot read shows only its label."""
    code, output = scoreboard(public("open", {"a.py": "a = 1\n"}), private=[missing])
    assert code == 0, output
    assert row(output, "gone")[1:] == ["—", "not measured: private", "", "", "", "", "", "", ""]


def test_a_private_repo_readable_anonymously_cannot_be_judged(scoreboard, public):
    """Cell 11, rule 4: a declared private repo anyone can read is never measured, and the run is red."""
    good = public("open", {"a.py": "a = 1\n"})
    leaky = public("leaky", {"src/inside.py": "a = 1\n"})
    code, output = scoreboard(good, private=[leaky])
    assert code == 2, output
    assert row(output, "leaky")[1:] == ["—", "cannot judge: declared private but readable", "", "", "", "", "", "", ""]
    assert "inside" not in output
    assert row(output, "open")[3] == "1 / 0"


def test_a_repo_that_cannot_be_cloned_names_git_exit_and_no_local_path(scoreboard, public, missing, tmp_path):
    """Cell 12, rule 5: the reason carries git's exit code only, never a local absolute path."""
    code, output = scoreboard(missing)
    assert code == 2, output
    assert row(output, "gone")[1:3] == ["—", "cannot judge: cannot clone (git exited 128)"]
    assert str(tmp_path) not in output


def test_one_failing_repo_leaves_every_other_row_complete(scoreboard, public, missing):
    """Cell 13, rule 5: the other rows still show in full, and the run exits 2."""
    first = public("first", {"a.py": "a = 1\n"})
    last = public("last", {"tests/test_a.py": "a = 1\n"})
    code, output = scoreboard(first, missing, last)
    assert code == 2, output
    assert [row(output, name)[3] for name in ("first", "last")] == ["1 / 0", "0 / 1"]
    assert output.index("| first |") < output.index("| gone |") < output.index("| last |")


class AskingForCredentials(BaseHTTPRequestHandler):
    """A git host that answers every request as a private repository does: credentials, please."""

    requests = 0
    authorizations: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        type(self).requests += 1
        if authorization := self.headers.get("Authorization"):
            type(self).authorizations.append(authorization)
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="private"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_: object) -> None:
        """Silent, so the test output carries only the scoreboard."""


@pytest.fixture
def private_host() -> Iterator[str]:
    AskingForCredentials.requests = 0
    AskingForCredentials.authorizations = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), AskingForCredentials)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/overnightworks/edge"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_no_configured_credential_reaches_a_private_host(scoreboard, public, private_host, tmp_path, monkeypatch):
    """Cell 14, rules 3 and 4: a helper, an askpass and an Authorization header in system, global or environment
    config are never used, and the private repo stays unmeasured."""
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
    code, output = scoreboard(public("open", {"a.py": "a = 1\n"}), private=[private_host])
    assert code == 0, output
    assert row(output, "edge")[1:3] == ["—", "not measured: private"]
    assert AskingForCredentials.requests >= 1
    assert AskingForCredentials.authorizations == []
    assert sorted(path.name for path in tmp_path.iterdir() if path.name.startswith("asked")) == []


@pytest.mark.parametrize(
    ("list_name", "url", "name", "reason"),
    [
        ("public", "ssh://git@example.invalid/org/repo.git", "repo", "cannot clone (not an http, https or file URL)"),
        ("public", "git@example.invalid:org/repo.git", "repo", "cannot clone (not an http, https or file URL)"),
        ("private", "https://user:secret@example.invalid/org/edge", "edge",
         "cannot check that it is private (the URL carries credentials)"),
        ("public", "https://user:secret@example.invalid/", "example.invalid", "cannot clone (the URL carries credentials)"),
        ("public", "https://[broken/repo", "unnamed URL", "cannot clone (not a valid URL)"),
        ("private", "https://[broken/edge", "unnamed URL", "cannot check that it is private (not a valid URL)"),
        ("public", "HTTPS://example.invalid/org/repo", "repo", "cannot clone (not an http, https or file URL)"),
        ("public", "https:example.invalid/org/repo", "repo", "cannot clone (not an http, https or file URL)"),
        ("public", "file:/tmp/org/repo", "repo", "cannot clone (not an http, https or file URL)"),
        ("public", "https://example.invalid/org/repo\n", "repo", "cannot clone (not an http, https or file URL)"),
        ("public", "https://[dead]/repo", "unnamed URL", "cannot clone (not a valid URL)"),
        ("public", "https://[::1]x[::2]/repo", "unnamed URL", "cannot clone (not a valid URL)"),
        ("private", "https://[dead]/edge", "unnamed URL", "cannot check that it is private (not a valid URL)"),
        ("public", "--upload-pack=touch pwned", "--upload-pack=touch pwned", "cannot clone (reads as a git option)"),
        ("private", "--upload-pack=touch pwned", "--upload-pack=touch pwned",
         "cannot check that it is private (reads as a git option)"),
    ],
    ids=["ssh", "scp-like", "credentials in the URL", "credentials and no path", "invalid", "invalid private",
         "uppercase scheme", "scheme without slashes", "file without slashes", "trailing line break", "bad IPv6 host",
         "mixed IPv6 hosts", "bad IPv6 host private", "option", "option private"],
)
def test_a_url_git_could_misread_or_authenticate_with_cannot_be_judged(scoreboard, public, list_name, url, name, reason):
    """Rules 3 to 5 and #63 rule 3, written through the fleet file: only a valid anonymous URL that cannot read as a
    git option is cloned or listed; any other is a cannot-judge row, the rest still show."""
    open_repo = public("open", {"a.py": "a = 1\n"})
    listed = {"public": ([url], []), "private": ([], [url])}[list_name]
    code, output = scoreboard(open_repo, *listed[0], private=listed[1])
    assert code == 2, output
    assert row(output, name)[1:3] == ["—", f"cannot judge: {reason}"]
    assert "secret" not in output
    assert row(output, "open")[3] == "1 / 0"


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/overnightworks/quality",
        "file:///tmp/org/repo",
        "file:///tmp/org/my repo",
        "http://127.0.0.1:8080/o/r",
        "http://[::1]:8080/o/r",
        "https://[fe80::1%25eth0]/repo",
    ],
)
def test_an_anonymous_url_is_let_through(url):
    """#63 rule 2: an http, https or file URL with `//`, no credentials and no line break is not refused."""
    require_anonymous(url)


def test_only_the_default_branch_tip_is_measured(scoreboard, public):
    """Cell 15, rule 3: a shallow clone of the default branch; another branch's files do not count."""
    url = public("branches", {"a.py": "a = 1\n"})
    directory = url.removeprefix("file://")
    tip = head_of(url)
    git = ["git", "-C", directory, *GIT_IDENTITY]
    subprocess.run([*git, "checkout", "-q", "-b", "other"], check=True)
    Path(directory, "b.py").write_text("b = 1\n" * 7)
    subprocess.run([*git, "add", "b.py"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "other"], check=True)
    subprocess.run([*git, "checkout", "-q", "-"], check=True)
    code, output = scoreboard(url)
    assert code == 0, output
    assert row(output, "branches")[1:4] == [tip[:7], "Python 1", "1 / 0"]


def git_that_signals(directory: Path, signal_name: str) -> Path:
    """A directory whose `git` runs the real one, but first sends `signal_name` to its caller on `rev-parse`.

    The scoreboard runs `rev-parse` once the clone exists, so the signal arrives while the clone is on disk.
    """
    directory.mkdir()
    fake = directory / "git"
    fake.write_text(
        f'#!/bin/sh\ncase " $* " in *" rev-parse "*) kill -{signal_name} $PPID;; esac\nexec {shlex.quote(shutil.which("git"))} "$@"\n'
    )
    fake.chmod(0o755)
    return directory


def scoreboard_signalled(fleet: Path, scratch: Path, fake_git: Path) -> int:
    """The exit status of `quality scoreboard` on `fleet` in its own process, its temporary directories made in `scratch`."""
    environment = {**os.environ, "PATH": f"{fake_git}{os.pathsep}{os.environ['PATH']}", "TMPDIR": str(scratch)}
    program = "import sys; from quality import cli; sys.exit(cli.main(sys.argv[1:]))"
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
    url = public("cleaned", {"a.py": "a = 1\n"})
    status = scoreboard_signalled(write_fleet(tmp_path / "fleet", [url]), scratch, git_that_signals(tmp_path / "bin", stop.name.removeprefix("SIG")))
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
    assert summary.read_text() == "earlier step\n" + output


def test_without_a_step_summary_nothing_is_written(scoreboard, public, tmp_path, monkeypatch):
    """Cell 18, rule 6: without GITHUB_STEP_SUMMARY only stdout carries the table."""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    before = sorted(tmp_path.rglob("*"))
    _, output = scoreboard(public("quiet", {}))
    assert "| quiet |" in output
    assert [path for path in sorted(tmp_path.rglob("*")) if path not in before and not {"remotes", "fleet"} & set(path.parts)] == []


def test_a_url_given_as_the_path_is_a_missing_fleet_file_like_any_other_path(capsys):
    """#63 rule 1: a URL is read as a PATH, so no URL reaches git."""
    assert cli.main(["scoreboard", "https://github.com/o/r"]) == 2
    assert "quality/fleet.toml is missing" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [["https://github.com/o/r", "--private", "https://github.com/o/p"], ["--private", "https://github.com/o/p"],
     ["checkout", "https://github.com/o/r"]],
    ids=["URL and --private", "--private", "second positional"],
)
def test_a_fleet_given_on_the_command_line_is_a_usage_error(capsys, arguments):
    """#63 rule 1: `--private` and a second positional are argparse usage errors."""
    with pytest.raises(SystemExit) as stopped:
        cli.main(["scoreboard", *arguments])
    assert stopped.value.code == 2
    assert "scoreboard" in capsys.readouterr().err


def test_a_private_only_fleet_is_measured(scoreboard, missing):
    """#63 rule 2: an empty public list is fine; the private row stands alone."""
    code, output = scoreboard(private=[missing])
    assert code == 0, output
    assert row(output, "gone")[1:3] == ["—", "not measured: private"]


def test_the_fleet_file_is_read_from_the_working_directory_by_default(capsys, public, tmp_path, monkeypatch):
    """#63 rule 1: `quality scoreboard` without PATH reads ./quality/fleet.toml."""
    monkeypatch.chdir(write_fleet(tmp_path / "checkout", [public("here", {"a.py": "a = 1\n"})]))
    assert cli.main(["scoreboard"]) == 0
    assert row(capsys.readouterr().out, "here")[3] == "1 / 0"


def test_public_rows_come_first_then_private_ones_each_in_file_order(scoreboard, public, missing):
    """#63 rule 4: the file's order is the table's order within each list."""
    second = public("second", {"a.py": "a = 1\n"})
    first = public("first", {"a.py": "a = 1\n"})
    _, output = scoreboard(second, first, private=[missing])
    assert output.index("| second |") < output.index("| first |") < output.index("| gone |")


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        (None, "is missing"),
        ('public = ["https://x/a"', "is not valid TOML"),
        ('public = ["https://x/a"]\nprivate = []\nextra = 1\n', "unknown key(s) ['extra']"),
        ('public = "https://x/a"\nprivate = []\n', "needs public as a list of strings"),
        ('public = [1]\nprivate = []\n', "needs public as a list of strings"),
        ('public = ["https://x/a"]\n', "needs private as a list of strings"),
        ('public = ["https://x/a", "https://x/a"]\nprivate = []\n', "public[1] repeats public[0]"),
        ('public = ["https://x/a"]\nprivate = ["https://x/b", "https://x/a"]\n', "private[1] repeats public[0]"),
        ("public = []\nprivate = []\n", "lists no URL"),
    ],
    ids=["missing file", "invalid TOML", "unknown key", "not a list", "not a string", "missing key", "duplicate",
         "in both lists", "no URL"],
)
def test_a_fleet_file_that_cannot_be_used_cannot_be_judged(capsys, tmp_path, content, problem):
    """#63 rule 2: exit 2, naming quality/fleet.toml, the problem and the right shape, and no table."""
    (tmp_path / "quality").mkdir()
    if content is not None:
        (tmp_path / "quality" / "fleet.toml").write_text(content)
    assert cli.main(["scoreboard", str(tmp_path)]) == 2
    captured = capsys.readouterr()
    assert "quality/fleet.toml" in captured.err
    assert problem in captured.err
    assert 'public = ["https://github.com/o/open"]' in captured.err
    assert captured.out == ""


def test_the_fleet_file_lists_the_fleet_the_workflow_reports():
    """#63 rule 5: nine public and four private repositories, and the workflow carries no URL."""
    fleet = read_fleet(Path(__file__).parent.parent)
    assert (len(fleet.public), len(fleet.private)) == (9, 4)
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


def test_a_repeated_credentialed_url_is_named_by_its_place_never_its_text(capsys, tmp_path):
    """#63 rule 2 and the secrets rule: the duplicate report must not print a password embedded in the URL."""
    url = "https://user:secret@example.invalid/o/r"
    fleet = write_fleet(tmp_path / "fleet", [url, url], [url])
    assert cli.main(["scoreboard", str(fleet)]) == 2
    captured = capsys.readouterr()
    assert "public[1] repeats public[0]" in captured.err
    assert "private[0] repeats public[0]" in captured.err
    assert "secret" not in captured.out + captured.err


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
    assert row(output, "twins")[8] == pairs
