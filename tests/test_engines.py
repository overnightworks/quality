import hashlib
import http.server
import io
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import urllib.request
from pathlib import Path

import pytest

from quality import cli, engines
from quality.engines import EngineError, Pin

OUTPUTS = Path(__file__).parent / "fixtures" / "engine-outputs"
FAKE_KEY = "AKIA" + "QYLPMTYX3QZ5ABCD"  # assembled so quality's own scan stays green
OTHER_KEY = "AKIA" + "QYLPMTYX3QZ5WXYZ"  # a second assembled key, to prove another value stays red


def recorded(name: str) -> str:
    return (OUTPUTS / name).read_text(encoding="utf-8")


def archive_with(binary_text: str) -> bytes:
    buffer = io.BytesIO()
    content = binary_text.encode()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        entry = tarfile.TarInfo("gitleaks")
        entry.size = len(content)
        tar.addfile(entry, io.BytesIO(content))
    return buffer.getvalue()


def local_pin(tmp_path: Path, archive: bytes, version: str = "1.2.3", sha256: str | None = None) -> Pin:
    served = tmp_path / "served.tar.gz"
    served.write_bytes(archive)
    return Pin(version, served.as_uri(), sha256 or hashlib.sha256(archive).hexdigest())


PINNED = engines.read_pins()["gitleaks"].version


def fake_gitleaks(tmp_path: Path, version: str = PINNED, on_scan: str = "exit 0", version_exit: int = 0) -> Path:
    """An engine directory whose gitleaks prints `version`, and on any other command runs `on_scan`."""
    engine_dir = tmp_path / "engines"
    engine_dir.mkdir(parents=True)
    binary = engine_dir / "gitleaks"
    binary.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = version ]; then echo {version}; exit {version_exit}; fi\n'
        'printf "%s\\n" "$@" > "$0.arguments"\n'
        'while [ $# -gt 0 ]; do [ "$1" = --report-path ] && report=$2; shift; done\n'
        f"{on_scan}\n"
    )
    binary.chmod(0o755)
    return engine_dir


def hygiene_exit_2(repo: Path, engine_dir: Path, capsys) -> str:
    assert cli.main(["hygiene", "--engines", str(engine_dir), str(repo)]) == 2
    return capsys.readouterr().err


