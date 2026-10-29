import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Sequence
from pathlib import Path, PosixPath, PurePosixPath

import pytest

from quality import advisories, cli
from quality import scoreboard as scoreboard_module
from quality.ratchet import BASELINE_PATH

pytest.register_assert_rewrite("tests.support")  # before the first import of a support module, so its asserts are rewritten

from tests.support.advisories import advisory, zipped
from tests.support.git_repositories import GIT_IDENTITY, head_commit, make_repo, put
from tests.support.scanners import OUTPUTS, fake_engine
from tests.support.scoreboard import RELEASES, write_fleet

PROCESS_OR_SOCKET_FIXTURES = frozenset(
    {
        "build_repo",
        "public",
        "fake_git",
        "stub_server",
        "installed_engines",
        "clean_engines",
        "loopback",
        "serve",
        "served_databases",
        "release_checkout",
    }
)
KIND_MARKERS = frozenset({"unit", "integration"})
# The one owner of the audit event names that define `integration`.
STARTING_EVENTS = frozenset(
    {
        "subprocess.Popen",
        "os.system",
        "os.posix_spawn",
        "os.exec",
        "os.spawn",
        "os.fork",
        "os.forkpty",
        "socket.connect",
        "socket.bind",
    }
)
EVENT_DETAIL_ARGUMENTS = {
    "subprocess.Popen": slice(0, 2),
    "os.system": slice(0, 1),
    "os.posix_spawn": slice(0, 2),
    "os.exec": slice(0, 2),
    "os.spawn": slice(1, 3),
    "socket.connect": slice(1, 2),
    "socket.bind": slice(1, 2),
}

RepoBuilder = Callable[[dict[str, str]], Path]
Responder = Callable[[http.server.BaseHTTPRequestHandler], None]


# Exact built-in types whose repr cannot raise; any other type, subclasses included, prints as its name.
REPRESENTABLE_TYPES = frozenset({int, float, bool, bytes, type(None), PurePosixPath, PosixPath})

_recording: list[tuple[str, tuple]] | None = None


def _record_starting_event(event: str, arguments: tuple) -> None:
    if event in STARTING_EVENTS and _recording is not None:
        _recording.append((event, arguments[EVENT_DETAIL_ARGUMENTS.get(event, slice(0, 0))]))  # never the environment: run_env puts a token there


sys.addaudithook(_record_starting_event)


def _describe_value(value: object) -> str:
    """Never calls the value's own `__repr__`, which may raise."""
    if isinstance(value, str):
        return str.__repr__(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_describe_value(item) for item in value) + "]"
    if type(value) in REPRESENTABLE_TYPES:
        return repr(value)
    return f"<{type(value).__name__}>"


def _describe(event: str, detail: tuple) -> str:
    return f"{event} {', '.join(_describe_value(value) for value in detail)}" if detail else event


def _stop_recording_and_judge(item) -> None:
    global _recording
    events, _recording = _recording, None
    if events and item.get_closest_marker("unit"):
        pytest.fail(
            f"unit test started a process or opened a socket: {_describe(*events[0])}. "
            "Request a fixture that does (build_repo, ...) or mark the test @pytest.mark.integration.",
            pytrace=False,
        )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(session, config, items):
    for item in items:
        hand_marked = KIND_MARKERS & {mark.name for mark in item.iter_markers()}
        if len(hand_marked) == 2:
            raise pytest.UsageError(f"{item.nodeid} carries both unit and integration")
        if not hand_marked:
            starts_process = PROCESS_OR_SOCKET_FIXTURES.intersection(item.fixturenames) or item.get_closest_marker("engine")
            item.add_marker("integration" if starts_process else "unit")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    global _recording
    _recording = []
    try:
        return (yield)
    except (pytest.skip.Exception, pytest.xfail.Exception):  # a skipped or xfailed setup never reaches the call phase
        _stop_recording_and_judge(item)
        raise


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    try:
        return (yield)
    finally:
        _stop_recording_and_judge(item)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item):
    global _recording
    _recording = None
    return (yield)


@pytest.fixture(autouse=True)
def isolated_git_config(monkeypatch):
    """Fixture repositories never see the developer's global git config, hooks included."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture(autouse=True)
def local_door(monkeypatch):
    """Under GitHub Actions the door stops workflow commands; tests that expect the local door must not inherit that."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


@pytest.fixture
def build_repo(tmp_path: Path) -> RepoBuilder:
    return lambda files: make_repo(tmp_path, files)


@pytest.fixture
def elsewhere(tmp_path_factory) -> Path:
    """A scratch directory outside every fixture repository."""
    return tmp_path_factory.mktemp("elsewhere")


