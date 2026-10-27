import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from quality import cli, hygiene, sonar
from quality.report import Finding
from tests.support.engines import fake_engine
from tests.support.git_repositories import make_repo
from tests.support.hygiene import CLEAN_LINE, ENGINE_RULES, FILE_RULES
from tests.support.hygiene import hygiene_output as run


def compose(*port_lines: str) -> dict[str, str]:
    body = "".join(f"      {line}\n" for line in port_lines)
    return {"compose.yaml": f"services:\n  web:\n    image: x\n    ports:\n{body}"}


def deleted_compose_repo(build_repo, tmp_path):
    repo = build_repo(compose('- "8080:80"'))
    (repo / "compose.yaml").unlink()
    return repo


def truncated_compose_repo(build_repo, tmp_path):
    return build_repo({"compose.yaml": "services: [\n"})


def non_utf8_filename_repo(build_repo, tmp_path):
    return build_repo({"docs/\udcff.txt": "x\n"})


@pytest.mark.parametrize(
    ("arrange", "expected_code", "expected_text"),
    [
        (lambda build_repo, tmp_path: build_repo({}), 0, CLEAN_LINE),
        (non_utf8_filename_repo, 0, CLEAN_LINE),
        (lambda build_repo, tmp_path: tmp_path, 2, "cannot judge"),
        (lambda build_repo, tmp_path: build_repo({".env": "A=1\n"}), 1, "finding(s)"),
        (lambda build_repo, tmp_path: build_repo({"\udcff.env": "A=1\n"}), 1, "env-file \\udcff.env - "),
        (deleted_compose_repo, 0, CLEAN_LINE),
        (truncated_compose_repo, 2, "cannot judge"),
    ],
    ids=[
        "empty-repository",
        "non-utf8-filename",
        "not-a-git-repository",
        "tracked-env-file",
        "cell 9 and #86 cell 7 non-utf8-env-filename-rendered-safely",
        "#76 cell 5 compose deleted from disk is gone",
        "malformed-compose-yaml",
    ],
)
def test_exit_code_tells_clean_from_cannot_judge(build_repo, tmp_path, capsys, arrange, expected_code, expected_text):
    """#3 rule 1: exit 0 clean, 1 findings, 2 cannot judge."""
    code, output = run(capsys, arrange(build_repo, tmp_path))
    assert code == expected_code
    assert expected_text in output


def test_compose_specific_tags_are_judged_not_refused(build_repo, capsys):
    """#3 rule 1/4: valid Compose YAML tags are read, and their ports still checked."""
    body = 'services:\n  web:\n    image: x\n    ports: !override\n      - "127.0.0.1:80:80"\n    profiles: !reset []\n'
    code, output = run(capsys, build_repo({"compose.yaml": body}))
    assert (code, CLEAN_LINE in output) == (0, True)


@pytest.mark.parametrize("tagged", ["!reset null", "!reset []"], ids=["null", "empty-list"])
def test_reset_clears_ports_and_is_clean(build_repo, capsys, tagged):
    """#3 rule 1/4: `ports: !reset null` is valid Compose that clears the ports, so it is clean."""
    code, _ = run(capsys, build_repo({"compose.yaml": f"services:\n  web:\n    image: x\n    ports: {tagged}\n"}))
    assert code == 0


def test_unknown_yaml_tag_cannot_be_judged(build_repo, capsys):
    """#3 rule 1: a tag Compose does not define is not read as a plain value."""
    code, output = run(capsys, build_repo({"compose.yaml": "services:\n  web:\n    image: !foo x\n"}))
    assert code == 2
    assert "cannot judge" in output


def assert_refusal(output: str, rule: str, path: str) -> None:
    assert re.search(rf"^{rule} {re.escape(path)} - \S.*\n  Fix: \S", output, re.MULTILINE), output


@pytest.mark.parametrize(
    ("name", "refused"),
    [
        (".env", True),
        ("config/prod.env", True),
        (".env.local", True),
        ("docs/.env.example", False),
        ("docs/docker/.env.docker.example", False),
        ("docs/environment.txt", False),
    ],
)
def test_tracked_env_files(build_repo, capsys, name, refused):
    """#3 rule 2/3: a tracked env file is refused with rule id, path, reason and fix; templates pass."""
    code, output = run(capsys, build_repo({name: "A=1\n"}))
    assert code == (1 if refused else 0)
    if refused:
        assert_refusal(output, "env-file", name)


def test_untracked_ignored_env_file_is_not_a_finding(build_repo, capsys):
    """#3 rule 2/3: an untracked, ignored env file is not a finding."""
    repo = build_repo({".gitignore": ".env\n"})
    (repo / ".env").write_text("A=1\n")
    assert run(capsys, repo)[0] == 0


