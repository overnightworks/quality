import hashlib
import http.server
import json
import os
import shutil
import subprocess
import threading
import unicodedata
from collections.abc import Callable
from pathlib import Path

import pytest

from quality import cli, engines
from quality.ratchet import BASELINE_PATH

FAKE_KEY = "AKIA" + "QYLPMTYX3QZ5ABCD"  # assembled so quality's own scan stays green
OTHER_KEY = "AKIA" + "QYLPMTYX3QZ5WXYZ"  # a second assembled key, to prove another value stays red
GIT_IDENTITY = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
UNKNOWN_KEY_CONFIGS = pytest.mark.parametrize(
    "config",
    [
        '[gate]\nmandatory_jobs = ["checks"]\nwhatever = 1\n',
        '[gate]\nmandatory_jobs = ["checks"]\n[future]\nkey = 1\n',
    ],
    ids=["unknown gate key", "unknown table"],
)
RepoBuilder = Callable[[dict[str, str]], Path]
Responder = Callable[[http.server.BaseHTTPRequestHandler], None]
SHOP = {"src/shop/__init__.py": "", "src/shop/web.py": "", "src/shop/api.py": "", "src/shop/db.py": "x = 1\n"}
SHOP_DB_IMPORTS_WEB = {**SHOP, "src/shop/db.py": "import shop.web\n"}
FORBIDDEN = {"type": "forbidden", "source_modules": ["shop.db"], "forbidden_modules": ["shop.web"]}


def sha256_of(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def toml(contract: dict, roots: tuple[str, ...] = ("shop",)) -> str:
    """A `pyproject.toml` with one contract named `guarded`; JSON strings, lists and booleans are TOML too."""
    entries = "".join(f"{key} = {json.dumps(value)}\n" for key, value in {"name": "guarded", **contract}.items())
    return f"[tool.importlinter]\nroot_packages = {json.dumps(list(roots))}\n\n[[tool.importlinter.contracts]]\n{entries}"


def assert_every_line_starts_with_the_door(lines: list[str]) -> None:
    """#97 rule 2, cell 18: no line repository text could forge, and no raw control character between the lines."""
    for line in lines:
        assert not line.lstrip().startswith(("::", "File ", "raise ")), line
        assert all(unicodedata.category(character) != "Cc" for character in line), line


def allow_config(*entries: tuple[str, str, str, str]) -> str:
    """Entries are (rule, file, secret, reason); the config holds the secret's hash only."""
    rows = ", ".join(
        f'{{ rule = "{rule}", file = "{file}", secret_sha256 = "{sha256_of(secret)}", reason = "{reason}" }}'
        for rule, file, secret, reason in entries
    )
    return f"[gate]\nmandatory_jobs = [\"checks\"]\n[secrets]\nallow = [{rows}]\n"


@pytest.fixture(autouse=True)
def isolated_git_config(monkeypatch):
    """Fixture repositories never see the developer's global git config, hooks included."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture(autouse=True)
def local_door(monkeypatch):
    """Under GitHub Actions the door stops workflow commands; tests that expect the local door must not inherit that."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


def make_repo(directory: Path, files: dict[str, str]) -> Path:
    """A git repository at `directory` whose one commit tracks exactly `files`, ignored names included."""
    git = ["git", "-C", str(directory), *GIT_IDENTITY]
    subprocess.run([*git, "init", "-q"], check=True)
    for name, content in files.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    subprocess.run([*git, "add", "-A", "-f"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "fixture"], check=True)
    return directory


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


def fake_engine(directory: Path, name: str, on_scan: str) -> Path:
    """Writes a fake `name` engine that prints its pinned version and runs `on_scan` (shell) for any other call."""
    version_arguments = " ".join(engines.VERSION_ARGUMENTS[name])
    pinned = engines.read_pins()[name].version
    binary = directory / name
    binary.write_text(f'#!/bin/sh\nif [ "$*" = "{version_arguments}" ]; then echo {pinned}; exit 0; fi\n{on_scan}\n')
    binary.chmod(0o755)
    return binary


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


def head_commit(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


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


def put(path: Path, content: str | bytes | None) -> None:
    """Writes `content` to `path`, or removes the file when `content` is None."""
    if content is None:
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())


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
