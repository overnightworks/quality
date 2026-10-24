import datetime
import functools
import http.server
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from advisory_support import (
    ADVISORY_ID,
    OUTPUTS,
    SAFE,
    UV_LOCK_ALLOWANCE,
    advisory,
    package_of,
    reporting,
    uv_lock,
)
from conftest import allowance_config, osv_engines

from quality import advisories, engines
from quality.engines import EngineError

LODASH = "GHSA-npm1-aaaa-bbbb"
NPM_AFFECTED_LINE = f"osv-scanner {LODASH} (CVE-2020-2): npm lodash 4.17.15 is affected."
PNPM_FIX = "upgrade it, or its parent when it is transitive, or set a pnpm override in package.json"
LODASH_ADVISORY = advisory(LODASH, fixed=("4.17.21",), aliases=("CVE-2020-2",), package="lodash", ecosystem="npm")
FRONTEND_LOCK = "src/frontend/pnpm-lock.yaml"


def pnpm_lock(packages=(("lodash", "4.17.15"),), transitive=()) -> str:
    """A lockfileVersion 9.0 pnpm lock whose `packages` are development dependencies and whose `transitive` ones are only resolved."""
    direct = "".join(f"      {name}:\n        specifier: {version}\n        version: {version}\n" for name, version in packages)
    resolved = "".join(f"  {name}@{version}:\n    resolution: {{integrity: sha512-x}}\n\n" for name, version in (*packages, *transitive))
    snapshots = "".join(f"  {name}@{version}: {{}}\n\n" for name, version in (*packages, *transitive))
    return (
        "lockfileVersion: '9.0'\n\nimporters:\n\n  .:\n    devDependencies:\n" + direct
        + "\npackages:\n\n" + resolved + "snapshots:\n\n" + snapshots
    )


@pytest.mark.engine
def test_a_pnpm_lock_is_judged_with_its_development_packages_and_names_the_folder(
    build_repo, publish_database, installed_engines, run_gate
):
    """#145 C1, C6, I1: a nested pnpm-lock.yaml is red with the fixed version and its folder, the fix saying how a
    transitive package is fixed and printing no command that would not work."""
    publish_database([LODASH_ADVISORY], ecosystem="npm")
    code, output = run_gate(build_repo({FRONTEND_LOCK: pnpm_lock()}), installed_engines)
    assert code == 1, output
    assert f"advisory {FRONTEND_LOCK} - {NPM_AFFECTED_LINE}\n  Fix: upgrade lodash to >= 4.17.21 (in src/frontend/: {PNPM_FIX})" in output


@pytest.mark.engine
def test_two_vulnerable_versions_of_one_package_in_a_pnpm_lock_are_two_findings(
    build_repo, publish_database, installed_engines, run_gate
):
    """#145 I1: the version is part of a finding's identity, so one package at two versions is red twice."""
    publish_database([advisory(LODASH, fixed=("4.17.21",), package="lodash", ecosystem="npm")], ecosystem="npm")
    lock = pnpm_lock(transitive=[("lodash", "4.17.19")])
    code, output = run_gate(build_repo({FRONTEND_LOCK: lock}), installed_engines)
    assert code == 1, output
    assert "hygiene: 2 finding(s)" in output
    assert "npm lodash 4.17.15 is affected." in output
    assert "npm lodash 4.17.19 is affected." in output


@pytest.mark.engine
def test_several_fixed_versions_keep_the_guidance_of_the_pnpm_lock(build_repo, publish_database, installed_engines, run_gate):
    """#145 C6: a transitive npm package is fixed by its parent or an override, whether osv lists one fixed version or several."""
    publish_database([advisory(LODASH, fixed=("4.17.21", "5.0.0"), package="lodash", ecosystem="npm")], ecosystem="npm")
    code, output = run_gate(build_repo({FRONTEND_LOCK: pnpm_lock()}), installed_engines)
    assert code == 1, output
    assert "to a fixed version: " in output
    assert PNPM_FIX in output


@pytest.mark.engine
def test_a_requirements_file_is_not_judged_and_downloads_no_database(
    build_repo, publish_database, served_databases, installed_engines, run_gate
):
    """#145 C2 (moved to #176): requirements*.txt is no lock, so a vulnerable pin in one is neither a finding nor a download."""
    publish_database()
    code, output = run_gate(build_repo({"docs/requirements.txt": "pyyaml==5.3\n"}), installed_engines)
    assert (code, served_databases.requested) == (0, []), output