@pytest.mark.parametrize(
    ("files", "refused_path"),
    [
        (compose('- "8080:80"'), "compose.yaml"),
        (compose('- "80"'), "compose.yaml"),
        (compose("- 80"), "compose.yaml"),
        (compose('- "0.0.0.0:8080:80"'), "compose.yaml"),
        ({"compose.yaml": 'services:\n  a:\n    ports: !override\n      - "0.0.0.0:8080:80"\n'}, "compose.yaml"),
        (compose("- target: 80", "  published: 8080"), "compose.yaml"),
        (compose("- target: 80", "  host_ip: 0.0.0.0"), "compose.yaml"),
        ({"compose.yaml": "services:\n  a:\n    ports: 8080\n"}, "compose.yaml"),
        (
            {"deploy/docker-compose.prod.yml": "services:\n  a:\n    ports: ['80:80']\n"},
            "deploy/docker-compose.prod.yml",
        ),
        (compose('- "127.0.0.1:8080:80"'), None),
        (compose("- target: 80", "  host_ip: 127.0.0.1"), None),
        (compose('- "[::1]:8080:80"'), None),
        (compose("- target: 80", "  host_ip: '::1'"), None),
        (compose('- "[::]:8080:80"'), "compose.yaml"),
        (compose('- "127.0.0.1:${WEB_PORT:-8080}:8080"'), None),
        ({"README.md": "no compose here\n"}, None),
    ],
)
def test_compose_ports_must_bind_loopback(build_repo, capsys, files, refused_path):
    """#3 rule 2/4: a compose port beyond loopback is refused with rule id, path, reason and fix; edge's real file is judged in test_hygiene_container_host (#78 cell 15)."""
    code, output = run(capsys, build_repo(files))
    assert code == (0 if refused_path is None else 1)
    if refused_path:
        assert_refusal(output, "compose-port", refused_path)


def test_quoted_tagged_port_is_read_as_the_string_it_wraps(build_repo, capsys):
    """#3 rule 4: `!override "22:22"` is the string 22:22, not a sexagesimal number."""
    code, output = run(capsys, build_repo(compose('- !override "22:22"')))
    assert code == 1
    assert "port 22:22 " in output


@pytest.mark.parametrize(
    ("port_line", "described"),
    [
        ("- 22:22", "22:22"),
        ("- 2222:22", "2222:22"),
        ("- !override 22:22", "22:22"),
        ("- 22", "22"),
        ("- 22:22.5", "22:22.5"),
        ('- "8080:80"', "8080:80"),
    ],
    ids=["cell 1", "cell 2 plain", "cell 2 override", "cell 3 bare", "rule 2 base-60 float", "cell 3 quoted"],
)
def test_a_port_is_described_as_compose_reads_it(build_repo, capsys, port_line, described):
    """#78 rule 2, cells 1-3: `22:22` is text, not the base-60 number 1342."""
    code, output = run(capsys, build_repo(compose(port_line)))
    assert (code, f"port {described} beyond loopback" in output) == (1, True), output


def test_a_loopback_port_with_an_ip_is_clean(build_repo, capsys):
    """#78 cell 3: `127.0.0.1:22:22` stays clean."""
    assert run(capsys, build_repo(compose("- 127.0.0.1:22:22")))[0] == 0


OPEN_PORT = compose('- "0.0.0.0:1:1"')["compose.yaml"]


@pytest.mark.parametrize(
    "name",
    [
        "keycloak/bootstrap.compose.yaml",
        "docs/oidc/suite-compose.yaml",
        "probe/compose.probe.yaml",
        "docker-compose.ci.yml",
        "compose.override.yml",
        "compose_x.yml",
        "Compose.YAML",
    ],
)
def test_a_compose_name_is_judged(build_repo, capsys, name):
    """#78 rule 1, cell 4: the word `compose` between `.`, `-` or `_` makes a file a compose file."""
    code, output = run(capsys, build_repo({name: OPEN_PORT}))
    assert code == 1
    assert_refusal(output, "compose-port", name)


@pytest.mark.parametrize(
    "name",
    ["composer.yml", "composed.yaml", "decompose.yml", "compose2.yaml", "compose.yaml.example", "compose.json",
     "edge_stack.yml"],
)
def test_a_name_without_the_compose_word_is_not_judged(build_repo, capsys, name):
    """#78 rule 1, cell 5: a near miss is not a compose file."""
    assert run(capsys, build_repo({f"docs/{name}": OPEN_PORT}))[0] == 0


