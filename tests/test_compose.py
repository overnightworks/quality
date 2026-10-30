import os
import subprocess
from pathlib import Path

import pytest

from tests.support.check import assert_refusal, compose, is_clean, refusal_row, refused
from tests.support.check import hygiene_output as run
from tests.support.git_repositories import make_repo


def test_compose_specific_tags_are_judged_not_refused(build_repo, capsys):
    """#3 rule 1/4: valid Compose YAML tags are read, and their ports still checked."""
    body = 'services:\n  web:\n    image: x\n    ports: !override\n      - "127.0.0.1:80:80"\n    profiles: !reset []\n'
    code, output = run(capsys, build_repo({"compose.yaml": body}))
    assert (code, is_clean(output)) == (0, True)


@pytest.mark.parametrize("tagged", ["!reset null", "!reset []"], ids=["null", "empty-list"])
def test_reset_clears_ports_and_is_clean(build_repo, capsys, tagged):
    """#3 rule 1/4: `ports: !reset null` is valid Compose that clears the ports, so it is clean."""
    code, _ = run(capsys, build_repo({"compose.yaml": f"services:\n  web:\n    image: x\n    ports: {tagged}\n"}))
    assert code == 0


def test_unknown_yaml_tag_cannot_be_judged(build_repo, capsys):
    """#3 rule 1: a tag Compose does not define is not read as a plain value."""
    code, output = run(capsys, build_repo({"compose.yaml": "services:\n  web:\n    image: !foo x\n"}))
    assert code == 2
    assert refused(output, "files")


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
    """#3 rule 2/4: a compose port beyond loopback is refused with rule id, path, reason and fix; edge's real file is judged in test_compose_container_host (#78 cell 15)."""
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
    assert (code, "has a port that is not a string, a number or a mapping whose published (or, without one, target) is text or a number (unreadable)" in output, max(len(line) for line in output.splitlines() if "has a port" in line) < 400) == (1, True, True), output


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
    assert (code, is_clean(output), str(repo) in output) == (0, True, False), output


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 file, so no permission is refused")
def test_a_tracked_compose_the_filesystem_refuses_to_read_is_exit_2(build_repo, capsys):
    """#76 rule 3, cell 9 (E10): a compose file without read permission is never gone."""
    repo = build_repo(compose('- "127.0.0.1:8080:80"'))
    (repo / "compose.yaml").chmod(0)
    code, output = run(capsys, repo)
    assert (code, "cannot read compose.yaml" in output, str(repo) in output) == (2, True, False), output


@pytest.mark.integration
def test_compose_files_are_read_and_parsed_one_at_a_time(tmp_path, capsys):
    """#76 E6, rule 1, cell 4: the first file's YAML error is reported although a later file cannot be decoded."""
    repo = make_repo(tmp_path, {"docs/compose-a.yaml": "a: [\n", "docs/compose-b.yaml": "x"})
    (repo / "docs/compose-b.yaml").write_bytes(b"\xff")
    code, output = run(capsys, repo)
    assert (code, f"{refusal_row('files')}cannot read docs/compose-a.yaml" in output, "compose-b.yaml" in output) == (2, True, False), output