@pytest.mark.engine
@pytest.mark.parametrize(
    "files",
    [
        {"package.json": '{"pnpm": {"auditConfig": {"ignoreCves": ["CVE-2020-2"], "ignoreGhsas": ["GHSA-npm1-aaaa-bbbb"]}}}'},
        {"src/.npmrc": "audit-level=critical\n", "src/frontend/.npmrc": "audit=false\n"},
        {"osv-scanner.toml": f'[[IgnoredVulns]]\nid = "{LODASH}"\n', "src/frontend/osv-scanner.toml": '[[PackageOverrides]]\nname = "lodash"\necosystem = "npm"\nignore = true\n'},
    ],
    ids=["pnpm.auditConfig", ".npmrc", "osv-scanner.toml"],
)
def test_nothing_a_repository_writes_silences_an_npm_advisory(build_repo, publish_database, installed_engines, run_gate, files):
    """#145 R2: only a real fix in the lock counts, never the repository's own ignore list."""
    publish_database([LODASH_ADVISORY], ecosystem="npm")
    code, output = run_gate(build_repo({FRONTEND_LOCK: pnpm_lock(), **files}), installed_engines)
    assert code == 1, output
    assert f"advisory {FRONTEND_LOCK} - " in output


@pytest.mark.engine
def test_a_big_frontend_shows_every_finding_without_a_cap(build_repo, publish_database, installed_engines, run_gate):
    """#145 X3."""
    names = [f"package-{number}" for number in range(25)]
    records = [advisory(f"GHSA-npm{number:02d}-aaaa-bbbb", package=name, ecosystem="npm") for number, name in enumerate(names)]
    publish_database(records, ecosystem="npm")
    code, output = run_gate(build_repo({FRONTEND_LOCK: pnpm_lock([(name, "1.0.0") for name in names])}), installed_engines)
    assert code == 1, output
    assert "hygiene: 25 finding(s)" in output


@pytest.mark.engine
def test_a_package_listed_as_malicious_is_red_and_the_fix_is_to_remove_or_replace_it(
    build_repo, publish_database, installed_engines, run_gate
):
    """#145 I3."""
    malicious = advisory("MAL-2026-1", fixed=(), aliases=(), package="evil-package", ecosystem="npm")
    publish_database([malicious], ecosystem="npm")
    code, output = run_gate(build_repo({FRONTEND_LOCK: pnpm_lock([("evil-package", "1.0.0")])}), installed_engines)
    assert code == 1, output
    assert "MAL-2026-1: npm evil-package 1.0.0 is affected." in output
    assert "osv lists no fixed version: remove or replace evil-package" in output


@pytest.fixture
def served_databases(publish_database, monkeypatch):
    """Serves the published databases over HTTP and points the gate at them; each request is recorded as it arrives and
    `.on_request` is called with each path before it is answered.
    The server is stopped at teardown."""
    served = SimpleNamespace(requested=[], on_request=lambda path: None)
    root = Path(urllib.request.url2pathname(urllib.parse.urlparse(advisories.DATABASE_ROOT).path))

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            served.requested.append(self.path)
            served.on_request(self.path)
            try:
                super().do_GET()
            except OSError:
                pass  # the downloader went away

        def log_message(self, *arguments):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(root)))
    server.daemon_threads = True
    serving = threading.Thread(target=server.serve_forever)
    serving.start()
    monkeypatch.setattr(advisories, "DATABASE_ROOT", f"http://127.0.0.1:{server.server_address[1]}")
    yield served
    server.shutdown()
    serving.join()
    server.server_close()


@pytest.mark.engine
def test_a_repository_with_both_kinds_downloads_each_database_once_and_judges_both(
    build_repo, publish_database, served_databases, installed_engines, run_gate
):
    """#145 X1: two pnpm locks and a uv.lock cost one npm and one PyPI download."""
    publish_database([LODASH_ADVISORY], ecosystem="npm")
    publish_database()
    repo = build_repo({FRONTEND_LOCK: pnpm_lock(), "src/admin/pnpm-lock.yaml": pnpm_lock(), "uv.lock": uv_lock()})
    code, output = run_gate(repo, installed_engines)
    assert code == 1, output
    assert sorted(served_databases.requested) == ["/PyPI/all.zip", "/npm/all.zip"]
    assert (FRONTEND_LOCK in output, "src/admin/pnpm-lock.yaml" in output, "advisory uv.lock - " in output) == (True, True, True)


def test_one_deadline_spans_every_database_download(build_repo, clean_engines, publish_database, served_databases):
    """#145 S4: the first download (PyPI) spends the whole budget, so the second (npm) is not even requested and the run
    is `cannot judge`, not a hung job. The clock is injected and moves by the PyPI request alone."""
    publish_database([LODASH_ADVISORY], ecosystem="npm")
    publish_database()
    now = 0.0

    def spend_budget_on_the_first_download(path: str) -> None:
        nonlocal now
        now = advisories.DATABASE_DOWNLOAD_DEADLINE_SECONDS

    served_databases.on_request = spend_budget_on_the_first_download
    repo = build_repo({FRONTEND_LOCK: pnpm_lock(), "uv.lock": uv_lock()})
    locks = {name: repo / name for name in (FRONTEND_LOCK, "uv.lock")}
    engine_dir = osv_engines(clean_engines, "exit 99")
    pins = engines.read_pins()
    with pytest.raises(EngineError, match="the npm advisory database download exceeded 300 seconds"):
        advisories.scan(engine_dir, pins, locks, repo, [], datetime.date(2026, 10, 21), lambda: now)
    assert served_databases.requested == ["/PyPI/all.zip"]


