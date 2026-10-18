"""`quality scoreboard` (#56): cells name the item's scenario matrix, rules its numbered rules."""

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest
import yaml
from conftest import GIT_IDENTITY, make_repo
from test_ratchet import branchy_function, long_file, long_function

from quality import __version__, cli

WORKFLOWS = Path(__file__).parent.parent / ".github" / "workflows"
HEADER = (
    "| Repo | Commit | Lines prod / test | test:prod | Files ≥ 800 prod / test | Functions ≥ 60 prod / test "
    "| CCN > 15 prod / test | Note |"
)
PublicRepo = Callable[[str, dict[str, str]], str]


def scoreboard(capsys, *arguments: str) -> tuple[int, str]:
    code = cli.main(["scoreboard", *arguments])
    return code, capsys.readouterr().out


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


def test_a_public_repo_is_one_row_of_commit_lines_ratio_and_debt(capsys, public):
    """Cell 1, rule 2: commit, physical lines prod / test, test:prod with two decimals and the debt counts."""
    url = public("alpha", {"src/app.py": "a = 1\nb = 2\nc = 3\n", "tests/test_app.py": "x = 1\n" * 5})
    code, output = scoreboard(capsys, url)
    assert code == 0, output
    assert row(output, "alpha") == [
        "alpha", head_of(url)[:7], "3 / 5", "1.67", "0 / 0", "0 / 0", "0 / 0", ""
    ]


def test_debt_is_counted_from_the_size_rules_thresholds(capsys, public):
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
    code, output = scoreboard(capsys, url)
    assert code == 0, output
    assert row(output, "edges")[4:7] == ["1 / 1", "1 / 0", "1 / 0"]


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
    ],
)
def test_the_file_name_decides_production_or_test(capsys, public, path, lines):
    """Cell 3, rule 2: production versus test is ratchet.area_of's name rule."""
    code, output = scoreboard(capsys, public("named", {path: "x = 1\n"}))
    assert code == 0, output
    assert row(output, "named")[2] == lines


@pytest.mark.parametrize(
    "source", ["a = 1\n\n# note\n", "a = 1\r\n\r\n# note\r\n", "a = 1\r\r# note\r"], ids=["LF", "CRLF", "CR"]
)
def test_blank_and_comment_lines_count_whatever_the_line_ending(capsys, public, source):
    """Cell 4, rule 2: physical lines, as the 800-line rule counts them."""
    code, output = scoreboard(capsys, public("endings", {"src/a.py": source}))
    assert code == 0, output
    assert row(output, "endings")[2] == "3 / 0"


def test_untracked_files_are_not_counted(capsys, public):
    """Cell 5, rule 2: only tracked Python files count."""
    url = public("tracked", {"src/a.py": "a = 1\n", ".gitignore": "ignored.py\n"})
    directory = Path(url.removeprefix("file://"))
    (directory / "untracked.py").write_text("u = 1\n" * 10)
    (directory / "ignored.py").write_text("i = 1\n" * 10)
    code, output = scoreboard(capsys, url)
    assert code == 0, output
    assert row(output, "tracked")[2] == "1 / 0"


@pytest.mark.parametrize(
    ("files", "cells"),
    [
        ({"tests/test_a.py": "x = 1\n"}, ["0 / 1", "—", "0 / 0", "0 / 0", "0 / 0"]),
        ({"README.md": "# no python\n"}, ["0 / 0", "—", "0 / 0", "0 / 0", "0 / 0"]),
    ],
    ids=["tests only", "no Python"],
)
def test_without_production_lines_the_ratio_is_a_dash(capsys, public, files, cells):
    """Cells 6 and 7, rule 2: no production lines gives "—"; a repository without Python is a row of zeros."""
    code, output = scoreboard(capsys, public("sparse", files))
    assert code == 0, output
    assert row(output, "sparse")[2:7] == cells


def test_an_unparsable_file_is_counted_but_never_named(capsys, public):
    """Cell 8, rule 6: its lines count, the row says how many files are not valid Python, the name stays out."""
    code, output = scoreboard(capsys, public("broken", {"src/secret_name.py": "def (:\n", "src/ok.py": "a = 1\n"}))
    assert code == 0, output
    assert row(output, "broken")[2] == "2 / 0"
    assert row(output, "broken")[7] == "1 file not valid Python"
    assert "secret_name" not in output