UNREADABLE_YAML = [
    ("services: [\n", "line 2, column 1: "),
    ("a: b\n c: d\n", "line 2, column 3: mapping values are not allowed here"),
    ("services: *nowhere\n", "found undefined alias"),
    ("a: 1\n---\nb: 2\n", "but found another document"),
    ("a: \x07\n", "position 3: special characters are not allowed"),
    ("services:\n  web:\n    image: !foo x\n", "could not determine a constructor for the tag '!foo'"),
    ("a: !!int xyz\n", "a value does not fit its tag"),
    ("a: !!float xyz\n", "a value does not fit its tag"),
    ("a: !!timestamp 2001-13-45\n", "a value does not fit its tag"),
    ("a: !!bool xyz\n", "a value does not fit its tag"),
    ("a: !!int\n", "a value does not fit its tag"),
    ("a: 2001-99-99\n", "a value does not fit its tag"),
    ("[" * 3000 + "]" * 3000, "nested too deeply"),
]


@pytest.mark.parametrize(
    ("text", "reason"),
    UNREADABLE_YAML,
    ids=["cell 6 open bracket", "cell 7 bad indent", "cell 8 alias", "cell 8 two documents", "cell 9 control character",
         "cell 10 unknown tag", "rule 3 bad int", "rule 3 bad float",
         "rule 3 bad timestamp tag", "rule 3 bad bool", "rule 3 empty int", "rule 3 bad implicit timestamp",
         "cell 11 deep nesting"],
)
def test_a_yaml_error_is_one_short_line(build_repo, capsys, text, reason):
    """#78 rule 3, cells 6-11 and 18: exit 2, one line, no caret, source line, stream name or traceback."""
    repo = build_repo({"compose.yaml": text})
    code, output = run(capsys, repo)
    line = next(line for line in output.splitlines() if "cannot read compose.yaml" in line)
    assert (code, reason in line, len(line) < 200) == (2, True, True), output
    assert not any(leaked in output for leaked in ("<unicode string>", "^", "Traceback", "in \"", "services: ["))
    assert str(repo) not in output


ALIAS_BOMB = "a0: &a0 [x, x, x]\n" + "".join(f"a{n}: &a{n} [*a{n - 1}, *a{n - 1}, *a{n - 1}]\n" for n in range(1, 6))


@pytest.mark.parametrize(
    "service",
    ["    ports: [*a5]\n", "    ports:\n      - target: 80\n        published: *a5\n"],
    ids=["short syntax", "long syntax"],
)
def test_a_port_that_is_a_container_is_one_short_finding(build_repo, capsys, service):
    """#78 rule 4, cell 12: an aliased container is never printed."""
    repo = build_repo({"compose.yaml": f"x-bomb:\n  {ALIAS_BOMB.replace(chr(10), chr(10) + '  ')}\nservices:\n  web:\n{service}"})
    code, output = run(capsys, repo)
    assert (code, "has a port that is not a string, a number or a mapping whose published (or, without one, target) is text or a number (unreadable)" in output, len(output) < 800) == (1, True, True), output


def test_an_untracked_compose_file_is_not_judged_until_it_is_added(build_repo, capsys):
    """#78 cells 13-14: only what git tracks counts, and a staged file is tracked."""
    repo = build_repo({"README.md": "x\n"})
    (repo / "compose.yaml").write_text(OPEN_PORT)
    untracked = run(capsys, repo)[0]
    subprocess.run(["git", "-C", str(repo), "add", "compose.yaml"], check=True)
    assert (untracked, run(capsys, repo)[0]) == (0, 1)


def test_webauth_suite_compose_is_clean(build_repo, capsys):
    """#78 cell 16: a short-syntax `127.0.0.1::8080` port (empty host port) binds loopback."""
    files = {"docs/oidc/suite-compose.yaml": compose('- "127.0.0.1::8080"')["compose.yaml"]}
    assert run(capsys, build_repo(files))[0] == 0


@pytest.mark.integration
def test_quality_tracks_no_env_or_compose_file():
    """#3 rule 5: the quality repository passes its own hygiene check and tracks no env or compose file."""
    root = Path(__file__).resolve().parents[1]
    tracked = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True, text=True, check=True).stdout
    assert cli.main(["hygiene", str(root)]) == 0
    assert "compose" not in tracked
    assert ".env" not in tracked


def not_a_directory(tmp_path):
    target = tmp_path / "a-file"
    target.write_text("x")
    return target


@pytest.mark.integration
@pytest.mark.parametrize(
    "bad_path",
    [not_a_directory, lambda tmp_path: tmp_path / "missing", lambda tmp_path: tmp_path / "."],
    ids=["regular-file", "missing-path", "directory-without-git-repository"],
)
def test_a_path_that_is_no_repository_is_exit_2(tmp_path, capsys, bad_path):
    """#31 rule 1 (S8705): a path that is not a readable git repository is exit 2, never clean."""
    code, output = run(capsys, bad_path(tmp_path))
    assert code == 2
    assert "cannot judge" in output