def test_an_npm_database_that_cannot_be_downloaded_is_cannot_judge_and_named(build_repo, clean_engines, run_gate, publish_database):
    """#145 S3: the npm database missing makes the whole rule `cannot judge`, though the PyPI one is there."""
    publish_database()
    repo = build_repo({FRONTEND_LOCK: pnpm_lock(), "uv.lock": uv_lock()})
    code, output = run_gate(repo, osv_engines(clean_engines, "exit 99"))
    assert (code, "cannot download the npm advisory database" in output) == (2, True), output


def test_without_a_pnpm_lock_the_npm_database_is_not_downloaded(build_repo, clean_engines, run_gate, publish_database):
    """#145 S1: a repository with only a uv.lock is judged although no npm database is published."""
    publish_database()
    engine_dir = osv_engines(clean_engines, reporting(OUTPUTS / "osv-scanner-clean.json", 0))
    code, output = run_gate(build_repo({"uv.lock": uv_lock(SAFE)}), engine_dir)
    assert code == 0, output


def npm_report(package: str, affected_name: str):
    """An edit of the finding fixture into an npm finding for `package` whose osv record fixes `affected_name`."""
    def edit(report: dict) -> None:
        found = package_of(report)
        found["package"].update(name=package, ecosystem="npm", version="4.6.0")
        found["vulnerabilities"][0]["affected"][0]["package"] = {"name": affected_name, "ecosystem": "npm"}
    return edit


@pytest.mark.parametrize(
    ("affected_name", "expected"),
    [("lodash.merge", "Fix: upgrade lodash.merge to >= 5.4"), ("lodash-merge", f"Fix: see https://osv.dev/vulnerability/{ADVISORY_ID}")],
    ids=["the same name", "Python's name rule is not npm's"],
)
def test_npm_names_match_exactly_as_written(
    build_repo, clean_engines, run_gate, publish_database, edited_report, affected_name, expected
):
    """#145 I2: `lodash.merge` is not `lodash-merge`."""
    publish_database(ecosystem="npm")
    report = edited_report("osv-scanner-finding.json", npm_report("lodash.merge", affected_name))
    code, output = run_gate(build_repo({FRONTEND_LOCK: pnpm_lock()}), osv_engines(clean_engines, reporting(report, 1)))
    assert code == 1, output
    assert expected in output


PNPM_ALLOWANCE = UV_LOCK_ALLOWANCE | {"lock": FRONTEND_LOCK}


def test_an_allowance_in_a_pnpm_lock_allows_its_advisory_until_its_date(
    build_repo, clean_engines, run_gate, publish_database, edited_report
):
    """#145 R1: the same [[advisories.allow]] entry, with the lock's path, allows an npm advisory; once the lock is
    fixed the entry matches nothing and is red until it is deleted (U1)."""
    publish_database(ecosystem="npm")
    config = allowance_config(**PNPM_ALLOWANCE, until=datetime.date(2999, 12, 31))
    repo = build_repo({FRONTEND_LOCK: pnpm_lock(), "quality/config.toml": config})
    report = edited_report("osv-scanner-finding.json", npm_report("lodash", "lodash"))
    code, output = run_gate(repo, osv_engines(clean_engines, reporting(report, 1)))
    assert code == 0, output
    assert f"  allowed {FRONTEND_LOCK} {ADVISORY_ID} (CVE-2020-1): npm lodash 4.6.0 until 2999-12-31 (#116) - fixture" in output
    fixed = osv_engines(clean_engines, reporting(OUTPUTS / "osv-scanner-clean.json", 0))
    code, output = run_gate(repo, fixed)
    assert (code, f"advisory quality/config.toml - the allowance for {ADVISORY_ID} in {FRONTEND_LOCK} matches no advisory." in output) == (1, True), output


SETUP_AND_OTHER_RULES_MINUTES = 10


def test_the_hygiene_job_outlasts_the_downloads_and_the_scan_so_cannot_judge_is_what_the_run_shows():
    """#145 S4: GitHub cancelling the job would show nothing; quality's own limits must expire first."""
    gate = yaml.safe_load((Path(__file__).parent.parent / ".github/workflows/gate.yml").read_text(encoding="utf-8"))
    limits_minutes = (advisories.DATABASE_DOWNLOAD_DEADLINE_SECONDS + advisories.ADVISORY_SCAN_TIMEOUT_SECONDS) / 60
    assert gate["jobs"]["hygiene"]["timeout-minutes"] >= limits_minutes + SETUP_AND_OTHER_RULES_MINUTES