def test_a_file_name_that_is_not_utf8_is_measured(capsys, public):
    """Cell 9: a tracked .py whose name is not UTF-8 does not crash the row."""
    code, output = scoreboard(capsys, public("bytes", {"caf\udce9.py": "a = 1\n"}))
    assert code == 0, output
    assert row(output, "bytes")[2] == "1 / 0"


def test_an_unreadable_private_repo_is_not_measured(capsys, public, missing):
    """Cell 10, rule 4: a declared private repo that anonymous git cannot read shows only its label."""
    code, output = scoreboard(capsys, public("open", {"a.py": "a = 1\n"}), "--private", missing)
    assert code == 0, output
    assert row(output, "gone")[1:] == ["—", "not measured: private", "", "", "", "", ""]


def test_a_private_repo_readable_anonymously_cannot_be_judged(capsys, public):
    """Cell 11, rule 4: a declared private repo anyone can read is never measured, and the run is red."""
    good = public("open", {"a.py": "a = 1\n"})
    leaky = public("leaky", {"src/inside.py": "a = 1\n"})
    code, output = scoreboard(capsys, good, "--private", leaky)
    assert code == 2, output
    assert row(output, "leaky")[1:] == ["—", "cannot judge: declared private but readable", "", "", "", "", ""]
    assert "inside" not in output
    assert row(output, "open")[2] == "1 / 0"


def test_a_repo_that_cannot_be_cloned_names_git_exit_and_no_local_path(capsys, public, missing, tmp_path):
    """Cell 12, rule 5: the reason carries git's exit code only, never a local absolute path."""
    code, output = scoreboard(capsys, missing)
    assert code == 2, output
    assert row(output, "gone")[1:3] == ["—", "cannot judge: cannot clone (git exited 128)"]
    assert str(tmp_path) not in output


def test_one_failing_repo_leaves_every_other_row_complete(capsys, public, missing):
    """Cell 13, rule 5: the other rows still show in full, and the run exits 2."""
    first = public("first", {"a.py": "a = 1\n"})
    last = public("last", {"tests/test_a.py": "a = 1\n"})
    code, output = scoreboard(capsys, first, missing, last)
    assert code == 2, output
    assert [row(output, name)[2] for name in ("first", "last")] == ["1 / 0", "0 / 1"]
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


def test_no_configured_credential_reaches_a_private_host(capsys, public, private_host, tmp_path, monkeypatch):
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
    code, output = scoreboard(capsys, public("open", {"a.py": "a = 1\n"}), "--private", private_host)
    assert code == 0, output
    assert row(output, "edge")[1:3] == ["—", "not measured: private"]
    assert AskingForCredentials.requests >= 1
    assert AskingForCredentials.authorizations == []
    assert sorted(path.name for path in tmp_path.iterdir() if path.name.startswith("asked")) == []


@pytest.mark.parametrize(
    ("option", "url", "name", "reason"),
    [
        ([], "ssh://git@example.invalid/org/repo.git", "repo", "cannot clone (not an http, https or file URL)"),
        ([], "git@example.invalid:org/repo.git", "repo", "cannot clone (not an http, https or file URL)"),
        (["--private"], "https://user:secret@example.invalid/org/edge", "edge",
         "cannot check that it is private (the URL carries credentials)"),
        ([], "https://user:secret@example.invalid/", "example.invalid", "cannot clone (the URL carries credentials)"),
    ],
    ids=["ssh", "scp-like", "credentials in the URL", "credentials and no path"],
)
def test_a_url_git_could_authenticate_with_cannot_be_judged(capsys, public, option, url, name, reason):
    """Rules 3 to 5: only an anonymous URL is cloned or listed; any other is a cannot-judge row, the rest still show."""
    code, output = scoreboard(capsys, public("open", {"a.py": "a = 1\n"}), *option, url)
    assert code == 2, output
    assert row(output, name)[1:3] == ["—", f"cannot judge: {reason}"]
    assert "secret" not in output
    assert row(output, "open")[2] == "1 / 0"


def test_only_the_default_branch_tip_is_measured(capsys, public):
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
    code, output = scoreboard(capsys, url)
    assert code == 0, output
    assert row(output, "branches")[1:3] == [tip[:7], "1 / 0"]