def test_a_tracked_compose_symlink_leaving_the_repository_is_exit_2(build_repo, elsewhere, capsys):
    """#31 rule 1 (S8705) and #52 rule 5, cell 19: a tracked compose file that resolves outside the repository is
    exit 2, never read, and the refusal names no absolute path."""
    repo = build_repo({})
    outside = elsewhere / "compose.yml"
    outside.write_text("services:\n  web:\n    image: x\n    ports:\n      - '8080:80'\n")
    (repo / "compose.yaml").symlink_to(outside)
    subprocess.run(["git", "-C", str(repo), "add", "compose.yaml"], check=True)
    code, output = run(capsys, repo)
    assert code == 2
    assert "outside the repository" in output
    assert (str(repo) in output, str(outside.parent) in output) == (False, False)


def link_to_nowhere_inside(repo: Path, elsewhere: Path) -> None:
    (repo / "compose.yaml").symlink_to("missing.yaml")


def link_to_nowhere_outside(repo: Path, elsewhere: Path) -> None:
    (repo / "compose.yaml").symlink_to(elsewhere / "missing.yaml")


def link_to_itself(repo: Path, elsewhere: Path) -> None:
    (repo / "compose.yaml").symlink_to("compose.yaml")


@pytest.mark.parametrize(
    "link",
    [link_to_nowhere_inside, link_to_nowhere_outside, link_to_itself],
    ids=["#76 cell 6 inside", "#76 cell 6 outside", "#76 cell 7 loop (flips #52 cell 19)"],
)
def test_a_tracked_compose_linked_to_nowhere_is_gone_not_unjudgeable(build_repo, elsewhere, capsys, link):
    """#76 rule 3: a tracked compose file that leads to nothing is skipped like a missing .py, never a traceback,
    and nothing prints the checkout."""
    repo = build_repo({"compose.yaml": "services: [\n"})
    (repo / "compose.yaml").unlink()
    link(repo, elsewhere)
    code, output = run(capsys, repo)
    assert (code, CLEAN_LINE in output, str(repo) in output) == (0, True, False), output


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 file, so no permission is refused")
def test_a_tracked_compose_the_filesystem_refuses_to_read_is_exit_2(build_repo, capsys):
    """#76 rule 3, cell 9 (E10): a compose file without read permission is never gone."""
    repo = build_repo(compose('- "127.0.0.1:8080:80"'))
    (repo / "compose.yaml").chmod(0)
    code, output = run(capsys, repo)
    assert (code, "cannot read compose.yaml" in output, str(repo) in output) == (2, True, False), output


def test_a_tracked_workflow_missing_from_disk_is_never_handed_to_an_engine(build_repo, clean_engines, capsys):
    """#76 rule 3, cell 10: no workflow engine is installed, so any name passed on would be cannot judge."""
    repo = build_repo({".github/workflows/w.yml": "on: push\n"})
    (repo / ".github/workflows/w.yml").unlink()
    code = cli.main(["hygiene", "--engines", str(clean_engines), str(repo)])
    captured = capsys.readouterr()
    assert (code, "hygiene: clean (" in captured.out) == (0, True), captured.err


def test_a_tracked_env_file_deleted_from_disk_is_still_a_finding(build_repo, capsys):
    """#76 rule 5, cell 14 (E5): the rule judges the index's names, so only `git rm --cached` clears it."""
    repo = build_repo({".env": "A=1\n"})
    (repo / ".env").unlink()
    code, output = run(capsys, repo)
    assert code == 1
    assert_refusal(output, "env-file", ".env")


BASELINE = {"quality/baseline.toml": ""}
NEW_DEBT = "def long_one():\n" + "    x = 1\n" * 61
ENV_FINDING = "env-file .env - "


def config_linked_out_of_the_repository(build_repo, elsewhere):
    repo = build_repo({".env": "A=1\n"})
    (elsewhere / "config.toml").write_text('[gate]\nmandatory_jobs = ["checks"]\n')
    (repo / "quality").mkdir()
    (repo / "quality/config.toml").symlink_to(elsewhere / "config.toml")
    subprocess.run(["git", "-C", str(repo), "add", "quality/config.toml"], check=True)
    return repo


def files(tracked: dict[str, str]):
    return lambda build_repo, elsewhere: build_repo(tracked)