class Redirector(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append(dict(self.headers))
        if self.server.redirect_to:
            self.send_response(302)
            self.send_header("Location", self.server.redirect_to)
            self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(self.server.body)

    def log_message(self, *_):
        pass


@pytest.fixture
def serve():
    servers = []

    def start(body: bytes = b"", redirect_to: str = "") -> http.server.HTTPServer:
        server = http.server.HTTPServer(("127.0.0.1", 0), Redirector)
        server.body, server.redirect_to, server.requests = body, redirect_to, []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def url_of(server: http.server.HTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_port}/gitleaks.tar.gz"


@pytest.mark.parametrize("engine", ["gitleaks", "actionlint", "zizmor"])
def test_pin_file_names_every_engine_with_a_full_checksum(engine):
    """#13 rule 1, #15 rule 1: the pin file names the version, a download of that version, and a sha256."""
    pin = engines.read_pins()[engine]
    assert pin.version
    assert pin.version in pin.url
    assert re.fullmatch(r"[0-9a-f]{64}", pin.sha256)


def test_install_unpacks_a_verified_download_as_an_executable(tmp_path):
    """#13 rule 1: a download that matches its checksum is installed as an executable."""
    pins = {"gitleaks": local_pin(tmp_path, archive_with("#!/bin/sh\n"))}
    engines.install(tmp_path / "bin", pins)
    assert os.access(tmp_path / "bin" / "gitleaks", os.X_OK)


def test_install_follows_a_redirect_to_another_host_without_sending_credentials(tmp_path, serve):
    """#13 rule 1 and fact 3: a redirect to another host is followed, and no credential travels with it."""
    archive = archive_with("#!/bin/sh\n")
    target = serve(body=archive)
    release = serve(redirect_to=url_of(target))
    pins = {"gitleaks": Pin("1.2.3", url_of(release), hashlib.sha256(archive).hexdigest())}
    engines.install(tmp_path / "bin", pins)
    sent = {name.lower() for request in release.requests + target.requests for name in request}
    assert os.access(tmp_path / "bin" / "gitleaks", os.X_OK)
    assert not sent & {"authorization", "cookie"}


def test_a_redirected_download_with_the_wrong_checksum_installs_nothing(tmp_path, serve):
    """#13 rule 1 and fact 3: integrity comes from the checksum, whatever host the redirect leads to."""
    target = serve(body=archive_with("tampered"))
    release = serve(redirect_to=url_of(target))
    pins = {"gitleaks": Pin("1.2.3", url_of(release), "0" * 64)}
    with pytest.raises(EngineError, match="sha256"):
        engines.install(tmp_path / "bin", pins)
    assert not (tmp_path / "bin" / "gitleaks").exists()


def test_wrong_checksum_is_exit_2_and_installs_nothing(tmp_path, capsys):
    """#13 rule 1: a wrong checksum is exit 2 and leaves no binary."""
    pin = local_pin(tmp_path, archive_with("x"), sha256="0" * 64)
    assert cli.main(["engines", "install", str(tmp_path / "bin")], read_pins=lambda: {"gitleaks": pin}) == 2
    assert "sha256" in capsys.readouterr().err
    assert not (tmp_path / "bin" / "gitleaks").exists()


def test_unreachable_download_is_exit_2(tmp_path, capsys):
    """#13 rule 1: a download that cannot be fetched is exit 2."""
    pin = Pin("1.2.3", (tmp_path / "missing.tar.gz").as_uri(), "0" * 64)
    assert cli.main(["engines", "install", str(tmp_path / "bin")], read_pins=lambda: {"gitleaks": pin}) == 2
    assert "cannot download" in capsys.readouterr().err


def test_scan_refuses_an_installed_binary_at_another_version(tmp_path, build_repo, capsys):
    """#13 rule 2: a binary at another version is exit 2 naming both versions."""
    err = hygiene_exit_2(build_repo({}), fake_gitleaks(tmp_path / "fake", "9.9.9"), capsys)
    assert "'9.9.9'" in err
    assert repr(PINNED) in err


def test_scan_never_falls_back_to_a_gitleaks_on_path(tmp_path, build_repo, monkeypatch, capsys):
    """#13 rule 2: a gitleaks on PATH is never used in place of the installed one."""
    on_path = fake_gitleaks(tmp_path / "other")
    monkeypatch.setenv("PATH", f"{on_path}:{os.environ['PATH']}")
    assert "cannot run" in hygiene_exit_2(build_repo({}), tmp_path / "empty", capsys)


def test_a_version_command_that_fails_is_exit_2_even_when_it_prints_the_pin(tmp_path, build_repo, capsys):
    """#13 rule 2: an engine whose version command exits non-zero is not trusted."""
    engine_dir = fake_gitleaks(tmp_path / "fake", version_exit=3)
    assert "version exited 3" in hygiene_exit_2(build_repo({}), engine_dir, capsys)


@pytest.mark.parametrize(
    ("on_scan", "expected"),
    [
        ("echo boom >&2; exit 3", "exited 3 without writing its report: boom"),
        ("exit 0", "exited 0 without writing its report"),
        ('echo "[]" > "$report"; echo boom >&2; exit 3', "exited 3, neither 'clean' nor 'findings': boom"),
        ('printf "\\377\\376" > "$report"; exit 0', "cannot read the gitleaks report"),
    ],
)
def test_an_engine_that_crashes_or_leaves_no_usable_report_is_exit_2(tmp_path, build_repo, capsys, on_scan, expected):
    """#13 rule 3: a missing, undecodable or exit-code-inconsistent report is exit 2, with the engine's stderr."""
    assert expected in hygiene_exit_2(build_repo({}), fake_gitleaks(tmp_path / "fake", on_scan=on_scan), capsys)


@pytest.mark.parametrize(
    ("report", "exit_code", "expected"),
    [
        ("gitleaks-clean.json", 0, []),
        ("gitleaks-finding.json", 7, ["secret deploy/config.txt:3"]),
    ],
)
def test_report_becomes_findings(report, exit_code, expected):
    """#13 rule 3: each leak in a recorded report becomes a secret finding at file:line."""
    findings = engines.secret_findings(recorded(report), exit_code)
    assert [f"{f.rule} {f.location}" for f in findings] == expected


@pytest.mark.parametrize(
    ("report", "exit_code"),
    [
        ("gitleaks-garbled.json", 7),
        ("gitleaks-garbled.json", 0),
        ("", 0),
        ("{}", 0),
        ('""', 0),
        ('[{"File": "a", "StartLine": 1, "RuleID": "r", "Commit": null}]', 7),
        ("gitleaks-clean.json", 7),
        ("gitleaks-finding.json", 0),
        ("gitleaks-clean.json", 1),
        ("gitleaks-finding.json", 126),
    ],
)
def test_unreadable_or_inconsistent_gitleaks_result_cannot_be_judged(report, exit_code):
    """#13 rule 3: a garbled, mis-shaped or inconsistent result is an EngineError, never clean."""
    text = recorded(report) if report.endswith(".json") else report
    with pytest.raises(EngineError):
        engines.secret_findings(text, exit_code)


@pytest.fixture(scope="session")
def installed_engines(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("engines")
    assert cli.main(["engines", "install", str(directory)]) == 0
    return directory


def scan(repo: Path, installed_engines: Path) -> int:
    return cli.main(["hygiene", "--engines", str(installed_engines), str(repo)])


def commit(repo: Path, message: str) -> None:
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", message], check=True)


@pytest.mark.engine
def test_clean_history_passes(build_repo, installed_engines):
    """#13 rule 4: a repository without a secret passes."""
    assert scan(build_repo({"notes.txt": "nothing here\n"}), installed_engines) == 0


@pytest.mark.engine
def test_committed_secret_fails(build_repo, installed_engines, capsys):
    """#13 rule 4: a committed key fails, reported at file:line with the secret redacted."""
    repo = build_repo({"config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    assert scan(repo, installed_engines) == 1
    out = capsys.readouterr().out
    assert "secret config.txt:1" in out
    assert FAKE_KEY not in out


@pytest.mark.engine
def test_secret_removed_in_a_later_commit_still_fails(build_repo, installed_engines):
    """#13 rules 4 and 5: a key removed in a later commit still fails because it lives in history."""
    repo = build_repo({"config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    (repo / "config.txt").write_text("aws_access_key_id = removed\n")
    commit(repo, "remove the key")
    assert scan(repo, installed_engines) == 1


@pytest.mark.engine
def test_secret_reachable_only_from_a_ref_outside_head_still_fails(build_repo, installed_engines):
    """#13 rule 5: the whole history includes branches fetched as remote-tracking refs, not only HEAD's ancestry."""
    repo = build_repo({"notes.txt": "nothing here\n"})
    (repo / "config.txt").write_text(f"aws_access_key_id = {FAKE_KEY}\n")
    commit(repo, "commit the key")
    subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/origin/side", "HEAD"], check=True)
    subprocess.run(["git", "-C", str(repo), "reset", "-q", "--hard", "HEAD~1"], check=True)
    assert scan(repo, installed_engines) == 1


@pytest.mark.engine
def test_a_relative_repository_path_starting_with_a_dash_is_scanned_as_that_repository(
    build_repo, installed_engines, tmp_path, monkeypatch
):
    """#13 rule 4: a repository path beginning with '-' is a path, never an option to gitleaks."""
    repo = build_repo({"config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    subprocess.run(["git", "clone", "-q", str(repo), "--", str(workdir / "-repo")], check=True)
    monkeypatch.chdir(workdir)
    findings = engines.scan_secrets(Path("-repo"), installed_engines, engines.read_pins(), {}).findings
    assert [(finding.path, finding.line) for finding in findings] == [("config.txt", 1)]


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


@pytest.mark.engine
@pytest.mark.parametrize("attribute_line", ["config.txt -diff", "config.txt binary", "* -diff -text"])
def test_a_committed_attribute_line_cannot_hide_a_key(build_repo, installed_engines, attribute_line):
    """#21 rule 1: a .gitattributes in the repository that makes git print "Binary files differ" is still red."""
    repo = build_repo({".gitattributes": f"{attribute_line}\n", "config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    assert scan(repo, installed_engines) == 1


@pytest.fixture
def keyed_repo(build_repo) -> Path:
    return build_repo({"config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})


@pytest.fixture
def hiding_attributes(tmp_path) -> Path:
    path = tmp_path / "hiding-attributes"
    path.write_text("config.txt -diff\n")
    return path


@pytest.mark.engine
def test_a_global_attributes_file_cannot_hide_a_key(keyed_repo, installed_engines, tmp_path, monkeypatch):
    """#21 rule 1: attributes from the user's global file are not read either."""
    config_home = tmp_path / "config-home"
    (config_home / "git").mkdir(parents=True)
    (config_home / "git" / "attributes").write_text("config.txt -diff\n")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    assert scan(keyed_repo, installed_engines) == 1


@pytest.mark.engine
def test_a_configured_core_attributes_file_cannot_hide_a_key(
    keyed_repo, installed_engines, tmp_path, monkeypatch, hiding_attributes
):
    """#21 rule 1: a core.attributesFile in the user's own git config is not read."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(f"[core]\n\tattributesFile = {hiding_attributes}\n")
    monkeypatch.setenv("HOME", str(home))
    assert scan(keyed_repo, installed_engines) == 1


@pytest.mark.engine
def test_an_inherited_git_config_variable_cannot_bring_an_attributes_file_back(
    keyed_repo, installed_engines, monkeypatch, hiding_attributes
):
    """#21 rule 1: every other inherited GIT_* variable is removed, GIT_CONFIG_COUNT included."""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.attributesFile")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(hiding_attributes))
    assert scan(keyed_repo, installed_engines) == 1


@pytest.mark.engine
def test_a_diff_driver_marked_binary_cannot_hide_a_key(build_repo, installed_engines, tmp_path, monkeypatch):
    """#21 rule 1: a diff=<driver> attribute whose configured driver is binary does not hide the key."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text('[diff "hidden"]\n\tbinary = true\n')
    monkeypatch.setenv("HOME", str(home))
    repo = build_repo({".gitattributes": "config.txt diff=hidden\n", "config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    assert scan(repo, installed_engines) == 1


def test_the_git_environment_cuts_off_every_attributes_source(tmp_path, monkeypatch):
    """#21 rule 1: the git environment excludes the system file, forces an empty attributes file, drops other GIT_*."""
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "5")
    environment = engines.attribute_free_git_environment(tmp_path / "home")
    assert {name: value for name, value in environment.items() if name.startswith("GIT_")} == {
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_ATTR_SOURCE": engines.EMPTY_TREE,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.attributesFile",
        "GIT_CONFIG_VALUE_0": os.devnull,
    }


@pytest.mark.engine
def test_a_core_attributes_file_in_the_repository_config_cannot_hide_a_key(
    keyed_repo, installed_engines, hiding_attributes
):
    """#21 rule 1: a core.attributesFile in the scanned repository's own config does not reach the mirror."""
    subprocess.run(["git", "-C", str(keyed_repo), "config", "core.attributesFile", str(hiding_attributes)], check=True)
    assert scan(keyed_repo, installed_engines) == 1


@pytest.mark.engine
def test_the_repositorys_info_attributes_cannot_hide_a_key(keyed_repo, installed_engines):
    """#21 rule 1: the scanned repository's .git/info/attributes is not read."""
    (keyed_repo / ".git" / "info").mkdir(exist_ok=True)
    (keyed_repo / ".git" / "info" / "attributes").write_text("config.txt -diff\n")
    assert scan(keyed_repo, installed_engines) == 1


@pytest.mark.engine
def test_a_template_directory_cannot_put_attributes_into_the_mirror(
    keyed_repo, installed_engines, tmp_path, fake_git
):
    """#21 rule 1: attributes shipped by git's default template directory do not reach the mirror."""
    template = tmp_path / "template" / "info"
    template.mkdir(parents=True)
    (template / "attributes").write_text("* -diff\n")
    fake_git(f'export GIT_TEMPLATE_DIR="{template.parent}"')
    assert scan(keyed_repo, installed_engines) == 1


@pytest.mark.engine
@pytest.mark.parametrize("git_version", ["2.40.1", "1.9.0"])
def test_a_git_older_than_2_41_is_exit_2_naming_the_version(
    installed_engines, build_repo, fake_git, capsys, git_version
):
    """#21 rule 2: below the floor the scan cannot hide attributes, so it is exit 2 with one line, never clean.

    The git floor is checked right after gitleaks' version and before any scan, so the fake git alone decides this test.
    """
    fake_git(f'[ "$1" = "--version" ] && echo "git version {git_version}" && exit 0')
    err = hygiene_exit_2(build_repo({}), installed_engines, capsys)
    assert err.strip() == (
        f"hygiene: cannot judge - git {git_version} cannot hide .gitattributes from the gitleaks scan, "
        "it needs git 2.41"
    )


@pytest.mark.engine
def test_installed_binary_reports_the_pinned_version(build_repo, installed_engines):
    """#13 rule 2: the installed real gitleaks passes the version check against the pin."""
    assert engines.scan_secrets(build_repo({}), installed_engines, engines.read_pins(), {}).findings == []


@pytest.fixture
def escaping_engine_dir(tmp_path) -> tuple[Path, Path]:
    """An engine directory whose gitleaks is a symlink to a script outside it."""
    outside = tmp_path / "outside" / "gitleaks"
    outside.parent.mkdir()
    outside.write_text(f"#!/bin/sh\ntouch {tmp_path / 'executed'}\necho {PINNED}\n")
    outside.chmod(0o755)
    engine_dir = tmp_path / "engines"
    engine_dir.mkdir()
    (engine_dir / "gitleaks").symlink_to(outside)
    return engine_dir, outside


def test_install_refuses_an_engine_symlinked_outside_the_directory(escaping_engine_dir, tmp_path, capsys):
    """#13 rule 1: install never writes through a symlink that leaves the engine directory."""
    engine_dir, outside = escaping_engine_dir
    before = outside.read_bytes()
    pin = local_pin(tmp_path, archive_with("x"))
    assert cli.main(["engines", "install", str(engine_dir)], read_pins=lambda: {"gitleaks": pin}) == 2
    assert "not a plain file name" in capsys.readouterr().err
    assert outside.read_bytes() == before


def test_scan_never_runs_an_engine_symlinked_outside_the_directory(escaping_engine_dir, tmp_path, build_repo, capsys):
    """#13 rule 2: hygiene refuses a symlinked engine and never executes its target."""
    engine_dir, _ = escaping_engine_dir
    assert "not a plain file name" in hygiene_exit_2(build_repo({}), engine_dir, capsys)
    assert not (tmp_path / "executed").exists()


FINDING_EXIT = {"actionlint": 1, "zizmor": 12}


def workflow_findings(engine: str, report_text: str, exit_code: int) -> list:
    return engines.engine_findings(engines.WORKFLOW_ENGINES[engine], report_text, exit_code, "")


@pytest.mark.parametrize(
    ("engine", "expected"),
    [
        ("actionlint", "workflow-lint .github/workflows/a.yml:4"),
        ("zizmor", "workflow-security .github/workflows/w.yml:7"),
    ],
)
def test_workflow_engine_reports_become_findings(engine, expected):
    """#15 rule 2: a recorded clean report is no finding; a recorded finding is one at file:line."""
    assert workflow_findings(engine, recorded(f"{engine}-clean.json"), 0) == []
    findings = workflow_findings(engine, recorded(f"{engine}-finding.json"), FINDING_EXIT[engine])
    assert [f"{f.rule} {f.location}" for f in findings] == [expected]


@pytest.mark.parametrize("engine", ["actionlint", "zizmor"])
@pytest.mark.parametrize(
    ("report", "exit_code"),
    [
        ("garbled", 0),
        ("garbled", "finding"),
        ("", 0),
        ("{}", 0),
        ('[{"unexpected": "shape"}]', "finding"),
        ('[{"message": null}]', "finding"),
        ("clean", "finding"),
        ("finding", 0),
        ("clean", 2),
        ("finding", 126),
    ],
)
def test_unreadable_or_inconsistent_workflow_result_cannot_be_judged(engine, report, exit_code):
    """#15 rule 2: a garbled, cut-off, mis-shaped or inconsistent result is an EngineError, never clean."""
    text = recorded(f"{engine}-{report}.json") if report in ("garbled", "clean", "finding") else report
    code = FINDING_EXIT[engine] if exit_code == "finding" else exit_code
    with pytest.raises(EngineError):
        workflow_findings(engine, text, code)


def fake_workflow_engines(tmp_path: Path, on_scan: str) -> Path:
    """An engine directory with all three engines printing their pinned version, running `on_scan` otherwise."""
    engine_dir = fake_gitleaks(tmp_path / "fake", on_scan='echo "[]" > "$report"; exit 0')
    for name, version_flag in (("actionlint", "-version"), ("zizmor", "--version")):
        binary = engine_dir / name
        pinned = engines.read_pins()[name].version
        binary.write_text(f'#!/bin/sh\nif [ "$1" = {version_flag} ]; then echo {pinned}; exit 0; fi\n{on_scan}\n')
        binary.chmod(0o755)
    return engine_dir


@pytest.mark.parametrize(
    ("on_scan", "expected"),
    [
        ("exit 0", "cannot read the actionlint report"),
        ("echo boom >&2; exit 3", "actionlint exited 3, neither 'clean' nor 'findings': boom"),
        ('printf "[" ; exit 0', "cannot read the actionlint report"),
        ('printf "\\377\\376"; exit 0', "cannot run"),
    ],
    ids=["exit-0-without-report", "error-exit", "cut-off-report", "undecodable-report"],
)
def test_a_workflow_engine_without_a_usable_report_is_exit_2(tmp_path, build_repo, capsys, on_scan, expected):
    """#15 rule 2: an engine that exits 0 with no report, or errors out, is exit 2 and never clean."""
    repo = build_repo({".github/workflows/w.yml": "on: push\n"})
    assert expected in hygiene_exit_2(repo, fake_workflow_engines(tmp_path, on_scan), capsys)


@pytest.mark.parametrize("engine", ["gitleaks", "actionlint", "zizmor"])
def test_an_engine_that_is_a_symlink_inside_the_directory_is_still_judged_by_its_name(
    tmp_path, build_repo, capsys, engine
):
    """#15 rule 2: a pinned engine reached through a symlink to a versioned file in the directory is not a crash."""
    engine_dir = fake_workflow_engines(tmp_path, 'echo "[]"; exit 0')
    versioned = engine_dir / f"{engine}-{engines.read_pins()[engine].version}"
    (engine_dir / engine).rename(versioned)
    (engine_dir / engine).symlink_to(versioned)
    repo = build_repo({".github/workflows/w.yml": "on: push\n"})
    assert cli.main(["hygiene", "--engines", str(engine_dir), str(repo)]) == 0


def test_no_workflow_engine_runs_when_the_repository_has_no_workflow(tmp_path):
    """#15 rule 2: without a `.github/workflows/*.y{a,}ml` there is nothing to lint, so no engine is needed."""
    assert engines.scan_workflows(tmp_path, tmp_path / "no-engines", engines.read_pins(), []) == []


def test_a_run_without_engines_names_the_skipped_scans(build_repo, capsys):
    """#15 rule 5: a local run without --engines says which scans it skipped."""
    assert cli.main(["hygiene", str(build_repo({}))]) == 0
    assert "skipped without --engines: secret, workflow-lint, workflow-security" in capsys.readouterr().out


@pytest.fixture
def workflow_repo(build_repo):
    def build(workflow: str) -> Path:
        return build_repo({".github/workflows/w.yml": workflow})

    return build


UNPINNED_WITHOUT_PERMISSIONS = (
    "on: push\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n"
)

def local_reusable_call(prefix: str) -> str:
    return (
        "on: push\npermissions: {}\njobs:\n  j:\n    permissions:\n      contents: read\n"
        f"    uses: {prefix}/.github/workflows/w.yml\n"
    )


INVALID_RUNS_ON = "on: push\npermissions: {}\njobs:\n  j:\n    runs-on: ${{ foo( }}\n    steps:\n      - run: 'true'\n"


@pytest.mark.engine
@pytest.mark.parametrize(
    ("workflow", "expected"),
    [
        (UNPINNED_WITHOUT_PERMISSIONS, "workflow-security .github/workflows/w.yml:6"),
        (INVALID_RUNS_ON, "workflow-lint .github/workflows/w.yml:5"),
        (local_reusable_call("."), "workflow-security .github/workflows/w.yml:7"),
    ],
    ids=["unpinned-action-without-permissions", "invalid-runs-on-expression", "local-reusable-call-with-dot-slash"],
)
def test_a_known_bad_workflow_fails(workflow_repo, installed_engines, capsys, workflow, expected):
    """#15 rule 3: the real engines fail a known bad workflow at file:line."""
    assert scan(workflow_repo(workflow), installed_engines) == 1
    assert expected in capsys.readouterr().out


@pytest.mark.engine
def test_quality_own_workflows_pass_both_engines(build_repo, installed_engines):
    """#15 rules 3 and 4: quality's own workflows pass actionlint and zizmor unchanged."""
    own = Path(__file__).parent.parent / ".github" / "workflows"
    repo = build_repo({f".github/workflows/{file.name}": file.read_text() for file in own.glob("*.yml")})
    assert scan(repo, installed_engines) == 0


@pytest.mark.engine
def test_a_local_reusable_call_in_the_dollar_slash_form_passes_both_engines(workflow_repo, installed_engines):
    """#15 rule 4: `uses: $/…` satisfies zizmor's self-repository audit and actionlint's false error is ignored."""
    assert scan(workflow_repo(local_reusable_call("$")), installed_engines) == 0


ONLY_AN_ONLINE_AUDIT_FLAGS_THIS = (
    "on: push\npermissions: {}\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n"
    "      - uses: actions/checkout@0000000000000000000000000000000000000000\n"
    "        with:\n          persist-credentials: false\n"
)


@pytest.mark.engine
def test_a_run_with_engines_names_no_skipped_scan_and_discloses_that_online_audits_do_not_run(
    workflow_repo, installed_engines, capsys
):
    """#15 rule 5: a workflow only zizmor's online audit (impostor-commit) would flag passes, and the run says so."""
    assert scan(workflow_repo(ONLY_AN_ONLINE_AUDIT_FLAGS_THIS), installed_engines) == 0
    out = capsys.readouterr().out
    assert "skipped" not in out
    assert "online-only audits do not run" in out


CALLER_SILENCES = {
    ".github/zizmor.yml": "rules:\n  unpinned-uses:\n    disable: true\n  excessive-permissions:\n    disable: true\n",
    ".github/actionlint.yaml": "paths:\n  '.github/workflows/w.yml':\n    ignore:\n      - '.*'\n",
}
INLINE_IGNORE = UNPINNED_WITHOUT_PERMISSIONS.replace(
    "actions/checkout@v4", "actions/checkout@v4 # zizmor: ignore[unpinned-uses,artipacked]"
)


@pytest.mark.engine
@pytest.mark.parametrize(
    ("workflow", "caller_files", "expected"),
    [
        (UNPINNED_WITHOUT_PERMISSIONS, {".github/zizmor.yml": CALLER_SILENCES[".github/zizmor.yml"]}, "unpinned-uses"),
        (INLINE_IGNORE, {}, "unpinned-uses"),
        (INVALID_RUNS_ON, {".github/actionlint.yaml": CALLER_SILENCES[".github/actionlint.yaml"]}, "workflow-lint"),
    ],
    ids=["caller-zizmor-config", "inline-zizmor-ignore", "caller-actionlint-config"],
)
def test_what_the_scanned_repository_says_never_silences_a_workflow_finding(
    build_repo, installed_engines, capsys, workflow, caller_files, expected
):
    """#15 material risk: a caller's engine config or ignore comment cannot turn a failing workflow green."""
    repo = build_repo({".github/workflows/w.yml": workflow, **caller_files})
    assert scan(repo, installed_engines) == 1
    assert expected in capsys.readouterr().out


@pytest.mark.engine
def test_a_shellcheck_on_path_never_runs_during_workflow_lint(build_repo, installed_engines, tmp_path, monkeypatch):
    """#15 rule 1: actionlint judges without whichever shellcheck PATH offers."""
    planted = tmp_path / "planted"
    planted.mkdir()
    marker = tmp_path / "shellcheck-ran"
    (planted / "shellcheck").write_text(f"#!/bin/sh\ntouch {marker}\necho '[]'\n")
    (planted / "shellcheck").chmod(0o755)
    monkeypatch.setenv("PATH", f"{planted}:{os.environ['PATH']}")
    workflow = "on: push\npermissions: {}\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo $FOO\n"
    scan(build_repo({".github/workflows/w.yml": workflow}), installed_engines)
    assert not marker.exists()


@pytest.fixture(scope="module")
def pinned_zizmor_asset() -> bytes:
    with urllib.request.urlopen(engines.read_pins()["zizmor"].url, timeout=engines.DOWNLOAD_TIMEOUT_SECONDS) as asset:
        return asset.read()


@pytest.mark.engine
@pytest.mark.parametrize(("tamper", "exit_code"), [(False, 0), (True, 2)], ids=["pinned-bytes", "one-byte-changed"])
def test_a_redirected_pinned_release_asset_is_decided_by_its_checksum_alone(
    tmp_path, serve, pinned_zizmor_asset, tamper, exit_code
):
    """#15 rule 6, #5 finding 24: the pinned asset served through a redirect installs; a changed byte is exit 2."""
    served = bytearray(pinned_zizmor_asset)
    if tamper:
        served[-1] = (served[-1] + 1) % 256
    release = serve(redirect_to=url_of(serve(body=bytes(served))))
    pinned = engines.read_pins()["zizmor"]
    pin = Pin(pinned.version, url_of(release), pinned.sha256)
    assert cli.main(["engines", "install", str(tmp_path / "bin")], read_pins=lambda: {"zizmor": pin}) == exit_code
    assert (tmp_path / "bin" / "zizmor").exists() is (exit_code == 0)


RECORDED_SECRET = "PLACEHOLDER-SECRET-VALUE"  # the Secret in the recorded gitleaks-finding.json
RECORDED_RULE = "aws-access-token"
RECORDED_FILE = "deploy/config.txt"


def sha256_of(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def allow_config(*entries: tuple[str, str, str, str]) -> str:
    """Entries are (rule, file, secret, reason); the config holds the secret's hash only."""
    rows = ", ".join(
        f'{{ rule = "{rule}", file = "{file}", secret_sha256 = "{sha256_of(secret)}", reason = "{reason}" }}'
        for rule, file, secret, reason in entries
    )
    return f"[gate]\nmandatory_jobs = [\"checks\"]\n[secrets]\nallow = [{rows}]\n"


def run_with_recorded_leak(build_repo, tmp_path, capsys, config: str | None) -> tuple[int, str]:
    files = {"quality/config.toml": config} if config is not None else {}
    engine_dir = fake_gitleaks(tmp_path / "fake", on_scan=f'cp {OUTPUTS / "gitleaks-finding.json"} "$report"; exit 7')
    code = cli.main(["hygiene", "--engines", str(engine_dir), str(build_repo(files))])
    return code, capsys.readouterr().out


@pytest.mark.parametrize(
    ("config", "code", "printed", "not_printed"),
    [
        (None, 1, "secret deploy/config.txt:3", "allowed"),
        (allow_config((RECORDED_RULE, RECORDED_FILE, "another value", "other value")), 1,
         "secret deploy/config.txt:3", "allowed"),
        (allow_config((RECORDED_RULE, "src/app.py", RECORDED_SECRET, "other file")), 1,
         "secret deploy/config.txt:3", "allowed"),
        (allow_config(("generic-api-key", RECORDED_FILE, RECORDED_SECRET, "other rule")), 1,
         "secret deploy/config.txt:3", "allowed"),
        (allow_config((RECORDED_RULE, RECORDED_FILE, RECORDED_SECRET, "test data")), 0,
         "secret: 1 allowed (deploy/config.txt:3)", "hygiene: 1 finding"),
    ],
    ids=[
        "no-config-is-red-not-exit-2",
        "another-value-at-the-same-place-is-red",
        "the-value-in-another-file-is-red",
        "the-value-under-another-rule-is-red",
        "allowed-value-is-green-and-printed",
    ],
)
def test_a_secret_finding_is_allowed_only_by_rule_file_and_value(
    build_repo, tmp_path, capsys, config, code, printed, not_printed
):
    """#24 rule 1: a finding is allowed only when rule, file and the hash of its value match one entry."""
    exit_code, out = run_with_recorded_leak(build_repo, tmp_path, capsys, config)
    assert exit_code == code, out
    assert printed in out
    assert not_printed not in out
    assert RECORDED_SECRET not in out
    if code == 0:
        assert RECORDED_RULE in out


def test_a_red_secret_finding_prints_the_hash_to_allow_and_not_the_value(build_repo, tmp_path, capsys):
    """#24 rule 4: the output names the hash a reviewer would put into the allowance, never the value."""
    _, out = run_with_recorded_leak(build_repo, tmp_path, capsys, None)
    assert f"sha256 {sha256_of(RECORDED_SECRET)}, value not shown" in out
    assert "allow this rule, file and sha256 with a reason in quality/config.toml" in out
    assert RECORDED_SECRET not in out


@pytest.mark.parametrize(
    "entry", ['""', "{ fingerprint = \"x\" }", '{ fingerprint = "x", reason = "reason" }', '"x"'],
    ids=["empty", "no-reason", "old-fingerprint-form", "bare-string"],
)
def test_an_invalid_allow_entry_is_exit_2(build_repo, tmp_path, capsys, entry):
    """#24 rule 2: an allow entry that is not rule, file, hash and reason cannot be judged."""
    config = f'[gate]\nmandatory_jobs = ["checks"]\n[secrets]\nallow = [{entry}]\n'
    engine_dir = fake_gitleaks(tmp_path / "fake")
    assert "[secrets] allow entry" in hygiene_exit_2(build_repo({"quality/config.toml": config}), engine_dir, capsys)


@pytest.mark.parametrize(
    "report",
    ['[{"File": "a", "StartLine": 1, "RuleID": "r", "Commit": "c"}]',
     '[{"File": "a", "StartLine": 1, "RuleID": "r", "Commit": "c", "Secret": 5}]'],
    ids=["no-secret", "secret-not-a-string"],
)
def test_a_report_entry_without_a_string_secret_is_exit_2(build_repo, tmp_path, capsys, report):
    """#24 rule 5: a finding whose value cannot be hashed is never clean."""
    engine_dir = fake_gitleaks(tmp_path / "fake", on_scan=f"echo '{report}' > \"$report\"; exit 7")
    hygiene_exit_2(build_repo({}), engine_dir, capsys)


def test_the_scan_passes_the_gates_own_config(build_repo, tmp_path):
    """#16: gitleaks is told to use quality's config, not the repository's."""
    engine_dir = fake_gitleaks(tmp_path / "fake", on_scan='echo "[]" > "$report"; exit 0')
    repo = build_repo({".gitleaks.toml": "[allowlist]\n"})
    engines.scan_secrets(repo, engine_dir, engines.read_pins(), {})
    given = (engine_dir / "gitleaks.arguments").read_text().split()
    assert given[given.index("--config") + 1] == str(engines.GITLEAKS_CONFIG)


@pytest.mark.engine
def test_a_repository_config_and_ignore_file_do_not_silence_the_scan(build_repo, installed_engines):
    """#16: a committed .gitleaks.toml, a .gitleaksignore holding the real fingerprint and gitleaks:allow
    have no effect."""
    repo = build_repo({"config.txt": f"aws_access_key_id = {FAKE_KEY}  # gitleaks:allow\n"})
    first_commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    (repo / ".gitleaks.toml").write_text('[extend]\nuseDefault = true\n[allowlist]\npaths = [\'\'\'config.txt\'\'\']\n')
    (repo / ".gitleaksignore").write_text(f"{first_commit}:config.txt:aws-access-token:1\n")
    commit(repo, "try to silence the scan")
    assert scan(repo, installed_engines) == 1


def allow_fake_key(repo: Path, file: str = "config.txt") -> None:
    (repo / "quality").mkdir(exist_ok=True)
    (repo / "quality" / "config.toml").write_text(allow_config(("aws-access-token", file, FAKE_KEY, "test key")))
    commit(repo, "allow the fixture key")


@pytest.mark.engine
def test_an_allowed_value_stays_green_at_a_shifted_line_and_across_commits(build_repo, installed_engines, capsys):
    """#24 rule 1: one entry covers the value at line 1, at a later line and in every commit that holds it."""
    repo = build_repo({"config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    (repo / "config.txt").write_text("removed\n")
    commit(repo, "remove the key")
    (repo / "config.txt").write_text(f"# one\n# two\n# three\naws_access_key_id = {FAKE_KEY}\n")
    commit(repo, "key again, three lines further down")
    allow_fake_key(repo)
    assert scan(repo, installed_engines) == 0
    summary = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("secret: 2 allowed ("))
    assert set(summary.removeprefix("secret: 2 allowed (").removesuffix(")").split(", ")) == {"config.txt:1", "config.txt:4"}


@pytest.mark.engine
@pytest.mark.parametrize(
    ("write", "placed"),
    [
        (lambda repo: (repo / "config.txt").write_text("aws_access_key_id = " + OTHER_KEY + "\n"), "config.txt:1"),
        (lambda repo: (repo / "src.txt").write_text(f"aws_access_key_id = {FAKE_KEY}\n"), "src.txt:1"),
    ],
    ids=["another-value-at-the-same-place", "the-value-in-another-file"],
)
def test_an_allowance_never_spreads_to_another_value_or_file(build_repo, installed_engines, capsys, write, placed):
    """#24 rule 1: the allowed value stays allowed only in its file; any other value or file is red."""
    repo = build_repo({"config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    allow_fake_key(repo)
    write(repo)
    commit(repo, "a second leak")
    assert scan(repo, installed_engines) == 1
    assert f"secret {placed}" in capsys.readouterr().out


@pytest.mark.engine
def test_a_repository_without_quality_config_is_scanned_and_its_key_is_red(build_repo, installed_engines, capsys):
    """#24 rule 3: no quality/config.toml means no allowances, and the scan still judges."""
    repo = build_repo({"config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    assert not (repo / "quality").exists()
    assert scan(repo, installed_engines) == 1
    assert "secret config.txt:1" in capsys.readouterr().out


@pytest.mark.engine
@pytest.mark.parametrize("failure", ["none", "gitleaks-fails", "parsing-raises"])
def test_the_key_reaches_no_output_and_no_file_and_the_scratch_directory_is_gone(
    keyed_repo, installed_engines, tmp_path, monkeypatch, capsys, failure
):
    """#24 rule 4: after a normal run, a gitleaks failure and a parse error nothing holds the key."""
    scratch_parent = tmp_path / "scratch-parent"
    scratch_parent.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch_parent))
    engine_dir = installed_engines
    if failure == "gitleaks-fails":
        engine_dir = fake_gitleaks(
            tmp_path / "failing", on_scan=f'printf \'[{{"Secret":"{FAKE_KEY}"}}]\' > "$report"; exit 1'
        )
    if failure == "parsing-raises":
        monkeypatch.setattr(engines, "secret_findings", lambda *_: (_ for _ in ()).throw(EngineError("parse error")))
    exit_code = scan(keyed_repo, engine_dir)
    assert exit_code == (1 if failure == "none" else 2)
    heard = capsys.readouterr()
    assert FAKE_KEY not in heard.out + heard.err
    assert list(scratch_parent.iterdir()) == []
