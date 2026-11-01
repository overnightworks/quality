import os
import shlex
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from quality import cli, process, scanners
from quality.rules import Rule
from quality.scanners import ScannerError
from tests.support.advisories import uv_lock
from tests.support.check import env_row_stands, heard, refusal_row, refusals
from tests.support.scanners import (
    NOT_INSTALLED,
    OUTPUTS,
    PINNED,
    Locator,
    commit,
    fake_gitleaks,
    fake_scanner,
    fake_zizmor,
    hygiene_exit_2,
    lacking,
    recording,
    scan,
    serving,
)
from tests.support.secrets import FAKE_KEY, UNKNOWN_KEY_CONFIGS, assert_key_found


def recorded(name: str) -> str:
    return (OUTPUTS / name).read_text(encoding="utf-8")



@pytest.mark.parametrize(
    ("version", "on_version", "expected"), [(PINNED, "exit 3", " gitleaks version exited 3"), (PINNED, "exec sleep 30", "did not finish within 0.2 seconds"),
     ("", "exit 0", f"gitleaks reports no version, the release pins {PINNED}")],
    ids=["fails-after-printing-the-pin", "does-not-finish", "prints-nothing"],
)
def test_a_version_command_that_fails_hangs_or_reports_no_version_is_exit_2(served_scanners, build_repo, monkeypatch, capsys, version, on_version, expected):
    """#13 rule 2, #134 rule 1: a scanner whose version command exits non-zero, hangs or reports no version is not trusted."""
    monkeypatch.setattr(process, "VERSION_TIMEOUT_SECONDS", 0.2)
    fake_gitleaks(served_scanners, version, on_version=on_version)
    assert expected in hygiene_exit_2(build_repo({}), capsys)


@pytest.mark.parametrize(
    "on_scan",
    [
        "echo boom >&2; exit 3",
        "exit 0",
        'echo "[]" > "$report"; echo boom >&2; exit 3',
        'printf "\\377\\376" > "$report"; exit 0',
    ],
    ids=["crash-without-report", "exit-0-without-report", "unexpected-exit-with-report", "report-not-utf8"],
)
def test_a_scanner_that_crashes_or_leaves_no_usable_report_is_exit_2(served_scanners, build_repo, capsys, on_scan):
    """#13 rule 3: a missing, undecodable or exit-code-inconsistent report is exit 2 for the secret rule alone, and
    gitleaks' stderr reaches no printed line."""
    fake_gitleaks(served_scanners, on_scan=on_scan)
    repo = build_repo({})
    printed = hygiene_exit_2(repo, capsys)
    assert set(refusals(repo)) == {Rule.LEAK}
    assert "boom" not in printed


@pytest.mark.parametrize(
    ("report", "exit_code", "expected"),
    [("gitleaks-clean.json", 0, []), ("gitleaks-finding.json", 7, ["secret deploy/config.txt:3"])],
)
def test_report_becomes_findings(report, exit_code, expected):
    """#13 rule 3: each leak in a recorded report becomes a secret finding at file:line."""
    findings = scanners.scanner_findings(scanners.GITLEAKS_SCANNER, recorded(report), exit_code, "")
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
    """#13 rule 3: a garbled, mis-shaped or inconsistent result is an ScannerError, never clean."""
    text = recorded(report) if report.endswith(".json") else report
    with pytest.raises(ScannerError):
        scanners.scanner_findings(scanners.GITLEAKS_SCANNER, text, exit_code, "")



@pytest.mark.scanner
@UNKNOWN_KEY_CONFIGS
def test_hygiene_refuses_a_config_with_unknown_keys(build_repo, installed_scanners, capsys, config):
    """#40 rule 2: hygiene keeps refusing unknown keys of quality/config.toml."""
    assert scan(build_repo({"quality/config.toml": config})) == 2
    assert "unknown key" in heard(capsys)



@pytest.mark.scanner
def test_clean_history_passes(build_repo, installed_scanners):
    """#13 rule 4: a repository without a secret passes."""
    assert scan(build_repo({"docs/notes.txt": "nothing here\n"})) == 0


