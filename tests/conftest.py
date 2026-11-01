import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path, PosixPath, PurePosixPath

import pytest

from quality import advisories, cli
from quality import fleet as fleet_module
from quality.ratchet import BASELINE_PATH

pytest.register_assert_rewrite("tests.support")  # before the first import of a support module, so its asserts are rewritten

from tests.support.advisories import advisory, zipped
from tests.support.fleet import GITHUB_BASE_VARIABLE, GITHUB_REDIRECT, RELEASES, TEST_OWNER, write_fleet
from tests.support.git_repositories import GIT_IDENTITY, head_commit, make_repo, put
from tests.support.scanners import CLEAN_SCANS, OUTPUTS, fake_scanner, serving, write_mise
from tests.support.secrets import FAKE_KEY

PROCESS_OR_SOCKET_FIXTURES = frozenset(
    {
        "build_repo",
        "public",
        "fake_git",
        "stub_server",
        "use_mise",
        "installed_scanners",
        "loopback",
        "serve",
        "served_databases",
        "release_checkout",
    }
)
REAL_DATABASE_ROOT = advisories.DATABASE_ROOT
MISE_DIRECTORIES = (
    ("MISE_DATA_DIR", "XDG_DATA_HOME", ".local/share"),
    ("MISE_CACHE_DIR", "XDG_CACHE_HOME", ".cache"),
    ("MISE_STATE_DIR", "XDG_STATE_HOME", ".local/state"),
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
            starts_process = PROCESS_OR_SOCKET_FIXTURES.intersection(item.fixturenames) or item.get_closest_marker("scanner")
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
def keyed_repo(build_repo) -> Path:
    return build_repo({"config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})


@pytest.fixture
def elsewhere(tmp_path_factory) -> Path:
    """A scratch directory outside every fixture repository."""
    return tmp_path_factory.mktemp("elsewhere")


@pytest.fixture
def use_mise(tmp_path_factory, monkeypatch):
    """`use_mise(body)` puts a `mise` that runs `body` (shell) first on PATH and returns its directory."""

    def put(body: str) -> Path:
        directory = tmp_path_factory.mktemp("fake-mise")
        write_mise(directory, body)
        monkeypatch.setenv("PATH", f"{directory}:{os.environ['PATH']}")
        return directory

    return put


@pytest.fixture
def path_without_mise(tmp_path_factory, monkeypatch):
    """A PATH that holds git and nothing else, so no mise is found whatever the machine has installed."""
    directory = tmp_path_factory.mktemp("only-git")
    (directory / "git").symlink_to(shutil.which("git"))
    monkeypatch.setenv("PATH", str(directory))


@pytest.fixture(autouse=True)
def served_scanners(tmp_path_factory, monkeypatch, publish_database) -> Path:
    """The directory whose clean fake gitleaks, actionlint and osv-scanner a fake `mise` on PATH serves, so no test
    reaches a real scanner or the network by accident; a valid advisory database for each ecosystem is published
    locally for the same reason."""
    toolchain = tmp_path_factory.mktemp("toolchain")
    directory = toolchain / "scanners"
    directory.mkdir()
    for name, on_scan in CLEAN_SCANS.items():
        fake_scanner(directory, name, on_scan)
    write_mise(toolchain / "bin", serving(directory))
    monkeypatch.setenv("PATH", f"{toolchain / 'bin'}:{os.environ['PATH']}")
    for ecosystem in sorted({kind.ecosystem for kind in advisories.LOCK_KINDS.values()}):
        publish_database(ecosystem=ecosystem)
    return directory


@pytest.fixture
def installed_scanners(served_scanners, monkeypatch) -> None:
    """The real mise and the real scanners it installed from quality's manifest.

    mise keeps its installs under the user's home, which some tests move; its directories are pinned first.
    """
    fake_mise_directory = str(served_scanners.parent / "bin")
    on_path = [entry for entry in os.environ["PATH"].split(os.pathsep) if entry != fake_mise_directory]
    monkeypatch.setenv("PATH", os.pathsep.join(on_path))
    for variable, xdg_variable, fallback in MISE_DIRECTORIES:
        default = Path(os.environ.get(xdg_variable) or Path.home() / fallback) / "mise"
        monkeypatch.setenv(variable, os.environ.get(variable, str(default)))


@pytest.fixture
def live_database(monkeypatch) -> None:
    """The advisory database the gate reads is the real one, over the network."""
    monkeypatch.setattr(advisories, "DATABASE_ROOT", REAL_DATABASE_ROOT)


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


PublicRepo = Callable[..., str]


@pytest.fixture
def scoreboard(capsys, tmp_path, public) -> Callable[..., tuple[int, str]]:
    """Runs `quality scoreboard` on a fleet file listing the given `owner/name`s (#63 rule 1)."""

    def run(*repositories: str, tags: fleet_module.TagReader = lambda: RELEASES) -> tuple[int, str]:
        fleet = write_fleet(tmp_path / "fleet", repositories)
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
def remotes(tmp_path) -> Path:
    """The directory standing in for github.com: `<owner>/<name>` lies at `remotes/<owner>/<name>`."""
    return tmp_path / "remotes"


@pytest.fixture
def github_base(remotes, monkeypatch) -> None:
    """Where `https://github.com/` is served from; a test may point it at a local HTTP host instead."""
    monkeypatch.setenv(GITHUB_BASE_VARIABLE, f"{remotes}/")


@pytest.fixture
def public(remotes, scratch, fake_git, github_base) -> PublicRepo:
    """Serves a repository as `https://github.com/<owner>/<name>`, which the scoreboard clones as it clones a public one
    (rule 3). A repository nobody served fails with git exit 128 and never reaches the network."""
    fake_git(GITHUB_REDIRECT)

    def build(name: str, files: dict[str, str], owner: str = TEST_OWNER) -> str:
        directory = remotes / owner / name
        directory.mkdir(parents=True)
        make_repo(directory, files)
        return f"{owner}/{name}"

    return build


@pytest.fixture
def missing(public) -> str:
    return f"{TEST_OWNER}/gone"


@pytest.fixture
def database_root(tmp_path_factory, monkeypatch) -> Path:
    """A file:// advisory database root the gate reads instead of the live one; nothing is published in it."""
    root = tmp_path_factory.mktemp("osv-database")
    monkeypatch.setattr(advisories, "DATABASE_ROOT", root.as_uri())
    return root


@pytest.fixture
def publish_database(database_root):
    """Publishes an ecosystem's archive at the local database root (PyPI unless told otherwise)."""

    def publish(records=None, archive: bytes | None = None, ecosystem: str = "PyPI") -> None:
        records = [advisory()] if records is None else records
        archive = zipped({f"{record['id']}.json": json.dumps(record) for record in records}) if archive is None else archive
        (database_root / ecosystem).mkdir(exist_ok=True)
        (database_root / ecosystem / "all.zip").write_bytes(archive)

    return publish


@pytest.fixture
def run_gate(capsys, served_scanners):
    """Runs the gate over a repository and returns (exit code, all output); no output names the checkout or the scanner directory (#53 cell 21)."""
    def run(repo: Path) -> tuple[int, str]:
        code = cli.main(["hygiene", str(repo)])
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert str(repo) not in output
        assert str(repo).lstrip("/") not in output
        assert str(served_scanners) not in output
        assert str(served_scanners).lstrip("/") not in output
        return code, captured.out + captured.err
    return run


@pytest.fixture
def edited_report(tmp_path_factory):
    """Writes the scanner-output fixture, edited, outside the fixture repository."""
    def write(fixture: str, edit) -> Path:
        report = json.loads((OUTPUTS / fixture).read_text())
        edit(report)
        path = tmp_path_factory.mktemp("report") / "report.json"
        path.write_text(json.dumps(report))
        return path
    return write


@pytest.fixture
def gate_clone(public):
    """The gate's own repository, served from a local repository holding `files`; returns its name."""

    def build(files: dict[str, str]) -> str:
        owner, name = fleet_module.GATE_REPOSITORY.split("/")
        return public(name, files, owner=owner)

    return build