@pytest.mark.parametrize(
    ("arrange", "with_engines", "expected_code", "expected_lines"),
    [
        (files({**BASELINE, ".env": "A=1\n", "src/bad.py": "def broken(:\n"}), False, 2,
         [ENV_FINDING, "hygiene: size unparsable - src/bad.py is not valid Python",
          "hygiene: 2 finding(s); verdicts: 10 clean, 2 findings, 1 unparsable"]),
        (config_linked_out_of_the_repository, True, 2,
         [ENV_FINDING, "hygiene: compose-port cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: secret cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: advisory cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: root-layout cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: file-suffix cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: script-name cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: script-header cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: test-layout cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: test-import cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: python-floor cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: 1 finding(s); verdicts: 6 clean, 1 findings, 10 cannot judge"]),
        (files({**BASELINE, "compose.yaml": "services: [\n", "src/a.py": NEW_DEBT}), False, 2,
         ["hygiene: compose-port cannot judge - cannot read compose.yaml", "size src/a.py:1 - function long_one"]),
        (files({".env": "A=1\n", **compose('- "8080:80"')}), False, 1,
         [ENV_FINDING, "compose-port compose.yaml - ", "hygiene: 3 finding(s); verdicts: 10 clean, 3 findings"]),
        (files({}), False, 0,
         [f"{CLEAN_LINE}; skipped without --engines: secret, workflow-lint, workflow-security, advisory"]),
        (files({}), True, 0,
         [f"hygiene: clean ({FILE_RULES}, {ENGINE_RULES});"]),
        (files({"compose-\udcff.yaml": compose('- "8080:80"')["compose.yaml"]}), False, 1,
         ["compose-port compose-\\udcff.yaml - service web publishes port 8080:80"]),
        (files({**BASELINE, "src/\udcff.py": NEW_DEBT}), False, 1, ["size src/\\udcff.py:1 - function long_one"]),
        (files({"src/\udcff.py": "def broken(:\n"}), False, 0, ["functions unmeasured: src/\\udcff.py"]),
    ],
    ids=[
        "cell 1 unparsable size hides no env-file finding",
        "cell 2 secret cannot judge hides no env-file finding",
        "cell 3 unreadable compose hides no size finding",
        "cell 4 findings in two rules",
        "cell 5 clean names the file rules",
        "cell 6 clean with engines names all eleven rules",
        "cell 7 non-utf8 compose name",
        "cell 8 non-utf8 python name",
        "rule 4 non-utf8 python name in the size report",
    ],
)
def test_every_rule_is_judged_and_the_exit_is_the_worst(
    build_repo, elsewhere, clean_engines, capsys, arrange, with_engines, expected_code, expected_lines
):
    """#55 rules 1-4, cells 1-8 (cell 9 is the exit-code table's non-utf8 env case): each rule prints its own
    verdict, one rule's refusal hides no other rule's, the exit is the worst verdict's, the clean line names the
    rules, and every path prints backslash-escaped."""
    engine_arguments = ["--engines", str(clean_engines)] if with_engines else []
    code = cli.main(["hygiene", *engine_arguments, str(arrange(build_repo, elsewhere))])
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert code == expected_code, output
    assert [line for line in expected_lines if line not in output] == [], output


def test_a_finding_under_a_rule_its_check_does_not_judge_fails_loud(build_repo, monkeypatch):
    """#55 rule 2: a finding under a rule the check was not given is never dropped into a clean exit."""
    foreign = Finding("workflow-other", ".env", None, "an unlisted rule", "none")
    monkeypatch.setattr(hygiene, "check_env_files", lambda tracked: [foreign])
    arguments = ["hygiene", str(build_repo({}))]
    with pytest.raises(ValueError, match="workflow-other"):
        cli.main(arguments)


def test_a_refusing_workflow_engine_stops_no_other_workflow_rule(build_repo, clean_engines, capsys):
    """#55 rule 1: actionlint cannot judge, yet zizmor still runs and its finding prints under its own rule."""
    zizmor_report = Path(__file__).parent / "fixtures" / "engine-outputs" / "zizmor-finding.json"
    fake_engine(clean_engines, "actionlint", on_scan="echo boom >&2; exit 3")
    fake_engine(clean_engines, "zizmor", on_scan=f"cat '{zizmor_report}'; exit 12")
    repo = build_repo({".github/workflows/w.yml": "on: push\n"})
    code = cli.main(["hygiene", "--engines", str(clean_engines), str(repo)])
    captured = capsys.readouterr()
    assert code == 2
    assert "hygiene: workflow-lint cannot judge - actionlint exited 3" in captured.err
    assert "workflow-security .github/workflows/w.yml:7 - zizmor unpinned-uses" in captured.out


def locked_parent(build_repo, tmp_path):
    (tmp_path / "locked" / "repo").mkdir(parents=True)
    (tmp_path / "locked").chmod(0)
    return tmp_path / "locked" / "repo"


def bare_repository(build_repo, tmp_path):
    subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / "bare.git")], check=True)
    return tmp_path / "bare.git"


def git_directory(build_repo, tmp_path):
    return build_repo({}) / ".git"


