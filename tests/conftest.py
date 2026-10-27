import http.server
import json
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from quality import advisories, cli
from quality import scoreboard as scoreboard_module
from quality.ratchet import BASELINE_PATH

pytest.register_assert_rewrite("tests.support")  # before the first import of a support module, so its asserts are rewritten

from tests.support.advisories import advisory, zipped
from tests.support.engines import OUTPUTS, fake_engine
from tests.support.git_repositories import GIT_IDENTITY, head_commit, make_repo, put
from tests.support.scoreboard import RELEASES, write_fleet

RepoBuilder = Callable[[dict[str, str]], Path]
Responder = Callable[[http.server.BaseHTTPRequestHandler], None]


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