@pytest.fixture(scope="session")
def installed_engines(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("engines")
    assert cli.main(["engines", "install", str(directory)]) == 0
    return directory


@pytest.fixture
def clean_engines(tmp_path_factory) -> Path:
    """A gitleaks that reports its pin and no leak; with no workflow tracked, no other engine runs."""
    directory = tmp_path_factory.mktemp("engines")
    fake_engine(
        directory,
        "gitleaks",
        on_scan='while [ $# -gt 0 ]; do [ "$1" = --report-path ] && report=$2; shift; done\necho "[]" > "$report"',
    )
    return directory


@pytest.fixture
def fake_git(tmp_path, monkeypatch):
    """Puts a `git` first on PATH that runs `preamble` (shell) and then the real git."""
    real_git = shutil.which("git")

    def install(preamble: str) -> None:
        fake_bin = tmp_path / "fake-git"
        fake_bin.mkdir()
        (fake_bin / "git").write_text(f'#!/bin/sh\n{preamble}\nexec {real_git} "$@"\n')
        (fake_bin / "git").chmod(0o755)
        monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")

    return install


@pytest.fixture
def run_env(monkeypatch, build_repo, tmp_path):
    repo = build_repo({"quality/config.toml": '[gate]\nmandatory_jobs = ["checks"]\n'})
    summary = tmp_path / "summary.md"
    for key, value in {
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_RUN_ID": "1",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_TOKEN": "t",
        "GATE_NEEDS": json.dumps({"hygiene": {"result": "success"}}),
        "GITHUB_STEP_SUMMARY": str(summary),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("GATE_BASE_SHA", raising=False)
    return repo, summary


@pytest.fixture
def pull_request(monkeypatch, run_env):
    """Commits `base` as the pull request's base baseline and points GATE_BASE_SHA at that commit."""
    repo, _ = run_env

    def commit_base(base: str | bytes | None, config: str | None = None) -> str:
        put(repo / BASELINE_PATH, base)
        if config is not None:
            put(repo / "quality" / "config.toml", config)
        git = ["git", "-C", str(repo), *GIT_IDENTITY]
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "base"], check=True)
        sha = head_commit(repo)
        monkeypatch.setenv("GATE_BASE_SHA", sha)
        return sha

    return repo, commit_base


@pytest.fixture
def stub_server():
    """`serve(responder)` starts a loopback server and returns its origin and the headers of each request it got."""
    servers = []

    def serve(responder: Responder) -> tuple[str, list[dict[str, str]]]:
        heard: list[dict[str, str]] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                heard.append(dict(self.headers))
                responder(self)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}", heard

    yield serve
    for server in servers:
        server.shutdown()
        server.server_close()


PublicRepo = Callable[[str, dict[str, str]], str]


@pytest.fixture
def scoreboard(capsys, tmp_path) -> Callable[..., tuple[int, str]]:
    """Runs `quality scoreboard` on a fleet file listing the given URLs (#63 rule 1)."""

    def run(
        *public: str, private: Sequence[str] = (), tags: scoreboard_module.TagReader = lambda: RELEASES
    ) -> tuple[int, str]:
        fleet = write_fleet(tmp_path / "fleet", public, private)
        code = cli.main(["scoreboard", str(fleet)], release_tags=tags)
        return code, capsys.readouterr().out

    return run


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


@pytest.fixture
def publish_database(tmp_path_factory, monkeypatch):
    """Points the gate at a file:// advisory database root and publishes an ecosystem's archive there (PyPI unless told otherwise)."""
    root = tmp_path_factory.mktemp("osv-database")
    monkeypatch.setattr(advisories, "DATABASE_ROOT", root.as_uri())

    def publish(records=None, archive: bytes | None = None, ecosystem: str = "PyPI") -> None:
        records = [advisory()] if records is None else records
        archive = zipped({f"{record['id']}.json": json.dumps(record) for record in records}) if archive is None else archive
        (root / ecosystem).mkdir(exist_ok=True)
        (root / ecosystem / "all.zip").write_bytes(archive)

    return publish


@pytest.fixture
def run_gate(capsys):
    """Runs the gate over a repository and returns (exit code, all output); no output names the checkout or the engine directory (#53 cell 21)."""
    def run(repo: Path, engine_dir: Path) -> tuple[int, str]:
        code = cli.main(["hygiene", "--engines", str(engine_dir), str(repo)])
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert str(repo) not in output
        assert str(repo).lstrip("/") not in output
        assert str(engine_dir) not in output
        assert str(engine_dir).lstrip("/") not in output
        return code, captured.out + captured.err
    return run


@pytest.fixture
def edited_report(tmp_path_factory):
    """Writes the engine-output fixture, edited, outside the fixture repository."""
    def write(fixture: str, edit) -> Path:
        report = json.loads((OUTPUTS / fixture).read_text())
        edit(report)
        path = tmp_path_factory.mktemp("report") / "report.json"
        path.write_text(json.dumps(report))
        return path
    return write


@pytest.fixture
def gate_clone(public, fake_git):
    """The gate's own repository URL, served from a local repository holding `files` by a git on PATH that redirects it."""

    def build(files: dict[str, str]) -> str:
        url = f"https://github.com/{scoreboard_module.GATE_REPOSITORY}"
        fake_git(
            f"export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0='url.{public('gate', files)}.insteadOf' GIT_CONFIG_VALUE_0='{url}'"
        )
        return url

    return build