@pytest.mark.parametrize(
    "arrange",
    [
        pytest.param(locked_parent, marks=pytest.mark.skipif(os.geteuid() == 0, reason="root passes a mode-0 parent")),
        bare_repository,
        git_directory,
    ],
    ids=["parent without permission", "bare repository", "inside .git"],
)
@pytest.mark.parametrize("command", [["hygiene"], ["baseline", "write"]])
def test_a_path_git_cannot_read_names_the_path_as_given_and_never_a_traceback(
    build_repo, tmp_path, capsys, arrange, command
):
    """#76 rule 2, cell 15: git's refusal, whichever its cause, is exit 2 naming the PATH."""
    path = arrange(build_repo, tmp_path)
    try:
        code = cli.main([*command, str(path)])
    finally:
        if (tmp_path / "locked").exists():
            (tmp_path / "locked").chmod(0o755)
    err = capsys.readouterr().err
    assert (code, f"{path} is not a readable git repository" in err, "Traceback" in err) == (2, True, False), err


@pytest.mark.integration
def test_the_hint_for_a_path_git_refuses_quotes_it(tmp_path, monkeypatch, capsys):
    """#76 rule 2, cell 16: the command the message advises runs as printed, whatever the PATH holds."""
    (tmp_path / "a b").write_text("x")
    monkeypatch.chdir(tmp_path)
    code = cli.main(["hygiene", "a b"])
    err = capsys.readouterr().err
    assert (code, "a b is not a readable git repository: git rev-parse exited 128 (`git -C 'a b' status` says why)" in err) == (2, True), err


def test_a_working_directory_that_was_removed_is_cannot_judge_not_a_traceback(tmp_path, monkeypatch, capsys):
    """#76 E8, rule 2: a relative PATH cannot be resolved without a working directory."""
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()
    results = [(command, cli.main(command), capsys.readouterr().err) for command in (["hygiene", "."], ["baseline", "write", "."])]
    assert [(code, ". is not a readable git repository: the working directory cannot be read ([Errno 2]" in err, "Traceback" in err) for _, code, err in results] == [(2, True, False)] * 2, results


@pytest.mark.integration
def test_a_machine_without_git_is_cannot_judge_not_a_traceback(tmp_path, monkeypatch, capsys):
    """#76 rule 2: git missing from the PATH names that git did not run."""
    repo = make_repo(tmp_path, {"src/a.py": "x = 1\n"})
    nowhere = tmp_path / "empty-bin"
    nowhere.mkdir()
    monkeypatch.setenv("PATH", str(nowhere))
    code = cli.main(["hygiene", str(repo)])
    err = capsys.readouterr().err
    assert (code, f"{repo} is not a readable git repository: git did not run ([Errno 2]" in err, "Traceback" in err) == (2, True, False), err


@pytest.mark.integration
def test_compose_files_are_read_and_parsed_one_at_a_time(tmp_path, capsys):
    """#76 E6, rule 1, cell 4: the first file's YAML error is reported although a later file cannot be decoded."""
    repo = make_repo(tmp_path, {"docs/compose-a.yaml": "a: [\n", "docs/compose-b.yaml": "x"})
    (repo / "docs/compose-b.yaml").write_bytes(b"\xff")
    code, output = run(capsys, repo)
    assert (code, "compose-port cannot judge - cannot read docs/compose-a.yaml" in output, "compose-b.yaml" in output) == (2, True, False), output


