import re
import subprocess
from pathlib import Path

import pytest

from quality import cli, engines, hygiene
from quality.report import Finding

EDGE_STACK = (Path(__file__).parent / "data" / "edge_stack.yml").read_text(encoding="utf-8")
CLEAN_FILE_RULES = "hygiene: clean (env-file, compose-port, size)"


def compose(*port_lines: str) -> dict[str, str]:
    body = "".join(f"      {line}\n" for line in port_lines)
    return {"compose.yaml": f"services:\n  web:\n    image: x\n    ports:\n{body}"}


def run(capsys: pytest.CaptureFixture[str], path: Path) -> tuple[int, str]:
    code = cli.main(["hygiene", str(path)])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def unreadable_compose_repo(build_repo, tmp_path):
    repo = build_repo(compose('- "127.0.0.1:8080:80"'))
    (repo / "compose.yaml").unlink()
    return repo


def truncated_compose_repo(build_repo, tmp_path):
    return build_repo({"compose.yaml": "services: [\n"})


def non_utf8_filename_repo(build_repo, tmp_path):
    return build_repo({"\udcff.txt": "x\n"})


@pytest.mark.parametrize(
    ("arrange", "expected_code", "expected_text"),
    [
        (lambda build_repo, tmp_path: build_repo({}), 0, CLEAN_FILE_RULES),
        (non_utf8_filename_repo, 0, CLEAN_FILE_RULES),
        (lambda build_repo, tmp_path: tmp_path, 2, "cannot judge"),
        (lambda build_repo, tmp_path: build_repo({".env": "A=1\n"}), 1, "finding(s)"),
        (lambda build_repo, tmp_path: build_repo({"\udcff.env": "A=1\n"}), 1, "env-file \\udcff.env - "),
        (unreadable_compose_repo, 2, "cannot judge"),
        (truncated_compose_repo, 2, "cannot judge"),
    ],
    ids=[
        "empty-repository",
        "non-utf8-filename",
        "not-a-git-repository",
        "tracked-env-file",
        "cell 9 non-utf8-env-filename-rendered-safely",
        "unreadable-compose",
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
    assert (code, CLEAN_FILE_RULES in output) == (0, True)


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
        (".env.example", False),
        ("docker/.env.docker.example", False),
        ("environment.txt", False),
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
        ({"compose.yaml": EDGE_STACK}, None),
        ({"README.md": "no compose here\n"}, None),
    ],
)
def test_compose_ports_must_bind_loopback(build_repo, capsys, files, refused_path):
    """#3 rule 2/4: a compose port beyond loopback is refused with rule id, path, reason and fix; edge's real file passes."""
    code, output = run(capsys, build_repo(files))
    assert code == (0 if refused_path is None else 1)
    if refused_path:
        assert_refusal(output, "compose-port", refused_path)


def test_quoted_tagged_port_is_read_as_the_string_it_wraps(build_repo, capsys):
    """#3 rule 4: `!override "22:22"` is the string 22:22, not a sexagesimal number."""
    code, output = run(capsys, build_repo(compose('- !override "22:22"')))
    assert code == 1
    assert "port 22:22 " in output


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


@pytest.fixture
def elsewhere(tmp_path_factory) -> Path:
    """A scratch directory outside the repository, which build_repo builds in tmp_path."""
    return tmp_path_factory.mktemp("elsewhere")


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


def test_a_tracked_compose_symlink_loop_is_exit_2(build_repo, capsys):
    """#31 rule 1 (S8705) and #52 rule 5, cell 19: a tracked compose file that loops through symlinks is exit 2,
    never a traceback, and the reason names no absolute path."""
    repo = build_repo({})
    (repo / "compose.yaml").symlink_to("compose.yaml")
    subprocess.run(["git", "-C", str(repo), "add", "compose.yaml"], check=True)
    code, output = run(capsys, repo)
    assert code == 2
    assert "cannot read compose.yaml" in output
    assert str(repo) not in output


BASELINE = {"quality/baseline.toml": ""}
NEW_DEBT = "def long_one():\n" + "    x = 1\n" * 61
ENV_FINDING = "env-file .env - "


@pytest.fixture
def clean_engines(tmp_path_factory) -> Path:
    """A gitleaks that reports its pin and no leak; with no workflow tracked, no other engine runs."""
    directory = tmp_path_factory.mktemp("engines")
    gitleaks = directory / "gitleaks"
    gitleaks.write_text(
        f'#!/bin/sh\nif [ "$1" = version ]; then echo {engines.read_pins()["gitleaks"].version}; exit 0; fi\n'
        'while [ $# -gt 0 ]; do [ "$1" = --report-path ] && report=$2; shift; done\necho "[]" > "$report"\n'
    )
    gitleaks.chmod(0o755)
    return directory


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
        (files({**BASELINE, ".env": "A=1\n", "bad.py": "def broken(:\n"}), False, 2,
         [ENV_FINDING, "hygiene: size unparsable - bad.py is not valid Python",
          "hygiene: 1 finding(s); verdicts: 1 clean, 1 findings, 1 unparsable"]),
        (config_linked_out_of_the_repository, True, 2,
         [ENV_FINDING, "hygiene: secret cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: 1 finding(s); verdicts: 4 clean, 1 findings, 1 cannot judge"]),
        (files({**BASELINE, "compose.yaml": "services: [\n", "src/a.py": NEW_DEBT}), False, 2,
         ["hygiene: compose-port cannot judge - cannot read compose.yaml", "size src/a.py:1 - function long_one"]),
        (files({".env": "A=1\n", **compose('- "8080:80"')}), False, 1,
         [ENV_FINDING, "compose-port compose.yaml - ", "hygiene: 2 finding(s); verdicts: 1 clean, 2 findings"]),
        (files({}), False, 0,
         [f"{CLEAN_FILE_RULES}; skipped without --engines: secret, workflow-lint, workflow-security"]),
        (files({}), True, 0,
         ["hygiene: clean (env-file, compose-port, size, secret, workflow-lint, workflow-security);"]),
        (files({"compose\udcff.yaml": compose('- "8080:80"')["compose.yaml"]}), False, 1,
         ["compose-port compose\\udcff.yaml - service web publishes port 8080:80"]),
        (files({**BASELINE, "src/\udcff.py": NEW_DEBT}), False, 1, ["size src/\\udcff.py:1 - function long_one"]),
        (files({"src/\udcff.py": "def broken(:\n"}), False, 0, ["functions unmeasured: src/\\udcff.py"]),
    ],
    ids=[
        "cell 1 unparsable size hides no env-file finding",
        "cell 2 secret cannot judge hides no env-file finding",
        "cell 3 unreadable compose hides no size finding",
        "cell 4 findings in two rules",
        "cell 5 clean names the file rules",
        "cell 6 clean with engines names all six rules",
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
    with pytest.raises(ValueError, match="workflow-other"):
        cli.main(["hygiene", str(build_repo({}))])