def git_that_signals(directory: Path, signal_name: str) -> Path:
    """A directory whose `git` runs the real one, but first sends `signal_name` to its caller on `rev-parse`.

    The scoreboard runs `rev-parse` once the clone exists, so the signal arrives while the clone is on disk.
    """
    directory.mkdir()
    fake = directory / "git"
    fake.write_text(
        f'#!/bin/sh\ncase " $* " in *" rev-parse "*) kill -{signal_name} $PPID;; esac\nexec {shutil.which("git")} "$@"\n'
    )
    fake.chmod(0o755)
    return directory


def scoreboard_signalled(url: str, scratch: Path, fake_git: Path) -> int:
    """The exit status of `quality scoreboard url` in its own process, its temporary directories made in `scratch`."""
    environment = {**os.environ, "PATH": f"{fake_git}{os.pathsep}{os.environ['PATH']}", "TMPDIR": str(scratch)}
    program = "import sys; from quality import cli; sys.exit(cli.main(sys.argv[1:]))"
    return subprocess.run(
        [sys.executable, "-c", program, "scoreboard", url],
        env=environment, capture_output=True, check=False, timeout=60,
    ).returncode


@pytest.mark.parametrize("outcome", ["measured", "cannot judge"])
def test_no_temporary_clone_outlives_the_run(capsys, public, missing, scratch, outcome):
    """Cell 16, rule 3: the temporary directory goes after success and after cannot judge."""
    scoreboard(capsys, missing if outcome == "cannot judge" else public("cleaned", {"a.py": "a = 1\n"}))
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize("stop", [signal.SIGINT, signal.SIGTERM], ids=["Ctrl-C", "SIGTERM"])
def test_no_temporary_clone_outlives_a_stopped_run(public, scratch, tmp_path, stop):
    """Cell 16, rule 3: the temporary directory goes when Ctrl-C, a cancelled job or `timeout` stops the run."""
    url = public("cleaned", {"a.py": "a = 1\n"})
    status = scoreboard_signalled(url, scratch, git_that_signals(tmp_path / "bin", stop.name.removeprefix("SIG")))
    assert status in {-stop, 128 + stop}
    assert list(scratch.iterdir()) == []


def test_the_heading_names_date_version_and_languages(capsys, public):
    """Cell 17, rule 6: the date of the run, the quality version and "Python only"."""
    before = datetime.now(UTC).date()
    _, output = scoreboard(capsys, public("dated", {}))
    after = datetime.now(UTC).date()
    heading, blank, header, separator, *_ = output.splitlines()
    match = re.fullmatch(rf"\*\*Fleet scoreboard, (\S+), quality {re.escape(__version__)} — Python only\*\*", heading)
    assert match, heading
    assert match.group(1) in {before.isoformat(), after.isoformat()}
    assert (blank, header, separator) == ("", HEADER, "|---" * 8 + "|")


def test_the_table_goes_to_stdout_and_identically_to_the_step_summary(capsys, public, tmp_path, monkeypatch):
    """Cell 18, rule 6: with GITHUB_STEP_SUMMARY the same text is appended there."""
    summary = tmp_path / "summary.md"
    summary.write_text("earlier step\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    _, output = scoreboard(capsys, public("summed", {"a.py": "a = 1\n"}))
    assert summary.read_text() == "earlier step\n" + output


def test_without_a_step_summary_nothing_is_written(capsys, public, tmp_path, monkeypatch):
    """Cell 18, rule 6: without GITHUB_STEP_SUMMARY only stdout carries the table."""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    before = sorted(tmp_path.rglob("*"))
    _, output = scoreboard(capsys, public("quiet", {}))
    assert "| quiet |" in output
    assert [path for path in sorted(tmp_path.rglob("*")) if path not in before and "remotes" not in path.parts] == []


@pytest.mark.parametrize("arguments", [[], ["--private", "file:///x"]], ids=["nothing", "only private"])
def test_without_a_public_repo_the_command_is_a_usage_error(capsys, arguments):
    """Cell 20, rule 1: `quality scoreboard URL... --private URL...` needs at least one public URL."""
    with pytest.raises(SystemExit) as stopped:
        cli.main(["scoreboard", *arguments])
    assert stopped.value.code == 2
    assert "scoreboard" in capsys.readouterr().err


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