def make_submodule_path(tmp_path: Path) -> tuple[Path, Path]:
    (tmp_path / "inner").mkdir()
    (tmp_path / "outer").mkdir()
    inner = make_repo(tmp_path / "inner", {"src/a.py": "x = 1\n"})
    outer = make_repo(tmp_path / "outer", {})
    subprocess.run(["git", "-C", str(outer), "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(inner), "sub"], check=True)
    return outer, outer / "sub"


def make_linked_worktree_path(tmp_path: Path) -> tuple[Path, Path]:
    (tmp_path / "main").mkdir()
    main = make_repo(tmp_path / "main", {"src/a.py": "x = 1\n"})
    linked = tmp_path / "linked"
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", str(linked)], check=True)
    return main, linked


@pytest.mark.integration
@pytest.mark.parametrize("make", [make_submodule_path, make_linked_worktree_path], ids=["submodule", "linked worktree"])
def test_a_path_in_a_submodule_or_linked_worktree_means_its_own_top(tmp_path, capsys, make):
    """#76 rule 1, cell 19 (E7): the write lands in the work tree the PATH lies in, not in the superproject or main checkout."""
    superproject, own_top = make(tmp_path)
    assert cli.main(["baseline", "write", str(own_top / "src")]) == 0
    capsys.readouterr()
    assert ((own_top / "quality/baseline.toml").is_file(), (superproject / "quality").exists()) == (True, False)


UNPRINTABLE_NAME_HINT = "remove it from the index by its bytes (git ls-files -z shows them), then add it to .gitignore."
FIX_SUFFIX = " and add it to .gitignore."
SHELLS = ["dash", "bash"]


def printed_fix(output: str, shown_name: str) -> str:
    """The command the `env-file` finding for `shown_name` prints after `Fix: `, without the closing sentence."""
    fix = re.search(rf"^env-file {re.escape(shown_name)} - .*\n  Fix: (.*)$", output, re.MULTILINE)
    assert fix, output
    assert fix.group(1).endswith(FIX_SUFFIX), output
    return fix.group(1).removesuffix(FIX_SUFFIX)


def tracked_names(repo: Path) -> set[bytes]:
    listing = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], check=True, capture_output=True).stdout
    return {name for name in listing.split(b"\0") if name}


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    ("target", "neighbours", "printed_word"),
    [
        (".env", [], ".env"),
        ("config/prod.env", [], "config/prod.env"),
        ("my file.env", [], "'my file.env'"),
        ("sub dir/.env.prod", [], "'sub dir/.env.prod'"),
        ("it's.env", [], "'it'\"'\"'s.env'"),
        ("grün.env", [], "'grün.env'"),
        ("-x.env", [], "-x.env"),
        (":x.env", ["x.env"], ":x.env"),
        ("*.env", ["a.env"], "'*.env'"),
        ("[x].env", ["x.env"], "'[x].env'"),
        ("100%.env", [], "100%.env"),
        ("back\\slash.env", [], "'back\\slash.env'"),
        ("=x.env", [], "'=x.env'"),
    ],
    ids=[
        "#86 cell 1 bare name", "#86 cell 1 nested name", "#86 cell 2 space", "#86 cell 2 space in directory",
        "#86 cell 2 apostrophe", "#86 cell 2 umlaut", "#86 cell 3 leading dash", "#86 cell 4 colon beside the plain name",
        "#86 cell 4 star beside a.env", "#86 cell 4 brackets beside x.env", "#86 cell 5 percent", "#86 cell 5 backslash",
        "#86 cell 6 leading equals",
    ],
)
def test_the_printed_env_fix_untracks_exactly_the_named_file_in_every_shell(
    build_repo, capsys, shell, target, neighbours, printed_word
):
    """#86 rule 2, E1-E3 and E5: the command as printed, run from the top, untracks that file and leaves it on disk."""
    shell_path = shutil.which(shell)
    if shell_path is None:
        pytest.skip(f"{shell} is not installed")
    repo = build_repo({name: "A=1\n" for name in [target, *neighbours]})
    before = tracked_names(repo)
    _, output = run(capsys, repo)
    fix = printed_fix(output, target)
    assert fix == f"git --literal-pathspecs rm --cached -- {printed_word}"
    subprocess.run([shell_path, "-c", fix], cwd=repo, check=True, capture_output=True)
    assert tracked_names(repo) == before - {os.fsencode(target)}
    assert (repo / target).exists()


@pytest.mark.parametrize(
    "name",
    ["lat\udce9.env", "line\nfeed.env", "esc\x1bape.env", "c1\x85.env", "del\x7f.env"],
    ids=["#86 cell 7 invalid UTF-8", "#86 cell 8 line feed", "#86 cell 8 escape", "#86 cell 8 C1 control", "#86 cell 8 DEL"],
)
def test_an_unprintable_env_name_gets_a_hint_and_never_a_command(build_repo, capsys, name):
    """#86 rule 2, E4: one hint line; #97 rule 2 prints the name on the finding line escaped."""
    _, output = run(capsys, build_repo({name: "A=1\n"}))
    assert f"\n  Fix: {UNPRINTABLE_NAME_HINT}\n" in output
    assert "rm --cached" not in output


@pytest.mark.integration
def test_the_hint_for_a_path_git_refuses_has_no_command_when_the_path_is_unprintable(tmp_path, monkeypatch, capsys):
    """#86 rule 1, cell 9: a PATH holding a line feed gets the hint without a `git -C` command."""
    (tmp_path / "a\nb").write_text("x")
    monkeypatch.chdir(tmp_path)
    code = cli.main(["hygiene", "a\nb"])
    err = capsys.readouterr().err
    assert code == 2
    assert "(`git status` run inside it says why)" in err
    assert "git -C" not in err


MODULE_DOOR = [sys.executable, "-m", "quality.cli"]
CONSOLE_SCRIPT_DOOR = [str(Path(sys.executable).parent / "quality")]


def quality_door(*arguments: str, cwd: Path, door: list[str] = MODULE_DOOR, **streams) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*door, *arguments], cwd=cwd, text=True, check=False, **streams)