@pytest.mark.scanner
def test_committed_secret_fails(build_repo, installed_scanners, capsys):
    """#13 rule 4: a committed key fails, reported at file:line with the secret redacted."""
    repo = build_repo({"config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    assert_key_found(repo, capsys)


@pytest.mark.scanner
def test_secret_removed_in_a_later_commit_still_fails(build_repo, installed_scanners, capsys):
    """#13 rules 4 and 5: a key removed in a later commit still fails because it lives in history."""
    repo = build_repo({"config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    (repo / "config.txt").write_text("aws_access_key_id = removed\n")
    commit(repo, "remove the key")
    assert_key_found(repo, capsys)


@pytest.mark.scanner
def test_secret_reachable_only_from_another_branch_is_not_this_verdicts(build_repo, installed_scanners):
    """#24 head ruling: the verdict judges the history reachable from HEAD, not every branch the checkout fetched."""
    repo = build_repo({"docs/notes.txt": "nothing here\n"})
    (repo / "config.txt").write_text(f"aws_access_key_id = {FAKE_KEY}\n")
    commit(repo, "commit the key")
    subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/origin/side", "HEAD"], check=True)
    subprocess.run(["git", "-C", str(repo), "reset", "-q", "--hard", "HEAD~1"], check=True)
    assert scan(repo) == 0


@pytest.mark.scanner
def test_a_relative_repository_path_starting_with_a_dash_is_scanned_as_that_repository(
    build_repo, installed_scanners, tmp_path, monkeypatch
):
    """#13 rule 4: a repository path beginning with '-' is a path, never an option to gitleaks."""
    repo = build_repo({"config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    subprocess.run(["git", "clone", "-q", str(repo), "--", str(workdir / "-repo")], check=True)
    monkeypatch.chdir(workdir)
    findings = scanners.scan_secrets(Path("-repo"), {}).findings
    assert [(finding.path, finding.line) for finding in findings] == [("config.txt", 1)]


@pytest.mark.scanner
@pytest.mark.parametrize("attribute_line", ["config.txt -diff", "config.txt binary", "* -diff -text"])
def test_a_committed_attribute_line_cannot_hide_a_key(build_repo, installed_scanners, attribute_line, capsys):
    """#21 rule 1: a .gitattributes in the repository that makes git print "Binary files differ" is still red."""
    repo = build_repo({".gitattributes": f"{attribute_line}\n", "config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    assert_key_found(repo, capsys)



@pytest.fixture
def hiding_attributes(tmp_path) -> Path:
    path = tmp_path / "hiding-attributes"
    path.write_text("config.txt -diff\n")
    return path


@pytest.mark.scanner
def test_a_global_attributes_file_cannot_hide_a_key(keyed_repo, installed_scanners, tmp_path, monkeypatch, capsys):
    """#21 rule 1: attributes from the user's global file are not read either."""
    config_home = tmp_path / "config-home"
    (config_home / "git").mkdir(parents=True)
    (config_home / "git" / "attributes").write_text("config.txt -diff\n")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    assert_key_found(keyed_repo, capsys)


@pytest.mark.scanner
def test_a_configured_core_attributes_file_cannot_hide_a_key(
    keyed_repo, installed_scanners, tmp_path, monkeypatch, hiding_attributes, capsys
):
    """#21 rule 1: a core.attributesFile in the user's own git config is not read."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(f"[core]\n\tattributesFile = {hiding_attributes}\n")
    monkeypatch.setenv("HOME", str(home))
    assert_key_found(keyed_repo, capsys)


@pytest.mark.scanner
def test_an_inherited_git_config_variable_cannot_bring_an_attributes_file_back(
    keyed_repo, installed_scanners, monkeypatch, hiding_attributes, capsys
):
    """#21 rule 1: every other inherited GIT_* variable is removed, GIT_CONFIG_COUNT included."""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.attributesFile")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(hiding_attributes))
    assert_key_found(keyed_repo, capsys)


@pytest.mark.scanner
def test_a_diff_driver_marked_binary_cannot_hide_a_key(build_repo, installed_scanners, tmp_path, monkeypatch, capsys):
    """#21 rule 1: a diff=<driver> attribute whose configured driver is binary does not hide the key."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text('[diff "hidden"]\n\tbinary = true\n')
    monkeypatch.setenv("HOME", str(home))
    repo = build_repo({".gitattributes": "config.txt diff=hidden\n", "config.txt": f"aws_access_key_id = {FAKE_KEY}\n"})
    assert_key_found(repo, capsys)


@pytest.mark.scanner
def test_a_core_attributes_file_in_the_repository_config_cannot_hide_a_key(
    keyed_repo, installed_scanners, hiding_attributes, capsys
):
    """#21 rule 1: a core.attributesFile in the scanned repository's own config does not reach the mirror."""
    subprocess.run(["git", "-C", str(keyed_repo), "config", "core.attributesFile", str(hiding_attributes)], check=True)
    assert_key_found(keyed_repo, capsys)


@pytest.mark.scanner
def test_the_repositorys_info_attributes_cannot_hide_a_key(keyed_repo, installed_scanners, capsys):
    """#21 rule 1: the scanned repository's .git/info/attributes is not read."""
    (keyed_repo / ".git" / "info").mkdir(exist_ok=True)
    (keyed_repo / ".git" / "info" / "attributes").write_text("config.txt -diff\n")
    assert_key_found(keyed_repo, capsys)


@pytest.mark.scanner
def test_a_template_directory_cannot_put_attributes_into_the_mirror(
    keyed_repo, installed_scanners, tmp_path, fake_git, capsys
):
    """#21 rule 1: attributes shipped by git's default template directory do not reach the mirror."""
    template = tmp_path / "template" / "info"
    template.mkdir(parents=True)
    (template / "attributes").write_text("* -diff\n")
    fake_git(f'export GIT_TEMPLATE_DIR="{template.parent}"')
    assert_key_found(keyed_repo, capsys)


@pytest.mark.scanner
@pytest.mark.parametrize(("command", "refusal"), [("hygiene", refusal_row("secrets")), ("scan-staged", "scan-staged: cannot judge - ")])
@pytest.mark.parametrize("git_version", ["2.40.1", "1.9.0"])
def test_a_git_older_than_2_41_is_exit_2_naming_the_version(
    installed_scanners, build_repo, fake_git, capsys, git_version, command, refusal
):
    """#21 rule 2, #25 rule 4: below the floor the scan cannot hide attributes, so it is exit 2 with one line.

    The git floor is checked right after gitleaks' version and before any scan, so the fake git alone decides this test.
    """
    fake_git(f'[ "$1" = "--version" ] && echo "git version {git_version}" && exit 0')
    assert cli.main([command, str(build_repo({}))]) == 2
    assert f"{refusal}git {git_version} cannot hide .gitattributes from the gitleaks scan, it needs git 2.41" in heard(capsys)


@pytest.mark.scanner
def test_installed_binary_reports_the_pinned_version(build_repo, installed_scanners):
    """#13 rule 2: the gitleaks mise installed passes the version check against the manifest."""
    assert scanners.scan_secrets(build_repo({}), {}).findings == []


@pytest.mark.scanner
def test_a_mise_config_above_the_manifests_parent_never_takes_part(installed_scanners, tmp_path, monkeypatch):
    """#265 D4: a checkout whose mise.toml and .miserc.toml do not parse; a found scanner proves mise reads neither."""
    checkout = tmp_path / "checkout"
    manifest_copy = checkout / "src" / "quality" / "mise"
    shutil.copytree(scanners.MANIFEST_DIRECTORY, manifest_copy)
    for name in ("mise.toml", ".miserc.toml"): (checkout / name).write_text("[tools\nthis does not parse")
    monkeypatch.chdir(checkout)
    monkeypatch.setattr(scanners, "MANIFEST_DIRECTORY", manifest_copy)
    assert scanners.located(scanners.GITLEAKS).is_file()


FINDING_EXIT = {"actionlint": 1, "zizmor": 12}


def workflow_findings(scanner: str, report_text: str, exit_code: int) -> list:
    return scanners.scanner_findings(next(judge for judge in scanners.WORKFLOW_SCANNERS.values() if judge.name == scanner), report_text, exit_code, "")


@pytest.mark.parametrize(
    ("scanner", "expected"),
    [
        ("actionlint", "workflow-lint .github/workflows/a.yml:4"),
        ("zizmor", "workflow-security .github/workflows/w.yml:7"),
    ],
)
def test_workflow_scanner_reports_become_findings(scanner, expected):
    """#15 rule 2: a recorded clean report is no finding; a recorded finding is one at file:line."""
    assert workflow_findings(scanner, recorded(f"{scanner}-clean.json"), 0) == []
    findings = workflow_findings(scanner, recorded(f"{scanner}-finding.json"), FINDING_EXIT[scanner])
    assert [f"{f.rule} {f.location}" for f in findings] == [expected]


@pytest.mark.parametrize("scanner", ["actionlint", "zizmor"])
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
def test_unreadable_or_inconsistent_workflow_result_cannot_be_judged(scanner, report, exit_code):
    """#15 rule 2: a garbled, cut-off, mis-shaped or inconsistent result is an ScannerError, never clean."""
    text = recorded(f"{scanner}-{report}.json") if report in ("garbled", "clean", "finding") else report
    code = FINDING_EXIT[scanner] if exit_code == "finding" else exit_code
    with pytest.raises(ScannerError):
        workflow_findings(scanner, text, code)


def fake_workflow_scanners(served: Path, elsewhere: Path, on_scan: str) -> Locator:
    """A fake actionlint (served, beside the clean gitleaks) and zizmor running `on_scan`; returns zizmor's locator."""
    fake_scanner(served, scanners.ACTIONLINT, on_scan)
    return fake_zizmor(elsewhere, on_scan)


@pytest.mark.parametrize(
    ("on_scan", "expected"),
    [
        ("exit 0", "cannot read the actionlint report"),
        ("echo boom >&2; exit 3", "actionlint exited 3, neither 'clean' nor 'findings': boom"),
        ('printf "[" ; exit 0', "cannot read the actionlint report"),
        ('printf "\\377\\376"; exit 0', "printed output that is not UTF-8"),
    ],
    ids=["exit-0-without-report", "error-exit", "cut-off-report", "undecodable-report"],
)
def test_a_workflow_scanner_without_a_usable_report_is_exit_2(served_scanners, tmp_path, build_repo, capsys, on_scan, expected):
    """#15 rule 2: a scanner that exits 0 with no report, or errors out, is exit 2 and never clean."""
    repo = build_repo({".github/workflows/w.yml": "on: push\n"})
    locate_zizmor = fake_workflow_scanners(served_scanners, tmp_path / "zizmor", on_scan)
    assert expected in hygiene_exit_2(repo, capsys, locate_zizmor)


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


@pytest.mark.scanner
@pytest.mark.parametrize(
    ("workflow", "expected"),
    [
        (UNPINNED_WITHOUT_PERMISSIONS, "workflow-security .github/workflows/w.yml:6"),
        (INVALID_RUNS_ON, "workflow-lint .github/workflows/w.yml:5"),
        (local_reusable_call("."), "workflow-security .github/workflows/w.yml:7"),
    ],
    ids=["unpinned-action-without-permissions", "invalid-runs-on-expression", "local-reusable-call-with-dot-slash"],
)
def test_a_known_bad_workflow_fails(workflow_repo, installed_scanners, capsys, workflow, expected):
    """#15 rules 3 and 4: the real scanners fail a known bad workflow at file:line."""
    assert scan(workflow_repo(workflow)) == 1
    assert expected in capsys.readouterr().out


@pytest.mark.scanner
def test_quality_own_workflows_pass_both_scanners(build_repo, installed_scanners):
    """#15 rules 3 and 4: quality's own workflows pass actionlint and zizmor unchanged."""
    own = Path(__file__).parent.parent / ".github" / "workflows"
    repo = build_repo({f".github/workflows/{file.name}": file.read_text() for file in own.glob("*.yml")})
    assert scan(repo) == 0


@pytest.mark.scanner
def test_a_local_reusable_call_in_the_dollar_slash_form_passes_both_scanners(workflow_repo, installed_scanners):
    """#15 rule 4: `uses: $/…` satisfies zizmor's self-repository audit and actionlint's false error is ignored."""
    assert scan(workflow_repo(local_reusable_call("$"))) == 0


ONLY_AN_ONLINE_AUDIT_FLAGS_THIS = (
    "on: push\npermissions: {}\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n"
    "      - uses: actions/checkout@0000000000000000000000000000000000000000\n"
    "        with:\n          persist-credentials: false\n"
)


@pytest.mark.scanner
def test_a_run_discloses_that_online_audits_do_not_run(workflow_repo, installed_scanners, capsys):
    """#15 rule 5: a workflow only zizmor's online audit (impostor-commit) would flag passes, and the run says so."""
    assert scan(workflow_repo(ONLY_AN_ONLINE_AUDIT_FLAGS_THIS)) == 0
    assert "online-only audits do not run" in capsys.readouterr().out


CALLER_SILENCES = {
    ".github/zizmor.yml": "rules:\n  unpinned-uses:\n    disable: true\n  excessive-permissions:\n    disable: true\n",
    ".github/actionlint.yaml": "paths:\n  '.github/workflows/w.yml':\n    ignore:\n      - '.*'\n",
}
INLINE_IGNORE = UNPINNED_WITHOUT_PERMISSIONS.replace(
    "actions/checkout@v4", "actions/checkout@v4 # zizmor: ignore[unpinned-uses,artipacked]"
)


@pytest.mark.scanner
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
    build_repo, installed_scanners, capsys, workflow, caller_files, expected
):
    """#15 material risk: a caller's scanner config or ignore comment cannot turn a failing workflow green."""
    repo = build_repo({".github/workflows/w.yml": workflow, **caller_files})
    assert scan(repo) == 1
    assert expected in capsys.readouterr().out


@pytest.mark.scanner
def test_a_shellcheck_on_path_never_runs_during_workflow_lint(build_repo, installed_scanners, tmp_path, monkeypatch):
    """#15 rule 1: actionlint judges without whichever shellcheck PATH offers."""
    planted = tmp_path / "planted"
    planted.mkdir()
    marker = tmp_path / "shellcheck-ran"
    (planted / "shellcheck").write_text(f"#!/bin/sh\ntouch {marker}\necho '[]'\n")
    (planted / "shellcheck").chmod(0o755)
    monkeypatch.setenv("PATH", f"{planted}:{os.environ['PATH']}")
    workflow = "on: push\npermissions: {}\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo $FOO\n"
    scan(build_repo({".github/workflows/w.yml": workflow}))
    assert not marker.exists()


# ---- mise finds the scanners (#228 rule 4) ----

WORKFLOW_AND_LOCK = {".github/workflows/w.yml": "on: push\n", "uv.lock": uv_lock()}
SCANNER_COMMANDS = [
    ("hygiene", scanners.GITLEAKS, Rule.LEAK),
    ("hygiene", scanners.ACTIONLINT, Rule.WORKFLOW_LINT),
    ("hygiene", scanners.OSV_SCANNER, Rule.ADVISORY),
    ("scan-staged", scanners.GITLEAKS, None),
]


@pytest.mark.parametrize(("command", "name", "rule"), SCANNER_COMMANDS)
def test_a_scanner_mise_has_not_installed_cannot_judge_and_names_the_install_command(
    build_repo, use_mise, served_scanners, elsewhere, capsys, command, name, rule
):
    """#228 rule 4: a missing scanner is exit 2 with the command that installs it, and no other rule is refused."""
    use_mise(lacking(served_scanners, name))
    repo, locate_zizmor = build_repo(WORKFLOW_AND_LOCK), fake_zizmor(elsewhere, "echo '[]'")
    code, captured = cli.main([command, str(repo)], locate_zizmor=locate_zizmor), capsys.readouterr()
    output, install = captured.out + captured.err, scanners.install_command()
    assert (code, captured.out.count(install), captured.err.count(install)) == (2, 0, 1), output
    assert f"mise could not locate {name} {scanners.pinned_tool(name).version}" in output
    if rule is None:
        assert output.count("cannot judge") == 1, output
    else:
        assert list(refusals(repo, locate_zizmor=locate_zizmor)) == [rule]


def test_without_mise_the_scanner_rules_cannot_judge_and_every_other_rule_still_judges(build_repo, path_without_mise, elsewhere, capsys):
    """#228 rule 4: no mise on PATH is exit 2 naming mise's install page and the command; the other rules still print."""
    repo = build_repo({**WORKFLOW_AND_LOCK, ".env": "A=1\n"})
    code = cli.main(["hygiene", str(repo)], locate_zizmor=fake_zizmor(elsewhere, "echo '[]'"))
    captured = capsys.readouterr()
    assert (code, captured.out.count("mise is not installed"), captured.out.count(scanners.install_command())) == (2, 3, 0), captured.err
    assert captured.err.splitlines()[1:] == ["next:", f"    install mise: {scanners.MISE_INSTALLATION_PAGE}", f"    {scanners.install_command()}"]
    assert set(refusals(repo, locate_zizmor=fake_zizmor(elsewhere, "echo '[]'"))) == {Rule.LEAK, Rule.WORKFLOW_LINT, Rule.ADVISORY}
    assert env_row_stands(captured.out)


@pytest.mark.parametrize("command", ["hygiene", "scan-staged"])
def test_a_scanner_that_reports_another_version_than_the_manifest_is_never_run(
    build_repo, served_scanners, tmp_path, capsys, command
):
    """#228 invariant 10: the exact pinned version runs; a binary at another version is exit 2 naming both versions."""
    marker = tmp_path / "scanned"
    fake_scanner(served_scanners, scanners.GITLEAKS, f"touch {marker}", version="9.9.9")
    code = cli.main([command, str(build_repo({}))])
    output = heard(capsys)
    assert (code, f"gitleaks 9.9.9 found, the release pins {PINNED}" in output) == (2, True), output
    assert not marker.exists()


@pytest.mark.parametrize("command", ["hygiene", "scan-staged"])
def test_a_gitleaks_on_path_never_runs(build_repo, use_mise, tmp_path, monkeypatch, capsys, command):
    """#228 invariant 10: when mise cannot locate gitleaks, one that PATH offers is never used in its place."""
    use_mise(NOT_INSTALLED)
    planted = tmp_path / "planted"
    planted.mkdir()
    marker = tmp_path / "ran"
    fake_scanner(planted, scanners.GITLEAKS, f"touch {marker}")
    monkeypatch.setenv("PATH", f"{planted}:{os.environ['PATH']}")
    assert cli.main([command, str(build_repo({}))]) == 2
    assert not marker.exists(), capsys.readouterr().err


def test_mise_is_asked_in_the_manifest_directory_under_its_ceiling(build_repo, use_mise, served_scanners, tmp_path, elsewhere, monkeypatch):
    """#265 D4: mise reads only the manifest directory under the real path of its parent (mise matches canonical paths)."""
    record = tmp_path / "asked"
    (elsewhere / "link").symlink_to(scanners.MANIFEST_DIRECTORY.parent)
    monkeypatch.setattr(scanners, "MANIFEST_DIRECTORY", elsewhere / "link" / scanners.MANIFEST_DIRECTORY.name)
    use_mise(recording(record, serving(served_scanners)))
    declared = '[[root.allow]]\nentry = "mise.toml"\nreason = "the scanned repository holds a mise config"\n'
    repo = build_repo({**WORKFLOW_AND_LOCK, "mise.toml": "[env]\nMISE_X = '1'\n", "quality/config.toml": declared})
    assert cli.main(["hygiene", str(repo)], locate_zizmor=fake_zizmor(elsewhere, "echo '[]'")) == 0
    directory, ceiling = scanners.MANIFEST_DIRECTORY, scanners.MANIFEST_DIRECTORY.resolve().parent
    assert sorted(record.read_text().splitlines()) == sorted(
        f"--cd {directory} which {name} --tool {tool.key}@{tool.version}|{ceiling}|{directory.resolve()}"
        for name, tool in ((name, scanners.pinned_tool(name)) for name in (scanners.GITLEAKS, scanners.ACTIONLINT, scanners.OSV_SCANNER))
    )


@pytest.mark.parametrize(("body", "reason"), [(f"echo {answer}", "mise which printed no gitleaks executable") for answer in ("''", "relative/gitleaks", "/nowhere/gitleaks")] + [("printf '\\377'", "mise printed output that is not UTF-8"), ('if [ "$1" = version ]; then printf \'\\377\'; else echo "$0"; fi', "printed output that is not UTF-8"), ('echo "mise ERROR error parsing config file" >&2; exit 1', f"mise could not locate gitleaks {PINNED}")], ids=["nothing", "relative-path", "no-file", "unreadable-mise-output", "unreadable-scanner-version", "config error"])
def test_a_mise_answer_that_is_no_usable_scanner_names_the_install_command(build_repo, use_mise, capsys, body, reason):
    """#228 rule 4, #265: external output that yields no pinned executable, a process error included, is exit 2 and the install command."""
    use_mise(body)
    exit_code, captured = cli.main(["hygiene", str(build_repo({}))]), capsys.readouterr()
    row = next(line for line in captured.out.splitlines() if line.startswith("  ✗ secrets"))
    stdout_and_stderr_counts = (captured.out.count(scanners.install_command()), captured.err.count(scanners.install_command()))
    assert (exit_code, reason in row, "config file" in row, *stdout_and_stderr_counts) == (2, True, False, 0, 1)


def test_a_repository_with_no_workflow_and_no_lock_needs_only_gitleaks(build_repo, path_without_mise, capsys):
    """#265 F1: nothing to scan needs no scanner, so without mise only the secret rule cannot judge."""
    repo = build_repo({})
    assert cli.main(["hygiene", str(repo)]) == 2
    assert list(refusals(repo)) == [Rule.LEAK], heard(capsys)


def test_the_manifest_pins_each_scanner_once():
    """#228 invariant 10: one version source, three scanners, each once."""
    with (scanners.MANIFEST_DIRECTORY / "mise.toml").open("rb") as handle:
        tools = tomllib.load(handle)["tools"]
    assert sorted(key.rsplit("/", 1)[-1] for key in tools) == [scanners.ACTIONLINT, scanners.GITLEAKS, scanners.OSV_SCANNER]


def test_the_install_command_names_the_manifest_directory_as_one_shell_word(tmp_path, monkeypatch):
    """#265 F4: a path with a space stays one word of the command a person copies."""
    monkeypatch.setattr(scanners, "MANIFEST_DIRECTORY", tmp_path / "with space")
    assert shlex.split(scanners.install_command()) == ["mise", "--cd", str(tmp_path / "with space"), "install", "--locked"]


def test_the_install_command_shows_a_path_no_shell_word_can_hold_escaped(tmp_path, monkeypatch):
    """#265 F4: a path with a control character is printed escaped on one line, never as a command to copy."""
    monkeypatch.setattr(scanners, "MANIFEST_DIRECTORY", tmp_path / "a\nb")
    command = scanners.install_command()
    assert "\n" not in command
    assert "a\\x0ab" in command