@pytest.fixture
def env_and_broken_compose(build_repo) -> Path:
    return build_repo({".env": "A=1\n", "compose.yaml": "services: [\n"})


@pytest.mark.parametrize("door", [MODULE_DOOR, CONSOLE_SCRIPT_DOOR], ids=["module", "console script"])
def test_a_merged_pipe_shows_the_hygiene_lines_in_run_order(env_and_broken_compose, door):
    """#86 rule 3, cells 10 and 12a: reports, the finding with its fix, `cannot judge`, then the summary."""
    done = quality_door(
        "hygiene", ".", cwd=env_and_broken_compose, door=door, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    prefixes = ["size: ", "clone: ", "import-contract: ", "env-file .env - ", "  Fix: ", "hygiene: compose-port cannot judge - ", "root-layout .env - ", "  Fix: ", "hygiene: 2 finding(s)"]
    lines = done.stdout.splitlines()
    assert all(line.startswith(prefix) for line, prefix in zip(lines, prefixes, strict=True)), done.stdout


def test_discarding_stderr_hides_exactly_the_cannot_judge_line(env_and_broken_compose):
    """#86 rule 3, cell 11: no line changes its stream; the exit stays 2."""
    done = quality_door("hygiene", ".", cwd=env_and_broken_compose, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert (done.returncode, "compose-port cannot judge" in done.stdout, "env-file .env" in done.stdout) == (2, False, True)


SONAR_FINDINGS_SCRIPT = """
import sys
sys.path.insert(0, {repository_root!r})
from tests.support.sonar import issues_page, sonar_answering
from quality import cli
cli.run(lambda: cli.main(["sonar", "findings"], transport=sonar_answering(issues=issues_page(2, 1, 2)), sleep=lambda seconds: None))
"""


@pytest.mark.integration
def test_a_merged_pipe_lists_the_sonar_findings_before_the_red_summary(tmp_path):
    """#86 rule 3, cell 12: the findings precede the summary through `2>&1`."""
    from tests.support.sonar import PULL_REQUEST_ENVIRONMENT, TASK_FIXTURE, legacy_config_absent

    (tmp_path / "quality").mkdir()
    (tmp_path / "quality" / "config.toml").write_text(legacy_config_absent)
    (tmp_path / sonar.TASK_FILE).parent.mkdir()
    (tmp_path / sonar.TASK_FILE).write_text(TASK_FIXTURE.read_text())
    script = tmp_path / "findings.py"
    script.write_text(textwrap.dedent(SONAR_FINDINGS_SCRIPT).format(repository_root=str(Path(__file__).parents[1])))
    done = subprocess.run(
        [sys.executable, str(script)], cwd=tmp_path, text=True, check=False, env={**os.environ, **PULL_REQUEST_ENVIRONMENT},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    prefixes = ["sonar src/a1.py:1 - ", "  Fix: ", "sonar src/a2.py:2 - ", "  Fix: ", "sonar: new 2"]
    lines = done.stdout.splitlines()
    assert (done.returncode, all(line.startswith(prefix) for line, prefix in zip(lines, prefixes, strict=True))) == (1, True), done.stdout


def test_a_replaced_stdout_still_prints_the_help():
    """#86 rule 3: `main` leaves the process's streams alone, so a captured stdout works."""
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), pytest.raises(SystemExit) as stopped:
        cli.main(["--help"])
    assert (stopped.value.code, "hygiene" in captured.getvalue()) == (0, True)


@pytest.mark.integration
def test_a_closed_stdout_stream_is_cannot_judge_with_its_diagnostic(tmp_path, capsys, monkeypatch):
    """#86 rule 3: a closed stdout object never crashes `main`; the diagnostic reaches stderr."""
    monkeypatch.setenv("NO_COLOR", "1")  # Python 3.14's argparse otherwise probes the closed stdout for colour
    closed = io.TextIOWrapper(io.BytesIO())
    closed.close()
    with contextlib.redirect_stdout(closed):
        code = cli.main(["hygiene", str(tmp_path / "missing")])
    assert (code, "hygiene: cannot judge" in capsys.readouterr().err) == (2, True)


@pytest.mark.integration
def test_a_closed_stdout_is_cannot_judge_and_never_a_crash(tmp_path):
    """#86 rule 3: with fd 1 closed the door still exits 2 with its cannot-judge line."""
    done = subprocess.run(
        ["bash", "-c", '"$PY" -m quality.cli hygiene "$MISSING" >&-'],
        env={**os.environ, "PY": sys.executable, "MISSING": str(tmp_path / "missing")},
        text=True, check=False, stderr=subprocess.PIPE,
    )
    assert (done.returncode, "hygiene: cannot judge" in done.stderr) == (2, True), done.stderr
